"""Serialize dependency graph edits, then invalidate semantic consumers atomically.

Graph writers acquire the advisory guard before resolving consumers, then Videos
in ID order, then the dependency. Completion only acquires Video -> Product ->
Brand and never the graph guard. Video creation also uses the guard, so a new
consumer cannot slip between resolution and mutation. VideoPatch cannot rebind
dependencies. Product brand reassignment uses this same guarded path.
"""

from contextlib import asynccontextmanager

from sqlalchemy import inspect, or_, select, text

from apps.api.app.core.errors import AppError
from apps.api.app.db.locking import lock_revisioned_row
from apps.api.app.db.models import Brand, Product, SceneGeneration, Video
from apps.api.app.services.generation_freshness import dependency_semantics

DEPENDENCY_GRAPH_LOCK = 1907310427


async def lock_dependency_graph(session):
    if session.bind.dialect.name == "postgresql":
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"), {"key": DEPENDENCY_GRAPH_LOCK}
        )


async def lock_pending_dependency_bindings(session):
    """Fence unflushed consumer links before resolving a generation snapshot.

    A graph editor may already have resolved consumers without seeing these
    links. Do not wait for its guard: a service caller may already hold Video,
    which would invert the graph -> Video order. Reject without flushing and
    let the caller retry the transaction instead.
    """
    if session.bind.dialect.name != "postgresql":
        return
    pending = False
    for row in session.dirty:
        if isinstance(row, Video):
            keys = ("product_id", "brand_id")
        elif isinstance(row, Product):
            keys = ("brand_id",)
        else:
            continue
        if any(inspect(row).attrs[key].history.has_changes() for key in keys):
            pending = True
            break
    if pending:
        with session.no_autoflush:
            acquired = await session.scalar(
                text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": DEPENDENCY_GRAPH_LOCK}
            )
        if not acquired:
            raise AppError(
                "REVISION_CONFLICT", "Dependency graph is being edited; retry the transaction", 412
            )


@asynccontextmanager
async def _invalidate_dependents(session, model, identity, predicate):
    # Consumer discovery reads persisted bindings. Never let a pending graph
    # rewrite hide a consumer, and never flush it before the canonical locks.
    for pending in session.dirty:
        keys = (
            ("product_id", "brand_id")
            if isinstance(pending, Video)
            else ("brand_id",)
            if isinstance(pending, Product)
            else ()
        )
        if any(inspect(pending).attrs[key].history.has_changes() for key in keys):
            raise AppError(
                "REVISION_CONFLICT",
                "Persist dependency bindings before editing dependency content",
                412,
            )
    await lock_dependency_graph(session)
    video_ids = list(
        (
            await session.scalars(
                select(Video.id)
                .where(predicate)
                .order_by(Video.id)
                .with_for_update()
                .execution_options(autoflush=False)
            )
        ).all()
    )
    videos = [await lock_revisioned_row(session, Video, video_id) for video_id in video_ids]
    row = await lock_revisioned_row(session, model, identity)
    if row is None:
        kind = "PRODUCT" if model is Product else "BRAND"
        raise AppError(f"{kind}_NOT_FOUND", f"{kind.title()} not found", 404)
    before = dependency_semantics(row, identity)
    # A prior successful PATCH may still be dirty with autoflush disabled.
    # Account only for edits invalidated in this transaction; arbitrary staged
    # content must not turn a real dependency change into a semantic no-op.
    transaction = (
        session.sync_session.get_nested_transaction() or session.sync_session.get_transaction()
    )
    accounted_transaction, accounted = session.info.get("dependency_invalidations", (None, {}))
    if accounted_transaction is not transaction:
        accounted = {}
        session.info["dependency_invalidations"] = (transaction, accounted)
    semantic_pending = any(
        inspect(row).attrs[key].history.has_changes()
        for key in ("name", "description", "context", "archived")
    )
    if semantic_pending and accounted.get((model, identity)) != before:
        raise AppError(
            "REVISION_CONFLICT",
            "Pending dependency content has not been invalidated; retry the edit",
            412,
        )
    old_brand_id = row.brand_id if model is Product else None
    yield row
    semantic_changed = before != dependency_semantics(row, identity)
    link_changed = model is Product and old_brand_id != row.brand_id
    affected = [
        video for video in videos if semantic_changed or (link_changed and not video.brand_id)
    ]
    if not affected:
        accounted[(model, identity)] = dependency_semantics(row, identity)
        return
    generated = set(
        (
            await session.scalars(
                select(SceneGeneration.video_id)
                .where(SceneGeneration.video_id.in_([video.id for video in affected]))
                .distinct()
            )
        ).all()
    )
    for video in affected:
        video.revision += 1
        if video.status != "ARCHIVED" and (video.id in generated or video.current_final_video_id):
            video.status = "DIRTY"
    accounted[(model, identity)] = dependency_semantics(row, identity)


def invalidate_product_dependents(session, product_id):
    return _invalidate_dependents(session, Product, product_id, Video.product_id == product_id)


def invalidate_brand_dependents(session, brand_id):
    inherited_products = select(Product.id).where(Product.brand_id == brand_id)
    return _invalidate_dependents(
        session,
        Brand,
        brand_id,
        or_(
            Video.brand_id == brand_id,
            Video.brand_id.is_(None) & Video.product_id.in_(inherited_products),
        ),
    )

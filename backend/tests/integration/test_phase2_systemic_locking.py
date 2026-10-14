"""Agent A: adversarial dirty identity-map checks using disposable SQLite only.

These exercise refresh/history semantics, not PostgreSQL lock serialization.
"""

from copy import deepcopy

import pytest
from sqlalchemy import event, inspect, select, update

from apps.api.app.core.errors import AppError
from apps.api.app.db.locking import lock_revisioned_row
from apps.api.app.db.models import Brand, Product, Project, Video
from apps.api.app.services.video_dependency_guard import (
    lock_creation_dependencies,
    lock_video_dependencies,
    read_video_dependencies,
)
from tests.integration.test_video_dependency_guard import creation, seed_guard

ROWS = [(Project, "project"), (Product, "product"), (Brand, "brand"), (Video, "video")]


@pytest.mark.parametrize("admin", [False, True])
async def test_actual_product_brand_archive_cannot_hide_behind_override(session_factory, admin):
    ids = await seed_guard(session_factory)
    async with session_factory() as session, session.begin():
        video = await session.get(Video, ids["video"])
        video.brand_id = ids["override"]
        await session.flush()
        actual_brand = await session.get(Brand, ids["brand"])
        principal = ids["admin"] if admin else ids["owner"]
        await read_video_dependencies(session, video, user_id=principal, is_admin=admin)
        await session.execute(
            update(Brand).where(Brand.id == actual_brand.id)
            .values(archived=True, revision=2)
            .execution_options(synchronize_session=False)
        )
        assert actual_brand.archived is False
        for helper in (read_video_dependencies, lock_video_dependencies):
            with pytest.raises(AppError) as rejected:
                await helper(session, video, user_id=principal, is_admin=admin)
            assert (rejected.value.status_code, rejected.value.code) == (409, "BRAND_NOT_ACTIVE")
        assert actual_brand.archived is True
        assert video.revision == 1


@pytest.mark.parametrize("model,key", ROWS)
async def test_dirty_refresh_preserves_intent_and_reads_other_columns(session_factory, model, key):
    ids = await seed_guard(session_factory)
    async with session_factory() as session, session.begin():
        session.autoflush = True
        row = await session.get(model, ids[key])
        field = "title" if model is Video else "name"
        fresh_field = "brief" if model is Video else "description"
        setattr(row, field, "pending intent")
        # Simulate persisted-column change without refreshing the identity map.
        # The changed field's baseline and revision remain compatible.
        with session.no_autoflush:
            await session.execute(
                update(model)
                .where(model.id == row.id)
                .values(**{fresh_field: "fresh database text"})
                .execution_options(synchronize_session=False)
            )
        flushes = []

        def observe_flush(*_args):
            flushes.append(True)

        event.listen(session.sync_session, "before_flush", observe_flush)
        try:
            for _ in range(2):
                locked = await lock_revisioned_row(session, model, row.id)
                assert locked is row
                assert getattr(row, field) == "pending intent"
                assert getattr(row, fresh_field) == "fresh database text"
                assert inspect(row).attrs[field].history.has_changes()
                with session.no_autoflush:
                    persisted = await session.scalar(
                        select(getattr(model, field)).where(model.id == row.id)
                    )
                assert persisted != "pending intent"
            assert flushes == []
        finally:
            event.remove(session.sync_session, "before_flush", observe_flush)
    async with session_factory() as session:
        persisted = await session.get(model, ids[key])
        assert getattr(persisted, field) == "pending intent"
        assert getattr(persisted, fresh_field) == "fresh database text"


@pytest.mark.parametrize("model,key", ROWS)
async def test_same_revision_changed_baseline_rejects_without_erasing_intent(
    session_factory, model, key
):
    ids = await seed_guard(session_factory)
    async with session_factory() as session, session.begin():
        row = await session.get(model, ids[key])
        field = "title" if model is Video else "name"
        setattr(row, field, "pending intent")
        history = deepcopy(inspect(row).attrs[field].history)
        await session.execute(
            update(model)
            .where(model.id == row.id)
            .values(**{field: "concurrent intent"})
            .execution_options(synchronize_session=False)
        )
        with pytest.raises(AppError) as raised:
            await lock_revisioned_row(session, model, row.id)
        assert (raised.value.status_code, raised.value.code) == (412, "REVISION_CONFLICT")
        assert getattr(row, field) == "pending intent"
        assert inspect(row).attrs[field].history == history
        assert await session.scalar(select(model.revision).where(model.id == row.id)) == 1
        await session.rollback()


@pytest.mark.parametrize("model,key", ROWS)
async def test_pending_owner_change_is_immutable(session_factory, model, key):
    ids = await seed_guard(session_factory)
    async with session_factory() as session, session.begin():
        row = await session.get(model, ids[key])
        row.created_by = ids["other"]
        history = deepcopy(inspect(row).attrs.created_by.history)
        with pytest.raises(AppError) as raised:
            await lock_revisioned_row(
                session, model, row.id, immutable_fields=("created_by",)
            )
        assert (raised.value.status_code, raised.value.code) == (412, "REVISION_CONFLICT")
        assert row.created_by == ids["other"]
        assert inspect(row).attrs.created_by.history == history
        assert await session.scalar(select(model.created_by).where(model.id == row.id)) == ids[
            "owner"
        ]
        await session.rollback()


async def test_unknown_dirty_baseline_fails_closed(session_factory):
    ids = await seed_guard(session_factory)
    async with session_factory() as session, session.begin():
        product = await session.get(Product, ids["product"])
        session.expire(product, ["context"])
        product.context = {"pending": True}
        history = deepcopy(inspect(product).attrs.context.history)
        assert not history.deleted
        with pytest.raises(AppError) as raised:
            await lock_creation_dependencies(session, **creation(ids))
        assert (raised.value.status_code, raised.value.code) == (412, "REVISION_CONFLICT")
        assert product.context == {"pending": True}
        assert inspect(product).attrs.context.history == history
        await session.rollback()


async def test_pending_revision_increment_is_preserved(session_factory):
    ids = await seed_guard(session_factory)
    async with session_factory() as session, session.begin():
        product = await session.get(Product, ids["product"])
        product.context = {"pending": True}
        product.revision += 1
        locked = await lock_creation_dependencies(session, **creation(ids))
        assert locked.product.context == {"pending": True}
        assert locked.product.revision == 2
        assert inspect(product).attrs.revision.history.deleted == [1]
        assert await session.scalar(
            select(Product.revision).where(Product.id == product.id)
        ) == 1
    async with session_factory() as session:
        product = await session.get(Product, ids["product"])
        assert product.context == {"pending": True}
        assert product.revision == 2


async def test_creation_graph_lock_precedes_all_row_locks(session_factory, monkeypatch):
    import apps.api.app.services.video_dependency_guard as guard

    ids = await seed_guard(session_factory)
    calls = []
    original_graph = guard.lock_dependency_graph
    original_row = guard.lock_revisioned_row

    async def graph(session):
        calls.append("graph")
        await original_graph(session)

    async def row(session, model, identity, **kwargs):
        calls.append((model, identity))
        return await original_row(session, model, identity, **kwargs)

    monkeypatch.setattr(guard, "lock_dependency_graph", graph)
    monkeypatch.setattr(guard, "lock_revisioned_row", row)
    async with session_factory() as session, session.begin():
        await lock_creation_dependencies(session, **creation(ids))
    assert calls == [
        "graph",
        (Project, ids["project"]),
        (Product, ids["product"]),
        (Brand, ids["brand"]),
    ]

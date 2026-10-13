"""Validate Video parent ownership without trusting preloaded ORM dependencies.

Creation takes graph -> Project -> Product -> Brands (ID order). Existing writes
take Video -> Project -> Product -> Brands -> children, never a blocking graph
lock after Video. Both Product's own Brand and an explicit override are checked;
existing same-owner overrides are supported, creation retains its pairing rule.

Results contain plain column snapshots, usable after releasing the session. A
locked result reflects refreshed persisted rows plus pending intent verified by
lock_revisioned_row; it never discards edits or flushes the unit of work.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.core.errors import AppError
from apps.api.app.db.locking import lock_revisioned_row
from apps.api.app.db.models import Brand, Product, Project, Video
from apps.api.app.services.deletion_state import is_logically_deleted
from apps.api.app.services.generation_dependency_invalidation import (
    lock_dependency_graph,
    lock_pending_dependency_bindings,
)


@dataclass(frozen=True)
class DependencySnapshot:
    """All scalar planner-context fields, with no ORM state or lazy loading."""

    id: str
    name: str
    description: str
    archived: bool
    revision: int
    created_by: str
    created_at: datetime
    updated_at: datetime
    context: dict[str, Any] | None = None
    brand_id: str | None = None


@dataclass(frozen=True)
class VideoDependencies:
    project: DependencySnapshot
    product: DependencySnapshot | None
    effective_brand: DependencySnapshot | None
    business_owner_id: str
    authoritative: bool
    video: Video | None = None


def _not_found(model: type) -> AppError:
    kind = model.__name__.upper()
    return AppError(f"{kind}_NOT_FOUND", f"{model.__name__} not found", 404)


async def _columns(session: AsyncSession, model: type, identity: str) -> dict:
    # Column projection bypasses identity-map column reuse entirely, and does
    # not overwrite either dirty attributes or their change histories.
    result = await session.execute(select(*model.__table__.c).where(model.id == identity))
    row = result.mappings().one_or_none()
    if row is None:
        raise _not_found(model)
    return dict(row)


async def _locked(session: AsyncSession, model: type, identity: str) -> dict:
    immutable = ("created_by", "project_id") if model is Video else ("created_by",)
    row = await lock_revisioned_row(session, model, identity, immutable_fields=immutable)
    if row is None:
        raise _not_found(model)
    return {column.key: deepcopy(getattr(row, column.key)) for column in model.__table__.c}


def _snapshot(values: dict) -> DependencySnapshot:
    return DependencySnapshot(
        **{
            key: deepcopy(values[key])
            for key in DependencySnapshot.__dataclass_fields__
            if key in values
        }
    )


def _owner(values: dict, model: type, owner: str) -> None:
    if values["created_by"] != owner:
        raise _not_found(model)


def _active(values: dict, model: type) -> None:
    if values["archived"]:
        kind = model.__name__.upper()
        raise AppError(f"{kind}_NOT_ACTIVE", f"{model.__name__} is archived", 409)


async def _resolve(
    session: AsyncSession,
    *,
    project_id: str,
    product_id: str | None,
    brand_id: str | None,
    business_owner_id: str | None,
    user_id: str,
    is_admin: bool,
    locked: bool,
    require_active: bool,
    require_pairing: bool,
    video: Video | None = None,
) -> VideoDependencies:
    load = _locked if locked else _columns
    project = await load(session, Project, str(project_id))
    owner = project["created_by"] if business_owner_id is None else business_owner_id
    _owner(project, Project, owner)
    if not is_admin and owner != user_id:
        raise _not_found(Video if business_owner_id is not None else Project)
    product = await load(session, Product, str(product_id)) if product_id else None
    if product is not None:
        _owner(product, Product, owner)
    actual_brand_id = product["brand_id"] if product else None
    effective_brand_id = str(brand_id) if brand_id else actual_brand_id
    brands = {}
    for identity in sorted(
        {identity for identity in (actual_brand_id, effective_brand_id) if identity}
    ):
        brand = await load(session, Brand, identity)
        _owner(brand, Brand, owner)
        brands[identity] = brand
    # Ownership always precedes state/pairing errors, avoiding foreign metadata.
    if require_active:
        _active(project, Project)
        if product is not None:
            _active(product, Product)
        for brand in brands.values():
            _active(brand, Brand)
    if require_pairing and brand_id and actual_brand_id and str(brand_id) != actual_brand_id:
        raise AppError(
            "PRODUCT_BRAND_CONFLICT", "Selected product belongs to a different brand", 422
        )
    return VideoDependencies(
        project=_snapshot(project),
        product=_snapshot(product) if product is not None else None,
        effective_brand=_snapshot(brands[effective_brand_id]) if effective_brand_id else None,
        business_owner_id=owner,
        authoritative=locked,
        video=video,
    )


async def read_creation_dependencies(
    session: AsyncSession,
    *,
    project_id: str,
    product_id: str | None = None,
    brand_id: str | None = None,
    user_id: str,
    is_admin: bool = False,
) -> VideoDependencies:
    """Cheap persisted-state preflight; no locks, autoflush, or identity refresh."""
    with session.no_autoflush:
        return await _resolve(
            session,
            project_id=project_id,
            product_id=product_id,
            brand_id=brand_id,
            business_owner_id=None,
            user_id=user_id,
            is_admin=is_admin,
            locked=False,
            require_active=True,
            require_pairing=True,
        )


async def read_video_dependencies(
    session: AsyncSession,
    video: Video,
    *,
    user_id: str,
    is_admin: bool = False,
    require_active: bool = True,
) -> VideoDependencies:
    """Fresh persisted parent graph; optional archive checks for read endpoints."""
    with session.no_autoflush:
        persisted = await _columns(session, Video, video.id)
        if not is_admin and persisted["created_by"] != user_id:
            raise _not_found(Video)
        return await _resolve(
            session,
            project_id=persisted["project_id"],
            product_id=persisted["product_id"],
            brand_id=persisted["brand_id"],
            business_owner_id=persisted["created_by"],
            user_id=user_id,
            is_admin=is_admin,
            locked=False,
            require_active=require_active,
            require_pairing=False,
        )


async def lock_creation_dependencies(
    session: AsyncSession,
    *,
    project_id: str,
    product_id: str | None = None,
    brand_id: str | None = None,
    user_id: str,
    is_admin: bool = False,
) -> VideoDependencies:
    """Caller must preflight first; holds graph and dependency locks until commit."""
    with session.no_autoflush:
        await lock_dependency_graph(session)
        return await _resolve(
            session,
            project_id=project_id,
            product_id=product_id,
            brand_id=brand_id,
            business_owner_id=None,
            user_id=user_id,
            is_admin=is_admin,
            locked=True,
            require_active=True,
            require_pairing=True,
        )


async def lock_video_dependencies(
    session: AsyncSession,
    video: Video,
    *,
    user_id: str,
    is_admin: bool = False,
) -> VideoDependencies:
    """Call before child locks; safe if Video is already locked by this transaction.

    The pending-binding fence may only TRY the graph guard, never wait for it.
    Lock/refresh Video again to verify clean state and preserve compatible edits.
    """
    with session.no_autoflush:
        await lock_pending_dependency_bindings(session)
        locked_video = await lock_revisioned_row(
            session, Video, video.id, immutable_fields=("created_by", "project_id")
        )
        if locked_video is None:
            raise _not_found(Video)
        if await is_logically_deleted(session, "VIDEO", locked_video.id):
            raise _not_found(Video)
        persisted = {column.key: getattr(locked_video, column.key) for column in Video.__table__.c}
        if not is_admin and persisted["created_by"] != user_id:
            raise _not_found(Video)
        return await _resolve(
            session,
            project_id=persisted["project_id"],
            product_id=persisted["product_id"],
            brand_id=persisted["brand_id"],
            business_owner_id=persisted["created_by"],
            user_id=user_id,
            is_admin=is_admin,
            locked=True,
            require_active=True,
            require_pairing=False,
            video=locked_video,
        )

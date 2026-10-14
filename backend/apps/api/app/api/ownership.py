"""SQL-level ownership predicates shared by user-facing API routes."""

from __future__ import annotations

from typing import Any
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import and_, inspect, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.core.errors import AppError
from apps.api.app.db.models import (
    Asset,
    Brand,
    DirectorRun,
    DirectorRunAttempt,
    DirectorRunMember,
    FinalVideo,
    GenerationAsset,
    GenerationAttempt,
    Product,
    Project,
    Scene,
    SceneGeneration,
    Video,
)
from apps.api.app.services.deletion_state import not_logically_deleted_predicate


def is_admin(principal: Any) -> bool:
    state = inspect(principal, raiseerr=False)
    if state is not None:
        # ORM principals may be expired after a transaction boundary. Read only
        # loaded state; an authorization helper must never trigger implicit IO.
        return state.dict.get("role") == "ADMIN"
    return getattr(principal, "role", None) == "ADMIN"


def principal_id(principal: Any) -> Any:
    state = inspect(principal, raiseerr=False)
    if state is not None:
        return state.identity[0] if state.identity else state.dict.get("id")
    return getattr(principal, "id", principal)


def video_parent_consistency() -> Any:
    """Filter historical corrupt graphs without changing ownership or locking reads.

    A same-owner explicit Brand override remains valid for existing Videos.
    Both that Brand and the Product's own Brand must have the business owner.
    Archived state is handled separately at active-operation boundaries.
    """
    product_brand = or_(
        Product.brand_id.is_(None),
        select(Brand.id).where(
            Brand.id == Product.brand_id, Brand.created_by == Video.created_by
        ).correlate(Product, Video).exists(),
    )
    return and_(
        select(Project.id).where(
            Project.id == Video.project_id, Project.created_by == Video.created_by
        ).correlate(Video).exists(),
        or_(
            Video.product_id.is_(None),
            select(Product.id).where(
                Product.id == Video.product_id,
                Product.created_by == Video.created_by,
                product_brand,
            ).correlate(Video).exists(),
        ),
        or_(
            Video.brand_id.is_(None),
            select(Brand.id).where(
                Brand.id == Video.brand_id, Brand.created_by == Video.created_by
            ).correlate(Video).exists(),
        ),
    )


def _output_asset_parent_scope(user_id: Any, *, allow_all: bool = False) -> Any:
    """Require outputs to have a consistent parent and stable owner provenance."""
    viewer_matches_video_owner = true() if allow_all else Video.created_by == user_id
    stable_output_owner = or_(
        and_(
            Asset.resource_owner_id == Video.created_by,
            Asset.ownership_provenance == "VIDEO_PARENT",
        ),
        and_(
            Asset.resource_owner_id.is_(None),
            Asset.ownership_provenance.is_(None),
            Asset.created_by == Video.created_by,
        ),
    )
    if allow_all:
        stable_output_owner = or_(
            and_(
                Asset.resource_owner_id == Video.created_by,
                Asset.ownership_provenance == "VIDEO_PARENT",
            ),
            and_(Asset.resource_owner_id.is_(None), Asset.ownership_provenance.is_(None)),
        )
    project_matches_video_owner = (
        select(Project.id)
        .where(Project.id == Video.project_id, Project.created_by == Video.created_by)
        .exists()
    )
    generation_link = or_(
        SceneGeneration.output_asset_id == Asset.id,
        select(GenerationAsset.id)
        .where(
            GenerationAsset.generation_id == SceneGeneration.id,
            GenerationAsset.asset_id == Asset.id,
            GenerationAsset.role.like("DIRECTOR!_%", escape="!"),
        )
        .exists(),
    )
    generated_video_parent = (
        select(SceneGeneration.id)
        .join(Video, Video.id == SceneGeneration.video_id)
        .where(
            Asset.role == "GENERATED_VIDEO",
            generation_link,
            not_logically_deleted_predicate(
                "GENERATION", SceneGeneration.id, SceneGeneration.video_id
            ),
            select(Scene.id)
            .where(
                Scene.id == SceneGeneration.scene_id,
                not_logically_deleted_predicate("SCENE", Scene.id, Scene.video_id),
            )
            .exists(),
            Video.project_id == Asset.project_id,
            project_matches_video_owner,
            video_parent_consistency(),
            not_logically_deleted_predicate("VIDEO", Video.id),
            stable_output_owner,
            viewer_matches_video_owner,
        )
        .exists()
    )
    final_video_parent = (
        select(FinalVideo.id)
        .join(Video, Video.id == FinalVideo.video_id)
        .where(
            Asset.role == "FINAL_VIDEO",
            FinalVideo.output_asset_id == Asset.id,
            not_logically_deleted_predicate(
                "FINAL_VIDEO", FinalVideo.id, FinalVideo.video_id
            ),
            Video.project_id == Asset.project_id,
            project_matches_video_owner,
            video_parent_consistency(),
            not_logically_deleted_predicate("VIDEO", Video.id),
            stable_output_owner,
            viewer_matches_video_owner,
        )
        .exists()
    )
    return or_(
        Asset.role.not_in(("GENERATED_VIDEO", "FINAL_VIDEO")),
        generated_video_parent,
        final_video_parent,
    )


def owner_scope(model: Any, principal: Any, *, allow_all: bool = False) -> Any:
    """Return a SQL predicate restricting a resource to its owning editor.

    Top-level records use their existing ``created_by`` value. Child records
    inherit ownership from their video, so a stale or inconsistent child
    ``created_by`` cannot grant access across video boundaries.
    """
    user_id = principal_id(principal)
    admin_access = allow_all or is_admin(principal)
    video_children = {Scene, SceneGeneration, DirectorRun, FinalVideo}
    indirect_children = {GenerationAttempt, GenerationAsset, DirectorRunAttempt, DirectorRunMember}
    if admin_access and model not in {Asset, Video, *video_children, *indirect_children}:
        return true()

    if model is Video:
        return and_(
            video_parent_consistency(),
            not_logically_deleted_predicate("VIDEO", Video.id),
            true() if admin_access else Video.created_by == user_id,
        )

    if model in {Project, Brand, Product}:
        return model.created_by == user_id

    if model is Asset:
        # created_by is the audit actor. Editors use stable ownership provenance;
        # legacy rows are accepted only when their creator is the parent owner.
        parent_scope = _output_asset_parent_scope(user_id, allow_all=admin_access)
        if admin_access:
            return parent_scope
        assigned_by_admin = and_(
            Asset.ownership_provenance == "ADMIN_ASSIGNMENT",
            Asset.resource_owner_id == user_id,
        )
        created_by_owner = and_(
            Asset.created_by == user_id,
            or_(
                and_(
                    Asset.ownership_provenance == "OWNER",
                    Asset.resource_owner_id == user_id,
                ),
                and_(
                    Asset.ownership_provenance.is_(None),
                    Asset.resource_owner_id.is_(None),
                ),
            ),
        )
        return and_(
            parent_scope,
            or_(
                select(Project.id)
                .where(
                    Project.id == Asset.project_id,
                    Project.created_by == user_id,
                    or_(
                        Asset.resource_owner_id == Project.created_by,
                        and_(
                            Asset.resource_owner_id.is_(None),
                            Asset.created_by == Project.created_by,
                        ),
                    ),
                    or_(assigned_by_admin, created_by_owner),
                )
                .exists(),
                select(Product.id)
                .where(
                    Product.id == Asset.product_id,
                    Product.created_by == user_id,
                    or_(
                        Asset.resource_owner_id == Product.created_by,
                        and_(
                            Asset.resource_owner_id.is_(None),
                            Asset.created_by == Product.created_by,
                        ),
                    ),
                    or_(assigned_by_admin, created_by_owner),
                )
                .exists(),
                and_(
                    Asset.project_id.is_(None),
                    Asset.product_id.is_(None),
                    Asset.created_by == user_id,
                    or_(assigned_by_admin, created_by_owner),
                ),
                and_(
                    Asset.role.in_(("GENERATED_VIDEO", "FINAL_VIDEO")),
                    parent_scope,
                ),
            ),
        )

    if model is Scene:
        return select(Video.id).where(
            Video.id == model.video_id,
            owner_scope(Video, principal, allow_all=allow_all),
            not_logically_deleted_predicate("SCENE", Scene.id, Scene.video_id),
        ).exists()

    if model is SceneGeneration:
        scene_is_visible = (
            select(Scene.id)
            .where(
                Scene.id == SceneGeneration.scene_id,
                Scene.video_id == SceneGeneration.video_id,
                not_logically_deleted_predicate("SCENE", Scene.id, Scene.video_id),
            )
            .exists()
        )
        return and_(
            select(Video.id)
            .where(
                Video.id == SceneGeneration.video_id,
                owner_scope(Video, principal, allow_all=allow_all),
            )
            .exists(),
            not_logically_deleted_predicate(
                "GENERATION", SceneGeneration.id, SceneGeneration.video_id
            ),
            scene_is_visible,
        )

    if model is FinalVideo:
        return select(Video.id).where(
            Video.id == FinalVideo.video_id,
            owner_scope(Video, principal, allow_all=allow_all),
            not_logically_deleted_predicate(
                "FINAL_VIDEO", FinalVideo.id, FinalVideo.video_id
            ),
        ).exists()

    if model is DirectorRun:
        return select(Video.id).where(
            Video.id == DirectorRun.video_id,
            owner_scope(Video, principal, allow_all=allow_all),
        ).exists()

    if model is GenerationAttempt:
        return (
            select(SceneGeneration.id)
            .where(
                SceneGeneration.id == model.generation_id,
                owner_scope(SceneGeneration, principal, allow_all=allow_all),
            )
            .exists()
        )

    if model is GenerationAsset:
        return (
            select(SceneGeneration.id)
            .where(
                SceneGeneration.id == model.generation_id,
                owner_scope(SceneGeneration, principal, allow_all=allow_all),
            )
            .exists()
        )

    if model is DirectorRunAttempt:
        return (
            select(DirectorRun.id)
            .where(
                DirectorRun.id == model.director_run_id,
                owner_scope(DirectorRun, principal, allow_all=allow_all),
            )
            .exists()
        )

    if model is DirectorRunMember:
        return (
            select(DirectorRun.id)
            .where(
                DirectorRun.id == model.director_run_id,
                owner_scope(DirectorRun, principal, allow_all=allow_all),
                select(SceneGeneration.id)
                .where(
                    SceneGeneration.id == DirectorRunMember.scene_generation_id,
                    owner_scope(SceneGeneration, principal, allow_all=allow_all),
                )
                .exists(),
            )
            .exists()
        )

    raise TypeError(f"No ownership rule is defined for {model!r}")


async def get_owned(
    session: AsyncSession,
    model: Any,
    resource_id: str,
    principal: Any,
    *,
    error_code: str,
    message: str,
    allow_all: bool = False,
) -> Any:
    """Fetch one row under the caller's ownership scope or return a 404."""
    row = await session.scalar(
        select(model).where(
            model.id == resource_id,
            owner_scope(model, principal, allow_all=allow_all),
        )
    )
    if row is None:
        raise AppError(error_code, message, 404)
    return row


async def get_video_output_asset(
    session: AsyncSession,
    video: Video,
    asset_id: str,
    principal: Any,
    *,
    role: str,
    generation_id: str | None = None,
    final_video_id: str | None = None,
    for_update: bool = False,
    allow_all: bool = False,
) -> Asset:
    """Fetch an output only when it belongs to the exact requested parent row."""
    if role == "GENERATED_VIDEO":
        if generation_id is None or final_video_id is not None:
            raise ValueError("Generated output lookup requires one generation_id")
        canonical_output_id = str(
            uuid5(NAMESPACE_URL, f"ai-video-studio:GENERATED_VIDEO:{generation_id}")
        )
        generation_link = or_(
            and_(
                SceneGeneration.output_asset_id == Asset.id,
                Asset.id == canonical_output_id,
            ),
            select(GenerationAsset.id)
            .where(
                GenerationAsset.generation_id == generation_id,
                GenerationAsset.asset_id == Asset.id,
                GenerationAsset.role.like("DIRECTOR!_%", escape="!"),
            )
            .exists(),
        )
        exact_parent = select(SceneGeneration.id).where(
            SceneGeneration.id == generation_id,
            SceneGeneration.video_id == video.id,
            generation_link,
        ).exists()
    elif role == "FINAL_VIDEO":
        if final_video_id is None or generation_id is not None:
            raise ValueError("Final output lookup requires one final_video_id")
        canonical_output_id = str(
            uuid5(NAMESPACE_URL, f"ai-video-studio:FINAL_VIDEO:{final_video_id}")
        )
        exact_parent = select(FinalVideo.id).where(
            FinalVideo.id == final_video_id,
            FinalVideo.video_id == video.id,
            FinalVideo.output_asset_id == Asset.id,
            Asset.id == canonical_output_id,
        ).exists()
    else:
        raise ValueError(f"Unsupported worker output role: {role}")
    statement = select(Asset).where(
        Asset.id == asset_id,
        Asset.project_id == video.project_id,
        Asset.role == role,
        exact_parent,
        owner_scope(Asset, principal, allow_all=allow_all),
    )
    if for_update:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    asset = await session.scalar(statement)
    if asset is None:
        raise AppError("ASSET_NOT_FOUND", "Asset was not found", 404)
    return asset


async def get_video_reference_asset(
    session: AsyncSession,
    video: Video,
    asset_id: str,
    principal: Any,
) -> Asset:
    """Validate persisted reference scope, returning a read-only row snapshot.

    A loaded ORM Asset may predate the Video lock. Column projections bypass
    that cache without autoflush or discarding unrelated pending Asset edits.
    Callers use the result for validation, never lifecycle mutations.
    """
    with session.no_autoflush:
        values = (
            await session.execute(
                select(*Asset.__table__.c).where(
                    Asset.id == asset_id, owner_scope(Asset, principal)
                )
            )
        ).mappings().one_or_none()
    if values is None:
        raise AppError("ASSET_NOT_FOUND", "Asset not found", 404)
    asset = Asset(**values)
    project_matches = asset.project_id is not None and asset.project_id == video.project_id
    product_matches = (
        asset.product_id is not None
        and video.product_id is not None
        and asset.product_id == video.product_id
    )
    scope_is_consistent = (
        (asset.project_id is None or project_matches)
        and (asset.product_id is None or product_matches)
    )
    asset_owner_id = asset.resource_owner_id or asset.created_by
    if (
        asset_owner_id != video.created_by
        or not scope_is_consistent
        or not (project_matches or product_matches)
    ):
        raise AppError("ASSET_NOT_FOUND", "Asset not found", 404)
    return asset

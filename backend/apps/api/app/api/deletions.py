"""HTTP routes for previewing and requesting resource deletion."""

from fastapi import APIRouter, Depends, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.api.deps import AuthPrincipal, require_csrf, require_editor
from apps.api.app.core.errors import AppError
from apps.api.app.db.models import DeletionOperation
from apps.api.app.db.session import get_session
from apps.api.app.schemas.deletion import (
    DeletionOperationDTO,
    DeletionPreviewDTO,
    DeletionRequest,
)
from apps.api.app.services.final_version_deletion import (
    preview_final_version_deletion,
    request_final_version_deletion,
)
from apps.api.app.services.generation_deletion import (
    preview_generation_deletion,
    request_generation_deletion,
)
from apps.api.app.services.scene_deletion import (
    preview_scene_deletion,
    request_scene_deletion,
)
from apps.api.app.services.video_deletion import (
    preview_video_deletion,
    request_video_deletion,
)

router = APIRouter(tags=["deletions"])


@router.get(
    "/videos/{video_id}/deletion-preview", response_model=DeletionPreviewDTO
)
async def preview_video_deletion_route(
    video_id: str,
    user: AuthPrincipal = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
) -> DeletionPreviewDTO:
    preview = await preview_video_deletion(
        session, video_id=video_id, user_id=user.id, is_admin=user.role == "ADMIN"
    )
    return DeletionPreviewDTO.model_validate(preview)


@router.delete(
    "/videos/{video_id}",
    response_model=DeletionOperationDTO,
    status_code=status.HTTP_202_ACCEPTED,
)
async def delete_video_route(
    video_id: str,
    payload: DeletionRequest,
    user: AuthPrincipal = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
) -> DeletionOperationDTO:
    operation = await request_video_deletion(
        session,
        video_id=video_id,
        user_id=user.id,
        is_admin=user.role == "ADMIN",
        expected_revision=payload.expected_revision,
        confirmation_token=payload.confirmation_token,
    )
    return DeletionOperationDTO.from_operation(operation)


@router.get(
    "/scenes/{scene_id}/deletion-preview", response_model=DeletionPreviewDTO
)
async def preview_scene_deletion_route(
    scene_id: str,
    user: AuthPrincipal = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
) -> DeletionPreviewDTO:
    preview = await preview_scene_deletion(
        session, scene_id=scene_id, user_id=user.id, is_admin=user.role == "ADMIN"
    )
    return DeletionPreviewDTO.model_validate(preview)


@router.delete(
    "/scenes/{scene_id}",
    response_model=DeletionOperationDTO,
    status_code=status.HTTP_202_ACCEPTED,
)
async def delete_scene_route(
    scene_id: str,
    payload: DeletionRequest,
    user: AuthPrincipal = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
) -> DeletionOperationDTO:
    operation = await request_scene_deletion(
        session,
        scene_id=scene_id,
        user_id=user.id,
        is_admin=user.role == "ADMIN",
        expected_revision=payload.expected_revision,
        confirmation_token=payload.confirmation_token,
    )
    return DeletionOperationDTO.from_operation(operation)


@router.get(
    "/generations/{generation_id}/deletion-preview", response_model=DeletionPreviewDTO
)
async def preview_generation_deletion_route(
    generation_id: str,
    user: AuthPrincipal = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
) -> DeletionPreviewDTO:
    preview = await preview_generation_deletion(
        session, generation_id=generation_id, user_id=user.id, is_admin=user.role == "ADMIN"
    )
    return DeletionPreviewDTO.model_validate(preview)


@router.delete(
    "/generations/{generation_id}",
    response_model=DeletionOperationDTO,
    status_code=status.HTTP_202_ACCEPTED,
)
async def delete_generation_route(
    generation_id: str,
    payload: DeletionRequest,
    user: AuthPrincipal = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
) -> DeletionOperationDTO:
    operation = await request_generation_deletion(
        session,
        generation_id=generation_id,
        user_id=user.id,
        is_admin=user.role == "ADMIN",
        expected_revision=payload.expected_revision,
        confirmation_token=payload.confirmation_token,
    )
    return DeletionOperationDTO.from_operation(operation)


@router.get(
    "/final-versions/{final_version_id}/deletion-preview",
    response_model=DeletionPreviewDTO,
)
async def preview_final_version_deletion_route(
    final_version_id: str,
    user: AuthPrincipal = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
) -> DeletionPreviewDTO:
    preview = await preview_final_version_deletion(
        session, final_video_id=final_version_id, user_id=user.id,
        is_admin=user.role == "ADMIN",
    )
    return DeletionPreviewDTO.model_validate(preview)


@router.delete(
    "/final-versions/{final_version_id}",
    response_model=DeletionOperationDTO,
    status_code=status.HTTP_202_ACCEPTED,
)
async def delete_final_version_route(
    final_version_id: str,
    payload: DeletionRequest,
    user: AuthPrincipal = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
) -> DeletionOperationDTO:
    operation = await request_final_version_deletion(
        session,
        final_video_id=final_version_id,
        user_id=user.id,
        is_admin=user.role == "ADMIN",
        expected_revision=payload.expected_revision,
        confirmation_token=payload.confirmation_token,
    )
    return DeletionOperationDTO.from_operation(operation)


@router.get(
    "/deletion-operations/{operation_id}", response_model=DeletionOperationDTO
)
async def get_deletion_operation_route(
    operation_id: str,
    user: AuthPrincipal = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
) -> DeletionOperationDTO:
    query = select(DeletionOperation).where(DeletionOperation.id == operation_id)
    if user.role != "ADMIN":
        query = query.where(
            or_(
                DeletionOperation.requested_by_id == user.id,
                DeletionOperation.resource_owner_id == user.id,
            )
        )
    operation = await session.scalar(query)
    if operation is None:
        raise AppError("DELETION_OPERATION_NOT_FOUND", "Deletion operation was not found", 404)
    return DeletionOperationDTO.from_operation(operation)

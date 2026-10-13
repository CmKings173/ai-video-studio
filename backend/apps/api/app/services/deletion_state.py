"""Shared deletion-state queries for authorization and normal resource reads."""

from sqlalchemy import exists, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from ..db.models import (
    DELETION_HIDDEN_STATUSES,
    DELETION_RESOURCE_TYPES,
    DeletionOperation,
)

# DELETE_REQUESTED is pre-acceptance intent. Accepted operations start at
# LOGICALLY_DELETED; dependency waiting and cleanup states stay hidden.


def _validate_resource_type(resource_type: str) -> None:
    if resource_type not in DELETION_RESOURCE_TYPES:
        raise ValueError(f"Unsupported deletion resource type: {resource_type!r}")


def _hidden_operation_exists(
    resource_type: str,
    resource_id: str | ColumnElement[str],
    video_id: str | ColumnElement[str] | None = None,
) -> ColumnElement[bool]:
    filters = [
        DeletionOperation.resource_type == resource_type,
        DeletionOperation.resource_id == resource_id,
        DeletionOperation.status.in_(DELETION_HIDDEN_STATUSES),
    ]
    if video_id is not None:
        filters.append(DeletionOperation.video_id == video_id)
    return exists(select(DeletionOperation.id).where(*filters))


def _resource_is_hidden_expression(
    resource_type: str,
    resource_id: str | ColumnElement[str],
    video_id: str | ColumnElement[str] | None = None,
) -> ColumnElement[bool]:
    direct = _hidden_operation_exists(
        resource_type,
        resource_id,
        video_id if resource_type != "VIDEO" else None,
    )
    if resource_type == "VIDEO" or video_id is None:
        return direct
    return or_(direct, _hidden_operation_exists("VIDEO", video_id))


async def is_logically_deleted(
    session: AsyncSession,
    resource_type: str,
    resource_id: str,
    video_id: str | None = None,
) -> bool:
    """Return whether a resource, or its owning video, is durably hidden.

    For VIDEO, ``resource_id`` identifies the video. For child resource types,
    pass ``video_id`` to apply the same parent-video visibility rule used by the
    SQL predicate builder.
    """
    _validate_resource_type(resource_type)
    hidden = _resource_is_hidden_expression(resource_type, resource_id, video_id)
    return bool(await session.scalar(select(hidden)))


def not_logically_deleted_predicate(
    resource_type: str,
    resource_id: ColumnElement[str],
    video_id: ColumnElement[str] | None = None,
) -> ColumnElement[bool]:
    """Build a SQL visibility predicate for a resource and optional video.

    Use ``video_id`` for SCENE, GENERATION, and FINAL_VIDEO rows so a logically
    deleted owning video hides its descendants in the same SQL query. The
    predicate is composable with ``owner_scope`` and does not load ORM objects.
    """
    _validate_resource_type(resource_type)
    return not_(_resource_is_hidden_expression(resource_type, resource_id, video_id))


__all__ = [
    "DELETION_HIDDEN_STATUSES",
    "is_logically_deleted",
    "not_logically_deleted_predicate",
]

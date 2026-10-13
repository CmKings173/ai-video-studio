"""Request and response DTOs for logical deletion."""

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from apps.api.app.schemas.api import Input, Output

DeletionResourceType = Literal["VIDEO", "SCENE", "GENERATION", "FINAL_VIDEO"]
DeletionOperationStatus = Literal[
    "DELETE_REQUESTED",
    "WAITING_FOR_DEPENDENCIES",
    "LOGICALLY_DELETED",
    "CLEANUP_PENDING",
    "CLEANUP_COMPLETE",
    "CLEANUP_FAILED",
    "CLEANUP_BLOCKED",
]


class DeletionRequest(Input):
    expected_revision: int = Field(ge=1)
    confirmation_token: str | None = None


class DeletionBlockerDTO(Output):
    code: str
    label: str
    count: int = 0


class DeletionPreviewDTO(Output):
    resource_type: DeletionResourceType
    resource_id: str
    video_id: str | None = None
    name: str | None = None
    revision: int = Field(ge=1)
    allowed: bool
    reason: str | None = None
    blockers: list[DeletionBlockerDTO] = Field(default_factory=list)
    affected_counts: dict[str, int] = Field(default_factory=dict)
    shared_assets_preserved: bool | int | None = None
    cleanup_eligible_outputs: int | None = None
    cleanup_blocked_outputs: int | None = None
    current: dict[str, Any] | str | int | None = None
    async_cleanup: bool = False
    confirmation_token: str | None = None


class DeletionOperationDTO(Output):
    id: str
    status: DeletionOperationStatus
    resource_type: DeletionResourceType
    resource_id: str
    video_id: str
    expected_revision: int = Field(ge=1)
    requested_at: datetime
    logically_deleted_at: datetime | None = None
    cleanup_started_at: datetime | None = None
    cleanup_completed_at: datetime | None = None
    next_attempt_at: datetime | None = None
    retry_count: int = Field(ge=0)
    cleanup_progress: float | dict[str, Any] | None = None
    cleanup_progress_percent: float | None = None
    cleanup_status: str
    cleanup_error_code: str | None = None
    cleanup_error_message: str | None = None
    error_code: str | None = None

    @classmethod
    def from_operation(cls, operation: Any) -> "DeletionOperationDTO":
        """Build the public DTO without exposing the operation's snapshots."""
        return cls(
            id=operation.id,
            status=operation.status,
            resource_type=operation.resource_type,
            resource_id=operation.resource_id,
            video_id=operation.video_id,
            expected_revision=operation.expected_revision,
            requested_at=operation.requested_at,
            logically_deleted_at=operation.logically_deleted_at,
            cleanup_started_at=operation.cleanup_started_at,
            cleanup_completed_at=operation.cleanup_completed_at,
            next_attempt_at=operation.next_attempt_at,
            retry_count=operation.retry_count,
            cleanup_status=operation.status,
            cleanup_error_code=operation.last_error_code,
            cleanup_error_message=operation.last_error_message,
            error_code=operation.last_error_code,
        )

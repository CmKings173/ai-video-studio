from __future__ import annotations

import asyncio
import shutil
from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.api.assets import store
from apps.api.app.api.common import pagination
from apps.api.app.api.deps import require_admin, require_csrf
from apps.api.app.core.config import Settings, get_settings
from apps.api.app.core.errors import AppError
from apps.api.app.core.security import hash_password
from apps.api.app.db.models import (
    Asset,
    User,
    WorkflowRecord,
    utcnow,
)
from apps.api.app.db.models import (
    Session as AuthSession,
)
from apps.api.app.db.session import get_session, get_session_factory
from apps.api.app.integrations.comfy_adapter import ComfyAdapter
from apps.api.app.integrations.minio import AssetStore
from apps.api.app.schemas.api import (
    CleanupRequest,
    CleanupResultDTO,
    Page,
    ReconciliationDTO,
    RuntimeComponentDTO,
    StorageSummaryDTO,
    SystemStatusDTO,
    UserCreate,
    UserDTO,
    UserPatch,
    WorkflowApproval,
    WorkflowCreate,
    WorkflowDTO,
)
from apps.api.app.services.asset_service import asset_is_referenced
from apps.api.app.services.h3_validator import H3Profile
from apps.api.app.services.workflow_registry import ApprovedWorkflow, WorkflowSlotError
from workers.reconciliation import AssetReconciler

router = APIRouter(prefix="/admin", tags=["admin"])


def _email(value: str) -> str:
    normalized = value.strip().lower()
    if normalized.count("@") != 1 or normalized.startswith("@") or normalized.endswith("@"):
        raise AppError("EMAIL_INVALID", "A valid email address is required", 422)
    return normalized


def _disk_usage(root: Path):
    return shutil.disk_usage(root if root.exists() else root.parent)


@router.get("/users", response_model=Page[UserDTO])
async def list_users(
    paging: tuple[int, int] = Depends(pagination),
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> Page[UserDTO]:
    page, size = paging
    total = await session.scalar(select(func.count()).select_from(User)) or 0
    rows = list(
        (
            await session.scalars(
                select(User)
                .order_by(User.created_at.desc(), User.id.desc())
                .offset((page - 1) * size)
                .limit(size)
            )
        ).all()
    )
    return Page(
        items=[UserDTO.model_validate(row) for row in rows],
        total=total,
        page=page,
        page_size=size,
    )


@router.post("/users", response_model=UserDTO, status_code=201)
async def create_user(
    payload: UserCreate,
    admin: User = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
) -> UserDTO:
    if admin.role != "ADMIN":
        raise AppError("FORBIDDEN", "Administrator access required", 403)
    email = _email(payload.email)
    if await session.scalar(select(User.id).where(User.email == email)):
        raise AppError("EMAIL_IN_USE", "Email address is already in use", 409)
    user = User(
        email=email,
        name=payload.name,
        password_hash=hash_password(payload.password),
        role=payload.role,
    )
    try:
        async with session.begin_nested():
            session.add(user)
            await session.flush()
    except IntegrityError as exc:
        raise AppError("EMAIL_IN_USE", "Email address is already in use", 409) from exc
    return UserDTO.model_validate(user)


@router.patch("/users/{user_id}", response_model=UserDTO)
async def patch_user(
    user_id: str,
    payload: UserPatch,
    admin: User = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
) -> UserDTO:
    if admin.role != "ADMIN":
        raise AppError("FORBIDDEN", "Administrator access required", 403)
    # Serialize every active-admin mutation in a deterministic order. Locking
    # only the target row lets two admins concurrently demote/disable the last
    # two accounts after both observe the same count.
    await session.scalars(
        select(User.id)
        .where(User.role == "ADMIN")
        .order_by(User.id)
        .with_for_update()
    )
    user = await session.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None:
        raise AppError("USER_NOT_FOUND", "User was not found", 404)
    values = payload.model_dump(exclude_unset=True)
    removes_active_admin = (
        user.role == "ADMIN"
        and user.is_active
        and (values.get("role") == "EDITOR" or values.get("is_active") is False)
    )
    if removes_active_admin:
        active_admins = await session.scalar(
            select(func.count())
            .select_from(User)
            .where(User.role == "ADMIN", User.is_active.is_(True))
        )
        if active_admins <= 1:
            raise AppError(
                "LAST_ADMIN_REQUIRED", "The last active administrator cannot be removed", 409
            )
    password = values.pop("password", None)
    for key, value in values.items():
        if value is not None:
            setattr(user, key, value)
    if password is not None:
        user.password_hash = hash_password(password)
    if password is not None or values.get("is_active") is False:
        await session.execute(AuthSession.__table__.delete().where(AuthSession.user_id == user.id))
    return UserDTO.model_validate(user)


@router.get("/workflows", response_model=Page[WorkflowDTO])
async def list_workflows(
    paging: tuple[int, int] = Depends(pagination),
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> Page[WorkflowDTO]:
    page, size = paging
    total = await session.scalar(select(func.count()).select_from(WorkflowRecord)) or 0
    rows = list(
        (
            await session.scalars(
                select(WorkflowRecord)
                .order_by(WorkflowRecord.created_at.desc(), WorkflowRecord.id.desc())
                .offset((page - 1) * size)
                .limit(size)
            )
        ).all()
    )
    return Page(
        items=[WorkflowDTO.model_validate(row) for row in rows],
        total=total,
        page=page,
        page_size=size,
    )


def _approved(payload: WorkflowCreate) -> ApprovedWorkflow:
    workflow = ApprovedWorkflow(
        mode=payload.mode,
        version=payload.version,
        workflow=payload.workflow,
        slots=payload.slots,
        required_slots=frozenset(payload.required_slots),
    )
    try:
        workflow.validate()
        H3Profile(
            **{
                key: value
                for key, value in payload.profile.items()
                if key in H3Profile.__dataclass_fields__
            }
        )
    except (WorkflowSlotError, TypeError, ValueError) as exc:
        raise AppError("WORKFLOW_INVALID", str(exc), 422) from exc
    return workflow


@router.post("/workflows", response_model=WorkflowDTO, status_code=201)
async def create_workflow(
    payload: WorkflowCreate,
    admin: User = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
) -> WorkflowDTO:
    if admin.role != "ADMIN":
        raise AppError("FORBIDDEN", "Administrator access required", 403)
    approved = _approved(payload)
    record = WorkflowRecord(
        **payload.model_dump(mode="json"),
        workflow_hash=approved.workflow_hash,
        slot_map_hash=approved.slot_map_hash,
        enabled=False,
        created_by=admin.id,
    )
    try:
        async with session.begin_nested():
            session.add(record)
            await session.flush()
    except IntegrityError as exc:
        raise AppError(
            "WORKFLOW_VERSION_EXISTS", "Workflow code/version already exists", 409
        ) from exc
    return WorkflowDTO.model_validate(record)


@router.patch("/workflows/{workflow_id}/approval", response_model=WorkflowDTO)
async def approve_workflow(
    workflow_id: str,
    payload: WorkflowApproval,
    admin: User = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
) -> WorkflowDTO:
    if admin.role != "ADMIN":
        raise AppError("FORBIDDEN", "Administrator access required", 403)
    record = await session.get(WorkflowRecord, workflow_id, with_for_update=True)
    if record is None:
        raise AppError("WORKFLOW_NOT_FOUND", "Workflow was not found", 404)
    approved = ApprovedWorkflow(
        mode=record.mode,
        version=record.version,
        workflow=record.workflow,
        slots={key: tuple(value) for key, value in record.slots.items()},
        required_slots=frozenset(record.required_slots),
    )
    try:
        approved.validate()
    except WorkflowSlotError as exc:
        raise AppError("WORKFLOW_INVALID", str(exc), 409) from exc
    if (
        approved.workflow_hash != record.workflow_hash
        or approved.slot_map_hash != record.slot_map_hash
    ):
        raise AppError("WORKFLOW_INTEGRITY_FAILED", "Workflow hash mismatch", 409)
    if payload.enabled:
        await session.execute(
            update(WorkflowRecord)
            .where(WorkflowRecord.mode == record.mode, WorkflowRecord.id != record.id)
            .values(enabled=False)
        )
        record.approved_at = utcnow()
    record.enabled = payload.enabled
    return WorkflowDTO.model_validate(record)


async def _system_status(
    session: AsyncSession, settings: Settings, asset_store: AssetStore
) -> SystemStatusDTO:
    postgres = True
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        postgres = False
    adapter = ComfyAdapter(settings)
    minio, comfyui = await asyncio.gather(asset_store.health(), adapter.health())
    root = Path(settings.workspace_root)
    try:
        usage = await asyncio.to_thread(_disk_usage, root)
        local = RuntimeComponentDTO(
            healthy=usage.free >= settings.min_free_disk_bytes,
            details={"free_bytes": usage.free, "total_bytes": usage.total},
        )
    except OSError as exc:
        local = RuntimeComponentDTO(healthy=False, details={"error": str(exc)})
    healthy = postgres and minio and comfyui and local.healthy
    return SystemStatusDTO(
        status="ok" if healthy else "degraded",
        postgres=RuntimeComponentDTO(healthy=postgres),
        minio=RuntimeComponentDTO(healthy=minio),
        comfyui=RuntimeComponentDTO(healthy=comfyui),
        local_storage=local,
    )


@router.get("/system/status", response_model=SystemStatusDTO)
async def admin_system_status(
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    asset_store: AssetStore = Depends(store),
) -> SystemStatusDTO:
    return await _system_status(session, settings, asset_store)


@router.get("/storage/summary", response_model=StorageSummaryDTO)
async def storage_summary(
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    asset_store: AssetStore = Depends(store),
) -> StorageSummaryDTO:
    database_assets = await session.scalar(select(func.count()).select_from(Asset)) or 0
    database_bytes = await session.scalar(select(func.sum(Asset.size_bytes))) or 0
    counts = {}
    for status in ("PENDING", "PENDING_UPLOAD", "VALIDATING", "FAILED", "DELETED"):
        counts[status] = (
            await session.scalar(
                select(func.count()).select_from(Asset).where(Asset.status == status)
            )
            or 0
        )
    objects = await asset_store.list_objects()
    root = Path(settings.workspace_root)
    usage = await asyncio.to_thread(_disk_usage, root)
    return StorageSummaryDTO(
        database_assets=database_assets,
        database_bytes=database_bytes,
        object_count=len(objects),
        object_bytes=sum(item["size"] for item in objects),
        pending_assets=counts["PENDING"] + counts["PENDING_UPLOAD"] + counts["VALIDATING"],
        failed_assets=counts["FAILED"],
        deleted_assets=counts["DELETED"],
        local_free_bytes=usage.free,
    )


@router.post("/storage/cleanup", response_model=CleanupResultDTO)
async def cleanup_storage(
    payload: CleanupRequest,
    admin: User = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
    asset_store: AssetStore = Depends(store),
) -> CleanupResultDTO:
    if admin.role != "ADMIN":
        raise AppError("FORBIDDEN", "Administrator access required", 403)
    now = utcnow()
    pending_cutoff = now - timedelta(hours=payload.pending_older_than_hours)
    deleted_cutoff = now - timedelta(hours=payload.deleted_older_than_hours)
    rows = list(
        (
            await session.scalars(
                select(Asset)
                .where(
                    (
                        Asset.status.in_({"PENDING", "PENDING_UPLOAD", "FAILED"})
                        & (Asset.created_at < pending_cutoff)
                    )
                    | ((Asset.status == "DELETED") & (Asset.deleted_at < deleted_cutoff))
                )
                .order_by(Asset.created_at, Asset.id)
            )
        ).all()
    )
    candidates = []
    deletable = []
    for asset in rows:
        referenced = await asset_is_referenced(session, asset.id)
        candidates.append(
            {
                "asset_id": asset.id,
                "object_key": asset.object_key,
                "status": asset.status,
                "referenced": referenced,
            }
        )
        if not referenced:
            deletable.append(asset)
    deleted_objects = 0
    if not payload.dry_run:
        for asset in deletable:
            await asset_store.delete(asset.object_key)
            asset.status = "DELETED"
            asset.deleted_at = asset.deleted_at or now
            deleted_objects += 1
    return CleanupResultDTO(
        dry_run=payload.dry_run,
        candidates=candidates,
        deleted_objects=deleted_objects,
        retained_records=len(deletable),
    )


@router.post("/storage/reconcile", response_model=ReconciliationDTO)
async def reconcile_storage(
    admin: User = Depends(require_csrf),
    factory=Depends(get_session_factory),
    settings: Settings = Depends(get_settings),
    asset_store: AssetStore = Depends(store),
) -> ReconciliationDTO:
    if admin.role != "ADMIN":
        raise AppError("FORBIDDEN", "Administrator access required", 403)
    report = await AssetReconciler(factory, asset_store, settings).run()
    return ReconciliationDTO(
        missing_objects=report.missing_objects,
        corrupt_objects=report.corrupt_objects,
        orphan_objects=report.orphan_objects,
        repaired_assets=report.repaired_assets,
    )


system_status = _system_status

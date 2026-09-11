from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.api.common import apply_patch, pagination
from apps.api.app.api.deps import expected_revision, require_csrf, require_editor
from apps.api.app.core.errors import AppError
from apps.api.app.db.models import Asset, Project, User, Video
from apps.api.app.db.session import get_session
from apps.api.app.schemas.api import (
    AssetDTO,
    Page,
    ProjectDTO,
    ResourceCreate,
    ResourcePatch,
    VideoDTO,
)

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=Page[ProjectDTO])
async def list_projects(
    paging: tuple[int, int] = Depends(pagination),
    search: str | None = Query(default=None, min_length=1, max_length=200),
    archived: bool | None = None,
    user: User = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
) -> Page[ProjectDTO]:
    page, size = paging
    filters = []
    if search:
        filters.append(Project.name.ilike(f"%{search}%"))
    if archived is not None:
        filters.append(Project.archived.is_(archived))
    total_query = select(func.count()).select_from(Project)
    query = select(Project)
    if filters:
        total_query = total_query.where(*filters)
        query = query.where(*filters)
    total = await session.scalar(total_query) or 0
    rows = list(
        (
            await session.scalars(
                query
                .order_by(Project.created_at.desc(), Project.id.desc())
                .offset((page - 1) * size)
                .limit(size)
            )
        ).all()
    )
    return Page(
        items=[ProjectDTO.model_validate(row) for row in rows],
        total=total,
        page=page,
        page_size=size,
    )


@router.post("", response_model=ProjectDTO, status_code=201)
async def create_project(
    payload: ResourceCreate,
    user: User = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
) -> ProjectDTO:
    project = Project(**payload.model_dump(), created_by=user.id)
    session.add(project)
    await session.flush()
    return ProjectDTO.model_validate(project)


@router.get("/{project_id}", response_model=ProjectDTO)
async def get_project(
    project_id: str,
    user: User = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
) -> ProjectDTO:
    project = await session.get(Project, project_id)
    if project is None:
        raise AppError("PROJECT_NOT_FOUND", "Project not found", 404)
    return ProjectDTO.model_validate(project)


@router.patch("/{project_id}", response_model=ProjectDTO)
async def patch_project(
    project_id: str,
    payload: ResourcePatch,
    revision: int = Depends(expected_revision),
    user: User = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
) -> ProjectDTO:
    project = await session.get(Project, project_id, with_for_update=True)
    if project is None:
        raise AppError("PROJECT_NOT_FOUND", "Project not found", 404)
    apply_patch(project, payload, revision)
    return ProjectDTO.model_validate(project)


@router.post("/{project_id}/archive", response_model=ProjectDTO)
async def archive_project(
    project_id: str,
    revision: int = Depends(expected_revision),
    user: User = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
) -> ProjectDTO:
    project = await session.get(Project, project_id, with_for_update=True)
    if project is None:
        raise AppError("PROJECT_NOT_FOUND", "Project not found", 404)
    if project.revision != revision:
        raise AppError(
            "REVISION_CONFLICT",
            "Resource changed since it was loaded",
            412,
            {"expected": revision, "actual": project.revision},
        )
    if not project.archived:
        project.archived = True
        project.revision += 1
    return ProjectDTO.model_validate(project)


@router.get("/{project_id}/videos", response_model=Page[VideoDTO])
async def project_videos(
    project_id: str,
    paging: tuple[int, int] = Depends(pagination),
    user: User = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
) -> Page[VideoDTO]:
    if await session.get(Project, project_id) is None:
        raise AppError("PROJECT_NOT_FOUND", "Project not found", 404)
    page, size = paging
    where = Video.project_id == project_id
    total = await session.scalar(select(func.count()).select_from(Video).where(where)) or 0
    rows = list(
        (
            await session.scalars(
                select(Video)
                .where(where)
                .order_by(Video.created_at.desc(), Video.id.desc())
                .offset((page - 1) * size)
                .limit(size)
            )
        ).all()
    )
    return Page(
        items=[VideoDTO.model_validate(row) for row in rows],
        total=total,
        page=page,
        page_size=size,
    )


@router.get("/{project_id}/assets", response_model=Page[AssetDTO])
async def project_assets(
    project_id: str,
    paging: tuple[int, int] = Depends(pagination),
    user: User = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
) -> Page[AssetDTO]:
    if await session.get(Project, project_id) is None:
        raise AppError("PROJECT_NOT_FOUND", "Project not found", 404)
    page, size = paging
    where = (Asset.project_id == project_id) & Asset.deleted_at.is_(None)
    total = await session.scalar(select(func.count()).select_from(Asset).where(where)) or 0
    rows = list(
        (
            await session.scalars(
                select(Asset)
                .where(where)
                .order_by(Asset.created_at.desc(), Asset.id.desc())
                .offset((page - 1) * size)
                .limit(size)
            )
        ).all()
    )
    return Page(
        items=[AssetDTO.model_validate(row) for row in rows],
        total=total,
        page=page,
        page_size=size,
    )

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.api.common import apply_patch, pagination
from apps.api.app.api.deps import AuthPrincipal, expected_revision, require_csrf, require_editor
from apps.api.app.api.ownership import get_owned, owner_scope, principal_id
from apps.api.app.db.models import Brand
from apps.api.app.db.session import get_session
from apps.api.app.schemas.api import BrandCreate, BrandDTO, BrandPatch, Page
from apps.api.app.services.generation_dependency_invalidation import invalidate_brand_dependents

router = APIRouter(prefix="/brands", tags=["brands"])


@router.get("", response_model=Page[BrandDTO])
async def list_brands(
    paging: tuple[int, int] = Depends(pagination),
    search: str | None = Query(default=None, min_length=1, max_length=200),
    archived: bool | None = None,
    user: AuthPrincipal = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
) -> Page[BrandDTO]:
    page, size = paging
    filters = [owner_scope(Brand, user)]
    if search:
        filters.append(Brand.name.ilike(f"%{search}%"))
    if archived is not None:
        filters.append(Brand.archived.is_(archived))
    total_query = select(func.count()).select_from(Brand)
    query = select(Brand)
    if filters:
        total_query = total_query.where(*filters)
        query = query.where(*filters)
    total = await session.scalar(total_query) or 0
    rows = list(
        (
            await session.scalars(
                query.order_by(Brand.created_at.desc(), Brand.id.desc())
                .offset((page - 1) * size)
                .limit(size)
            )
        ).all()
    )
    return Page(
        items=[BrandDTO.model_validate(row) for row in rows], total=total, page=page, page_size=size
    )


@router.post("", response_model=BrandDTO, status_code=201)
async def create_brand(
    payload: BrandCreate,
    user: AuthPrincipal = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
) -> BrandDTO:
    brand = Brand(**payload.model_dump(), created_by=principal_id(user))
    session.add(brand)
    await session.flush()
    return BrandDTO.model_validate(brand)


@router.get("/{brand_id}", response_model=BrandDTO)
async def get_brand(
    brand_id: str,
    user: AuthPrincipal = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
) -> BrandDTO:
    brand = await get_owned(
        session,
        Brand,
        brand_id,
        user,
        error_code="BRAND_NOT_FOUND",
        message="Brand not found",
    )
    return BrandDTO.model_validate(brand)


@router.patch("/{brand_id}", response_model=BrandDTO)
async def patch_brand(
    brand_id: str,
    payload: BrandPatch,
    revision: int = Depends(expected_revision),
    user: AuthPrincipal = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
) -> BrandDTO:
    await get_owned(
        session,
        Brand,
        brand_id,
        user,
        error_code="BRAND_NOT_FOUND",
        message="Brand not found",
    )
    async with invalidate_brand_dependents(session, brand_id) as brand:
        apply_patch(brand, payload, revision)
    return BrandDTO.model_validate(brand)

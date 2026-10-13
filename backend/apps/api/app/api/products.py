from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.api.common import pagination
from apps.api.app.api.deps import AuthPrincipal, expected_revision, require_csrf, require_editor
from apps.api.app.api.ownership import get_owned, owner_scope, principal_id
from apps.api.app.core.errors import AppError
from apps.api.app.db.locking import lock_revisioned_row
from apps.api.app.db.models import Asset, Brand, Product, Video
from apps.api.app.db.session import get_session
from apps.api.app.schemas.api import (
    AssetDTO,
    Page,
    ProductCreate,
    ProductDTO,
    ProductPatch,
    VideoDTO,
)
from apps.api.app.services.generation_dependency_invalidation import (
    invalidate_product_dependents,
    lock_dependency_graph,
)

router = APIRouter(prefix="/products", tags=["products"])


async def _require_product_brand(
    session: AsyncSession, brand_id: str | None, owner_id: str,
    user: AuthPrincipal, *, lock: bool = False,
) -> None:
    """Admin access never permits a Product/Brand business-owner mismatch."""
    if brand_id is None:
        return
    with session.no_autoflush:
        brand = (
            await lock_revisioned_row(
                session, Brand, str(brand_id), immutable_fields=("created_by",)
            )
            if lock else await get_owned(
                session, Brand, str(brand_id), user,
                error_code="BRAND_NOT_FOUND", message="Brand not found",
            )
        )
    if brand is None or brand.created_by != owner_id:
        raise AppError("BRAND_NOT_FOUND", "Brand not found", 404)
    if brand.archived:
        raise AppError("BRAND_NOT_ACTIVE", "Brand is archived", 409)


@router.get("", response_model=Page[ProductDTO])
async def list_products(
    paging: tuple[int, int] = Depends(pagination),
    search: str | None = Query(default=None, min_length=1, max_length=200),
    archived: bool | None = None,
    brand_id: str | None = Query(default=None, min_length=1, max_length=36),
    user: AuthPrincipal = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
) -> Page[ProductDTO]:
    page, size = paging
    filters = [owner_scope(Product, user)]
    if search:
        filters.append(Product.name.ilike(f"%{search}%"))
    if archived is not None:
        filters.append(Product.archived.is_(archived))
    if brand_id:
        filters.append(Product.brand_id == brand_id)
    total_query = select(func.count()).select_from(Product)
    query = select(Product)
    if filters:
        total_query = total_query.where(*filters)
        query = query.where(*filters)
    total = await session.scalar(total_query) or 0
    rows = list(
        (
            await session.scalars(
                query.order_by(Product.created_at.desc(), Product.id.desc())
                .offset((page - 1) * size)
                .limit(size)
            )
        ).all()
    )
    return Page(
        items=[ProductDTO.model_validate(row) for row in rows],
        total=total,
        page=page,
        page_size=size,
    )


@router.post("", response_model=ProductDTO, status_code=201)
async def create_product(
    payload: ProductCreate,
    user: AuthPrincipal = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
) -> ProductDTO:
    owner_id = principal_id(user)
    brand_id = str(payload.brand_id) if payload.brand_id else None
    await _require_product_brand(session, brand_id, owner_id, user)
    await lock_dependency_graph(session)
    await _require_product_brand(session, brand_id, owner_id, user, lock=True)
    product = Product(**payload.model_dump(mode="json"), created_by=owner_id)
    session.add(product)
    await session.flush()
    return ProductDTO.model_validate(product)


@router.get("/{product_id}", response_model=ProductDTO)
async def get_product(
    product_id: str,
    user: AuthPrincipal = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
) -> ProductDTO:
    product = await get_owned(
        session,
        Product,
        product_id,
        user,
        error_code="PRODUCT_NOT_FOUND",
        message="Product not found",
    )
    return ProductDTO.model_validate(product)


@router.patch("/{product_id}", response_model=ProductDTO)
async def patch_product(
    product_id: str,
    payload: ProductPatch,
    revision: int = Depends(expected_revision),
    user: AuthPrincipal = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
) -> ProductDTO:
    values = payload.model_dump(exclude_unset=True, mode="json")
    with session.no_autoflush:
        product = await get_owned(
            session,
            Product,
            product_id,
            user,
            error_code="PRODUCT_NOT_FOUND",
            message="Product not found",
        )
    brand_id = values.get("brand_id", product.brand_id)
    await _require_product_brand(session, brand_id, product.created_by, user)
    async with invalidate_product_dependents(session, product_id) as product:
        await _require_product_brand(
            session, values.get("brand_id", product.brand_id), product.created_by, user, lock=True
        )
        if product.revision != revision:
            raise AppError(
                "REVISION_CONFLICT",
                "Resource changed since it was loaded",
                412,
                {"expected": revision, "actual": product.revision},
            )
        for key, value in values.items():
            setattr(product, key, value)
        product.revision += 1
    return ProductDTO.model_validate(product)


@router.post("/{product_id}/archive", response_model=ProductDTO)
async def archive_product(
    product_id: str,
    revision: int = Depends(expected_revision),
    user: AuthPrincipal = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
) -> ProductDTO:
    await get_owned(
        session,
        Product,
        product_id,
        user,
        error_code="PRODUCT_NOT_FOUND",
        message="Product not found",
    )
    async with invalidate_product_dependents(session, product_id) as product:
        if product.revision != revision:
            raise AppError(
                "REVISION_CONFLICT",
                "Resource changed since it was loaded",
                412,
                {"expected": revision, "actual": product.revision},
            )
        if not product.archived:
            product.archived = True
            product.revision += 1
    return ProductDTO.model_validate(product)


@router.get("/{product_id}/assets", response_model=Page[AssetDTO])
async def product_assets(
    product_id: str,
    paging: tuple[int, int] = Depends(pagination),
    user: AuthPrincipal = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
) -> Page[AssetDTO]:
    await get_owned(
        session,
        Product,
        product_id,
        user,
        error_code="PRODUCT_NOT_FOUND",
        message="Product not found",
    )
    page, size = paging
    where = (
        (Asset.product_id == product_id)
        & Asset.deleted_at.is_(None)
        & owner_scope(Asset, user)
    )
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


@router.get("/{product_id}/videos", response_model=Page[VideoDTO])
async def product_videos(
    product_id: str,
    paging: tuple[int, int] = Depends(pagination),
    user: AuthPrincipal = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
) -> Page[VideoDTO]:
    await get_owned(
        session,
        Product,
        product_id,
        user,
        error_code="PRODUCT_NOT_FOUND",
        message="Product not found",
    )
    page, size = paging
    where = (Video.product_id == product_id) & owner_scope(Video, user)
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

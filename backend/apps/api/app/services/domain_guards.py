"""Shared business-state guards used at write boundaries."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.core.errors import AppError
from apps.api.app.db.models import Brand


async def require_active_brand(session: AsyncSession, brand_id: str | None) -> Brand | None:
    if not brand_id:
        return None
    brand = await session.get(Brand, str(brand_id))
    if brand is None:
        raise AppError("BRAND_NOT_FOUND", "Brand not found", 404)
    if brand.archived:
        raise AppError("BRAND_NOT_ACTIVE", "Brand is archived", 409)
    return brand


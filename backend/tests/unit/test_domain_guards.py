import pytest

from apps.api.app.core.errors import AppError
from apps.api.app.services.domain_guards import require_active_brand


class Session:
    def __init__(self, brand):
        self.brand = brand

    async def get(self, _model, _brand_id):
        return self.brand


@pytest.mark.asyncio
async def test_active_brand_guard_rejects_archived_brand():
    with pytest.raises(AppError) as error:
        await require_active_brand(Session(type("Brand", (), {"archived": True})()), "brand-1")
    assert error.value.code == "BRAND_NOT_ACTIVE"


@pytest.mark.asyncio
async def test_active_brand_guard_allows_empty_reference():
    assert await require_active_brand(Session(None), None) is None


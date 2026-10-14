from types import SimpleNamespace

import pytest
from sqlalchemy import select, update

from apps.api.app.api.ownership import owner_scope
from apps.api.app.db.models import Brand, Product, Scene, Video
from tests.integration.test_video_scene_ownership import seed_ownership_graph


@pytest.mark.asyncio
@pytest.mark.parametrize("broken", ["project", "product", "brand", "inherited_brand"])
@pytest.mark.parametrize("viewer", ["EDITOR_A", "ADMIN"])
async def test_inconsistent_video_and_children_hidden_by_sql(session_factory, broken, viewer):
    graph = await seed_ownership_graph(session_factory)
    # Seed historical inconsistencies without changing ownership to repair them.
    async with session_factory() as session, session.begin():
        video = await session.get(Video, graph["videos"]["A"])
        if broken == "project":
            video.project_id = graph["projects"]["B"]
        elif broken == "product":
            video.product_id = graph["products"]["B"]
        elif broken == "brand":
            video.brand_id = graph["brands"]["B"]
        else:
            video.brand_id = None
            await session.execute(update(Product).where(Product.id == video.product_id).values(
                brand_id=graph["brands"]["B"]
            ))
    principal = SimpleNamespace(
        id=graph["users"][viewer], role="ADMIN" if viewer == "ADMIN" else "EDITOR"
    )
    async with session_factory() as session:
        assert await session.scalar(select(Video.id).where(
            Video.id == graph["videos"]["A"], owner_scope(Video, principal)
        )) is None
        assert await session.scalar(select(Scene.id).where(
            Scene.id == graph["scenes"]["A"], owner_scope(Scene, principal)
        )) is None
        persisted_owner = await session.scalar(
            select(Video.created_by).where(Video.id == graph["videos"]["A"])
        )
        assert persisted_owner == graph["users"]["EDITOR_A"]


@pytest.mark.asyncio
async def test_same_owner_explicit_override_is_not_hidden(session_factory):
    graph = await seed_ownership_graph(session_factory)
    async with session_factory() as session, session.begin():
        override = Brand(name="Valid explicit override", created_by=graph["users"]["EDITOR_A"])
        session.add(override)
        await session.flush()
        video = await session.get(Video, graph["videos"]["A"])
        video.brand_id = override.id
    for viewer in ("EDITOR_A", "ADMIN"):
        principal = SimpleNamespace(
            id=graph["users"][viewer], role="ADMIN" if viewer == "ADMIN" else "EDITOR"
        )
        async with session_factory() as session:
            assert await session.scalar(select(Video.id).where(
                Video.id == graph["videos"]["A"], owner_scope(Video, principal)
            )) == graph["videos"]["A"]

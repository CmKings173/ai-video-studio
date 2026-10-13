import pytest
from sqlalchemy import func, select

from apps.api.app.db.models import Asset, Video
from tests.integration.test_assets_assembly_ownership import WorkerOutputStore
from tests.integration.test_video_scene_ownership import seed_ownership_graph
from workers.common import save_output


@pytest.mark.asyncio
async def test_worker_rejects_inconsistent_source_before_output_or_upload(session_factory):
    graph = await seed_ownership_graph(session_factory)
    async with session_factory() as session, session.begin():
        video = await session.get(Video, graph["videos"]["A"])
        video.project_id = graph["projects"]["B"]
        before = await session.scalar(select(func.count()).select_from(Asset))
    store = WorkerOutputStore()
    with pytest.raises(ValueError, match="OUTPUT_PARENT_CONFLICT"):
        await save_output(
            session_factory, store,
            owner_id=graph["generations"]["A"], role="GENERATED_VIDEO",
            project_id=graph["projects"]["B"], created_by=graph["users"]["ADMIN"],
            data=b"inconsistent historical output",
            metadata={"kind": "VIDEO", "has_video": True},
        )
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Asset)) == before
    assert store.objects == {}

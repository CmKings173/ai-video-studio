import pytest
from sqlalchemy import func, select

from apps.api.app.core.config import Settings
from apps.api.app.core.errors import AppError
from apps.api.app.db.models import Scene, SceneGeneration
from apps.api.app.schemas.api import GenerationRequest
from apps.api.app.services.generation_service import GenerationService
from tests.integration.test_generation_preparation import seed


@pytest.mark.asyncio
@pytest.mark.parametrize("from_scene", [False, True])
async def test_motion_rejected_before_service_persistence(session_factory, tmp_path, from_scene):
    user, scene_id = await seed(session_factory)
    async with session_factory() as session, session.begin():
        if from_scene:
            scene = await session.get(Scene, scene_id)
            scene.generation_config = {"motion_context": {"enabled": True}}
            await session.flush()
        request = GenerationRequest(**({} if from_scene else {"motion_context": {"enabled": True}}))
        with pytest.raises(AppError) as error:
            await GenerationService(Settings(_env_file=None, workspace_root=tmp_path)).create(
                session, scene_id=scene_id, request=request, user_id=user, request_id=None
            )
        assert error.value.code == "DIRECTOR_AGGREGATE_REQUIRED"
        await session.flush()
        assert await session.scalar(select(func.count()).select_from(SceneGeneration)) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["REGENERATE", "VARIATION"])
async def test_derivative_motion_guard_precedes_persistence(session_factory, tmp_path, operation):
    user, scene_id = await seed(session_factory)
    service = GenerationService(Settings(_env_file=None, workspace_root=tmp_path))
    async with session_factory() as session, session.begin():
        parent = await service.create(
            session,
            scene_id=scene_id,
            request=GenerationRequest(seed=42),
            user_id=user,
            request_id=None,
        )
        parent.status = "COMPLETED"
        await session.flush()
        with pytest.raises(AppError) as error:
            await service.create(
                session,
                scene_id=scene_id,
                user_id=user,
                request_id=None,
                request=GenerationRequest(
                    operation=operation,
                    parent_generation_id=parent.id,
                    motion_context={"enabled": True},
                ),
            )
        assert error.value.code == "DIRECTOR_AGGREGATE_REQUIRED"
        await session.flush()
        assert await session.scalar(select(func.count()).select_from(SceneGeneration)) == 1


@pytest.mark.asyncio
async def test_deferred_motion_keeps_normal_workflow_validation(session_factory, tmp_path):
    user, scene_id = await seed(session_factory)
    async with session_factory() as session, session.begin():
        with pytest.raises(AppError) as error:
            await GenerationService(Settings(_env_file=None, workspace_root=tmp_path)).create(
                session,
                scene_id=scene_id,
                request=GenerationRequest(motion_context={"enabled": True}),
                user_id=user,
                request_id=None,
                defer_aggregate_qualification=True,
                persist=False,
            )
        # This seed has no aggregate workflow: deferral must preserve scope routing.
        assert error.value.code == "WORKFLOW_NOT_CONFIGURED"
        assert await session.scalar(select(func.count()).select_from(SceneGeneration)) == 0

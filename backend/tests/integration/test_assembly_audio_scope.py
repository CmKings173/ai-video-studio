from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from apps.api.app.api.assembly import assemble_video
from apps.api.app.core.config import Settings
from apps.api.app.core.errors import AppError
from apps.api.app.db.models import (
    Asset,
    FinalVideo,
    Product,
    Project,
    Scene,
    SceneGeneration,
    User,
    Video,
)
from apps.api.app.schemas.api import AssemblyRequest
from apps.api.app.services.assembly_service import AssemblyService
from apps.api.app.services.generation_freshness import capture_generation_freshness
from tests.integration.test_assembly_manifest import seed_ready_video


@pytest.mark.asyncio
@pytest.mark.parametrize("through_api", [False, True])
@pytest.mark.parametrize(
    "scope",
    ["project", "project_without_link", "product", "other_project", "other_product",
     "unowned", "product_without_link"],
)
async def test_background_audio_must_belong_to_video_scope(
    session_factory, tmp_path, scope, through_api
):
    user_id, video_id, scene_id = await seed_ready_video(session_factory)
    async with session_factory() as session, session.begin():
        video = await session.get(Video, video_id)
        product = Product(name="Linked product", created_by=user_id)
        other_product = Product(name="Unrelated product", created_by=user_id)
        other_project = Project(name="Unrelated project", created_by=user_id)
        session.add_all([product, other_product, other_project])
        await session.flush()
        if scope not in {"product_without_link", "project_without_link"}:
            video.product_id = product.id
        scene = await session.get(Scene, scene_id)
        generation = await session.get(SceneGeneration, scene.selected_generation_id)
        generation.input_snapshot = {
            **generation.input_snapshot,
            "generation_freshness": await capture_generation_freshness(session, scene, video),
        }
        project_scope = {
            "project": video.project_id,
            "project_without_link": video.project_id,
            "other_project": other_project.id,
        }
        product_scope = {
            "product": product.id,
            "product_without_link": product.id,
            "other_product": other_product.id,
        }
        audio = Asset(
            project_id=project_scope.get(scope),
            product_id=product_scope.get(scope),
            kind="AUDIO",
            filename="music.wav",
            content_type="audio/wav",
            object_key=f"audio/{scope}.wav",
            status="READY",
            checksum="c" * 64,
            size_bytes=10,
            created_by=user_id,
        )
        session.add(audio)
        await session.flush()
        request = AssemblyRequest(background_audio_asset_id=audio.id)

        async def create():
            if through_api:
                return await assemble_video(
                    video_id,
                    request,
                    SimpleNamespace(state=SimpleNamespace()),
                    revision=video.revision,
                    key=f"audio-{scope}",
                    user=await session.get(User, user_id),
                    session=session,
                    settings=Settings(_env_file=None, workspace_root=tmp_path),
                )
            return await AssemblyService().create(
                session,
                video_id=video_id,
                request=request,
                expected_revision=video.revision,
                user_id=user_id,
                request_id=None,
            )

        if scope in {"project", "project_without_link", "product"}:
            final = await create()
            assert final.manifest["background_audio"]["asset_id"] == audio.id
            assert final.manifest["background_audio"]["asset_checksum"] == audio.checksum
        else:
            with pytest.raises(AppError) as error:
                await create()
            assert error.value.code == "BACKGROUND_AUDIO_SCOPE_INVALID"
            await session.flush()
            assert await session.scalar(select(func.count()).select_from(FinalVideo)) == 0
            assert video.status != "ASSEMBLING"

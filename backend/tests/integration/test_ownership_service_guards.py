from __future__ import annotations

from uuid import uuid4

import pytest

from apps.api.app.core.config import Settings
from apps.api.app.core.errors import AppError
from apps.api.app.db.models import Asset, Brand, Product, Project, Scene, Video
from apps.api.app.schemas.api import (
    AssemblyRequest,
    AssetComplete,
    GenerationRequest,
    UploadRequest,
)
from apps.api.app.services.assembly_service import AssemblyService
from apps.api.app.services.asset_service import create_pending_asset, enqueue_asset_validation
from apps.api.app.services.director_run_service import create_director_run
from apps.api.app.services.generation_service import GenerationService
from tests.integration.test_authz import seed_user


async def _seed_foreign_video(session_factory):
    user_a = await seed_user(
        session_factory,
        email=f"service-owner-a-{uuid4().hex}@example.test",
        password="ownership-service-test-password",
    )
    user_b = await seed_user(
        session_factory,
        email=f"service-owner-b-{uuid4().hex}@example.test",
        password="ownership-service-test-password",
    )

    async with session_factory() as session, session.begin():
        project = Project(name="B project", created_by=user_b)
        brand = Brand(name="B brand", created_by=user_b)
        session.add_all((project, brand))
        await session.flush()
        product = Product(name="B product", brand_id=brand.id, created_by=user_b)
        video = Video(
            project_id=project.id,
            product_id=product.id,
            brand_id=brand.id,
            title="B video",
            brief="Private fixture",
            created_by=user_b,
        )
        session.add_all((product, video))
        await session.flush()
        scene = Scene(video_id=video.id, scene_order=0, prompt="Private scene")
        asset = Asset(
            project_id=project.id,
            kind="IMAGE",
            filename="private.png",
            content_type="image/png",
            object_key=f"ownership-service/{uuid4().hex}",
            created_by=user_b,
        )
        session.add_all((scene, asset))
        await session.flush()
        ids = {
            "project": project.id,
            "video": video.id,
            "scene": scene.id,
            "asset": asset.id,
        }
    return user_a, user_b, ids


@pytest.mark.asyncio
async def test_user_facing_services_reject_foreign_resources_before_side_effects(
    session_factory, tmp_path
):
    user_a, _user_b, ids = await _seed_foreign_video(session_factory)
    settings = Settings(_env_file=None, workspace_root=tmp_path)

    async with session_factory() as session, session.begin():
        with pytest.raises(AppError) as generation_error:
            await GenerationService(settings).create(
                session,
                scene_id=ids["scene"],
                request=GenerationRequest(),
                user_id=user_a,
                request_id="foreign-generation",
            )
        assert generation_error.value.status_code == 404

        with pytest.raises(AppError) as director_error:
            await create_director_run(
                session,
                [],
                [],
                video=await session.get(Video, ids["video"]),
                user_id=user_a,
            )
        assert director_error.value.status_code == 404

        with pytest.raises(AppError) as assembly_error:
            await AssemblyService().create(
                session,
                video_id=ids["video"],
                request=AssemblyRequest(),
                expected_revision=1,
                user_id=user_a,
                request_id="foreign-assembly",
            )
        assert assembly_error.value.status_code == 404

        with pytest.raises(AppError) as upload_error:
            await create_pending_asset(
                session,
                object(),
                settings,
                UploadRequest(
                    project_id=ids["project"],
                    filename="cross-owner.png",
                    content_type="image/png",
                    size_bytes=10,
                ),
                user_a,
            )
        assert upload_error.value.status_code == 404

        asset = await session.get(Asset, ids["asset"])
        with pytest.raises(AppError) as complete_error:
            await enqueue_asset_validation(
                session,
                settings,
                asset,
                AssetComplete(),
                user_id=user_a,
            )
        assert complete_error.value.status_code == 404

        assert await session.get(Asset, ids["asset"]) is asset
        assert asset.status == "PENDING"
        assert (await session.get(Video, ids["video"])).status == "DRAFT"

"""Native direct guards, graph creation boundaries and atomic run selection."""

from copy import deepcopy

import pytest
from sqlalchemy import func, select

from apps.api.app.api.products import create_product
from apps.api.app.api.videos import create_video
from apps.api.app.core.config import Settings
from apps.api.app.core.errors import AppError
from apps.api.app.db.models import (
    Asset,
    Brand,
    DirectorRun,
    Product,
    Scene,
    SceneGeneration,
    User,
    Video,
)
from apps.api.app.schemas.api import GenerationRequest, ProductCreate, VideoCreate
from apps.api.app.services.generation_service import GenerationService
from tests.integration.test_director_dispatcher import seed_run, worker
from tests.integration.test_generation_preparation import seed


@pytest.mark.asyncio
@pytest.mark.parametrize("member", [0, 1, 2])
@pytest.mark.parametrize("operation", ["ORIGINAL", "VARIATION", "REGENERATE"])
async def test_every_native_member_rejects_direct_job(session_factory, tmp_path, member, operation):
    user, first_id = await seed(session_factory)
    service = GenerationService(Settings(_env_file=None, workspace_root=tmp_path))
    async with session_factory() as session, session.begin():
        first = await session.get(Scene, first_id)
        scenes = [
            first,
            Scene(video_id=first.video_id, scene_order=1, prompt="B", duration_seconds=5),
            Scene(video_id=first.video_id, scene_order=2, prompt="C", duration_seconds=5),
        ]
        session.add_all(scenes[1:])
        await session.flush()
        parent = None
        if operation != "ORIGINAL":
            parent = await service.create(
                session,
                scene_id=scenes[member].id,
                request=GenerationRequest(seed=42),
                user_id=user,
                request_id=None,
            )
            parent.status = "COMPLETED"
        for row in scenes[1:]:
            row.spec = {"continuity": "CONTINUOUS"}
        await session.flush()
        before = await session.scalar(select(func.count()).select_from(SceneGeneration))
        with pytest.raises(AppError) as error:
            await service.create(
                session,
                scene_id=scenes[member].id,
                request=GenerationRequest(
                    operation=operation, parent_generation_id=parent.id if parent else None
                ),
                user_id=user,
                request_id=None,
            )
        assert error.value.status_code == 422
        assert error.value.code == "DIRECTOR_AGGREGATE_REQUIRED"
        await session.flush()
        assert await session.scalar(select(func.count()).select_from(SceneGeneration)) == before


@pytest.mark.asyncio
async def test_isolated_cut_still_allows_direct(session_factory, tmp_path):
    user, scene = await seed(session_factory)
    async with session_factory() as session, session.begin():
        result = await GenerationService(Settings(_env_file=None, workspace_root=tmp_path)).create(
            session,
            scene_id=scene,
            request=GenerationRequest(seed=42),
            user_id=user,
            request_id=None,
        )
        assert result.status == "CREATED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["archived_inherited", "archived_product_brand", "explicit_mismatch", "explicit_match"]
)
async def test_creation_brand_boundaries(session_factory, case):
    user_id, scene_id = await seed(session_factory)
    async with session_factory() as session, session.begin():
        user = await session.get(User, user_id)
        video = await session.get(Video, (await session.get(Scene, scene_id)).video_id)
        brand = Brand(
            name="Brand",
            description="",
            context={},
            created_by=user_id,
            archived=case.startswith("archived"),
        )
        other = Brand(name="Other", description="", context={}, created_by=user_id)
        session.add_all([brand, other])
        await session.flush()
        product = Product(
            name="Product", description="", context={}, brand_id=brand.id, created_by=user_id
        )
        session.add(product)
        await session.flush()
        model = Product if case == "archived_product_brand" else Video
        before = await session.scalar(select(func.count()).select_from(model))
        if case == "archived_product_brand":
            action = create_product(ProductCreate(name="New", brand_id=brand.id), user, session)
        else:
            action = create_video(
                VideoCreate(
                    project_id=video.project_id,
                    product_id=product.id,
                    brand_id=other.id
                    if case == "explicit_mismatch"
                    else brand.id
                    if case == "explicit_match"
                    else None,
                    title="New video",
                    brief="Test",
                ),
                user,
                session,
            )
        if case == "explicit_match":
            result = await action
            assert result.brand_id == brand.id
            assert result.product_id == product.id
        else:
            with pytest.raises(AppError) as error:
                await action
            assert error.value.code == (
                "PRODUCT_BRAND_CONFLICT" if case == "explicit_mismatch" else "BRAND_NOT_ACTIVE"
            )
            await session.flush()
            assert await session.scalar(select(func.count()).select_from(model)) == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "condition", ["empty", "partial", "occupied", "revision", "semantic", "disabled"]
)
async def test_director_auto_selection_is_atomic(session_factory, tmp_path, condition):
    run_id, ids = await seed_run(session_factory)
    dispatcher = worker(session_factory, tmp_path)
    assert await dispatcher.claim() == run_id
    async with session_factory() as session, session.begin():
        run = await session.get(DirectorRun, run_id)
        video = await session.get(Video, run.video_id)
        scenes = [
            await session.get(Scene, (await session.get(SceneGeneration, key)).scene_id)
            for key in ids
        ]
        for index, scene in enumerate(scenes):
            if condition == "occupied" or (condition == "partial" and index == 0):
                original = await session.get(SceneGeneration, ids[index])
                historical = SceneGeneration(
                    video_id=video.id,
                    scene_id=scene.id,
                    workflow_id=original.workflow_id,
                    generation_no=2,
                    mode="t2v",
                    status="COMPLETED",
                    input_snapshot=deepcopy(original.input_snapshot),
                    created_by=run.created_by,
                )
                session.add(historical)
                await session.flush()
                scene.selected_generation_id = historical.id
        if condition == "revision":
            scenes[1].revision += 1
        elif condition == "semantic":
            scenes[1].prompt = "Changed semantics"
            video.status = "DIRTY"
        elif condition == "disabled":
            scenes[1].enabled = False
        before_ids = [row.selected_generation_id for row in scenes]
        before_revisions = [row.revision for row in scenes]
        before_video_revision = video.revision
        artifacts = []
        for index in range(2):
            asset = Asset(
                kind="VIDEO",
                filename="segment.mp4",
                content_type="video/mp4",
                object_key=f"core/{run_id}/{index}",
                status="READY",
                checksum="a" * 64,
                size_bytes=4,
                created_by=run.created_by,
            )
            session.add(asset)
            await session.flush()
            artifacts.append({"role": "segment", "member_index": index, "asset_id": asset.id})
    await dispatcher._finish(run_id, "COMPLETED", artifacts=artifacts)
    async with session_factory() as session:
        run = await session.get(DirectorRun, run_id)
        video = await session.get(Video, run.video_id)
        scenes = [
            await session.get(Scene, (await session.get(SceneGeneration, key)).scene_id)
            for key in ids
        ]
        assert run.status == "COMPLETED"
        assert [row.selected_generation_id for row in scenes] == (
            ids if condition == "empty" else before_ids
        )
        assert [row.revision for row in scenes] == [
            value + (condition == "empty") for value in before_revisions
        ]
        assert video.revision == before_video_revision + (condition == "empty")
        for key in ids:
            generation = await session.get(SceneGeneration, key)
            assert generation.status == "COMPLETED"
            assert (await session.get(Asset, generation.output_asset_id)).status == "READY"

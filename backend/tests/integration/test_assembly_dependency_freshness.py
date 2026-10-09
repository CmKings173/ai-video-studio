"""Dependency edits preserve immutable renders while preventing current promotion."""

import copy
import hashlib

import pytest

from apps.api.app.core.config import Settings
from apps.api.app.db.models import Asset, Brand, FinalVideo, Product, Scene, User, Video
from apps.api.app.schemas.api import AssemblyRequest, BrandPatch, GenerationRequest, ProductPatch
from apps.api.app.services.assembly_service import AssemblyService
from apps.api.app.services.generation_service import GenerationService
from tests.integration.test_assembly_manifest import CapturingFFmpeg, MemoryStore, worker_settings
from tests.integration.test_generation_preparation import seed
from workers.assembler import Assembler


@pytest.mark.asyncio
@pytest.mark.parametrize("dependency", ["product", "brand", "none", "selection"])
async def test_dependency_edit_after_enqueue_preserves_artifact_without_promotion(
    session_factory,
    tmp_path,
    dependency,
):
    user_id, scene_id = await seed(session_factory)
    async with session_factory() as session, session.begin():
        brand = Brand(name="Brand", description="", context={}, created_by=user_id)
        session.add(brand)
        await session.flush()
        product = Product(
            name="Product", description="", context={}, brand_id=brand.id, created_by=user_id
        )
        session.add(product)
        await session.flush()
        scene = await session.get(Scene, scene_id)
        video = await session.get(Video, scene.video_id)
        video.product_id = product.id
        generation = await GenerationService(
            Settings(
                _env_file=None,
                workspace_root=tmp_path,
                min_free_disk_bytes=0,
            )
        ).create(
            session,
            scene_id=scene_id,
            request=GenerationRequest(seed=42),
            user_id=user_id,
            request_id=None,
        )
        asset = Asset(
            project_id=video.project_id,
            kind="VIDEO",
            filename="scene.mp4",
            content_type="video/mp4",
            object_key="dependency/scene.mp4",
            status="READY",
            checksum=hashlib.sha256(b"scene-one").hexdigest(),
            size_bytes=9,
            created_by=user_id,
        )
        session.add(asset)
        await session.flush()
        generation.status = "COMPLETED"
        generation.output_asset_id = asset.id
        scene.selected_generation_id = generation.id
        await session.flush()
        final = await AssemblyService().create(
            session,
            video_id=video.id,
            request=AssemblyRequest(),
            expected_revision=video.revision,
            user_id=user_id,
            request_id=None,
        )
        final_id, video_id = final.id, video.id
        original_revision = video.revision
        frozen = copy.deepcopy(final.manifest)
        dependency_id = product.id if dependency == "product" else brand.id

    async with session_factory() as session, session.begin():
        user = await session.get(User, user_id)
        if dependency == "product":
            from apps.api.app.api.products import patch_product

            await patch_product(
                dependency_id,
                ProductPatch(context={"tone": "changed"}),
                revision=1,
                user=user,
                session=session,
            )
        elif dependency == "brand":
            from apps.api.app.api.brands import patch_brand

            await patch_brand(
                dependency_id,
                BrandPatch(context={"tone": "changed"}),
                revision=1,
                user=user,
                session=session,
            )
        elif dependency == "selection":
            scene = await session.get(Scene, scene_id)
            scene.selected_generation_id = None
        expected_revision = original_revision + (dependency in {"product", "brand"})
        assert (await session.get(Video, video_id)).revision == expected_revision

    store = MemoryStore()
    store.objects[frozen["scenes"][0]["asset_object_key"]] = b"scene-one"
    ffmpeg = CapturingFFmpeg()
    worker = Assembler(session_factory, store, ffmpeg, worker_settings(tmp_path))
    assert await worker.run_once()

    async with session_factory() as session:
        final = await session.get(FinalVideo, final_id)
        video = await session.get(Video, video_id)
        output = await session.get(Asset, final.output_asset_id)
        assert final.status == "READY"
        assert output.status == "READY"
        assert final.manifest == frozen
        assert video.revision == expected_revision
        if dependency == "none":
            assert video.current_final_video_id == final.id
            assert video.status == "READY"
        else:
            assert video.current_final_video_id is None
            assert video.status == "DIRTY"
    assert ffmpeg.input_payloads == [b"scene-one"]

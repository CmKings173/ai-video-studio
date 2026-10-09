"""Actual mutation/completion paths preserve history and reject stale promotion."""

import pytest

from apps.api.app.api.brands import patch_brand
from apps.api.app.api.products import archive_product, patch_product
from apps.api.app.core.config import Settings
from apps.api.app.db.models import (
    Asset,
    Brand,
    FinalVideo,
    Product,
    Scene,
    SceneGeneration,
    User,
    Video,
)
from apps.api.app.schemas.api import AssemblyRequest, BrandPatch, GenerationRequest, ProductPatch
from apps.api.app.services.assembly_service import AssemblyService
from apps.api.app.services.generation_freshness import is_selected_generation_fresh
from apps.api.app.services.generation_service import GenerationService
from tests.integration.test_assembly_manifest import CapturingFFmpeg, MemoryStore, worker_settings
from tests.integration.test_generation_preparation import seed
from workers.assembler import Assembler
from workers.dispatcher import Dispatcher


async def dependency_generation(factory, tmp_path):
    user_id, scene_id = await seed(factory)
    async with factory() as session, session.begin():
        scene = await session.get(Scene, scene_id)
        video = await session.get(Video, scene.video_id)
        brand = Brand(name="Brand", description="", context={}, created_by=user_id)
        session.add(brand)
        await session.flush()
        product = Product(
            name="Product", description="", context={}, brand_id=brand.id, created_by=user_id
        )
        session.add(product)
        await session.flush()
        video.product_id = product.id
        generation = await GenerationService(
            Settings(_env_file=None, workspace_root=tmp_path)
        ).create(
            session,
            scene_id=scene_id,
            request=GenerationRequest(seed=42),
            user_id=user_id,
            request_id=None,
        )
        return user_id, video.id, scene_id, generation.id, product.id, brand.id


async def edit_dependency(factory, user_id, product_id, brand_id, dependency, values=None):
    async with factory() as session, session.begin():
        user = await session.get(User, user_id)
        if dependency == "product":
            row = await session.get(Product, product_id)
            await patch_product(
                product_id,
                ProductPatch(**(values or {"context": {"tone": "new"}})),
                row.revision,
                user,
                session,
            )
        else:
            row = await session.get(Brand, brand_id)
            await patch_brand(
                brand_id,
                BrandPatch(**(values or {"context": {"tone": "new"}})),
                row.revision,
                user,
                session,
            )


async def ready_asset(session, user_id, key="scene"):
    asset = Asset(
        kind="VIDEO",
        filename="clip.mp4",
        content_type="video/mp4",
        object_key=key,
        status="READY",
        checksum="a" * 64,
        size_bytes=4,
        created_by=user_id,
    )
    session.add(asset)
    await session.flush()
    return asset


@pytest.mark.asyncio
@pytest.mark.parametrize("dependency", ["product", "brand"])
async def test_dependency_edit_during_standalone_completion(session_factory, tmp_path, dependency):
    user, video_id, scene_id, generation_id, product, brand = await dependency_generation(
        session_factory, tmp_path
    )
    worker = Dispatcher(
        session_factory, None, None, None, Settings(_env_file=None, workspace_root=tmp_path)
    )
    async with session_factory() as session, session.begin():
        generation = await session.get(SceneGeneration, generation_id)
        generation.claimed_by, generation.status = worker.owner, "RUNNING"
        output = await ready_asset(session, user)
        output_id = output.id
    await edit_dependency(session_factory, user, product, brand, dependency)
    await worker._finish(generation_id, "COMPLETED", asset_id=output_id)
    async with session_factory() as session:
        generation = await session.get(SceneGeneration, generation_id)
        assert generation.status == "COMPLETED"
        assert generation.output_asset_id == output_id
        assert (await session.get(Asset, output_id)).status == "READY"
        assert (await session.get(Scene, scene_id)).selected_generation_id is None
        assert (await session.get(Video, video_id)).status == "DIRTY"


@pytest.mark.asyncio
@pytest.mark.parametrize("dependency", ["product", "brand"])
async def test_edit_after_promoted_final_retains_history(session_factory, tmp_path, dependency):
    user, video_id, scene_id, generation_id, product, brand = await dependency_generation(
        session_factory, tmp_path
    )
    async with session_factory() as session, session.begin():
        generation = await session.get(SceneGeneration, generation_id)
        asset = await ready_asset(session, user)
        generation.status, generation.output_asset_id = "COMPLETED", asset.id
        scene = await session.get(Scene, scene_id)
        scene.selected_generation_id = generation_id
        video = await session.get(Video, video_id)
        await session.flush()
        final = await AssemblyService().create(
            session,
            video_id=video_id,
            request=AssemblyRequest(),
            expected_revision=video.revision,
            user_id=user,
            request_id=None,
        )
        final_id = final.id
        final.claimed_by, final.status = "test-assembler", "ASSEMBLING"
        output = await ready_asset(session, user, "final")
        output_id = output.id
    worker = Assembler(session_factory, MemoryStore(), CapturingFFmpeg(), worker_settings(tmp_path))
    worker.owner = "test-assembler"
    await worker._finish(final_id, "READY", asset_id=output_id)
    async with session_factory() as session:
        video = await session.get(Video, video_id)
        assert video.status == "READY"
        assert video.current_final_video_id == final_id
        revision = video.revision
    # A second queued assembly must not restore the historical pointer to
    # semantic currency if dependency editing invalidates it before cancellation.
    async with session_factory() as session, session.begin():
        queued = await AssemblyService().create(
            session,
            video_id=video_id,
            request=AssemblyRequest(),
            expected_revision=revision,
            user_id=user,
            request_id=None,
        )
        queued_id = queued.id
    await edit_dependency(session_factory, user, product, brand, dependency)
    from apps.api.app.api.assembly import cancel_final_version

    async with session_factory() as session, session.begin():
        await cancel_final_version(queued_id, await session.get(User, user), session)
    async with session_factory() as session:
        video = await session.get(Video, video_id)
        scene = await session.get(Scene, scene_id)
        assert video.status == "DIRTY" and video.revision == revision + 1
        assert video.current_final_video_id == final_id
        assert (await session.get(FinalVideo, final_id)).status == "READY"
        assert (await session.get(Asset, output_id)).status == "READY"
        assert scene.selected_generation_id == generation_id
        assert not await is_selected_generation_fresh(session, scene, video)


@pytest.mark.asyncio
@pytest.mark.parametrize("dependency", ["product", "brand"])
async def test_noop_semantic_patch_does_not_invalidate(session_factory, tmp_path, dependency):
    user, video_id, _, _, product, brand = await dependency_generation(session_factory, tmp_path)
    async with session_factory() as session:
        video = await session.get(Video, video_id)
        revision, status = video.revision, video.status
    await edit_dependency(session_factory, user, product, brand, dependency, {"context": {}})
    async with session_factory() as session:
        video = await session.get(Video, video_id)
        assert (video.revision, video.status) == (revision, status)


@pytest.mark.asyncio
async def test_product_archive_invalidates_dependents(session_factory, tmp_path):
    user, video_id, _, _, product, _ = await dependency_generation(session_factory, tmp_path)
    async with session_factory() as session, session.begin():
        await archive_product(product, 1, await session.get(User, user), session)
        video = await session.get(Video, video_id)
        assert video.status == "DIRTY"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["QUICK_CLIP", "LONG_VIDEO"])
async def test_delayed_stale_job_does_not_dirty_a_new_fresh_final(session_factory, tmp_path, kind):
    user, video_id, scene_id, old_id, product, brand = await dependency_generation(
        session_factory, tmp_path
    )
    dispatcher = Dispatcher(
        session_factory, None, None, None, Settings(_env_file=None, workspace_root=tmp_path)
    )
    async with session_factory() as session, session.begin():
        old = await session.get(SceneGeneration, old_id)
        old.status, old.claimed_by = "RUNNING", dispatcher.owner
    await edit_dependency(session_factory, user, product, brand, "product")
    async with session_factory() as session, session.begin():
        video = await session.get(Video, video_id)
        video.kind = kind
        current = await GenerationService(dispatcher.settings).create(
            session,
            scene_id=scene_id,
            request=GenerationRequest(seed=43),
            user_id=user,
            request_id=None,
        )
        output = await ready_asset(session, user, "fresh-scene")
        current.status, current.output_asset_id = "COMPLETED", output.id
        scene = await session.get(Scene, scene_id)
        scene.selected_generation_id = current.id
        await session.flush()
        final = await AssemblyService().create(
            session,
            video_id=video_id,
            request=AssemblyRequest(),
            expected_revision=video.revision,
            user_id=user,
            request_id=None,
        )
        final.claimed_by, final.status = "new-final-owner", "ASSEMBLING"
        final_output = await ready_asset(session, user, "fresh-final")
        stale_output = await ready_asset(session, user, "stale-scene")
        current_id, final_id, output_id, stale_id = (
            current.id,
            final.id,
            final_output.id,
            stale_output.id,
        )
    assembler = Assembler(
        session_factory, MemoryStore(), CapturingFFmpeg(), worker_settings(tmp_path)
    )
    assembler.owner = "new-final-owner"
    await assembler._finish(final_id, "READY", asset_id=output_id)
    await dispatcher._finish(old_id, "COMPLETED", asset_id=stale_id)
    async with session_factory() as session:
        video = await session.get(Video, video_id)
        assert video.status == "READY"
        assert video.current_final_video_id == final_id
        assert (await session.get(Scene, scene_id)).selected_generation_id == current_id
        assert (await session.get(SceneGeneration, old_id)).status == "COMPLETED"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["brand", "product_brand", "product"])
async def test_effective_brand_override_resolution(session_factory, tmp_path, change):
    user, video_id, _, _, product_id, brand_id = await dependency_generation(
        session_factory, tmp_path
    )
    async with session_factory() as session, session.begin():
        inherited = await session.get(Video, video_id)
        other_brand = Brand(name="Override", description="", context={}, created_by=user)
        session.add(other_brand)
        await session.flush()
        explicit = Video(
            project_id=inherited.project_id,
            product_id=product_id,
            brand_id=other_brand.id,
            title="Explicit",
            brief="Test",
            created_by=user,
        )
        direct = Video(
            project_id=inherited.project_id,
            brand_id=brand_id,
            title="Direct",
            brief="Test",
            created_by=user,
        )
        session.add_all([explicit, direct])
        await session.flush()
        explicit_id, direct_id, other_id = explicit.id, direct.id, other_brand.id
        revisions = {row.id: row.revision for row in [inherited, explicit, direct]}
    values = {"brand_id": other_id} if change == "product_brand" else None
    await edit_dependency(
        session_factory,
        user,
        product_id,
        brand_id,
        "brand" if change == "brand" else "product",
        values,
    )
    async with session_factory() as session:
        for key, affected in [
            (video_id, True),
            (explicit_id, change == "product"),
            (direct_id, change == "brand"),
        ]:
            assert (await session.get(Video, key)).revision == revisions[key] + affected


@pytest.mark.asyncio
@pytest.mark.parametrize("dependency", ["product", "brand"])
async def test_dependency_edit_during_aggregate_retains_all_outputs(
    session_factory, tmp_path, monkeypatch, dependency
):
    from apps.api.app.db.models import DirectorRun
    from apps.api.app.services.generation_freshness import (
        bind_execution_group_freshness,
        capture_generation_freshness,
    )
    from tests.integration.test_director_dynamic_aggregate import (
        qualified_batch_state,
        submit_batch,
    )
    from tests.integration.test_execution_group_freshness import complete_run

    user, video_id, scene_ids = await qualified_batch_state(
        session_factory, tmp_path, monkeypatch, ["CUT", "CONTINUOUS"]
    )
    result = await submit_batch(session_factory, tmp_path, user, video_id)
    ids = [row.id for row in result.generations]
    run_id = str(result.execution_groups[0].director_run_id)
    async with session_factory() as session, session.begin():
        video = await session.get(Video, video_id)
        brand = Brand(name="Brand", description="", context={}, created_by=user)
        session.add(brand)
        await session.flush()
        product = Product(
            name="Product", description="", context={}, brand_id=brand.id, created_by=user
        )
        session.add(product)
        await session.flush()
        video.product_id, video.status = product.id, "GENERATING"
        generations = [await session.get(SceneGeneration, key) for key in ids]
        for scene_id, generation in zip(scene_ids, generations, strict=True):
            scene = await session.get(Scene, scene_id)
            scene.selected_generation_id = None
            generation.status = "CREATED"
            generation.input_snapshot = {
                **generation.input_snapshot,
                "scene_revision": scene.revision,
                "generation_freshness": await capture_generation_freshness(session, scene, video),
            }
        run = await session.get(DirectorRun, run_id)
        run.status, run.claimed_by = "CREATED", None
        bind_execution_group_freshness(generations, run)
        product_id, brand_id = product.id, brand.id
    await edit_dependency(session_factory, user, product_id, brand_id, dependency)
    await complete_run(session_factory, tmp_path, run_id)
    async with session_factory() as session:
        assert (await session.get(DirectorRun, run_id)).status == "COMPLETED"
        assert (await session.get(Video, video_id)).status == "DIRTY"
        for scene_id, generation_id in zip(scene_ids, ids, strict=True):
            assert (await session.get(Scene, scene_id)).selected_generation_id is None
            generation = await session.get(SceneGeneration, generation_id)
            assert generation.status == "COMPLETED"
            assert (await session.get(Asset, generation.output_asset_id)).status == "READY"

"""Freshness through actual generation, selection and assembly services."""

import copy

import pytest

from apps.api.app.api.scenes import select_generation
from apps.api.app.core.config import Settings
from apps.api.app.core.errors import AppError
from apps.api.app.db.models import Asset, Brand, Product, Scene, SceneGeneration, User, Video
from apps.api.app.schemas.api import AssemblyRequest, GenerateAll, GenerationRequest, Selection
from apps.api.app.services.assembly_service import AssemblyService
from apps.api.app.services.batch_identity import capture_batch_inputs, unchanged_batch_inputs
from apps.api.app.services.generation_freshness import (
    is_generation_fresh,
    is_selected_generation_fresh,
)
from apps.api.app.services.generation_service import GenerationService
from tests.integration.test_generate_all import submit
from tests.integration.test_generation_preparation import seed


async def generated_selection(factory, tmp_path):
    user_id, scene_id = await seed(factory)
    async with factory() as session, session.begin():
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
            kind="VIDEO",
            filename="clip.mp4",
            content_type="video/mp4",
            object_key="freshness/clip.mp4",
            status="READY",
            checksum="a" * 64,
            size_bytes=9,
            created_by=user_id,
        )
        session.add(asset)
        await session.flush()
        generation.status = "COMPLETED"
        generation.output_asset_id = asset.id
        scene = await session.get(Scene, scene_id)
        scene.selected_generation_id = generation.id
        scene.revision += 1
        video = await session.get(Video, scene.video_id)
        video.revision += 1
        return user_id, video.id, scene_id, generation.id


@pytest.mark.asyncio
async def test_generation_selection_prompt_edit_rejects_assembly(session_factory, tmp_path):
    user, video_id, scene_id, generation_id = await generated_selection(session_factory, tmp_path)
    async with session_factory() as session, session.begin():
        scene = await session.get(Scene, scene_id)
        video = await session.get(Video, video_id)
        scene.prompt = "A different actual scene"
        scene.revision += 1
        video.revision += 1
        with pytest.raises(AppError) as caught:
            await AssemblyService().create(
                session,
                video_id=video_id,
                request=AssemblyRequest(),
                expected_revision=video.revision,
                user_id=user,
                request_id=None,
            )
        assert caught.value.code == "SCENE_SELECTION_STALE"
        assert scene.selected_generation_id == generation_id


@pytest.mark.asyncio
async def test_generation_snapshot_records_freshness_identity(session_factory, tmp_path):
    _, _, _, generation_id = await generated_selection(session_factory, tmp_path)
    async with session_factory() as session:
        generation = await session.get(SceneGeneration, generation_id)
        assert generation.input_snapshot.get("generation_freshness", {}).get("fingerprint")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    [
        "prompt",
        "negative_prompt",
        "duration_seconds",
        "spec",
        "generation_config",
        "brief",
        "aspect_ratio",
        "music",
    ],
)
async def test_semantic_edits_make_selected_output_eligible(session_factory, tmp_path, field):
    _, video_id, scene_id, generation_id = await generated_selection(session_factory, tmp_path)
    async with session_factory() as session, session.begin():
        scene = await session.get(Scene, scene_id)
        video = await session.get(Video, video_id)
        generation = await session.get(SceneGeneration, generation_id)
        assert await is_selected_generation_fresh(session, scene, video)
        if field == "music":
            video.config = {"music": "different"}
        elif field in {"brief", "aspect_ratio"}:
            setattr(video, field, "different")
        else:
            setattr(
                scene,
                field,
                {"camera": "new"}
                if field == "spec"
                else {"seed": 17}
                if field == "generation_config"
                else 6
                if field == "duration_seconds"
                else "different",
            )
        assert not await is_generation_fresh(session, scene, video, generation)
        assert not await is_selected_generation_fresh(session, scene, video)
        assert scene.selected_generation_id == generation_id


@pytest.mark.asyncio
async def test_generate_all_regenerates_stale_selected_scene(session_factory, tmp_path):
    user, video_id, scene_id, generation_id = await generated_selection(session_factory, tmp_path)
    async with session_factory() as session, session.begin():
        scene = await session.get(Scene, scene_id)
        scene.prompt = "Generate current semantics"
        scene.revision += 1
        video = await session.get(Video, video_id)
        video.revision += 1
    response = await submit(session_factory, tmp_path, user, video_id, GenerateAll())
    assert [row.scene_id for row in response.generations] == [scene_id]
    assert response.generations[0].id != generation_id
    async with session_factory() as session:
        scene = await session.get(Scene, scene_id)
        assert scene.selected_generation_id == generation_id


@pytest.mark.asyncio
async def test_selection_endpoint_rejects_stale_without_clearing_history(session_factory, tmp_path):
    user, video_id, scene_id, generation_id = await generated_selection(session_factory, tmp_path)
    async with session_factory() as session, session.begin():
        scene = await session.get(Scene, scene_id)
        scene.prompt = "Changed before selection"
        with pytest.raises(AppError) as caught:
            await select_generation(
                scene_id=scene_id,
                payload=Selection(generation_id=generation_id),
                revision=scene.revision,
                user=await session.get(User, user),
                session=session,
            )
        assert caught.value.code == "SCENE_SELECTION_STALE"
        assert scene.selected_generation_id == generation_id


@pytest.mark.asyncio
@pytest.mark.parametrize("dependency", ["product", "brand"])
async def test_semantic_dependency_edit_invalidates_selection_and_batch_replay(
    session_factory,
    tmp_path,
    dependency,
):
    user, scene_id = await seed(session_factory)
    async with session_factory() as session, session.begin():
        brand = Brand(name="Brand", description="", context={}, created_by=user)
        session.add(brand)
        await session.flush()
        product = Product(
            name="Product", description="", context={}, brand_id=brand.id, created_by=user
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
            user_id=user,
            request_id=None,
        )
        before = await capture_batch_inputs(session, video, [scene])
        assert await is_generation_fresh(session, scene, video, generation)
        target = product if dependency == "product" else brand
        # Dependency revision counters alone are not source semantics.
        target.revision += 1
        assert await is_generation_fresh(session, scene, video, generation)
        target.context = {"style": "changed"}
        assert not await is_generation_fresh(session, scene, video, generation)
        after = await capture_batch_inputs(session, video, [scene])
        assert not await unchanged_batch_inputs(session, before, after)


@pytest.mark.asyncio
async def test_unrelated_edit_and_selection_revision_retain_freshness(session_factory, tmp_path):
    user, video_id, scene_id, _ = await generated_selection(session_factory, tmp_path)
    async with session_factory() as session, session.begin():
        scene = await session.get(Scene, scene_id)
        video = await session.get(Video, video_id)
        video.title = "Display title only"
        video.config = {"assembly": {"transition": "CUT"}}
        video.revision += 1
        scene.revision += 1
        assert await is_selected_generation_fresh(session, scene, video)
        final = await AssemblyService().create(
            session,
            video_id=video_id,
            request=AssemblyRequest(),
            expected_revision=video.revision,
            user_id=user,
            request_id=None,
        )
        assert final.manifest["scenes"][0]["generation_id"] == scene.selected_generation_id


@pytest.mark.asyncio
async def test_derivative_after_edit_inherits_stale_parent_identity(session_factory, tmp_path):
    user, video_id, scene_id, generation_id = await generated_selection(session_factory, tmp_path)
    async with session_factory() as session, session.begin():
        parent = await session.get(SceneGeneration, generation_id)
        original = copy.deepcopy(parent.input_snapshot["generation_freshness"])
        scene = await session.get(Scene, scene_id)
        video = await session.get(Video, video_id)
        scene.prompt = "Current edited prompt"
        scene.revision += 1
        derivative = await GenerationService(
            Settings(
                _env_file=None,
                workspace_root=tmp_path,
                min_free_disk_bytes=0,
            )
        ).create(
            session,
            scene_id=scene_id,
            request=GenerationRequest(operation="VARIATION", parent_generation_id=parent.id),
            user_id=user,
            request_id=None,
        )
        assert derivative.input_snapshot["generation_freshness"] == original
        assert not await is_generation_fresh(session, scene, video, derivative)


@pytest.mark.asyncio
async def test_legacy_snapshot_fails_closed(session_factory, tmp_path):
    _, video_id, scene_id, generation_id = await generated_selection(session_factory, tmp_path)
    async with session_factory() as session:
        scene = await session.get(Scene, scene_id)
        video = await session.get(Video, video_id)
        generation = await session.get(SceneGeneration, generation_id)
        generation.input_snapshot = {"scene_revision": scene.revision}
        assert not await is_selected_generation_fresh(session, scene, video)


@pytest.mark.asyncio
async def test_scene_api_responses_project_current_freshness(session_factory, tmp_path):
    from apps.api.app.api.scenes import (
        disable_scene,
        enable_scene,
        get_scene,
        list_scenes,
        patch_scene,
        reorder,
    )
    from apps.api.app.schemas.api import Reorder, ScenePatch

    user_id, video_id, scene_id, generation_id = await generated_selection(
        session_factory, tmp_path
    )
    async with session_factory() as session, session.begin():
        user = await session.get(User, user_id)
        scene = await session.get(Scene, scene_id)
        video = await session.get(Video, video_id)
        assert (await get_scene(scene_id, user=user, session=session)).selected_generation_fresh
        assert (await list_scenes(video_id, user=user, session=session))[
            0
        ].selected_generation_fresh
        selected = await select_generation(
            scene_id,
            Selection(generation_id=generation_id),
            revision=scene.revision,
            user=user,
            session=session,
        )
        assert selected.selected_generation_fresh
        ordered = await reorder(
            video_id,
            Reorder(scene_ids=[scene_id]),
            revision=video.revision,
            user=user,
            session=session,
        )
        assert ordered[0].selected_generation_fresh
        disabled = await disable_scene(
            scene_id, revision=scene.revision, user=user, session=session
        )
        assert disabled.selected_generation_fresh
        enabled = await enable_scene(scene_id, revision=scene.revision, user=user, session=session)
        assert enabled.selected_generation_fresh
        edited = await patch_scene(
            scene_id,
            ScenePatch(prompt="Changed prompt"),
            revision=scene.revision,
            user=user,
            session=session,
        )
        assert not edited.selected_generation_fresh
        assert edited.selected_generation_id == generation_id


async def dirty_state_dependencies(factory, user_id, scene_id):
    """Commit dependency setup so only the mutation under test remains unflushed."""
    async with factory() as session, session.begin():
        inherited = Brand(
            name="Inherited brand", description="Original tone", context={}, created_by=user_id
        )
        override = Brand(
            name="Override brand", description="Override tone", context={}, created_by=user_id
        )
        session.add_all([inherited, override])
        await session.flush()
        original = Product(
            name="Original product",
            description="Original packaging",
            context={},
            brand_id=inherited.id,
            created_by=user_id,
        )
        replacement = Product(
            name="Replacement product",
            description="Replacement packaging",
            context={},
            brand_id=override.id,
            created_by=user_id,
        )
        session.add_all([original, replacement])
        await session.flush()
        scene = await session.get(Scene, scene_id)
        video = await session.get(Video, scene.video_id)
        video.product_id = original.id
        return video.id, original.id, replacement.id, inherited.id, override.id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    [
        "product_id",
        "brand_id",
        "scene_prompt",
        "scene_revision",
        "scene_spec",
        "video_config",
        "video_revision_status",
        "product_context",
        "brand_context",
    ],
)
async def test_same_transaction_dirty_inputs_survive_generation_and_commit(
    session_factory,
    tmp_path,
    mutation,
):
    from apps.api.app.services.generation_freshness import capture_generation_freshness

    user_id, scene_id = await seed(session_factory)
    video_id, product_id, replacement_id, brand_id, override_id = await dirty_state_dependencies(
        session_factory, user_id, scene_id
    )
    async with session_factory() as session, session.begin():
        assert session.autoflush is False
        scene = await session.get(Scene, scene_id)
        video = await session.get(Video, video_id)
        product = await session.get(Product, product_id)
        brand = await session.get(Brand, brand_id)
        target, field, value = {
            "product_id": (video, "product_id", replacement_id),
            "brand_id": (video, "brand_id", override_id),
            "scene_prompt": (scene, "prompt", "Dirty prompt with emerald lighting"),
            "scene_revision": (scene, "revision", scene.revision + 1),
            "scene_spec": (scene, "spec", {"subject": "Dirty subject", "camera": "orbit"}),
            "video_config": (
                video,
                "config",
                {"music": "Dirty orchestral score", "prompt_enhancer": {}},
            ),
            "video_revision_status": (video, "revision", video.revision + 1),
            "product_context": (product, "context", {"packaging": "emerald glass"}),
            "brand_context": (brand, "context", {"tone": "quiet luxury"}),
        }[mutation]
        setattr(target, field, value)
        if mutation == "video_revision_status":
            video.status = "DIRTY"
        assert target in session.dirty
        expected_identity = await capture_generation_freshness(session, scene, video)
        expected_scene_revision, expected_video_revision = scene.revision, video.revision
        # No caller flush: create must preserve the pending intent at its refresh boundary.
        generation = await GenerationService(
            Settings(_env_file=None, workspace_root=tmp_path, min_free_disk_bytes=0)
        ).create(
            session,
            scene_id=scene_id,
            request=GenerationRequest(seed=42),
            user_id=user_id,
            request_id=None,
        )
        generation_id = generation.id

    # A separate session proves persistence rather than merely identity-map visibility.
    async with session_factory() as session:
        persisted = await session.get(type(target), target.id)
        assert getattr(persisted, field) == value
        scene = await session.get(Scene, scene_id)
        video = await session.get(Video, video_id)
        generation = await session.get(SceneGeneration, generation_id)
        assert scene.revision == expected_scene_revision
        assert video.revision == expected_video_revision
        if mutation == "video_revision_status":
            assert video.status == "DIRTY"
        frozen = generation.input_snapshot
        assert frozen["scene_revision"] == scene.revision
        assert frozen["video_revision"] == video.revision
        assert frozen["source_scope"]["product_id"] == video.product_id
        assert frozen["generation_freshness"] == expected_identity
        assert frozen["generation_freshness"] == await capture_generation_freshness(
            session, scene, video
        )
        dependencies = frozen["generation_freshness"]["inputs"]["dependencies"]
        product = await session.get(Product, video.product_id)
        assert dependencies["product"]["id"] == video.product_id
        assert dependencies["brand"]["id"] == (video.brand_id or product.brand_id)
        assert await is_generation_fresh(session, scene, video, generation)
        if mutation == "scene_prompt":
            assert value in frozen["raw_prompt"]
        elif mutation == "video_config":
            assert value["music"] in frozen["raw_prompt"]
        elif mutation == "brand_context":
            assert value["tone"] in frozen["raw_prompt"]


@pytest.mark.asyncio
async def test_same_transaction_multiple_generation_calls_keep_distinct_snapshots(
    session_factory,
    tmp_path,
):
    from apps.api.app.services.generation_freshness import capture_generation_freshness

    user_id, scene_id = await seed(session_factory)
    async with session_factory() as session, session.begin():
        assert session.autoflush is False
        scene = await session.get(Scene, scene_id)
        video = await session.get(Video, scene.video_id)
        video_id = video.id
        service = GenerationService(
            Settings(_env_file=None, workspace_root=tmp_path, min_free_disk_bytes=0)
        )
        scene.prompt = "First uncommitted scene intent"
        scene.revision += 1
        first_identity = await capture_generation_freshness(session, scene, video)
        first = await service.create(
            session,
            scene_id=scene_id,
            request=GenerationRequest(seed=41),
            user_id=user_id,
            request_id=None,
        )
        first_snapshot = copy.deepcopy(first.input_snapshot)
        scene.prompt = "Second uncommitted scene intent"
        scene.revision += 1
        video.config = {"music": "Second uncommitted score"}
        second_revision = scene.revision
        second_identity = await capture_generation_freshness(session, scene, video)
        second = await service.create(
            session,
            scene_id=scene_id,
            request=GenerationRequest(seed=42),
            user_id=user_id,
            request_id=None,
        )
        first_id, second_id = first.id, second.id

    async with session_factory() as session:
        scene = await session.get(Scene, scene_id)
        video = await session.get(Video, video_id)
        first = await session.get(SceneGeneration, first_id)
        second = await session.get(SceneGeneration, second_id)
        assert scene.prompt == "Second uncommitted scene intent"
        assert scene.revision == second_revision
        assert video.config == {"music": "Second uncommitted score"}
        assert first.input_snapshot == first_snapshot
        assert first.input_snapshot["generation_freshness"] == first_identity
        assert second.input_snapshot["generation_freshness"] == second_identity
        assert second.input_snapshot["scene_revision"] == scene.revision
        assert second.input_snapshot["video_revision"] == video.revision
        assert second.input_snapshot["generation_freshness"] == await capture_generation_freshness(
            session, scene, video
        )
        assert first.generation_no == 1 and second.generation_no == 2
        assert first_identity["fingerprint"] != second_identity["fingerprint"]
        assert not await is_generation_fresh(session, scene, video, first)
        assert await is_generation_fresh(session, scene, video, second)


@pytest.mark.asyncio
@pytest.mark.parametrize("revision_owner", ["scene", "video"])
async def test_same_transaction_dirty_revision_rejects_stale_preview(
    session_factory,
    tmp_path,
    revision_owner,
):
    user_id, scene_id = await seed(session_factory)
    async with session_factory() as session, session.begin():
        assert session.autoflush is False
        scene = await session.get(Scene, scene_id)
        video = await session.get(Video, scene.video_id)
        old_scene_revision, old_video_revision = scene.revision, video.revision
        target = scene if revision_owner == "scene" else video
        target.revision += 1
        if revision_owner == "scene":
            scene.prompt = "Edited after preview acceptance"
        else:
            video.config = {"music": "Edited after preview acceptance"}
        expected_revision = target.revision
        assert target in session.dirty
        with pytest.raises(AppError) as caught:
            await GenerationService(
                Settings(_env_file=None, workspace_root=tmp_path, min_free_disk_bytes=0)
            ).create(
                session,
                scene_id=scene_id,
                request=GenerationRequest(
                    seed=42,
                    execution_prompt="Previously accepted execution prompt",
                    source_scene_revision=old_scene_revision,
                    source_video_revision=old_video_revision,
                ),
                user_id=user_id,
                request_id=None,
            )
        assert caught.value.code == "PROMPT_PREVIEW_STALE"
        assert target.revision == expected_revision


@pytest.mark.asyncio
async def test_same_transaction_preflight_preserves_dirty_intent_without_dml(
    session_factory,
    tmp_path,
):
    from sqlalchemy import event, select

    from apps.api.app.services.generation_freshness import capture_generation_freshness

    user_id, scene_id = await seed(session_factory)
    video_id, _, replacement_id, _, _ = await dirty_state_dependencies(
        session_factory, user_id, scene_id
    )
    async with session_factory() as session, session.begin():
        assert session.autoflush is False
        user = await session.get(User, user_id)
        scene = await session.get(Scene, scene_id)
        video = await session.get(Video, video_id)
        original_user_name = user.name
        user.name = "Unrelated pending user edit"
        video.product_id = replacement_id
        scene.prompt = "Unflushed preflight scene intent"
        scene.revision += 1
        expected_revision = scene.revision
        expected_identity = await capture_generation_freshness(session, scene, video)
        statements = []

        def record_sql(_connection, _cursor, statement, _parameters, _context, _many):
            statements.append(statement)

        engine = session.bind.sync_engine
        event.listen(engine, "before_cursor_execute", record_sql)
        try:
            generation = await GenerationService(
                Settings(_env_file=None, workspace_root=tmp_path, min_free_disk_bytes=0)
            ).create(
                session,
                scene_id=scene_id,
                request=GenerationRequest(seed=42),
                user_id=user_id,
                request_id=None,
                persist=False,
            )
        finally:
            event.remove(engine, "before_cursor_execute", record_sql)
        assert statements, "Preflight must exercise the actual database read boundary"
        assert not any(
            statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
            for statement in statements
        ), statements
        assert generation not in session
        assert user.name == "Unrelated pending user edit"
        assert user in session.dirty
        assert (
            await session.scalar(select(User.name).where(User.id == user_id)) == original_user_name
        )
        assert await session.scalar(select(SceneGeneration.id)) is None
        assert video.product_id == replacement_id
        assert scene.prompt == "Unflushed preflight scene intent"
        assert scene.revision == expected_revision
        assert video in session.dirty and scene in session.dirty
        assert generation.input_snapshot["generation_freshness"] == expected_identity
        assert generation.input_snapshot["source_scope"]["product_id"] == replacement_id
        assert generation.input_snapshot["scene_revision"] == expected_revision
        await session.rollback()


@pytest.mark.asyncio
@pytest.mark.parametrize("dependency", ["product", "brand"])
@pytest.mark.parametrize("first_noop", [True, False], ids=["noop_then_semantic", "semantic_twice"])
async def test_same_transaction_dependency_patches_preserve_pending_revisions(
    session_factory,
    tmp_path,
    dependency,
    first_noop,
):
    from apps.api.app.api.brands import patch_brand
    from apps.api.app.api.products import patch_product
    from apps.api.app.schemas.api import BrandPatch, ProductPatch
    from tests.integration.test_dependency_invalidation import dependency_generation

    user_id, video_id, _, generation_id, product_id, brand_id = await dependency_generation(
        session_factory, tmp_path
    )
    model, identity, patch, payload_type = (
        (Product, product_id, patch_product, ProductPatch)
        if dependency == "product"
        else (Brand, brand_id, patch_brand, BrandPatch)
    )
    async with session_factory() as session, session.begin():
        assert session.autoflush is False
        user = await session.get(User, user_id)
        row = await session.get(model, identity)
        video = await session.get(Video, video_id)
        baseline_revision, baseline_video_revision = row.revision, video.revision
        baseline_status = video.status
        first_context = {} if first_noop else {"tone": "First semantic patch"}
        first = await patch(
            identity,
            payload_type(context=first_context),
            revision=baseline_revision,
            user=user,
            session=session,
        )
        assert first.revision == baseline_revision + 1
        assert row.context == first_context
        assert video.revision == baseline_video_revision + (not first_noop)
        assert video.status == (baseline_status if first_noop else "DIRTY")
        # Use the DTO's accepted revision, without flushing or committing between calls.
        second_context = {"tone": "Second semantic patch"}
        second = await patch(
            identity,
            payload_type(context=second_context),
            revision=first.revision,
            user=user,
            session=session,
        )
        assert second.revision == baseline_revision + 2
        assert row.context == second_context
        semantic_count = 1 if first_noop else 2
        assert video.revision == baseline_video_revision + semantic_count
        assert video.status == "DIRTY"
        if first_noop:
            # A third call must retain the second call's pending invalidation as well.
            final_context = {"tone": "Third semantic patch"}
            third = await patch(
                identity,
                payload_type(context=final_context),
                revision=second.revision,
                user=user,
                session=session,
            )
            assert third.revision == baseline_revision + 3
            patch_count, semantic_count = 3, 2
        else:
            final_context, patch_count = second_context, 2
        assert row.context == final_context
        assert row.revision == baseline_revision + patch_count
        assert video.revision == baseline_video_revision + semantic_count
        assert video.status == "DIRTY"

    async with session_factory() as session:
        row = await session.get(model, identity)
        video = await session.get(Video, video_id)
        assert row.context == final_context
        assert row.revision == baseline_revision + patch_count
        assert video.revision == baseline_video_revision + semantic_count
        assert video.status == "DIRTY"
        assert (await session.get(SceneGeneration, generation_id)).status == "CREATED"


@pytest.mark.asyncio
@pytest.mark.parametrize("second_member", [0, 1], ids=["same_member", "sibling_member"])
async def test_same_transaction_aggregate_cancel_twice_retains_terminal_members(
    session_factory,
    second_member,
):
    from sqlalchemy import select

    from apps.api.app.api.generations import cancel_generation
    from apps.api.app.db.models import DirectorRun, DirectorRunMember
    from tests.integration.test_director_dispatcher import seed_run

    run_id, generation_ids = await seed_run(session_factory)
    async with session_factory() as session, session.begin():
        run = await session.get(DirectorRun, run_id)
        video_id, user_id = run.video_id, run.created_by
        video = await session.get(Video, video_id)
        video.status = "DIRTY"

    async with session_factory() as session, session.begin():
        assert session.autoflush is False
        user = await session.get(User, user_id)
        video = await session.get(Video, video_id)
        run = await session.get(DirectorRun, run_id)
        baseline_video_revision, baseline_run_revision = video.revision, run.revision
        generations = [await session.get(SceneGeneration, key) for key in generation_ids]
        baseline_revisions = [generation.revision for generation in generations]
        first = await cancel_generation(generation_ids[0], user=user, session=session)
        second = await cancel_generation(generation_ids[second_member], user=user, session=session)
        # Commit before assertions to verify the actual terminal state survives both calls.

    async with session_factory() as session:
        video = await session.get(Video, video_id)
        run = await session.get(DirectorRun, run_id)
        assert video.status == "DIRTY"
        assert video.revision == baseline_video_revision
        assert run.status == run.phase == "CANCELLED"
        assert run.revision == baseline_run_revision + 1
        assert run.finished_at is not None
        members = list(
            (
                await session.scalars(
                    select(DirectorRunMember)
                    .where(DirectorRunMember.director_run_id == run_id)
                    .order_by(DirectorRunMember.member_index)
                )
            ).all()
        )
        assert len(members) == len(generation_ids)
        for generation_id, baseline_revision, member in zip(
            generation_ids, baseline_revisions, members, strict=True
        ):
            generation = await session.get(SceneGeneration, generation_id)
            assert generation.status == generation.phase == "CANCELLED"
            assert generation.revision == baseline_revision + 1
            assert generation.finished_at == run.finished_at
            assert generation.output_asset_id is None
            assert member.status == "CANCELLED"
        assert first.status == second.status == "CANCELLED"
        assert first.revision == baseline_revisions[0] + 1
        assert second.revision == baseline_revisions[second_member] + 1


def _freshness_dependency_patch(dependency, product_id, brand_id):
    from apps.api.app.api.brands import patch_brand
    from apps.api.app.api.products import patch_product
    from apps.api.app.schemas.api import BrandPatch, ProductPatch

    return (
        (Product, product_id, patch_product, ProductPatch)
        if dependency == "product"
        else (Brand, brand_id, patch_brand, BrandPatch)
    )


async def _assert_dependency_patch_conflict_without_dml(
    session, patch, identity, payload, user, row
):
    from sqlalchemy import event

    statements = []

    def record_sql(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    engine = session.bind.sync_engine
    event.listen(engine, "before_cursor_execute", record_sql)
    try:
        with pytest.raises(AppError) as caught:
            await patch(identity, payload, revision=row.revision, user=user, session=session)
    finally:
        event.remove(engine, "before_cursor_execute", record_sql)
    assert caught.value.code == "REVISION_CONFLICT"
    assert not any(
        statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
        for statement in statements
    ), statements


@pytest.mark.asyncio
@pytest.mark.parametrize("dependency", ["product", "brand"])
async def test_staged_dependency_context_same_value_patch_rejected_without_dml(
    session_factory, tmp_path, dependency
):
    from tests.integration.test_dependency_invalidation import dependency_generation

    user_id, video_id, scene_id, generation_id, product_id, brand_id = await dependency_generation(
        session_factory, tmp_path
    )
    model, identity, patch, payload_type = _freshness_dependency_patch(
        dependency, product_id, brand_id
    )
    async with session_factory() as session:
        user = await session.get(User, user_id)
        row = await session.get(model, identity)
        video = await session.get(Video, video_id)
        scene = await session.get(Scene, scene_id)
        baseline = (row.revision, video.revision, video.status, scene.selected_generation_id)
        staged = {"tone": "Unaccounted staged context"}
        row.context = staged
        assert row in session.dirty
        await _assert_dependency_patch_conflict_without_dml(
            session, patch, identity, payload_type(context=staged), user, row
        )
        assert row.context == staged
        assert (
            row.revision, video.revision, video.status, scene.selected_generation_id
        ) == baseline
        await session.rollback()

    async with session_factory() as session:
        row = await session.get(model, identity)
        video = await session.get(Video, video_id)
        scene = await session.get(Scene, scene_id)
        assert row.context == {}
        assert (
            row.revision, video.revision, video.status, scene.selected_generation_id
        ) == baseline
        assert (await session.get(SceneGeneration, generation_id)).status == "CREATED"


@pytest.mark.asyncio
@pytest.mark.parametrize("dependency", ["product", "brand"])
@pytest.mark.parametrize("binding", ["video_product", "video_brand", "product_brand"])
async def test_pending_dependency_binding_patch_rejected_without_dml(
    session_factory, tmp_path, dependency, binding
):
    from tests.integration.test_dependency_invalidation import dependency_generation

    user_id, video_id, _, generation_id, product_id, brand_id = await dependency_generation(
        session_factory, tmp_path
    )
    model, identity, patch, payload_type = _freshness_dependency_patch(
        dependency, product_id, brand_id
    )
    async with session_factory() as session:
        user = await session.get(User, user_id)
        row = await session.get(model, identity)
        video = await session.get(Video, video_id)
        product = await session.get(Product, product_id)
        baseline = (row.revision, video.revision, video.status)
        if binding == "video_product":
            target, field, staged = video, "product_id", None
        elif binding == "video_brand":
            target, field, staged = video, "brand_id", brand_id
        else:
            target, field, staged = product, "brand_id", None
        original_binding = getattr(target, field)
        setattr(target, field, staged)
        assert target in session.dirty
        await _assert_dependency_patch_conflict_without_dml(
            session,
            patch,
            identity,
            payload_type(context={"tone": "Dependency semantic edit"}),
            user,
            row,
        )
        assert getattr(target, field) == staged
        assert row.context == {}
        assert (row.revision, video.revision, video.status) == baseline
        await session.rollback()

    async with session_factory() as session:
        row = await session.get(model, identity)
        video = await session.get(Video, video_id)
        product = await session.get(Product, product_id)
        target = product if binding == "product_brand" else video
        assert getattr(target, field) == original_binding
        assert row.context == {}
        assert (row.revision, video.revision, video.status) == baseline
        assert (await session.get(SceneGeneration, generation_id)).status == "CREATED"


@pytest.mark.asyncio
@pytest.mark.parametrize("dependency", ["product", "brand"])
async def test_accounted_semantic_patch_then_same_value_noop_commits_once(
    session_factory, tmp_path, dependency
):
    from tests.integration.test_dependency_invalidation import dependency_generation

    user_id, video_id, scene_id, generation_id, product_id, brand_id = await dependency_generation(
        session_factory, tmp_path
    )
    model, identity, patch, payload_type = _freshness_dependency_patch(
        dependency, product_id, brand_id
    )
    context = {"tone": "Successfully invalidated context"}
    async with session_factory() as session, session.begin():
        user = await session.get(User, user_id)
        row = await session.get(model, identity)
        video = await session.get(Video, video_id)
        baseline_revision, baseline_video_revision = row.revision, video.revision
        first = await patch(
            identity, payload_type(context=context), row.revision, user, session
        )
        assert row in session.dirty and video in session.dirty
        # Do not flush between accepted calls: the snapshot must account for pending edits.
        second = await patch(
            identity, payload_type(context=context), first.revision, user, session
        )
        third = await patch(
            identity, payload_type(context=context), second.revision, user, session
        )
        assert third.revision == baseline_revision + 3
        assert row.context == context
        assert video.revision == baseline_video_revision + 1
        assert video.status == "DIRTY"

    async with session_factory() as session:
        row = await session.get(model, identity)
        video = await session.get(Video, video_id)
        assert row.context == context
        assert row.revision == baseline_revision + 3
        assert video.revision == baseline_video_revision + 1
        assert video.status == "DIRTY"
        assert (await session.get(Scene, scene_id)).selected_generation_id is None
        assert (await session.get(SceneGeneration, generation_id)).status == "CREATED"


@pytest.mark.asyncio
@pytest.mark.parametrize("dependency", ["product", "brand"])
@pytest.mark.parametrize("retry_scope", ["outer", "new_savepoint"])
async def test_savepoint_rollback_does_not_account_for_reintroduced_dependency_edit(
    session_factory, tmp_path, dependency, retry_scope
):
    from tests.integration.test_dependency_invalidation import dependency_generation

    user_id, video_id, _, generation_id, product_id, brand_id = await dependency_generation(
        session_factory, tmp_path
    )
    model, identity, patch, payload_type = _freshness_dependency_patch(
        dependency, product_id, brand_id
    )
    context = {"tone": "Rolled back accounted context"}
    async with session_factory() as session:
        user = await session.get(User, user_id)
        row = await session.get(model, identity)
        video = await session.get(Video, video_id)
        baseline = (row.revision, video.revision, video.status)
        savepoint = await session.begin_nested()
        await patch(identity, payload_type(context=context), row.revision, user, session)
        assert video.revision == baseline[1] + 1
        assert video.status == "DIRTY"
        await savepoint.rollback()
        # Reload expired ORM state, then stage the exact previously accepted semantics.
        await session.refresh(row)
        await session.refresh(video)
        assert row.context == {}
        assert (row.revision, video.revision, video.status) == baseline
        retry_savepoint = await session.begin_nested() if retry_scope == "new_savepoint" else None
        row.context = context
        await _assert_dependency_patch_conflict_without_dml(
            session, patch, identity, payload_type(context=context), user, row
        )
        assert row.context == context
        assert (row.revision, video.revision, video.status) == baseline
        if retry_savepoint is not None:
            await retry_savepoint.rollback()
        await session.rollback()

    async with session_factory() as session:
        row = await session.get(model, identity)
        video = await session.get(Video, video_id)
        assert row.context == {}
        assert (row.revision, video.revision, video.status) == baseline
        assert (await session.get(SceneGeneration, generation_id)).status == "CREATED"

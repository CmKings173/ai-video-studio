import pytest
from sqlalchemy.exc import IntegrityError

from apps.api.app.db.models import (
    Asset,
    FinalVideo,
    GenerationAttempt,
    Product,
    Project,
    Scene,
    SceneGeneration,
    User,
    Video,
    WorkflowRecord,
)


async def seed(session_factory):
    async with session_factory() as session, session.begin():
        user = User(email="admin@example.test", name="Admin", password_hash="hash", role="ADMIN")
        session.add(user)
        await session.flush()
        project = Project(name="Campaign", description="", created_by=user.id)
        product = Product(name="Bottle", description="", created_by=user.id)
        session.add_all([project, product])
        await session.flush()
        video = Video(
            project_id=project.id,
            product_id=product.id,
            title="Launch",
            brief="Launch bottle",
            created_by=user.id,
        )
        workflow = WorkflowRecord(
            code="TEST",
            mode="t2v",
            version="1",
            workflow={"1": {"inputs": {"prompt": ""}}},
            slots={"PROMPT": ["1", "prompt"]},
            required_slots=["PROMPT"],
            profile={},
            workflow_hash="a" * 64,
            slot_map_hash="b" * 64,
            enabled=True,
            created_by=user.id,
        )
        session.add_all([video, workflow])
        await session.flush()
        scene = Scene(video_id=video.id, scene_order=0, prompt="Bottle", duration_seconds=5)
        session.add(scene)
        await session.flush()
        return {
            "user": user.id,
            "project": project.id,
            "product": product.id,
            "video": video.id,
            "workflow": workflow.id,
            "scene": scene.id,
        }


@pytest.mark.asyncio
async def test_scene_order_is_unique_per_video(session_factory):
    ids = await seed(session_factory)
    async with session_factory() as session:
        session.add(
            Scene(
                video_id=ids["video"],
                scene_order=0,
                prompt="Duplicate",
                duration_seconds=5,
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.asyncio
async def test_asset_deleting_state_is_a_supported_claim_state(session_factory):
    ids = await seed(session_factory)
    async with session_factory() as session, session.begin():
        asset = Asset(
            project_id=ids["project"],
            kind="IMAGE",
            role="PRODUCT_IMAGE",
            filename="claim.png",
            content_type="image/png",
            object_key="uploads/claim.png",
            status="DELETING",
            size_bytes=1,
            created_by=ids["user"],
        )
        session.add(asset)
        await session.flush()
        assert asset.status == "DELETING"


@pytest.mark.asyncio
async def test_asset_cannot_have_project_and_product_scope(session_factory):
    ids = await seed(session_factory)
    async with session_factory() as session:
        session.add(
            Asset(
                project_id=ids["project"],
                product_id=ids["product"],
                kind="IMAGE",
                role="PRODUCT_IMAGE",
                filename="x.png",
                content_type="image/png",
                object_key="uploads/x.png",
                size_bytes=1,
                created_by=ids["user"],
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.asyncio
async def test_selected_generation_must_belong_to_same_scene(session_factory):
    ids = await seed(session_factory)
    async with session_factory() as session, session.begin():
        other = Scene(video_id=ids["video"], scene_order=1, prompt="Other", duration_seconds=5)
        session.add(other)
        await session.flush()
        generation = SceneGeneration(
            video_id=ids["video"],
            scene_id=ids["scene"],
            mode="t2v",
            workflow_id=ids["workflow"],
            generation_no=1,
            operation="ORIGINAL",
            input_snapshot={},
            created_by=ids["user"],
        )
        session.add(generation)
        await session.flush()
        other_id, generation_id = other.id, generation.id
    async with session_factory() as session:
        other = await session.get(Scene, other_id)
        other.selected_generation_id = generation_id
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.asyncio
async def test_attempt_number_and_external_correlation_are_unique(session_factory):
    ids = await seed(session_factory)
    async with session_factory() as session, session.begin():
        generation = SceneGeneration(
            video_id=ids["video"],
            scene_id=ids["scene"],
            mode="t2v",
            workflow_id=ids["workflow"],
            generation_no=1,
            operation="ORIGINAL",
            input_snapshot={},
            created_by=ids["user"],
        )
        session.add(generation)
        await session.flush()
        session.add(
            GenerationAttempt(
                generation_id=generation.id,
                attempt_no=1,
                client_id="correlation-1",
            )
        )
        generation_id = generation.id
    async with session_factory() as session:
        session.add(
            GenerationAttempt(
                generation_id=generation_id,
                attempt_no=1,
                client_id="correlation-2",
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.asyncio
async def test_only_one_workflow_can_be_enabled_for_each_mode(session_factory):
    ids = await seed(session_factory)
    async with session_factory() as session:
        session.add(
            WorkflowRecord(
                code="TEST_ALTERNATE",
                mode="t2v",
                version="2",
                workflow={"1": {"inputs": {"prompt": ""}}},
                slots={"PROMPT": ["1", "prompt"]},
                required_slots=["PROMPT"],
                profile={},
                workflow_hash="c" * 64,
                slot_map_hash="d" * 64,
                enabled=True,
                created_by=ids["user"],
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.asyncio
async def test_only_one_active_assembly_can_exist_for_a_video(session_factory):
    ids = await seed(session_factory)
    manifest = {
        "schema_version": 1,
        "video_id": ids["video"],
        "video_revision": 1,
        "version_no": 1,
        "assembly_config": {},
        "scenes": [],
    }
    async with session_factory() as session, session.begin():
        session.add(
            FinalVideo(
                video_id=ids["video"],
                version_no=1,
                status="QUEUED",
                manifest=manifest,
                manifest_hash="e" * 64,
                created_by=ids["user"],
            )
        )
    async with session_factory() as session:
        session.add(
            FinalVideo(
                video_id=ids["video"],
                version_no=2,
                status="ASSEMBLING",
                manifest={**manifest, "version_no": 2},
                manifest_hash="f" * 64,
                created_by=ids["user"],
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()

"""Agent C regressions: actual input references and authoritative Asset state.

These use only the disposable SQLite fixture. Direct SQL updates deliberately
leave the ORM identity map stale; they do not claim PostgreSQL concurrency proof.
"""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text

from apps.api.app.api import generations as generation_api
from apps.api.app.api import scenes as scene_api
from apps.api.app.core.config import Settings
from apps.api.app.core.errors import AppError
from apps.api.app.db.models import (
    Asset,
    DirectorRun,
    DirectorRunMember,
    GenerationAsset,
    GenerationAttempt,
    IdempotencyKey,
    Product,
    Project,
    Scene,
    SceneGeneration,
    User,
    Video,
    WorkflowRecord,
    utcnow,
)
from apps.api.app.schemas.api import GenerationRequest, ScenePatch
from apps.api.app.services.director_run_service import create_director_run
from apps.api.app.services.generation_service import GenerationService
from tests.integration.test_director_dynamic_aggregate import prepared_members, qualify_run
from tests.integration.test_generation_contract_edges import reference_fixture

INPUT_FIELDS = (
    ("first_frame_asset_id", "IMAGE"),
    ("last_frame_asset_id", "IMAGE"),
    ("source_video_asset_id", "VIDEO"),
    ("reference_image_asset_ids", "IMAGE"),
    ("reference_video_asset_ids", "VIDEO"),
    ("reference_audio_asset_ids", "AUDIO"),
)


def _request(field, asset_id):
    value = [asset_id] if field.endswith("_ids") else asset_id
    return GenerationRequest.model_validate({field: value})


def _service(tmp_path):
    return GenerationService(Settings(_env_file=None, workspace_root=tmp_path))


async def _counts(session):
    return tuple(
        [
            await session.scalar(select(func.count()).select_from(model))
            for model in (
                SceneGeneration,
                GenerationAsset,
                GenerationAttempt,
                DirectorRun,
                DirectorRunMember,
            )
        ]
    )


async def _world(session_factory):
    owner, scene_id, asset_ids = await reference_fixture(session_factory)
    async with session_factory() as session, session.begin():
        scene = await session.get(Scene, scene_id)
        video = await session.get(Video, scene.video_id)
        foreign = User(email=f"foreign-{uuid4()}@example.test", name="Foreign", password_hash="x")
        session.add(foreign)
        await session.flush()
        product = Product(name="Owner product", created_by=owner)
        other_product = Product(name="Other owner product", created_by=owner)
        other_project = Project(name="Other owner project", created_by=owner)
        session.add_all((product, other_product, other_project))
        await session.flush()
        video.product_id = product.id
        asset = await session.get(Asset, asset_ids[0])
        asset.resource_owner_id = owner
        asset.ownership_provenance = "OWNER"
        await session.flush()
        return {
            "owner": owner,
            "foreign": foreign.id,
            "video": video.id,
            "scene": scene.id,
            "asset": asset.id,
            "other_project": other_project.id,
            "other_product": other_product.id,
        }


@pytest.mark.parametrize("field,kind", INPUT_FIELDS)
@pytest.mark.parametrize("mismatch", ["project", "product"])
async def test_generation_rejects_asset_with_one_wrong_scope_binding(
    session_factory, tmp_path, field, kind, mismatch
):
    ids = await _world(session_factory)
    async with session_factory() as session, session.begin():
        # Explicitly model a corrupt historical row; current schema prohibits
        # double-bound Assets. Never disable constraints on an application DB.
        await session.execute(text("PRAGMA ignore_check_constraints=ON"))
        asset = await session.get(Asset, ids["asset"])
        video = await session.get(Video, ids["video"])
        asset.product_id = video.product_id
        setattr(asset, f"{mismatch}_id", ids[f"other_{mismatch}"])
        asset.kind = kind
        asset.duration_seconds = 5
        asset.media_metadata = {
            "codec": "test-codec",
            "fps": 24,
            "video_duration_seconds": 5,
            "audio_duration_seconds": 5,
        }
        await session.flush()
        before = await _counts(session)
        with pytest.raises(AppError) as error:
            await _service(tmp_path)._load_assets(
                session, _request(field, asset.id), video, user_id=ids["owner"]
            )
        assert error.value.status_code == 422
        assert error.value.code == "ASSET_SCOPE_INVALID"
        assert await _counts(session) == before
        assert video.status == "DRAFT"


@pytest.mark.parametrize("admin", [False, True])
async def test_generation_rejects_foreign_asset_business_owner_even_for_admin(
    session_factory, tmp_path, admin
):
    ids = await _world(session_factory)
    async with session_factory() as session, session.begin():
        asset = await session.get(Asset, ids["asset"])
        asset.created_by = asset.resource_owner_id = ids["foreign"]
        await session.flush()
        before = await _counts(session)
        video = await session.get(Video, ids["video"])
        with pytest.raises(AppError) as error:
            await _service(tmp_path)._load_assets(
                session,
                _request("reference_image_asset_ids", asset.id),
                video,
                user_id=ids["owner"],
                is_admin=admin,
            )
        assert error.value.status_code == 404
        assert error.value.code == "ASSET_NOT_FOUND"
        assert await _counts(session) == before


@pytest.mark.parametrize(
    "mutation,code,status",
    [
        ({"status": "PENDING"}, "ASSET_NOT_READY", 409),
        ({"status": "DELETED"}, "ASSET_NOT_READY", 409),
        ({"deleted_at": "NOW"}, "ASSET_NOT_FOUND", 404),
        ({"checksum": "invalid"}, "ASSET_NOT_READY", 409),
    ],
)
async def test_real_generation_create_rejects_stale_asset_before_prompt_or_writes(
    session_factory, tmp_path, mutation, code, status
):
    ids = await _world(session_factory)
    service = _service(tmp_path)
    service.prompt_engine.compose = AsyncMock(wraps=service.prompt_engine.compose)
    async with session_factory() as session, session.begin():
        cached = await session.get(Asset, ids["asset"])
        changes = {key: utcnow() if value == "NOW" else value for key, value in mutation.items()}
        await session.execute(
            Asset.__table__.update().where(Asset.id == cached.id).values(**changes)
        )
        # The SELECT's database row changed, while its ORM instance stayed READY.
        assert cached.status == "READY"
        assert cached.deleted_at is None
        assert cached.checksum != "invalid"
        for field, expected in changes.items():
            persisted = await session.scalar(
                select(getattr(Asset, field)).where(Asset.id == cached.id)
            )
            assert persisted == expected
        before = await _counts(session)
        with pytest.raises(AppError) as error:
            await service.create(
                session,
                scene_id=ids["scene"],
                request=_request("reference_image_asset_ids", cached.id),
                user_id=ids["owner"],
                request_id="stale-asset-reproduction",
            )
        assert error.value.code == code
        assert error.value.status_code == status
        service.prompt_engine.compose.assert_not_awaited()
        assert await _counts(session) == before
        assert (await session.get(Video, ids["video"])).status == "DRAFT"


async def test_locked_asset_manifest_uses_current_bytes_and_preserves_pending_edits(
    session_factory, tmp_path
):
    ids = await _world(session_factory)
    async with session_factory() as session, session.begin():
        cached = await session.get(Asset, ids["asset"])
        cached.filename = "legitimate-pending-name.png"
        await session.execute(
            Asset.__table__.update()
            .where(Asset.id == cached.id)
            .values(checksum="b" * 64, object_key="current/object.png")
        )
        video = await session.get(Video, ids["video"])
        assets, _, _ = await _service(tmp_path)._load_assets(
            session,
            _request("reference_image_asset_ids", cached.id),
            video,
            user_id=ids["owner"],
        )
        manifest = GenerationService._asset_manifest(assets)
        assert manifest[0]["checksum"] == "b" * 64
        assert manifest[0]["object_key"] == "current/object.png"
        assert cached.filename == "legitimate-pending-name.png"
        assert cached in session.dirty
        stored_name = await session.scalar(select(Asset.filename).where(Asset.id == cached.id))
        assert stored_name == "image.png"


async def test_stale_asset_owner_reassignment_rejected_for_admin_generation(
    session_factory, tmp_path
):
    ids = await _world(session_factory)
    service = _service(tmp_path)
    service.prompt_engine.compose = AsyncMock(wraps=service.prompt_engine.compose)
    async with session_factory() as session, session.begin():
        cached = await session.get(Asset, ids["asset"])
        await session.execute(
            Asset.__table__.update()
            .where(Asset.id == cached.id)
            .values(resource_owner_id=ids["foreign"], created_by=ids["foreign"])
        )
        assert cached.resource_owner_id == ids["owner"]
        assert await session.scalar(
            select(Asset.resource_owner_id).where(Asset.id == cached.id)
        ) == ids["foreign"]
        before = await _counts(session)
        with pytest.raises(AppError) as error:
            await service.create(
                session,
                scene_id=ids["scene"],
                request=_request("reference_image_asset_ids", cached.id),
                user_id=ids["foreign"],
                is_admin=True,
                request_id="stale-asset-owner",
            )
        assert error.value.code == "ASSET_NOT_FOUND"
        assert error.value.status_code == 404
        service.prompt_engine.compose.assert_not_awaited()
        assert await _counts(session) == before


async def test_scene_patch_reauthorizes_asset_scope_after_parent_lock(
    session_factory, tmp_path, monkeypatch
):
    ids = await _world(session_factory)
    original = scene_api._locked_scene_and_video

    async def move_asset_after_preflight(session, scene_id, user):
        await session.execute(
            Asset.__table__.update()
            .where(Asset.id == ids["asset"])
            .values(project_id=ids["other_project"])
        )
        return await original(session, scene_id, user)

    monkeypatch.setattr(scene_api, "_locked_scene_and_video", move_asset_after_preflight)
    async with session_factory() as session, session.begin():
        cached = await session.get(Asset, ids["asset"])
        scene = await session.get(Scene, ids["scene"])
        video = await session.get(Video, ids["video"])
        before = (scene.revision, video.revision, deepcopy(scene.generation_config))
        with pytest.raises(AppError) as error:
            await scene_api.patch_scene(
                scene_id=scene.id,
                payload=ScenePatch(generation_config={"reference_image_asset_ids": [cached.id]}),
                revision=scene.revision,
                user=SimpleNamespace(id=ids["owner"], role="EDITOR"),
                session=session,
            )
        assert error.value.status_code == 404
        assert error.value.code == "ASSET_NOT_FOUND"
        assert (scene.revision, video.revision, scene.generation_config) == before


async def test_generation_scope_authorization_precedes_idempotency_claim(
    session_factory, tmp_path, monkeypatch
):
    ids = await _world(session_factory)
    claim = AsyncMock(wraps=generation_api.claim)
    monkeypatch.setattr(generation_api, "claim", claim)
    async with session_factory() as session, session.begin():
        asset = await session.get(Asset, ids["asset"])
        asset.project_id = ids["other_project"]
        await session.flush()
        before = await _counts(session)
        with pytest.raises(AppError) as error:
            await generation_api._create(
                scene_id=ids["scene"],
                payload=_request("reference_image_asset_ids", asset.id),
                request=SimpleNamespace(state=SimpleNamespace(request_id="wrong-asset-scope")),
                key="wrong-asset-scope",
                user=SimpleNamespace(id=ids["owner"], role="EDITOR"),
                session=session,
                settings=_service(tmp_path).settings,
                operation_name="create-generation",
            )
        assert error.value.status_code in {404, 422}
        assert error.value.code in {"ASSET_NOT_FOUND", "ASSET_SCOPE_INVALID"}
        claim.assert_not_awaited()
        assert await _counts(session) == before
        assert await session.scalar(select(func.count()).select_from(IdempotencyKey)) == 0


async def test_director_run_rejects_workflow_disabled_after_identity_map_preload(
    session_factory, tmp_path, monkeypatch
):
    owner, video_id, scene_ids, members = await prepared_members(
        session_factory, tmp_path, 2, persist=False
    )
    async with session_factory() as session, session.begin():
        scenes = [await session.get(Scene, identity) for identity in scene_ids]
        video = await session.get(Video, video_id)
        await qualify_run(session, members, scenes, video, owner, monkeypatch)
        cached = await session.get(WorkflowRecord, members[0].workflow_id)
        await session.execute(
            WorkflowRecord.__table__.update().where(WorkflowRecord.id == cached.id).values(
                enabled=False
            )
        )
        assert cached.enabled
        assert await session.scalar(
            select(WorkflowRecord.enabled).where(WorkflowRecord.id == cached.id)
        ) is False
        before = await _counts(session)
        snapshots = [deepcopy(member.input_snapshot) for member in members]
        with pytest.raises(AppError) as error:
            await create_director_run(
                session, members, scenes, video=video, user_id=owner
            )
        assert error.value.status_code == 409
        assert error.value.code == "WORKFLOW_NOT_QUALIFIED"
        assert await _counts(session) == before
        assert [member.input_snapshot for member in members] == snapshots


async def test_real_generation_snapshot_preserves_nested_business_context(
    session_factory, tmp_path
):
    ids = await _world(session_factory)
    context = {
        "assets": ["logo", "hero-image"],
        "asset_id": "marketing-reference",
        "reference_asset_ids": ["creative-a", "creative-b"],
        "parent_generation_id": "editorial-guideline",
        "inherited_from": "brand-template",
        "nested": {"asset_id": "nested-business-text"},
    }
    async with session_factory() as session, session.begin():
        video = await session.get(Video, ids["video"])
        product = await session.get(Product, video.product_id)
        product.context = deepcopy(context)
        await session.flush()
        generation = await _service(tmp_path).create(
            session,
            scene_id=ids["scene"],
            request=_request("reference_image_asset_ids", ids["asset"]),
            user_id=ids["owner"],
            request_id="real-context-snapshot",
        )
        await session.flush()
        snapshot = generation.input_snapshot
        dependencies = snapshot["generation_freshness"]["inputs"]["dependencies"]
        assert dependencies["product"]["context"] == context
        assert [item["id"] for item in snapshot["assets"]] == [ids["asset"]]
        assert [item["id"] for item in snapshot["generation_intent"]["assets"]] == [ids["asset"]]
        assert [item["id"] for item in snapshot["director_execution"]["assets"]] == [ids["asset"]]
        assert await _counts(session) == (1, 1, 0, 0, 0)

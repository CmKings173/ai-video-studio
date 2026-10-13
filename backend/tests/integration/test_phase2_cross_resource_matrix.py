"""Independent Phase 2 API attacks; PostgreSQL races and worker journeys live elsewhere."""

from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid4, uuid5

import httpx
import pytest
from sqlalchemy import select

from apps.api.app.api import assembly as assembly_api
from apps.api.app.api import generations as generations_api
from apps.api.app.api import videos as videos_api
from apps.api.app.api.assets import store
from apps.api.app.api.deps import require_csrf, require_editor
from apps.api.app.core.config import Settings, get_settings
from apps.api.app.db.models import (
    Asset,
    Brand,
    DirectorRun,
    DirectorRunMember,
    FinalVideo,
    FinalVideoScene,
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
)
from apps.api.app.db.session import get_session
from apps.api.app.main import app


class RecordingBoundaries:
    def __init__(self):
        self.plans = []
        self.presigns = []
        self.claims = []

    async def presign_download(self, key, filename):
        self.presigns.append((key, filename))
        return "https://storage.test/download"


@asynccontextmanager
async def matrix_client(session_factory, tmp_path, principal, boundaries):
    async def session_override():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except BaseException:
                await session.rollback()
                raise

    settings = Settings(
        _env_file=None,
        workspace_root=tmp_path,
        jwt_secret="phase2-api-matrix-test-only-secret-at-least-32-bytes",
        min_free_disk_bytes=0,
    )
    previous = app.dependency_overrides.copy()
    app.dependency_overrides.update(
        {
            get_session: session_override,
            get_settings: lambda: settings,
            require_editor: lambda: principal["value"],
            require_csrf: lambda: principal["value"],
            store: lambda: boundaries,
        }
    )
    try:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


@pytest.fixture
def boundaries(monkeypatch):
    recording = RecordingBoundaries()

    async def plan(_service, context, total_seconds, *, video_revision):
        recording.plans.append((deepcopy(context), total_seconds, video_revision))
        return {"scenes": storyboard()["scenes"], "video_revision": video_revision}

    monkeypatch.setattr(videos_api.StoryboardService, "plan", plan)
    for module in (videos_api, generations_api, assembly_api):
        original = module.claim

        def wrap_claim(claim_function, operation_module):
            async def recording_claim(*args, **kwargs):
                recording.claims.append((operation_module, kwargs["operation"]))
                return await claim_function(*args, **kwargs)

            return recording_claim

        monkeypatch.setattr(module, "claim", wrap_claim(original, module.__name__))
    return recording


async def seed_matrix(session_factory):
    async with session_factory() as session, session.begin():
        users = {
            key: User(
                email=f"{key}-{uuid4().hex}@phase2.test",
                name=key,
                password_hash="unused",
                role="ADMIN" if key.startswith("admin") else "EDITOR",
            )
            for key in ("A", "B", "admin_x", "admin_y")
        }
        session.add_all(users.values())
        await session.flush()
        projects = {
            key: Project(name=f"private-project-{key}", created_by=users[key].id)
            for key in ("A", "B")
        }
        brands = {
            key: Brand(name=f"private-brand-{key}", created_by=users[key].id)
            for key in ("A", "B")
        }
        session.add_all([*projects.values(), *brands.values()])
        await session.flush()
        products = {
            key: Product(
                name=f"private-product-{key}",
                brand_id=brands[key].id,
                created_by=users[key].id,
            )
            for key in ("A", "B")
        }
        session.add_all(products.values())
        await session.flush()
        videos = {
            key: Video(
                project_id=projects[key].id,
                product_id=products[key].id,
                brand_id=brands[key].id,
                title=f"private-video-{key}",
                brief=f"private-brief-{key}",
                kind="LONG_VIDEO",
                target_duration=30,
                aspect_ratio="16:9",
                created_by=users[key].id,
            )
            for key in ("A", "B")
        }
        session.add_all(videos.values())
        await session.flush()
        assets = {
            key: Asset(
                project_id=projects[key].id,
                filename=f"private-reference-{key}.png",
                object_key=f"private-object-{key}/{uuid4().hex}",
                content_type="image/png",
                size_bytes=12,
                kind="IMAGE",
                role="PROJECT_REFERENCE",
                status="READY",
                resource_owner_id=users[key].id,
                ownership_provenance="OWNER",
                created_by=users[key].id,
            )
            for key in ("A", "B")
        }
        session.add_all(assets.values())
        await session.flush()
        return {
            "users": {key: row.id for key, row in users.items()},
            "projects": {key: row.id for key, row in projects.items()},
            "products": {key: row.id for key, row in products.items()},
            "brands": {key: row.id for key, row in brands.items()},
            "videos": {key: row.id for key, row in videos.items()},
            "assets": {key: row.id for key, row in assets.items()},
        }


def actor(ids, key):
    return {"value": SimpleNamespace(
        id=ids["users"][key], role="ADMIN" if key.startswith("admin") else "EDITOR"
    )}


def storyboard():
    return {"scenes": [
        {"scene_order": index, "prompt": f"matrix scene {index}", "duration_seconds": 10}
        for index in range(3)
    ]}


def headers(revision=1, key="matrix-publish"):
    return {"If-Match": str(revision), "Idempotency-Key": key}


async def database_state(session_factory):
    """Compare full rows, so replacement, queues and provenance edits cannot hide in counts."""
    state = {}
    async with session_factory() as session:
        for model in (
            Project, Product, Brand, Video, Scene, IdempotencyKey, SceneGeneration,
            GenerationAttempt, DirectorRun, DirectorRunMember, GenerationAsset,
            FinalVideo, FinalVideoScene, Asset,
        ):
            rows = await session.execute(select(model.__table__).order_by(model.id))
            state[model.__name__] = deepcopy([dict(row) for row in rows.mappings()])
    return state


def assert_error(response, status, codes):
    assert response.status_code == status, response.text
    if isinstance(codes, str):
        codes = {codes}
    assert response.json()["error"]["code"] in codes, response.text
    for secret in ("private-project-", "private-product-", "private-brand-",
                   "private-video-", "private-brief-", "private-object-"):
        assert secret not in response.text


async def corrupt_graph(session_factory, ids, corruption):
    async with session_factory() as session, session.begin():
        video = await session.get(Video, ids["videos"]["A"])
        if corruption == "project":
            video.project_id = ids["projects"]["B"]
        elif corruption == "product":
            video.product_id = ids["products"]["B"]
            video.brand_id = None
        elif corruption == "brand":
            video.product_id = None
            video.brand_id = ids["brands"]["B"]
        elif corruption in {"inherited_brand", "product_brand_behind_override"}:
            if corruption == "inherited_brand":
                video.brand_id = None
            product = await session.get(Product, ids["products"]["A"])
            product.brand_id = ids["brands"]["B"]
        else:
            raise AssertionError(corruption)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("caller", "foreign", "code"),
    [("A", "project", "PROJECT_NOT_FOUND"),
     ("admin_x", "product", "PRODUCT_NOT_FOUND"),
     ("admin_x", "brand", "BRAND_NOT_FOUND"),
     ("admin_x", "inherited_brand", "BRAND_NOT_FOUND")],
)
async def test_t06_t08_t09_create_rejects_foreign_graph_without_rows(
    session_factory, tmp_path, boundaries, caller, foreign, code
):
    ids = await seed_matrix(session_factory)
    owner = "A" if caller == "A" else "B"
    payload = {
        "project_id": ids["projects"][owner],
        "product_id": ids["products"][owner],
        "brand_id": ids["brands"][owner],
        "title": "matrix creation", "brief": "matrix brief",
    }
    if foreign == "inherited_brand":
        payload.pop("brand_id")
        async with session_factory() as session, session.begin():
            product = await session.get(Product, ids["products"]["B"])
            product.brand_id = ids["brands"]["A"]
    else:
        payload[f"{foreign}_id"] = ids[f"{foreign}s"]["B" if caller == "A" else "A"]
        if foreign == "product":
            # Avoid a pairing conflict masking the foreign-owner failure.
            payload.pop("brand_id")
    before = await database_state(session_factory)
    async with matrix_client(session_factory, tmp_path, actor(ids, caller), boundaries) as client:
        response = await client.post("/api/v1/videos", json=payload)
    after = await database_state(session_factory)
    assert after == before
    assert boundaries.plans == boundaries.presigns == boundaries.claims == []
    assert_error(response, 404, code)


@pytest.mark.asyncio
@pytest.mark.parametrize("caller", ["A", "admin_x"])
@pytest.mark.parametrize("corruption", [
    "project", "product", "brand", "inherited_brand", "product_brand_behind_override",
])
@pytest.mark.parametrize("operation", ["preview", "publish"])
async def test_t10_t13_storyboard_corrupt_parents_fail_before_side_effects(
    session_factory, tmp_path, boundaries, caller, corruption, operation
):
    ids = await seed_matrix(session_factory)
    await corrupt_graph(session_factory, ids, corruption)
    payload = storyboard()
    if operation == "publish":
        async with session_factory() as session, session.begin():
            existing = Scene(
                video_id=ids["videos"]["A"], scene_order=0,
                prompt="untouched original", duration_seconds=10,
            )
            session.add(existing)
            await session.flush()
            payload.update(replace=True, replace_scene_revisions={existing.id: existing.revision})
    before = await database_state(session_factory)
    async with matrix_client(session_factory, tmp_path, actor(ids, caller), boundaries) as client:
        response = await client.post(
            f"/api/v1/videos/{ids['videos']['A']}/storyboard/{operation}",
            headers=headers(), json=payload if operation == "publish" else None,
        )
    after = await database_state(session_factory)
    assert boundaries.plans == boundaries.presigns == boundaries.claims == []
    assert after == before
    parent_code = {"project": "PROJECT_NOT_FOUND", "product": "PRODUCT_NOT_FOUND",
                   "brand": "BRAND_NOT_FOUND", "inherited_brand": "BRAND_NOT_FOUND",
                   "product_brand_behind_override": "BRAND_NOT_FOUND"}[corruption]
    assert_error(response, 404, {"VIDEO_NOT_FOUND", parent_code})


@pytest.mark.asyncio
@pytest.mark.parametrize("field", [
    "first_frame_asset_id", "last_frame_asset_id", "source_video_asset_id",
    "reference_image_asset_ids", "reference_video_asset_ids", "reference_audio_asset_ids",
])
@pytest.mark.parametrize("caller", ["A", "admin_x"])
async def test_t14_foreign_scene_asset_rejected_before_claim_or_replacement(
    session_factory, tmp_path, boundaries, field, caller
):
    ids = await seed_matrix(session_factory)
    payload = storyboard()
    async with session_factory() as session, session.begin():
        existing = Scene(
            video_id=ids["videos"]["A"], scene_order=0,
            prompt="untouched asset replacement", duration_seconds=10,
        )
        session.add(existing)
        await session.flush()
        payload.update(replace=True, replace_scene_revisions={existing.id: existing.revision})
    value = ids["assets"]["B"]
    payload["scenes"][1]["generation_config"] = {
        field: [value] if field.endswith("_ids") else value
    }
    before = await database_state(session_factory)
    async with matrix_client(session_factory, tmp_path, actor(ids, caller), boundaries) as client:
        response = await client.post(
            f"/api/v1/videos/{ids['videos']['A']}/storyboard/publish",
            headers=headers(), json=payload,
        )
    assert await database_state(session_factory) == before
    assert boundaries.claims == boundaries.plans == boundaries.presigns == []
    assert_error(response, 404, "ASSET_NOT_FOUND")


@pytest.mark.asyncio
async def test_admin_can_publish_storyboard_with_same_owner_reference_asset(
    session_factory, tmp_path, boundaries
):
    ids = await seed_matrix(session_factory)
    payload = storyboard()
    asset_id = ids["assets"]["B"]
    payload["scenes"][0]["generation_config"] = {
        "reference_image_asset_ids": [asset_id]
    }
    async with matrix_client(
        session_factory, tmp_path, actor(ids, "admin_x"), boundaries
    ) as client:
        response = await client.post(
            f"/api/v1/videos/{ids['videos']['B']}/storyboard/publish",
            headers=headers(),
            json=payload,
        )
    assert response.status_code == 200, response.text
    async with session_factory() as session:
        scene = await session.scalar(
            select(Scene).where(Scene.video_id == ids["videos"]["B"], Scene.scene_order == 0)
        )
        assert scene.generation_config["reference_image_asset_ids"] == [asset_id]
    assert len(boundaries.claims) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", [
    "project", "product", "brand", "inherited_brand", "product_brand_behind_override",
])
async def test_t15_replay_rechecks_parent_graph_and_preserves_existing_claim(
    session_factory, tmp_path, boundaries, corruption
):
    ids = await seed_matrix(session_factory)
    async with matrix_client(session_factory, tmp_path, actor(ids, "A"), boundaries) as client:
        url = f"/api/v1/videos/{ids['videos']['A']}/storyboard/publish"
        first = await client.post(url, headers=headers(), json=storyboard())
        assert first.status_code == 200, first.text
        await corrupt_graph(session_factory, ids, corruption)
        before = await database_state(session_factory)
        assert len(before["IdempotencyKey"]) == 1
        boundaries.claims.clear()
        replay = await client.post(url, headers=headers(), json=storyboard())
    assert await database_state(session_factory) == before
    assert boundaries.claims == boundaries.plans == boundaries.presigns == []
    parent_code = {"project": "PROJECT_NOT_FOUND", "product": "PRODUCT_NOT_FOUND",
                   "brand": "BRAND_NOT_FOUND", "inherited_brand": "BRAND_NOT_FOUND",
                   "product_brand_behind_override": "BRAND_NOT_FOUND"}[corruption]
    assert_error(replay, 404, {"VIDEO_NOT_FOUND", parent_code})


@pytest.mark.asyncio
async def test_t16_valid_replay_returns_same_scenes_without_revision_or_row_changes(
    session_factory, tmp_path, boundaries
):
    ids = await seed_matrix(session_factory)
    async with matrix_client(session_factory, tmp_path, actor(ids, "A"), boundaries) as client:
        url = f"/api/v1/videos/{ids['videos']['A']}/storyboard/publish"
        first = await client.post(url, headers=headers(), json=storyboard())
        assert first.status_code == 200, first.text
        before = await database_state(session_factory)
        replay = await client.post(url, headers=headers(), json=storyboard())
    assert replay.status_code == 200, replay.text
    assert replay.json() == first.json()
    assert first.json()["revision"] == 2
    assert len(first.json()["scenes"]) == 3
    assert len(before["IdempotencyKey"]) == 1
    assert await database_state(session_factory) == before
    assert boundaries.plans == boundaries.presigns == []


async def seed_outputs(session_factory, ids, owner="B"):
    async with session_factory() as session, session.begin():
        scene = Scene(video_id=ids["videos"][owner], scene_order=0,
                      prompt="output scene", duration_seconds=10)
        workflow = WorkflowRecord(
            code=f"MATRIX-{uuid4().hex}", mode="t2v", version="1", workflow={},
            slots={}, profile={}, workflow_hash="0" * 64, slot_map_hash="0" * 64,
            enabled=False, created_by=ids["users"]["admin_x"],
        )
        session.add_all([scene, workflow])
        generation_id, final_id = str(uuid4()), str(uuid4())
        assets = {}
        for role in ("GENERATED_VIDEO", "FINAL_VIDEO"):
            parent_id = generation_id if role == "GENERATED_VIDEO" else final_id
            assets[role] = Asset(
                id=str(uuid5(NAMESPACE_URL, f"ai-video-studio:{role}:{parent_id}")),
                project_id=ids["projects"][owner], filename=f"{role}.mp4",
                object_key=f"private-object-{owner}/{role}", content_type="video/mp4",
                kind="VIDEO", role=role, status="READY", size_bytes=12,
                resource_owner_id=ids["users"][owner], ownership_provenance="VIDEO_PARENT",
                created_by=ids["users"]["admin_x"],
            )
        session.add_all(assets.values())
        await session.flush()
        generation = SceneGeneration(
            id=generation_id,
            video_id=ids["videos"][owner], scene_id=scene.id,
            workflow_id=workflow.id, generation_no=1, mode="t2v",
            status="COMPLETED", phase="COMPLETED", input_snapshot={},
            output_asset_id=assets["GENERATED_VIDEO"].id,
            created_by=ids["users"]["admin_x"],
        )
        final = FinalVideo(
            id=final_id,
            video_id=ids["videos"][owner], version_no=1, status="READY", phase="READY",
            manifest={}, manifest_hash="0" * 64,
            output_asset_id=assets["FINAL_VIDEO"].id,
            created_by=ids["users"]["admin_x"],
        )
        session.add_all([generation, final])
        await session.flush()
        return {"scene": scene.id, "generation": generation.id, "final": final.id,
                "generated_asset": assets["GENERATED_VIDEO"].id,
                "final_asset": assets["FINAL_VIDEO"].id}


@pytest.mark.asyncio
async def test_t20_foreign_outputs_are_404_without_presigns_with_owner_control(
    session_factory, tmp_path, boundaries
):
    ids = await seed_matrix(session_factory)
    outputs = await seed_outputs(session_factory, ids)
    principal = actor(ids, "B")
    async with matrix_client(session_factory, tmp_path, principal, boundaries) as client:
        for path in (f"assets/{outputs['generated_asset']}/download",
                     f"final-versions/{outputs['final']}/download"):
            control = await client.get(f"/api/v1/{path}")
            assert control.status_code == 200, control.text
        assert len(boundaries.presigns) == 2
        boundaries.presigns.clear()
        principal["value"] = actor(ids, "A")["value"]
        before = await database_state(session_factory)
        for path, code in (
            (f"assets/{outputs['generated_asset']}", "ASSET_NOT_FOUND"),
            (f"assets/{outputs['generated_asset']}/download", "ASSET_NOT_FOUND"),
            (f"assets/{outputs['final_asset']}/download", "ASSET_NOT_FOUND"),
            (f"generations/{outputs['generation']}", "GENERATION_NOT_FOUND"),
            (f"final-versions/{outputs['final']}", "FINAL_VIDEO_NOT_FOUND"),
            (f"final-versions/{outputs['final']}/download", "FINAL_VIDEO_NOT_FOUND"),
        ):
            response = await client.get(f"/api/v1/{path}")
            assert_error(response, 404, code)
    assert await database_state(session_factory) == before
    assert boundaries.presigns == boundaries.claims == boundaries.plans == []


@pytest.mark.asyncio
@pytest.mark.parametrize("caller", ["A", "admin_x"])
@pytest.mark.parametrize("operation", ["generation", "generate_all", "assembly", "download"])
async def test_t21_historical_graph_cannot_queue_or_download_and_is_not_rewritten(
    session_factory, tmp_path, boundaries, caller, operation
):
    ids = await seed_matrix(session_factory)
    outputs = await seed_outputs(session_factory, ids, owner="A")
    await corrupt_graph(session_factory, ids, "project")
    before = await database_state(session_factory)
    async with matrix_client(session_factory, tmp_path, actor(ids, caller), boundaries) as client:
        video_id = ids["videos"]["A"]
        if operation == "download":
            response = await client.get(f"/api/v1/final-versions/{outputs['final']}/download")
            codes = {"VIDEO_NOT_FOUND", "FINAL_VIDEO_NOT_FOUND", "ASSET_NOT_FOUND",
                     "PROJECT_NOT_FOUND"}
        else:
            path = {"generation": f"scenes/{outputs['scene']}/generations",
                    "generate_all": f"videos/{video_id}/generate-all",
                    "assembly": f"videos/{video_id}/assemble"}[operation]
            response = await client.post(f"/api/v1/{path}", headers=headers(), json={})
            codes = {"VIDEO_NOT_FOUND", "SCENE_NOT_FOUND", "PROJECT_NOT_FOUND"}
    after = await database_state(session_factory)
    assert after == before
    assert boundaries.claims == boundaries.plans == boundaries.presigns == []
    assert_error(response, 404, codes)


@pytest.mark.asyncio
@pytest.mark.parametrize("dependency", ["project", "product", "brand"])
@pytest.mark.parametrize("operation", ["preview", "publish"])
async def test_archived_parent_reads_remain_visible_but_storyboard_returns_409(
    session_factory, tmp_path, boundaries, dependency, operation
):
    ids = await seed_matrix(session_factory)
    async with session_factory() as session, session.begin():
        model = {"project": Project, "product": Product, "brand": Brand}[dependency]
        row = await session.get(model, ids[f"{dependency}s"]["A"])
        row.archived = True
    before = await database_state(session_factory)
    async with matrix_client(session_factory, tmp_path, actor(ids, "A"), boundaries) as client:
        detail = await client.get(f"/api/v1/videos/{ids['videos']['A']}")
        assert detail.status_code == 200, detail.text
        response = await client.post(
            f"/api/v1/videos/{ids['videos']['A']}/storyboard/{operation}",
            headers=headers(), json=storyboard() if operation == "publish" else None,
        )
    assert await database_state(session_factory) == before
    assert boundaries.claims == boundaries.plans == boundaries.presigns == []
    assert_error(response, 409, f"{dependency.upper()}_NOT_ACTIVE")


@pytest.mark.asyncio
async def test_admin_same_owner_product_brand_pairing_is_422_without_rows(
    session_factory, tmp_path, boundaries
):
    ids = await seed_matrix(session_factory)
    async with session_factory() as session, session.begin():
        brand = Brand(name="other same-owner brand", created_by=ids["users"]["B"])
        session.add(brand)
        await session.flush()
        brand_id = brand.id
    before = await database_state(session_factory)
    principal = actor(ids, "admin_x")
    async with matrix_client(session_factory, tmp_path, principal, boundaries) as client:
        response = await client.post("/api/v1/videos", json={
            "project_id": ids["projects"]["B"], "product_id": ids["products"]["B"],
            "brand_id": brand_id, "title": "pairing", "brief": "pairing brief",
        })
    assert await database_state(session_factory) == before
    assert boundaries.claims == boundaries.plans == boundaries.presigns == []
    assert_error(response, 422, "PRODUCT_BRAND_CONFLICT")


@pytest.mark.asyncio
@pytest.mark.parametrize("caller", ["A", "admin_x"])
async def test_existing_same_owner_explicit_brand_override_remains_usable(
    session_factory, tmp_path, boundaries, caller
):
    ids = await seed_matrix(session_factory)
    async with session_factory() as session, session.begin():
        brand = Brand(name="same-owner override", created_by=ids["users"]["A"])
        session.add(brand)
        await session.flush()
        video = await session.get(Video, ids["videos"]["A"])
        video.brand_id = brand.id
    async with matrix_client(session_factory, tmp_path, actor(ids, caller), boundaries) as client:
        preview = await client.post(f"/api/v1/videos/{ids['videos']['A']}/storyboard/preview")
        assert preview.status_code == 200, preview.text
        assert boundaries.plans[0][0]["brand"]["name"] == "same-owner override"
        publish = await client.post(
            f"/api/v1/videos/{ids['videos']['A']}/storyboard/publish",
            headers=headers(), json=storyboard(),
        )
        assert publish.status_code == 200, publish.text
        assert len(publish.json()["scenes"]) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("dependency", ["project", "product", "brand"])
@pytest.mark.parametrize("operation", ["create", "patch", "disable", "reorder"])
async def test_redteam_archived_parent_blocks_scene_mutation_without_revision_changes(
    session_factory, tmp_path, boundaries, dependency, operation
):
    ids = await seed_matrix(session_factory)
    async with session_factory() as session, session.begin():
        scene = Scene(
            video_id=ids["videos"]["A"], scene_order=0,
            prompt="scene before archive", duration_seconds=10,
        )
        session.add(scene)
        model = {"project": Project, "product": Product, "brand": Brand}[dependency]
        parent = await session.get(model, ids[f"{dependency}s"]["A"])
        parent.archived = True
        await session.flush()
        scene_id = scene.id
    before = await database_state(session_factory)
    async with matrix_client(session_factory, tmp_path, actor(ids, "A"), boundaries) as client:
        if operation == "create":
            response = await client.post(
                f"/api/v1/videos/{ids['videos']['A']}/scenes", headers=headers(),
                json={"prompt": "scene after archive", "duration_seconds": 10},
            )
        elif operation == "patch":
            response = await client.patch(
                f"/api/v1/scenes/{scene_id}", headers=headers(),
                json={"prompt": "changed after archive"},
            )
        elif operation == "disable":
            response = await client.post(
                f"/api/v1/scenes/{scene_id}/disable", headers=headers(),
            )
        else:
            response = await client.post(
                f"/api/v1/videos/{ids['videos']['A']}/scenes/reorder", headers=headers(),
                json={"scene_ids": [scene_id]},
            )
    after = await database_state(session_factory)
    assert response.status_code == 409, (
        f"{dependency}/{operation}: {response.status_code}; "
        f"changed tables={[name for name in before if before[name] != after[name]]}; "
        f"response={response.text}"
    )
    assert_error(response, 409, f"{dependency.upper()}_NOT_ACTIVE")
    assert after == before
    assert boundaries.plans == boundaries.presigns == boundaries.claims == []


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", [
    "project", "product", "brand", "inherited_brand", "product_brand_behind_override",
])
@pytest.mark.parametrize("output_kind", ["generation", "final", "synthetic_artifact"])
async def test_redteam_worker_corrupt_parent_rejected_before_asset_claim_and_put(
    session_factory, corruption, output_kind
):
    from workers.common import save_output

    ids = await seed_matrix(session_factory)
    outputs = await seed_outputs(session_factory, ids, owner="A")
    await corrupt_graph(session_factory, ids, corruption)

    class OutputStore:
        def __init__(self):
            self.puts = []
            self.reads = []

        async def put_bytes(self, key, data, content_type):
            self.puts.append((key, data, content_type))

        async def get_bytes(self, key):
            self.reads.append(key)
            return b"matrix-output"

    output_store = OutputStore()
    before = await database_state(session_factory)
    owner_id = str(uuid4()) if output_kind == "synthetic_artifact" else outputs[output_kind]
    project_id = ids["projects"]["B" if corruption == "project" else "A"]
    with pytest.raises(ValueError, match="^OUTPUT_PARENT_CONFLICT$"):
        await save_output(
            session_factory, output_store, owner_id=owner_id,
            role="FINAL_VIDEO" if output_kind == "final" else "GENERATED_VIDEO",
            project_id=project_id, video_id=ids["videos"]["A"],
            created_by=ids["users"]["admin_x"], data=b"matrix-output",
            metadata={"kind": "VIDEO", "has_video": True},
        )
    assert await database_state(session_factory) == before
    assert output_store.puts == output_store.reads == []

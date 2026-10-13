"""F's independent attacks: contradictory scopes and omitted canonical relations.

Only disposable in-memory SQLite is used. These tests do not certify PG locks.
Workflow graph/evidence fixtures are shared; security assertions and worlds are not.
"""

from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from apps.api.app.api import generations as generation_api
from apps.api.app.api import videos as video_api
from apps.api.app.api.assets import store
from apps.api.app.api.deps import require_csrf, require_editor
from apps.api.app.core.config import Settings, get_settings
from apps.api.app.db.base import Base
from apps.api.app.db.models import (
    Asset,
    Brand,
    DirectorRun,
    DirectorRunMember,
    GenerationAsset,
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
from apps.api.app.services.generation_service import GenerationService
from tests.integration.test_generation_preparation import director_workflow_data

REFERENCE_FIELDS = (
    "first_frame_asset_id", "last_frame_asset_id", "source_video_asset_id",
    "reference_image_asset_ids", "reference_video_asset_ids", "reference_audio_asset_ids",
)
ATTACK_SCOPES = (
    "wrong_project", "wrong_product", "foreign_owner_matching_scope", "unscoped",
)


async def state(factory):
    """Full row comparison includes queue status, claims, revisions and owner audit."""
    async with factory() as session:
        return {
            table.name: deepcopy(list((await session.execute(
                select(table).order_by(table.c.id)
            )).mappings()))
            for table in Base.metadata.sorted_tables
        }


def counts(snapshot):
    return {name: len(rows) for name, rows in snapshot.items()}


def assert_unchanged(before, after):
    assert counts(after) == counts(before), (counts(before), counts(after))
    assert after == before, [name for name in before if after[name] != before[name]]


async def world(factory, scope="valid", kind="IMAGE"):
    async with factory() as session, session.begin():
        users = {
            name: User(email=f"{name}@f-adversarial.test", name=name, password_hash="unused",
                       role="ADMIN" if name == "X" else "EDITOR")
            for name in ("A", "B", "X")
        }
        session.add_all(users.values())
        await session.flush()
        projects = {name: Project(name=name, created_by=users[name].id) for name in ("A", "B")}
        brands = {name: Brand(name=name, created_by=users[name].id) for name in ("A", "B")}
        session.add_all([*projects.values(), *brands.values()])
        await session.flush()
        products = {
            name: Product(name=name, brand_id=brands["A" if name == "sibling" else name].id,
                          created_by=users["A" if name == "sibling" else name].id)
            for name in ("A", "B", "sibling")
        }
        session.add_all(products.values())
        await session.flush()
        video = Video(project_id=projects["A"].id, product_id=products["A"].id,
                      brand_id=brands["A"].id, created_by=users["A"].id,
                      title="F victim", brief="Victim campaign", kind="LONG_VIDEO",
                      target_duration=10, aspect_ratio="9:16")
        session.add(video)
        await session.flush()
        scene = Scene(video_id=video.id, scene_order=0, prompt="Bottle in warm light",
                      duration_seconds=5)
        project_id, product_id, owner = projects["A"].id, None, users["A"].id
        if scope == "project_matches_product_foreign":
            product_id = products["B"].id
        elif scope == "product_matches_project_foreign":
            project_id = projects["B"].id
            product_id = products["A"].id
        elif scope == "project_matches_product_sibling":
            product_id = products["sibling"].id
        elif scope == "wrong_project":
            project_id = projects["B"].id
        elif scope == "wrong_product":
            project_id, product_id = None, products["sibling"].id
        elif scope == "foreign_owner_matching_scope":
            owner = users["B"].id
        elif scope == "unscoped":
            project_id = product_id = None
        asset = Asset(project_id=project_id, product_id=product_id, created_by=owner,
                      resource_owner_id=owner, ownership_provenance="OWNER",
                      filename="private-reference.png", object_key="f/private-reference",
                      content_type={"IMAGE": "image/png", "VIDEO": "video/mp4",
                                    "AUDIO": "audio/wav"}[kind],
                      kind=kind, role="REFERENCE", status="READY", size_bytes=12,
                      checksum="a" * 64, width=480, height=864, duration_seconds=5)
        session.add_all([scene, asset])
        for mode in ("t2v", "r2v"):
            graph, slots, profile, approved = director_workflow_data(mode=mode)
            session.add(WorkflowRecord(
                code=f"F_{mode}", mode=mode, version="1", workflow=graph, slots=slots,
                required_slots=list(slots), profile=profile, workflow_hash=approved.workflow_hash,
                slot_map_hash=approved.slot_map_hash, enabled=True, created_by=users["A"].id,
            ))
        await session.flush()
        return {"users": {name: row.id for name, row in users.items()}, "video": video.id,
                "scene": scene.id, "asset": asset.id,
                "products": {name: row.id for name, row in products.items()},
                "brands": {name: row.id for name, row in brands.items()},
                "projects": {name: row.id for name, row in projects.items()}}


@pytest.fixture
def effects(monkeypatch):
    calls = SimpleNamespace(claims=[], persisted=[], external=[])
    original_claim = generation_api.claim
    original_persist = GenerationService.persist_prepared

    async def claim(*args, **kwargs):
        calls.claims.append(kwargs["operation"])
        return await original_claim(*args, **kwargs)

    async def persist(*args, **kwargs):
        calls.persisted.append(args[1].id)
        return await original_persist(*args, **kwargs)

    async def external(*args, **kwargs):
        calls.external.append("external")
        raise AssertionError("Unexpected planner/object-store side effect")

    monkeypatch.setattr(generation_api, "claim", claim)
    monkeypatch.setattr(GenerationService, "persist_prepared", staticmethod(persist))
    monkeypatch.setattr(video_api.StoryboardService, "plan", external)
    calls.store = SimpleNamespace(presign_download=external, presign_upload=external,
                                  put_bytes=external, delete=external)
    return calls


@asynccontextmanager
async def client(factory, tmp_path, ids, caller, effects):
    async def session_override():
        async with factory() as session:
            # SQLite's legacy driver may otherwise commit a top-level SAVEPOINT
            # used by idempotency.claim, even when the request rolls back later.
            # Establish a real outer transaction so counts measure app behavior.
            await session.execute(text("BEGIN"))
            try:
                yield session
                await session.commit()
            except BaseException:
                await session.rollback()
                raise

    async with factory() as session:
        user = await session.get(User, ids["users"][caller])
        principal = SimpleNamespace(id=user.id, role=user.role)
    previous = app.dependency_overrides.copy()
    app.dependency_overrides.update({
        get_session: session_override, require_csrf: lambda: principal,
        require_editor: lambda: principal, store: lambda: effects.store,
        get_settings: lambda: Settings(_env_file=None, workspace_root=tmp_path,
                                      min_free_disk_bytes=0,
                                      jwt_secret="f-isolated-test-secret-at-least-32-bytes"),
    })
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as http:
            yield http
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


async def accepted_batch_payload(http, video_id, payload):
    body = deepcopy(payload)
    preview = await http.post(
        f"/api/v1/videos/{video_id}/generate-all/preview", json=body
    )
    assert preview.status_code == 200, preview.text
    body["settings"] = {
        **body.get("settings", {}),
        "preview_configuration_hash": preview.json()["preview_configuration_hash"],
    }
    return body


def reference(field, asset_id):
    return {field: [asset_id] if field.endswith("_ids") else asset_id}


@pytest.mark.parametrize("scope", [
    "project_matches_product_foreign", "product_matches_project_foreign",
    "project_matches_product_sibling",
])
async def test_database_rejects_dual_scope_before_any_attack_request(session_factory, scope):
    before = await state(session_factory)
    with pytest.raises(IntegrityError, match="ck_assets_single_business_scope"):
        await world(session_factory, scope)
    assert_unchanged(before, await state(session_factory))


@pytest.mark.parametrize("caller", ["A", "X"])
@pytest.mark.parametrize("operation", ["create", "patch"])
@pytest.mark.parametrize("scope", ["valid", *ATTACK_SCOPES])
@pytest.mark.parametrize("field", REFERENCE_FIELDS)
async def test_scene_scope_attack_has_no_mutation(
    session_factory, tmp_path, effects, caller, operation, scope, field
):
    kind = "VIDEO" if "video" in field else "AUDIO" if "audio" in field else "IMAGE"
    ids = await world(session_factory, scope, kind)
    before = await state(session_factory)
    config = reference(field, ids["asset"])
    async with client(session_factory, tmp_path, ids, caller, effects) as http:
        if operation == "create":
            result = await http.post(f"/api/v1/videos/{ids['video']}/scenes",
                                     headers={"If-Match": "1"},
                                     json={"prompt": "Attack", "duration_seconds": 5,
                                           "generation_config": config})
        else:
            result = await http.patch(f"/api/v1/scenes/{ids['scene']}",
                                      headers={"If-Match": "1"},
                                      json={"generation_config": config})
    after = await state(session_factory)
    assert effects.claims == effects.persisted == effects.external == []
    if scope == "valid":
        assert result.status_code == (201 if operation == "create" else 200), result.text
        expected_count = len(before["scenes"]) + (operation == "create")
        assert len(after["scenes"]) == expected_count
        assert result.json()["generation_config"][field] == config[field]
        assert after["videos"][0]["revision"] == before["videos"][0]["revision"] + 1
        return
    assert_unchanged(before, after)
    assert result.status_code == 404, result.text
    assert result.json()["error"]["code"] == "ASSET_NOT_FOUND"


@pytest.mark.parametrize("caller", ["A", "X"])
@pytest.mark.parametrize("scope", ["valid", *ATTACK_SCOPES])
@pytest.mark.parametrize("route", ["single", "batch"])
async def test_generation_scope_attack_before_claim_and_persist(
    session_factory, tmp_path, effects, caller, scope, route
):
    ids = await world(session_factory, scope)
    before = await state(session_factory)
    settings = {"mode": "r2v", "reference_image_asset_ids": [ids["asset"]]}
    url = (f"/api/v1/scenes/{ids['scene']}/generations" if route == "single"
           else f"/api/v1/videos/{ids['video']}/generate-all")
    async with client(session_factory, tmp_path, ids, caller, effects) as http:
        body = {"settings": settings}
        if route == "batch" and scope == "valid":
            body = await accepted_batch_payload(http, ids["video"], body)
        result = await http.post(url,
                                 headers={"Idempotency-Key": "scope-attack"},
                                 json=settings if route == "single" else body)
    after = await state(session_factory)
    if scope == "valid":
        assert result.status_code == 202, result.text
        assert len(after["scene_generations"]) == len(before["scene_generations"]) + 1
        assert len(after["generation_assets"]) == 1
        assert len(after["idempotency_keys"]) == 1
        assert len(effects.persisted) == len(effects.claims) == 1
        dto = result.json() if route == "single" else result.json()["generations"][0]
        assert dto["input_snapshot"]["assets"][0]["id"] == ids["asset"]
    else:
        diagnostic = f"status={result.status_code}, counts={counts(after)}, claims={effects.claims}"
        # The API deliberately treats an asset outside the Video's authorized
        # reference scope as not found, including for an Admin actor.
        assert result.status_code == 404, diagnostic
        assert result.json()["error"]["code"] == "ASSET_NOT_FOUND"
        assert_unchanged(before, after)
        assert effects.claims == effects.persisted == [], diagnostic
    assert effects.external == []


@pytest.mark.parametrize("route", ["single", "batch"])
@pytest.mark.parametrize("caller", ["A", "X"])
@pytest.mark.parametrize("corruption", [
    "project", "product", "brand", "inherited_brand", "product_brand_behind_override",
])
async def test_generation_replay_after_parent_graph_mutation_is_side_effect_free(
    session_factory, tmp_path, effects, route, caller, corruption
):
    ids = await world(session_factory)
    url = (f"/api/v1/scenes/{ids['scene']}/generations" if route == "single"
           else f"/api/v1/videos/{ids['video']}/generate-all")
    async with client(session_factory, tmp_path, ids, caller, effects) as http:
        body = await accepted_batch_payload(http, ids["video"], {}) if route == "batch" else {}
        first = await http.post(url, headers={"Idempotency-Key": "parent-replay"}, json=body)
        assert first.status_code == 202, first.text
        async with session_factory() as session, session.begin():
            video = await session.get(Video, ids["video"])
            if corruption == "project":
                video.project_id = ids["projects"]["B"]
            elif corruption == "product":
                video.product_id = ids["products"]["B"]
                video.brand_id = None
            elif corruption == "brand":
                video.product_id = None
                video.brand_id = ids["brands"]["B"]
            else:
                product = await session.get(Product, ids["products"]["A"])
                product.brand_id = ids["brands"]["B"]
                if corruption == "inherited_brand":
                    video.brand_id = None
        before = await state(session_factory)
        assert len(before["scene_generations"]) == len(before["idempotency_keys"]) == 1
        effects.claims.clear()
        effects.persisted.clear()
        replay = await http.post(url, headers={"Idempotency-Key": "parent-replay"}, json=body)
    assert_unchanged(before, await state(session_factory))
    assert effects.claims == effects.persisted == effects.external == []
    assert replay.status_code == 404, replay.text
    assert replay.json()["error"]["code"] == (
        "SCENE_NOT_FOUND" if route == "single" else "VIDEO_NOT_FOUND"
    )


@pytest.mark.parametrize("caller", ["A", "X"])
@pytest.mark.parametrize("attack", [
    "omitted_foreign_link", "omitted_wrong_scope_link", "cached_private_metadata", "valid",
])
@pytest.mark.parametrize("route", ["single", "batch"])
async def test_replay_checks_canonical_relations_even_when_dto_omits_them(
    session_factory, tmp_path, effects, caller, attack, route
):
    ids = await world(session_factory)
    async with client(session_factory, tmp_path, ids, caller, effects) as http:
        url = (f"/api/v1/scenes/{ids['scene']}/generations" if route == "single"
               else f"/api/v1/videos/{ids['video']}/generate-all")
        body = await accepted_batch_payload(http, ids["video"], {}) if route == "batch" else {}
        first = await http.post(url, headers={"Idempotency-Key": "canonical-relations"}, json=body)
        assert first.status_code == 202, first.text
        dto = first.json() if route == "single" else first.json()["generations"][0]
        generation_id = dto["id"]
        # The original snapshot is produced by the real API/service, never a {} fixture.
        assert dto["input_snapshot"]["generation_intent"]
        async with session_factory() as session, session.begin():
            if attack.startswith("omitted_"):
                asset = await session.get(Asset, ids["asset"])
                if attack == "omitted_foreign_link":
                    asset.created_by = asset.resource_owner_id = ids["users"]["B"]
                    asset.project_id = ids["projects"]["B"]
                    asset.product_id = None
                else:
                    asset.project_id = ids["projects"]["B"]
                session.add(GenerationAsset(generation_id=generation_id, asset_id=asset.id,
                                            role="REFERENCE_IMAGE", order_index=0))
            elif attack == "cached_private_metadata":
                record = await session.scalar(select(IdempotencyKey))
                poisoned = deepcopy(record.response)
                entry = poisoned if route == "single" else poisoned["generations"][0]
                entry["input_snapshot"]["private_note"] = "private-B-cache-secret"
                entry["output_metadata"] = {"private_note": "private-B-cache-secret"}
                record.response = poisoned
        before = await state(session_factory)
        assert counts(before)["scene_generations"] == counts(before)["idempotency_keys"] == 1
        effects.claims.clear()
        effects.persisted.clear()
        replay = await http.post(url, headers={"Idempotency-Key": "canonical-relations"}, json=body)
    assert_unchanged(before, await state(session_factory))
    assert effects.persisted == effects.external == []
    assert len(effects.claims) == 1
    if attack.startswith("omitted_"):
        assert replay.status_code == 404, (
            f"{attack}/{caller}: replay={replay.status_code}; "
            f"counts={counts(before)}; canonical generation={generation_id}"
        )
        assert replay.json()["error"]["code"] == "GENERATION_NOT_FOUND"
    else:
        assert replay.status_code == 202, replay.text
        assert replay.json() == first.json()
        assert "private-B-cache-secret" not in replay.text


@pytest.mark.parametrize("member_owner", ["A", "B"])
async def test_batch_replay_rejects_director_member_from_another_video(
    session_factory, tmp_path, effects, member_owner
):
    ids = await world(session_factory)
    async with client(session_factory, tmp_path, ids, "A", effects) as http:
        url = f"/api/v1/videos/{ids['video']}/generate-all"
        body = await accepted_batch_payload(http, ids["video"], {})
        first = await http.post(url, headers={"Idempotency-Key": "director-poison"}, json=body)
        assert first.status_code == 202, first.text
        generation_id = first.json()["generations"][0]["id"]
        async with session_factory() as session, session.begin():
            original = await session.get(SceneGeneration, generation_id)
            owner = ids["users"][member_owner]
            other_video = Video(project_id=ids["projects"][member_owner], created_by=owner,
                                title="Other video", brief="Other", kind="LONG_VIDEO",
                                target_duration=10, aspect_ratio="9:16")
            session.add(other_video)
            await session.flush()
            other_scene = Scene(video_id=other_video.id, scene_order=0,
                                prompt="Other", duration_seconds=5)
            run = DirectorRun(video_id=ids["video"], workflow_id=original.workflow_id,
                              task="t2v", created_by=ids["users"]["A"], input_snapshot={
                                  "member_generation_ids": [generation_id]})
            session.add_all([other_scene, run])
            await session.flush()
            other_generation = SceneGeneration(
                video_id=other_video.id, scene_id=other_scene.id, workflow_id=original.workflow_id,
                mode="t2v", operation="ORIGINAL", generation_no=1, created_by=owner,
                input_snapshot=deepcopy(original.input_snapshot))
            session.add(other_generation)
            await session.flush()
            session.add(DirectorRunMember(director_run_id=run.id, scene_id=other_scene.id,
                                          scene_generation_id=other_generation.id, member_index=0,
                                          continuity="CUT"))
            record = await session.scalar(select(IdempotencyKey))
            poisoned = deepcopy(record.response)
            poisoned["director_run_id"] = run.id
            poisoned["execution_groups"][0].update(
                director_run_id=run.id, execution_scope="aggregate")
            record.response = poisoned
        before = await state(session_factory)
        effects.persisted.clear()
        replay = await http.post(url, headers={"Idempotency-Key": "director-poison"}, json=body)
    assert_unchanged(before, await state(session_factory))
    assert effects.persisted == effects.external == []
    assert replay.status_code == 404, replay.text
    assert replay.json()["error"]["code"] == "GENERATION_NOT_FOUND"


@pytest.mark.parametrize("actor_change", ["demoted", "disabled"])
async def test_delegated_video_keeps_b_owner_for_generation_after_actor_change(
    session_factory, tmp_path, effects, actor_change
):
    ids = await world(session_factory)
    before_create = await state(session_factory)
    async with client(session_factory, tmp_path, ids, "X", effects) as http:
        created = await http.post("/api/v1/videos", json={
            "project_id": ids["projects"]["B"], "title": "X creates for B",
            "brief": "Delegated business ownership", "kind": "QUICK_CLIP",
            "target_duration": 5,
        })
    assert created.status_code == 201, created.text
    after_create = await state(session_factory)
    assert len(after_create["videos"]) == len(before_create["videos"]) + 1
    assert len(after_create["scenes"]) == len(before_create["scenes"]) + 1
    video_id = created.json()["id"]
    async with session_factory() as session, session.begin():
        video = await session.get(Video, video_id)
        assert video.created_by == ids["users"]["B"]
        assert video.created_actor_id == ids["users"]["X"]
        scene_id = await session.scalar(select(Scene.id).where(Scene.video_id == video_id))
        actor = await session.get(User, ids["users"]["X"])
        if actor_change == "demoted":
            actor.role = "EDITOR"
        else:
            actor.is_active = False
    before_attack = await state(session_factory)
    async with client(session_factory, tmp_path, ids, "A", effects) as http:
        denied_read = await http.get(f"/api/v1/videos/{video_id}")
        denied_generate = await http.post(f"/api/v1/scenes/{scene_id}/generations",
                                          headers={"Idempotency-Key": "foreign-delegated"},
                                          json={})
    assert denied_read.status_code == denied_generate.status_code == 404
    assert denied_read.json()["error"]["code"] == "VIDEO_NOT_FOUND"
    assert denied_generate.json()["error"]["code"] == "SCENE_NOT_FOUND"
    assert_unchanged(before_attack, await state(session_factory))
    assert effects.claims == effects.persisted == effects.external == []
    async with client(session_factory, tmp_path, ids, "B", effects) as http:
        allowed_read = await http.get(f"/api/v1/videos/{video_id}")
        allowed_generate = await http.post(f"/api/v1/scenes/{scene_id}/generations",
                                           headers={"Idempotency-Key": "owner-delegated"}, json={})
    assert allowed_read.status_code == 200, allowed_read.text
    assert allowed_generate.status_code == 202, allowed_generate.text
    snapshot = allowed_generate.json()["input_snapshot"]
    assert snapshot["source_scope"]["project_id"] == ids["projects"]["B"]
    after_generation = await state(session_factory)
    assert len(after_generation["scene_generations"]) == 1
    assert len(after_generation["idempotency_keys"]) == 1
    assert len(effects.claims) == len(effects.persisted) == 1
    assert effects.external == []
    async with session_factory() as session:
        video = await session.get(Video, video_id)
        assert video.created_by == ids["users"]["B"]
        assert video.created_actor_id == ids["users"]["X"]

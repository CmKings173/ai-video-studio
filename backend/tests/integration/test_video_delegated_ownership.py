"""Delegated creation must preserve business ownership and durable actor attribution."""

import hashlib
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from apps.api.app.core.config import Settings
from apps.api.app.db.models import (
    Asset,
    Brand,
    FinalVideo,
    Product,
    SceneGeneration,
    User,
    Video,
    WorkflowRecord,
)
from tests.integration.test_assets_assembly_ownership import (
    RecordingAssetStore,
    api_client,
    login,
    seed_world,
)
from tests.integration.test_generation_preparation import director_workflow_data
from workers.assembler import Assembler
from workers.common import save_output_file
from workers.dispatcher import Dispatcher

ACTOR_REVISION = "c8f3b5d02e74"
PREVIOUS_REVISION = "b7e2a4c91d63"


class DeterministicOutputStore(RecordingAssetStore):
    """Inject only object I/O; real worker code persists and verifies outputs."""

    def __init__(self):
        super().__init__()
        self.objects = {}

    async def put_file_immutable(self, key, path, content_type, checksum, max_bytes):
        data = path.read_bytes()
        assert content_type == "video/mp4"
        assert 0 < len(data) <= max_bytes
        assert hashlib.sha256(data).hexdigest() == checksum
        if key in self.objects:
            assert self.objects[key] == data
        self.objects[key] = data

    async def get_bytes(self, key):
        return self.objects[key]


class DeterministicFFmpeg:
    async def assemble(self, inputs, output, config):
        assert inputs and all(path.read_bytes() for path in inputs)
        output.write_bytes(b"deterministic-final-video")
        return {
            "kind": "VIDEO", "has_video": True, "width": 480, "height": 864,
            "duration_seconds": 5, "fps": 24,
        }


async def delegated_world(session_factory):
    ids = await seed_world(session_factory)
    graph, slots, profile, approved = director_workflow_data()
    async with session_factory() as session, session.begin():
        brand = Brand(name="B Brand", created_by=ids["editorb_user"])
        session.add(brand)
        await session.flush()
        product = await session.get(Product, ids["editorb_product"])
        product.brand_id = brand.id
        workflow = await session.get(WorkflowRecord, ids["editorb_workflow"])
        workflow.workflow = graph
        workflow.slots = slots
        workflow.required_slots = list(slots)
        workflow.profile = profile
        workflow.workflow_hash = approved.workflow_hash
        workflow.slot_map_hash = approved.slot_map_hash
        workflow.enabled = True
        ids["editorb_brand"] = brand.id
    return ids


def creation_payload(ids, *, kind="QUICK_CLIP"):
    return {
        "project_id": ids["editorb_project"],
        "product_id": ids["editorb_product"],
        "brand_id": ids.get("editorb_brand"),
        "title": "Delegated creation",
        "brief": "Created by Admin for Editor B",
        "kind": kind,
        "target_duration": 30 if kind == "LONG_VIDEO" else 5,
    }


@pytest.mark.asyncio
async def test_admin_video_creation_records_owner_and_actor(session_factory, tmp_path):
    ids = await seed_world(session_factory)
    async with api_client(session_factory, tmp_path, RecordingAssetStore()) as client:
        await login(client, "admin@ownership.test")
        response = await client.post(
            "/api/v1/videos",
            json={
                "project_id": ids["editorb_project"],
                "product_id": ids["editorb_product"],
                "title": "Delegated creation",
                "brief": "Created by Admin for Editor B",
            },
        )
        assert response.status_code == 201, response.text
        async with session_factory() as session:
            video = await session.get(Video, response.json()["id"])
            assert video.created_by == ids["editorb_user"]
            assert video.created_actor_id == ids["admin_user"]
        assert "created_actor_id" not in response.json()


@pytest.mark.asyncio
@pytest.mark.parametrize("actor", ["editorb", "admin"])
async def test_creation_audit_and_storyboard_preserve_business_owner(
    session_factory, tmp_path, actor
):
    ids = await delegated_world(session_factory)
    async with api_client(session_factory, tmp_path, RecordingAssetStore()) as client:
        await login(client, f"{actor}@ownership.test")
        created = await client.post("/api/v1/videos", json=creation_payload(ids, kind="LONG_VIDEO"))
        assert created.status_code == 201, created.text
        video_id = created.json()["id"]
        preview = await client.post(f"/api/v1/videos/{video_id}/storyboard/preview")
        assert preview.status_code == 200, preview.text
        scenes = [
            {key: value for key, value in scene.items() if key not in {"title", "purpose"}}
            for scene in preview.json()["scenes"]
        ]
        published = await client.post(
            f"/api/v1/videos/{video_id}/storyboard/publish",
            headers={"If-Match": str(created.json()["revision"]), "Idempotency-Key": "delegation"},
            json={"scenes": scenes},
        )
        assert published.status_code == 200, published.text
        await login(client, "editorb@ownership.test")
        detail = await client.get(f"/api/v1/videos/{video_id}")
        assert detail.status_code == 200, detail.text
        assert len(detail.json()["scenes"]) == len(scenes)
        async with session_factory() as session:
            video = await session.get(Video, video_id)
            assert video.created_by == ids["editorb_user"]
            assert video.created_actor_id == ids[f"{actor}_user"]
            assert video.product_id == ids["editorb_product"]
            assert video.brand_id == ids["editorb_brand"]
        assert "created_actor_id" not in detail.json()


@pytest.mark.asyncio
@pytest.mark.parametrize("actor_change", ["demoted", "disabled"])
async def test_delegated_worker_journey_survives_actor_authority_change(
    session_factory, tmp_path, actor_change
):
    ids = await delegated_world(session_factory)
    storage = DeterministicOutputStore()
    settings = Settings(_env_file=None, workspace_root=tmp_path, min_free_disk_bytes=0)
    dispatcher = Dispatcher(session_factory, None, storage, None, settings)
    assembler = Assembler(session_factory, storage, DeterministicFFmpeg(), settings)
    async with api_client(session_factory, tmp_path, storage) as client:
        await login(client, "admin@ownership.test")
        created = await client.post("/api/v1/videos", json=creation_payload(ids))
        assert created.status_code == 201, created.text
        video_id = created.json()["id"]
        scene_id = created.json()["scenes"][0]["id"]
        async with session_factory() as session:
            video = await session.get(Video, video_id)
            assert video.created_by == ids["editorb_user"]
            assert video.created_actor_id == ids["admin_user"]

        generation_response = await client.post(
            f"/api/v1/scenes/{scene_id}/generations",
            headers={"Idempotency-Key": "delegated-worker-generation"},
            json={
                "workflow_id": ids["editorb_workflow"], "mode": "t2v",
                "quality_profile": "STANDARD", "seed_policy": "FIXED", "seed": 17,
                "aspect_ratio": "9:16", "width": 480, "height": 864,
            },
        )
        assert generation_response.status_code == 202, generation_response.text
        generation_id = generation_response.json()["id"]
        assert await dispatcher.claim() == generation_id
        path = tmp_path / "generation.mp4"
        path.write_bytes(b"deterministic-generated-video")
        output_id = await save_output_file(
            session_factory, storage, owner_id=generation_id, role="GENERATED_VIDEO",
            project_id=ids["editorb_project"], created_by=ids["admin_user"], path=path,
            metadata={
                "kind": "VIDEO", "has_video": True, "width": 480, "height": 864,
                "duration_seconds": 5, "fps": 24,
            },
        )
        await dispatcher._finish(generation_id, "COMPLETED", asset_id=output_id)
        async with session_factory() as session:
            generation = await session.get(SceneGeneration, generation_id)
            assert generation.status == "COMPLETED"
            assert generation.created_by == ids["admin_user"]
            assert generation.output_asset_id == output_id

        await login(client, "editorb@ownership.test")
        generation_read = await client.get(f"/api/v1/generations/{generation_id}")
        assert generation_read.status_code == 200, generation_read.text
        assert generation_read.json()["output_asset_id"] == output_id
        assert (await client.get(f"/api/v1/assets/{output_id}/download")).status_code == 200
        scene_read = await client.get(f"/api/v1/scenes/{scene_id}")
        assert scene_read.status_code == 200, scene_read.text
        selected = await client.post(
            f"/api/v1/scenes/{scene_id}/select-generation",
            headers={"If-Match": str(scene_read.json()["revision"])},
            json={"generation_id": generation_id},
        )
        assert selected.status_code == 200, selected.text
        video_read = await client.get(f"/api/v1/videos/{video_id}")
        assert video_read.status_code == 200, video_read.text
        assembled = await client.post(
            f"/api/v1/videos/{video_id}/assemble",
            headers={
                "If-Match": str(video_read.json()["revision"]),
                "Idempotency-Key": "delegated-worker-assembly",
            },
            json={},
        )
        assert assembled.status_code == 202, assembled.text
        final_id = assembled.json()["id"]
        assert await assembler.claim() == final_id
        await assembler.process(final_id)
        async with session_factory() as session:
            final = await session.get(FinalVideo, final_id)
            assert final.status == "READY", (final.error_code, final.error_message)
            assert final.created_by == ids["editorb_user"]
            final_output_id = final.output_asset_id
            assert final_output_id is not None
            video = await session.get(Video, video_id)
            assert video.current_final_video_id == final_id

        readable = [
            f"/api/v1/videos/{video_id}", f"/api/v1/scenes/{scene_id}",
            f"/api/v1/generations/{generation_id}", f"/api/v1/assets/{output_id}",
            f"/api/v1/assets/{output_id}/download", f"/api/v1/assets/{final_output_id}",
            f"/api/v1/assets/{final_output_id}/download", f"/api/v1/final-versions/{final_id}",
            f"/api/v1/final-versions/{final_id}/download",
        ]
        for route in readable:
            response = await client.get(route)
            assert response.status_code == 200, (route, response.text)

        async with session_factory() as session, session.begin():
            actor = await session.get(User, ids["admin_user"])
            if actor_change == "demoted":
                actor.role = "EDITOR"
            else:
                actor.is_active = False

        for route in readable:
            response = await client.get(route)
            assert response.status_code == 200, (route, response.text)
        denied_viewers = ["editora"]
        if actor_change == "demoted":
            denied_viewers.append("admin")
        else:
            disabled_login = await client.post(
                "/api/v1/auth/login",
                json={
                    "email": "admin@ownership.test",
                    "password": "ownership-integration-password",
                },
            )
            assert disabled_login.status_code == 403, disabled_login.text
            assert disabled_login.json()["error"]["code"] == "ACCOUNT_DISABLED"
        for viewer in denied_viewers:
            await login(client, f"{viewer}@ownership.test")
            presigns_before = len(storage.downloads)
            for route in readable:
                response = await client.get(route)
                assert response.status_code == 404, (route, response.text)
            assert len(storage.downloads) == presigns_before
        await login(client, "adminy@ownership.test")
        for route in readable:
            response = await client.get(route)
            assert response.status_code == 200, (route, response.text)

        async with session_factory() as session:
            video = await session.get(Video, video_id)
            assert (video.created_by, video.created_actor_id) == (
                ids["editorb_user"], ids["admin_user"],
            )
            for asset_id, audit_actor in (
                (output_id, ids["admin_user"]), (final_output_id, ids["editorb_user"])
            ):
                asset = await session.get(Asset, asset_id)
                assert asset.status == "READY"
                assert asset.created_by == audit_actor
                assert asset.resource_owner_id == ids["editorb_user"]
                assert asset.project_id == ids["editorb_project"]
                assert asset.ownership_provenance == "VIDEO_PARENT"


def migration_config(url):
    root = Path(__file__).resolve().parents[2]
    cfg = Config(root / "alembic.ini")
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


@pytest.mark.parametrize(
    "database_url",
    [None] + ([os.environ["VIDEO_ACTOR_MIGRATION_TEST_DATABASE_URL"]]
              if os.getenv("VIDEO_ACTOR_MIGRATION_TEST_DATABASE_URL") else []),
    ids=lambda value: "sqlite" if value is None else "isolated-postgres",
)
def test_actor_migration_upgrade_downgrade_preserves_history(tmp_path, database_url):
    """PG is opt-in only on a separately coordinated actor migration database."""
    url = make_url(database_url or f"sqlite:///{(tmp_path / 'actor.db').as_posix()}")
    if url.get_backend_name() == "postgresql":
        assert url.host in {"localhost", "127.0.0.1"}
        assert url.database and url.database.startswith("phase2_actor_")
        assert url.database != "phase2_remediation_20261010"
    else:
        assert url.get_backend_name() == "sqlite"
    cfg = migration_config(url.render_as_string(hide_password=False))
    command.upgrade(cfg, PREVIOUS_REVISION)
    sync_url = (
        url.set(drivername="postgresql+psycopg")
        if url.get_backend_name() == "postgresql" else url
    )
    engine = create_engine(sync_url)
    try:
        with engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO users (id,email,name,password_hash,role,is_active,"
                "created_at,updated_at) "
                "VALUES ('owner','owner@actor.test','Owner','hash','EDITOR',true,"
                "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP), "
                "('actor','actor@actor.test','Actor','hash','ADMIN',true,"
                "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP), "
                "('audit','audit@actor.test','Audit','hash','ADMIN',true,"
                "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            ))
            connection.execute(text(
                "INSERT INTO projects (id,name,description,archived,revision,created_by,"
                "created_at,updated_at) VALUES ('project','Project','',false,1,'owner',"
                "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            ))
            connection.execute(text(
                "INSERT INTO videos (id,project_id,title,kind,target_duration,aspect_ratio,brief,"
                "config,status,revision,created_by,created_at,updated_at) "
                "VALUES ('historical','project','Historical','QUICK_CLIP',5,'9:16','Brief',"
                "'{}','DRAFT',1,'actor',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            ))
        command.upgrade(cfg, ACTOR_REVISION)
        with engine.connect() as connection:
            columns = {
                column["name"]: column for column in inspect(connection).get_columns("videos")
            }
            assert columns["created_actor_id"]["nullable"] is True
            fk = next(
                item for item in inspect(connection).get_foreign_keys("videos")
                if item["constrained_columns"] == ["created_actor_id"]
            )
            assert fk["referred_table"] == "users"
            assert fk["options"]["ondelete"] == "RESTRICT"
            assert connection.execute(text(
                "SELECT created_by,created_actor_id FROM videos WHERE id='historical'"
            )).one() == ("actor", None)
        with engine.begin() as connection:
            if url.get_backend_name() == "sqlite":
                connection.execute(text("PRAGMA foreign_keys=ON"))
            connection.execute(text(
                "UPDATE videos SET created_actor_id='audit' WHERE id='historical'"
            ))
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(text(
                "UPDATE videos SET created_actor_id='missing-user' WHERE id='historical'"
            ))
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(text("DELETE FROM users WHERE id='audit'"))
        command.downgrade(cfg, PREVIOUS_REVISION)
        with engine.connect() as connection:
            assert "created_actor_id" not in {
                column["name"] for column in inspect(connection).get_columns("videos")
            }
            assert connection.execute(text(
                "SELECT created_by FROM videos WHERE id='historical'"
            )).scalar_one() == "actor"
        command.upgrade(cfg, ACTOR_REVISION)
        with engine.connect() as connection:
            assert connection.execute(text(
                "SELECT created_by,created_actor_id FROM videos WHERE id='historical'"
            )).one() == ("actor", None)
    finally:
        engine.dispose()

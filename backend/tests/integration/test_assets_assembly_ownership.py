from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import func, select

from apps.api.app.api.assets import store
from apps.api.app.core.config import Settings, get_settings
from apps.api.app.core.errors import AppError
from apps.api.app.core.security import hash_password
from apps.api.app.db.models import (
    Asset,
    DirectorRun,
    DirectorRunMember,
    FinalVideo,
    GenerationAsset,
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
from apps.api.app.db.session import get_session
from apps.api.app.main import app
from apps.api.app.schemas.api import AssemblyRequest, GenerationRequest, UploadRequest
from apps.api.app.services.assembly_service import AssemblyService
from apps.api.app.services.idempotency import payload_hash
from tests.integration.test_generation_preparation import director_workflow_data
from workers.common import save_output

PASSWORD = "ownership-integration-password"
JWT_SECRET = "integration-test-only-signing-secret-with-at-least-32-bytes"


class RecordingAssetStore:
    def __init__(self):
        self.uploads: list[tuple[str, str]] = []
        self.downloads: list[tuple[str, str]] = []

    async def presign_upload(self, key: str, content_type: str) -> dict:
        self.uploads.append((key, content_type))
        return {"url": "https://storage.test/upload", "fields": {"key": key}}

    async def presign_download(self, key: str, filename: str) -> str:
        self.downloads.append((key, filename))
        return "https://storage.test/download"


class WorkerOutputStore:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    async def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        self.objects[key] = data

    async def get_bytes(self, key: str, max_bytes: int | None = None) -> bytes:
        return self.objects[key]


@asynccontextmanager
async def api_client(session_factory, tmp_path, asset_store: RecordingAssetStore):
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
        jwt_secret=JWT_SECRET,
        jwt_issuer="ai-video-studio-tests",
        jwt_audience="ai-video-studio-api-tests",
        min_free_disk_bytes=0,
    )
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[store] = lambda: asset_store
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.clear()


async def login(client: httpx.AsyncClient, email: str) -> None:
    response = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]


async def seed_world(session_factory) -> dict[str, str]:
    async with session_factory() as session, session.begin():
        users = {
            name: User(
                email=f"{name.lower()}@ownership.test",
                name=name,
                password_hash=hash_password(PASSWORD),
                role=role,
            )
            for name, role in (
                ("EditorA", "EDITOR"),
                ("EditorB", "EDITOR"),
                ("Admin", "ADMIN"),
                ("AdminY", "ADMIN"),
            )
        }
        session.add_all(users.values())
        await session.flush()

        projects = {
            name: Project(name=f"{name} Project", created_by=users[name].id)
            for name in ("EditorA", "EditorB")
        }
        products = {
            name: Product(name=f"{name} Product", created_by=users[name].id)
            for name in ("EditorA", "EditorB")
        }
        session.add_all([*projects.values(), *products.values()])
        await session.flush()

        assets = {
            name: Asset(
                project_id=projects[name].id,
                kind="IMAGE",
                role="PROJECT_REFERENCE",
                filename=f"{name}.png",
                content_type="image/png",
                object_key=f"ownership/{name}.png",
                status="READY",
                size_bytes=12,
                checksum="a" * 64,
                created_by=users[name].id,
            )
            for name in ("EditorA", "EditorB")
        }
        videos = {
            name: Video(
                project_id=projects[name].id,
                product_id=products[name].id,
                title=f"{name} Video",
                brief="Ownership test video",
                kind="QUICK_CLIP",
                target_duration=5,
                aspect_ratio="9:16",
                created_by=users[name].id,
            )
            for name in ("EditorA", "EditorB")
        }
        session.add_all([*assets.values(), *videos.values()])
        await session.flush()

        workflows = {
            name: WorkflowRecord(
                code=f"OWNERSHIP_{name.upper()}",
                mode="t2v",
                version="1",
                workflow={"1": {"inputs": {"prompt": ""}}},
                slots={"PROMPT": ["1", "prompt"]},
                required_slots=["PROMPT"],
                profile={},
                workflow_hash=("d" if name == "EditorA" else "e") * 64,
                slot_map_hash=("f" if name == "EditorA" else "1") * 64,
                enabled=False,
                created_by=users[name].id,
            )
            for name in ("EditorA", "EditorB")
        }
        session.add_all(workflows.values())
        await session.flush()

        scenes = {
            name: Scene(
                video_id=videos[name].id,
                scene_order=0,
                prompt=f"{name} scene",
                duration_seconds=5,
            )
            for name in ("EditorA", "EditorB")
        }
        session.add_all(scenes.values())
        await session.flush()

        generations = {
            name: SceneGeneration(
                video_id=videos[name].id,
                scene_id=scenes[name].id,
                mode="t2v",
                workflow_id=workflows[name].id,
                generation_no=1,
                operation="ORIGINAL",
                status="COMPLETED",
                phase="COMPLETED",
                output_asset_id=assets[name].id,
                input_snapshot={},
                created_by=users[name].id,
            )
            for name in ("EditorA", "EditorB")
        }
        session.add_all(generations.values())
        await session.flush()
        for name, scene in scenes.items():
            scene.selected_generation_id = generations[name].id
        await session.flush()

        finals = {
            name: FinalVideo(
                video_id=videos[name].id,
                version_no=1,
                status="READY",
                phase="READY",
                manifest={"video_id": videos[name].id},
                manifest_hash=("b" if name == "EditorA" else "c") * 64,
                assembly_config={},
                output_asset_id=assets[name].id,
                created_by=users["EditorB" if name == "EditorA" else "EditorA"].id,
            )
            for name in ("EditorA", "EditorB")
        }
        session.add_all(finals.values())
        await session.flush()
        return {
            **{f"{name.lower()}_user": row.id for name, row in users.items()},
            **{f"{name.lower()}_project": row.id for name, row in projects.items()},
            **{f"{name.lower()}_product": row.id for name, row in products.items()},
            **{f"{name.lower()}_asset": row.id for name, row in assets.items()},
            **{f"{name.lower()}_video": row.id for name, row in videos.items()},
            **{f"{name.lower()}_scene": row.id for name, row in scenes.items()},
            **{f"{name.lower()}_generation": row.id for name, row in generations.items()},
            **{f"{name.lower()}_workflow": row.id for name, row in workflows.items()},
            **{f"{name.lower()}_final": row.id for name, row in finals.items()},
        }


@pytest.mark.asyncio
async def test_assets_are_sql_scoped_and_cross_user_operations_are_hidden(
    session_factory, tmp_path
):
    ids = await seed_world(session_factory)
    storage = RecordingAssetStore()
    async with api_client(session_factory, tmp_path, storage) as client:
        await login(client, "editora@ownership.test")
        own = await client.get(f"/api/v1/assets/{ids['editora_asset']}")
        assert own.status_code == 200
        hidden = await client.get(f"/api/v1/assets/{ids['editorb_asset']}")
        assert hidden.status_code == 404

        page = await client.get("/api/v1/assets?page=1&page_size=100")
        assert page.status_code == 200
        assert page.json()["total"] == 1
        assert [row["id"] for row in page.json()["items"]] == [ids["editora_asset"]]

        assert (
            await client.get(f"/api/v1/assets/{ids['editorb_asset']}/download")
        ).status_code == 404
        assert (
            await client.post(
                f"/api/v1/assets/{ids['editorb_asset']}/complete",
                json={"checksum_sha256": "a" * 64},
            )
        ).status_code == 404
        assert (await client.delete(f"/api/v1/assets/{ids['editorb_asset']}")).status_code == 404
        assert storage.downloads == []

        await login(client, "editorb@ownership.test")
        reverse = await client.get("/api/v1/assets?page=1&page_size=100")
        assert reverse.status_code == 200
        assert reverse.json()["total"] == 1
        assert [row["id"] for row in reverse.json()["items"]] == [ids["editorb_asset"]]

        await login(client, "admin@ownership.test")
        admin_page = await client.get("/api/v1/assets?page=1&page_size=100")
        assert admin_page.status_code == 200
        assert admin_page.json()["total"] == 2
        downloaded = await client.get(f"/api/v1/assets/{ids['editorb_asset']}/download")
        assert downloaded.status_code == 200
        assert storage.downloads == [("ownership/EditorB.png", "EditorB.png")]
        completed = await client.post(
            f"/api/v1/assets/{ids['editorb_asset']}/complete", json={}
        )
        assert completed.status_code == 202, completed.text
        uploaded = await client.post(
            "/api/v1/assets/upload-url",
            headers={"Idempotency-Key": "admin-upload-to-editor-project"},
            json={
                "project_id": ids["editorb_project"],
                "filename": "admin-reference.png",
                "content_type": "image/png",
                "size_bytes": 12,
                "role": "PROJECT_REFERENCE",
            },
        )
        assert uploaded.status_code == 201, uploaded.text
        project_upload_id = uploaded.json()["asset_id"]
        product_uploaded = await client.post(
            "/api/v1/assets/upload-url",
            headers={"Idempotency-Key": "admin-upload-to-editor-product"},
            json={
                "product_id": ids["editorb_product"],
                "filename": "admin-product-image.png",
                "content_type": "image/png",
                "size_bytes": 12,
                "role": "PRODUCT_IMAGE",
            },
        )
        assert product_uploaded.status_code == 201, product_uploaded.text
        product_upload_id = product_uploaded.json()["asset_id"]

        await login(client, "editorb@ownership.test")
        assert (await client.get(f"/api/v1/assets/{project_upload_id}")).status_code == 200
        assert (await client.get(f"/api/v1/assets/{product_upload_id}")).status_code == 200
        await login(client, "editora@ownership.test")
        assert (await client.get(f"/api/v1/assets/{project_upload_id}")).status_code == 404
        assert (await client.get(f"/api/v1/assets/{product_upload_id}")).status_code == 404

    async with session_factory() as session, session.begin():
        foreign = await session.get(Asset, ids["editorb_asset"])
        assert foreign.status == "READY"
        assert foreign.deleted_at is None
        project_upload = await session.get(Asset, project_upload_id)
        product_upload = await session.get(Asset, product_upload_id)
        assert project_upload is not None
        assert project_upload.project_id == ids["editorb_project"]
        assert project_upload.created_by == ids["admin_user"]
        assert product_upload is not None
        assert product_upload.product_id == ids["editorb_product"]
        assert product_upload.project_id is None
        assert product_upload.created_by == ids["admin_user"]
        assert product_upload.resource_owner_id == ids["editorb_user"]
        assert product_upload.ownership_provenance == "ADMIN_ASSIGNMENT"

        project_upload.status = "READY"
        project_upload.checksum = "3" * 64
        admin = await session.get(User, ids["admin_user"])
        assert admin is not None
        admin.role = "EDITOR"
        admin.is_active = False

    async with api_client(session_factory, tmp_path, storage) as client:
        await login(client, "editorb@ownership.test")
        assert (await client.get(f"/api/v1/assets/{project_upload_id}")).status_code == 200
        assert (await client.get(f"/api/v1/assets/{product_upload_id}")).status_code == 200
        assert (await client.get(f"/api/v1/assets/{project_upload_id}/download")).status_code == 200
        await login(client, "editora@ownership.test")
        assert (await client.get(f"/api/v1/assets/{project_upload_id}")).status_code == 404
        assert (await client.get(f"/api/v1/assets/{product_upload_id}")).status_code == 404
        await login(client, "adminy@ownership.test")
        assert (await client.get(f"/api/v1/assets/{project_upload_id}")).status_code == 200


@pytest.mark.asyncio
async def test_final_download_rejects_output_asset_from_another_project(session_factory, tmp_path):
    ids = await seed_world(session_factory)
    async with session_factory() as session, session.begin():
        final = await session.get(FinalVideo, ids["editora_final"])
        assert final is not None
        final.output_asset_id = ids["editorb_asset"]

    storage = RecordingAssetStore()
    async with api_client(session_factory, tmp_path, storage) as client:
        await login(client, "editora@ownership.test")
        response = await client.get(f"/api/v1/final-versions/{ids['editora_final']}/download")

    assert response.status_code == 404
    assert storage.downloads == []


@pytest.mark.asyncio
async def test_final_download_rejects_sibling_final_output_pointer(session_factory, tmp_path):
    ids = await seed_world(session_factory)
    async with session_factory() as session, session.begin():
        sibling = FinalVideo(
            video_id=ids["editorb_video"],
            version_no=2,
            status="READY",
            phase="READY",
            manifest={"video_id": ids["editorb_video"]},
            manifest_hash="4" * 64,
            assembly_config={},
            created_by=ids["admin_user"],
        )
        session.add(sibling)
        await session.flush()
        sibling_id = sibling.id

    worker_store = WorkerOutputStore()
    output_id = await save_output(
        session_factory,
        worker_store,
        owner_id=sibling_id,
        role="FINAL_VIDEO",
        project_id=ids["editorb_project"],
        created_by=ids["admin_user"],
        data=b"sibling-final-output",
        metadata={"kind": "VIDEO", "has_video": True},
    )
    async with session_factory() as session, session.begin():
        sibling = await session.get(FinalVideo, sibling_id)
        requested = await session.get(FinalVideo, ids["editorb_final"])
        assert sibling is not None and requested is not None
        sibling.output_asset_id = output_id
        requested.output_asset_id = output_id

    storage = RecordingAssetStore()
    async with api_client(session_factory, tmp_path, storage) as client:
        await login(client, "editorb@ownership.test")
        response = await client.get(
            f"/api/v1/final-versions/{ids['editorb_final']}/download"
        )
        assert response.status_code == 404
        await login(client, "admin@ownership.test")
        admin_response = await client.get(
            f"/api/v1/final-versions/{ids['editorb_final']}/download"
        )
        assert admin_response.status_code == 404
    assert storage.downloads == []


@pytest.mark.asyncio
async def test_generation_selection_rejects_sibling_generation_output_pointer(
    session_factory, tmp_path
):
    ids = await seed_world(session_factory)
    async with session_factory() as session, session.begin():
        sibling = SceneGeneration(
            video_id=ids["editorb_video"],
            scene_id=ids["editorb_scene"],
            mode="t2v",
            workflow_id=ids["editorb_workflow"],
            generation_no=2,
            operation="ORIGINAL",
            status="COMPLETED",
            phase="COMPLETED",
            input_snapshot={},
            created_by=ids["editorb_user"],
        )
        session.add(sibling)
        await session.flush()
        sibling_id = sibling.id

    worker_store = WorkerOutputStore()
    output_id = await save_output(
        session_factory,
        worker_store,
        owner_id=sibling_id,
        role="GENERATED_VIDEO",
        project_id=ids["editorb_project"],
        created_by=ids["editorb_user"],
        data=b"sibling-generation-output",
        metadata={"kind": "VIDEO", "has_video": True},
    )
    async with session_factory() as session, session.begin():
        sibling = await session.get(SceneGeneration, sibling_id)
        requested = await session.get(SceneGeneration, ids["editorb_generation"])
        scene = await session.get(Scene, ids["editorb_scene"])
        assert sibling is not None and requested is not None and scene is not None
        sibling.output_asset_id = output_id
        requested.output_asset_id = output_id
        revision = scene.revision

    storage = RecordingAssetStore()
    async with api_client(session_factory, tmp_path, storage) as client:
        await login(client, "editorb@ownership.test")
        response = await client.post(
            f"/api/v1/scenes/{ids['editorb_scene']}/select-generation",
            headers={"If-Match": str(revision)},
            json={"generation_id": ids["editorb_generation"]},
        )
        assert response.status_code == 404
    assert storage.downloads == []


@pytest.mark.asyncio
async def test_asset_creator_cannot_override_foreign_parent_scope(session_factory, tmp_path):
    ids = await seed_world(session_factory)
    async with session_factory() as session, session.begin():
        asset = Asset(
            project_id=ids["editora_project"],
            kind="VIDEO",
            role="GENERATED_VIDEO",
            filename="misbound.mp4",
            content_type="video/mp4",
            object_key="ownership/misbound.mp4",
            status="READY",
            size_bytes=20,
            checksum="9" * 64,
            created_by=ids["editorb_user"],
        )
        session.add(asset)
        await session.flush()
        asset_id = asset.id

    storage = RecordingAssetStore()
    async with api_client(session_factory, tmp_path, storage) as client:
        await login(client, "editorb@ownership.test")
        assert (await client.get(f"/api/v1/assets/{asset_id}")).status_code == 404
        assert (await client.get(f"/api/v1/assets/{asset_id}/download")).status_code == 404
        assert storage.downloads == []


@pytest.mark.asyncio
async def test_editor_created_asset_cannot_claim_another_editors_project(
    session_factory, tmp_path
):
    ids = await seed_world(session_factory)
    async with session_factory() as session, session.begin():
        asset = Asset(
            project_id=ids["editorb_project"],
            resource_owner_id=ids["editorb_user"],
            ownership_provenance="OWNER",
            kind="IMAGE",
            role="PROJECT_REFERENCE",
            filename="spoofed.png",
            content_type="image/png",
            object_key="ownership/spoofed.png",
            status="READY",
            size_bytes=12,
            checksum="6" * 64,
            created_by=ids["editora_user"],
        )
        session.add(asset)
        await session.flush()
        asset_id = asset.id

    storage = RecordingAssetStore()
    async with api_client(session_factory, tmp_path, storage) as client:
        await login(client, "editorb@ownership.test")
        assert (await client.get(f"/api/v1/assets/{asset_id}")).status_code == 404
        assert (await client.get(f"/api/v1/assets/{asset_id}/download")).status_code == 404
        await login(client, "editora@ownership.test")
        assert (await client.get(f"/api/v1/assets/{asset_id}")).status_code == 404
    assert storage.downloads == []


@pytest.mark.asyncio
async def test_direct_output_asset_access_requires_a_matching_parent_video(
    session_factory, tmp_path
):
    ids = await seed_world(session_factory)
    async with session_factory() as session, session.begin():
        generated = Asset(
            project_id=ids["editorb_project"],
            kind="VIDEO",
            role="GENERATED_VIDEO",
            filename="foreign-generation.mp4",
            content_type="video/mp4",
            object_key="ownership/foreign-generation.mp4",
            status="READY",
            size_bytes=32,
            checksum="8" * 64,
            created_by=ids["admin_user"],
        )
        final_output = Asset(
            project_id=ids["editorb_project"],
            kind="VIDEO",
            role="FINAL_VIDEO",
            filename="foreign-final.mp4",
            content_type="video/mp4",
            object_key="ownership/foreign-final.mp4",
            status="READY",
            size_bytes=32,
            checksum="7" * 64,
            created_by=ids["admin_user"],
        )
        session.add_all([generated, final_output])
        await session.flush()
        generation = await session.get(SceneGeneration, ids["editora_generation"])
        final = await session.get(FinalVideo, ids["editora_final"])
        generation.output_asset_id = generated.id
        final.output_asset_id = final_output.id
        generated_id, final_output_id = generated.id, final_output.id

    storage = RecordingAssetStore()
    async with api_client(session_factory, tmp_path, storage) as client:
        await login(client, "editorb@ownership.test")
        for asset_id in (generated_id, final_output_id):
            assert (await client.get(f"/api/v1/assets/{asset_id}")).status_code == 404
            assert (await client.get(f"/api/v1/assets/{asset_id}/download")).status_code == 404
    assert storage.downloads == []


@pytest.mark.asyncio
async def test_final_output_from_another_project_of_same_owner_is_hidden(
    session_factory, tmp_path
):
    ids = await seed_world(session_factory)
    async with session_factory() as session, session.begin():
        other_project = Project(name="Second B Project", created_by=ids["editorb_user"])
        session.add(other_project)
        await session.flush()
        project_id = other_project.id
        final = await session.get(FinalVideo, ids["editorb_final"])
        assert final is not None
        final.created_by = ids["admin_user"]
        final_id = final.id

    # A current worker refuses this binding before uploading. Seed a historical
    # corrupt row directly to keep testing the separate download boundary.
    async with session_factory() as session, session.begin():
        output = Asset(
            project_id=project_id,
            resource_owner_id=ids["editorb_user"],
            ownership_provenance="VIDEO_PARENT",
            created_by=ids["admin_user"],
            role="FINAL_VIDEO", kind="VIDEO", status="READY",
            filename="misbound.mp4", content_type="video/mp4",
            object_key="ownership/misbound-final.mp4", size_bytes=20,
            checksum="a" * 64,
        )
        session.add(output)
        await session.flush()
        output_id = output.id
        final = await session.get(FinalVideo, final_id)
        final.output_asset_id = output_id
        final.status = "READY"

    storage = RecordingAssetStore()
    async with api_client(session_factory, tmp_path, storage) as client:
        await login(client, "editorb@ownership.test")
        response = await client.get(f"/api/v1/final-versions/{final_id}/download")
    assert response.status_code == 404
    assert storage.downloads == []


@pytest.mark.asyncio
@pytest.mark.parametrize("asset_status", ["PENDING_UPLOAD", "DELETED"])
async def test_bound_unavailable_final_asset_never_mints_download_urls(
    session_factory, tmp_path, asset_status
):
    ids = await seed_world(session_factory)
    worker_store = WorkerOutputStore()
    final_id = ids["editorb_final"]
    output_id = await save_output(
        session_factory,
        worker_store,
        owner_id=final_id,
        role="FINAL_VIDEO",
        project_id=ids["editorb_project"],
        created_by=ids["admin_user"],
        data=b"temporarily-unavailable-final-output",
        metadata={"kind": "VIDEO", "has_video": True},
    )
    async with session_factory() as session, session.begin():
        asset = await session.get(Asset, output_id)
        final = await session.get(FinalVideo, final_id)
        assert asset is not None and final is not None
        asset.status = asset_status
        if asset_status == "DELETED":
            asset.deleted_at = utcnow()
        final.output_asset_id = output_id
        final.status = "READY"

    storage = RecordingAssetStore()
    async with api_client(session_factory, tmp_path, storage) as client:
        await login(client, "editorb@ownership.test")
        final_response = await client.get(f"/api/v1/final-versions/{final_id}/download")
        assert final_response.status_code == 409
        asset_response = await client.get(f"/api/v1/assets/{output_id}/download")
        expected_asset_status = 404 if asset_status == "DELETED" else 409
        assert asset_response.status_code == expected_asset_status
    assert storage.downloads == []


@pytest.mark.asyncio
async def test_worker_output_access_follows_video_project_and_keeps_actor_audit(
    session_factory, tmp_path
):
    ids = await seed_world(session_factory)
    async with session_factory() as session, session.begin():
        generation = await session.get(SceneGeneration, ids["editorb_generation"])
        assert generation is not None
        generation.created_by = ids["admin_user"]
        generation_id = generation.id

    worker_store = WorkerOutputStore()
    output_id = await save_output(
        session_factory,
        worker_store,
        owner_id=generation_id,
        role="GENERATED_VIDEO",
        project_id=ids["editorb_project"],
        created_by=ids["admin_user"],
        data=b"worker-generated-video",
        metadata={"kind": "VIDEO", "has_video": True},
    )
    async with session_factory() as session, session.begin():
        generation = await session.get(SceneGeneration, generation_id)
        generation.status = "COMPLETED"
        generation.phase = "COMPLETED"
        generation.output_asset_id = output_id
        scene = await session.get(Scene, ids["editorb_scene"])
        scene.selected_generation_id = generation_id

    async with session_factory() as session:
        asset = await session.get(Asset, output_id)
        generation = await session.get(SceneGeneration, generation_id)
        assert asset is not None and asset.status == "READY"
        assert asset.created_by == ids["admin_user"]
        assert asset.project_id == ids["editorb_project"]
        assert asset.resource_owner_id == ids["editorb_user"]
        assert asset.ownership_provenance == "VIDEO_PARENT"
        output_download = (asset.object_key, asset.filename)
        assert generation is not None and generation.created_by == ids["admin_user"]

    storage = RecordingAssetStore()
    async with api_client(session_factory, tmp_path, storage) as client:
        await login(client, "editora@ownership.test")
        assert (await client.get(f"/api/v1/assets/{output_id}")).status_code == 404
        assert (await client.get(f"/api/v1/assets/{output_id}/download")).status_code == 404
        assert storage.downloads == []

        await login(client, "editorb@ownership.test")
        assert (await client.get(f"/api/v1/assets/{output_id}")).status_code == 200
        assert (await client.get(f"/api/v1/assets/{output_id}/download")).status_code == 200
        assert storage.downloads == [output_download]

        await login(client, "admin@ownership.test")
        assert (await client.get(f"/api/v1/assets/{output_id}")).status_code == 200


@pytest.mark.asyncio
async def test_director_run_intermediate_output_keeps_video_owner_after_actor_disable(
    session_factory, tmp_path
):
    ids = await seed_world(session_factory)
    async with session_factory() as session, session.begin():
        run = DirectorRun(
            video_id=ids["editorb_video"],
            workflow_id=ids["editorb_workflow"],
            task="t2v",
            status="COMPLETED",
            input_snapshot={},
            created_by=ids["admin_user"],
        )
        session.add(run)
        await session.flush()
        run_id = run.id

    worker_store = WorkerOutputStore()
    output_id = await save_output(
        session_factory,
        worker_store,
        owner_id=f"{run_id}-intermediate",
        role="GENERATED_VIDEO",
        project_id=ids["editorb_project"],
        created_by=ids["admin_user"],
        data=b"director-intermediate-for-editor-b",
        metadata={"kind": "VIDEO", "has_video": True},
    )
    async with session_factory() as session, session.begin():
        session.add(
            DirectorRunMember(
                director_run_id=run_id,
                scene_id=ids["editorb_scene"],
                scene_generation_id=ids["editorb_generation"],
                member_index=0,
                continuity="CUT",
                status="COMPLETED",
                output_asset_id=output_id,
            )
        )
        session.add(
            GenerationAsset(
                generation_id=ids["editorb_generation"],
                asset_id=output_id,
                role="DIRECTOR_INTERMEDIATE",
                order_index=0,
            )
        )
        admin = await session.get(User, ids["admin_user"])
        assert admin is not None
        admin.role = "EDITOR"
        admin.is_active = False

    storage = RecordingAssetStore()
    async with api_client(session_factory, tmp_path, storage) as client:
        await login(client, "editorb@ownership.test")
        assert (await client.get(f"/api/v1/assets/{output_id}")).status_code == 200
        assert (await client.get(f"/api/v1/assets/{output_id}/download")).status_code == 200
        await login(client, "editora@ownership.test")
        assert (await client.get(f"/api/v1/assets/{output_id}")).status_code == 404
        await login(client, "adminy@ownership.test")
        assert (await client.get(f"/api/v1/assets/{output_id}")).status_code == 200

    async with session_factory() as session:
        asset = await session.get(Asset, output_id)
        assert asset is not None
        assert asset.created_by == ids["admin_user"]
        assert asset.resource_owner_id == ids["editorb_user"]
        assert asset.ownership_provenance == "VIDEO_PARENT"


@pytest.mark.asyncio
async def test_admin_generation_output_is_usable_only_in_video_owner_scope(
    session_factory, tmp_path
):
    ids = await seed_world(session_factory)
    graph, slots, profile, approved = director_workflow_data()
    async with session_factory() as session, session.begin():
        workflow = await session.get(WorkflowRecord, ids["editorb_workflow"])
        assert workflow is not None
        workflow.workflow = graph
        workflow.slots = slots
        workflow.required_slots = list(slots)
        workflow.profile = profile
        workflow.workflow_hash = approved.workflow_hash
        workflow.slot_map_hash = approved.slot_map_hash
        workflow.enabled = True

    storage = RecordingAssetStore()
    generation_payload = {
        "workflow_id": ids["editorb_workflow"],
        "mode": "t2v",
        "aspect_ratio": "9:16",
        "width": 480,
        "height": 864,
        "quality_profile": "STANDARD",
        "seed_policy": "FIXED",
        "seed": 17,
    }
    replay_key = "editor-a-cannot-replay-video-b-generation"
    async with api_client(session_factory, tmp_path, storage) as client:
        await login(client, "admin@ownership.test")
        created = await client.post(
            f"/api/v1/scenes/{ids['editorb_scene']}/generations",
            headers={"Idempotency-Key": "admin-generation-for-editor-b"},
            json=generation_payload,
        )
        assert created.status_code == 202, created.text
        generation_id = created.json()["id"]

        async with session_factory() as session:
            generation = await session.get(SceneGeneration, generation_id)
            video = await session.get(Video, ids["editorb_video"])
            assert generation is not None and video is not None
            assert generation.created_by == ids["admin_user"]
            assert generation.video_id == video.id
            project_id = video.project_id

        worker_store = WorkerOutputStore()
        output_id = await save_output(
            session_factory,
            worker_store,
            owner_id=generation_id,
            role="GENERATED_VIDEO",
            project_id=project_id,
            created_by=ids["admin_user"],
            data=b"admin-worker-output-for-editor-b",
            metadata={"kind": "VIDEO", "has_video": True},
        )
        async with session_factory() as session, session.begin():
            generation = await session.get(SceneGeneration, generation_id)
            assert generation is not None
            generation.status = "COMPLETED"
            generation.phase = "COMPLETED"
            generation.output_asset_id = output_id
            generation.finished_at = utcnow()

        async with session_factory() as session, session.begin():
            response_row = await session.get(SceneGeneration, generation_id)
            session.add(
                IdempotencyKey(
                    user_id=ids["editora_user"],
                    operation=f"create-generation:{ids['editorb_scene']}",
                    key=replay_key,
                    request_hash=payload_hash(
                        GenerationRequest.model_validate(generation_payload).model_dump(
                            mode="json", exclude_unset=True
                        )
                    ),
                    response=created.json(),
                    status_code=202,
                    expires_at=utcnow() + timedelta(hours=1),
                )
            )
            assert response_row is not None

        await login(client, "editorb@ownership.test")
        generation_response = await client.get(f"/api/v1/generations/{generation_id}")
        assert generation_response.status_code == 200
        assert generation_response.json()["output_asset_id"] == output_id
        assert (await client.get(f"/api/v1/assets/{output_id}")).status_code == 200
        generation_download = await client.get(f"/api/v1/assets/{output_id}/download")
        assert generation_download.status_code == 200

        async with session_factory() as session:
            scene = await session.get(Scene, ids["editorb_scene"])
            assert scene is not None
            scene_revision = scene.revision
        selected = await client.post(
            f"/api/v1/scenes/{ids['editorb_scene']}/select-generation",
            headers={"If-Match": str(scene_revision)},
            json={"generation_id": generation_id},
        )
        assert selected.status_code == 200, selected.text

        async with session_factory() as session:
            video = await session.get(Video, ids["editorb_video"])
            assert video is not None
            video_revision = video.revision
        assembled = await client.post(
            f"/api/v1/videos/{ids['editorb_video']}/assemble",
            headers={
                "If-Match": str(video_revision),
                "Idempotency-Key": "editor-b-assembles-admin-output",
            },
            json={},
        )
        assert assembled.status_code == 202, assembled.text
        final_id = assembled.json()["id"]

        async with session_factory() as session:
            final = await session.get(FinalVideo, final_id)
            assert final is not None
            assert final.video_id == ids["editorb_video"]
            assert final.created_by == ids["editorb_user"]
        final_asset_id = await save_output(
            session_factory,
            worker_store,
            owner_id=final_id,
            role="FINAL_VIDEO",
            project_id=project_id,
            created_by=ids["admin_user"],
            data=b"editor-b-final-video",
            metadata={"kind": "VIDEO", "has_video": True},
        )
        async with session_factory() as session, session.begin():
            final = await session.get(FinalVideo, final_id)
            final.output_asset_id = final_asset_id
            final.status = "READY"
            final.phase = "READY"
            final.finished_at = utcnow()

        assert (await client.get(f"/api/v1/final-versions/{final_id}")).status_code == 200
        final_download = await client.get(f"/api/v1/final-versions/{final_id}/download")
        assert final_download.status_code == 200

        async with session_factory() as session:
            output_asset = await session.get(Asset, output_id)
            final_asset = await session.get(Asset, final_asset_id)
            assert output_asset is not None and output_asset.created_by == ids["admin_user"]
            assert output_asset.project_id == project_id
            assert final_asset is not None and final_asset.created_by == ids["admin_user"]
            assert final_asset.project_id == project_id
            assert final_asset.resource_owner_id == ids["editorb_user"]
            assert final_asset.ownership_provenance == "VIDEO_PARENT"

        async with session_factory() as session, session.begin():
            admin = await session.get(User, ids["admin_user"])
            assert admin is not None
            admin.role = "EDITOR"
            admin.is_active = False

        assert (await client.get(f"/api/v1/assets/{output_id}")).status_code == 200
        assert (await client.get(f"/api/v1/assets/{output_id}/download")).status_code == 200
        assert (await client.get(f"/api/v1/assets/{final_asset_id}")).status_code == 200
        assert (await client.get(f"/api/v1/assets/{final_asset_id}/download")).status_code == 200

        # Simulate an unbackfilled historical row: actor attribution remains,
        # but the new stable ownership columns are absent.
        async with session_factory() as session, session.begin():
            for asset_id in (output_id, final_asset_id):
                legacy_asset = await session.get(Asset, asset_id)
                assert legacy_asset is not None
                legacy_asset.resource_owner_id = None
                legacy_asset.ownership_provenance = None
                assert legacy_asset.created_by == ids["admin_user"]

        downloads_before_legacy_requests = len(storage.downloads)
        assert (await client.get(f"/api/v1/assets/{output_id}")).status_code == 404
        assert (await client.get(f"/api/v1/assets/{output_id}/download")).status_code == 404
        assert (await client.get(f"/api/v1/assets/{final_asset_id}")).status_code == 404
        assert (
            await client.get(f"/api/v1/assets/{final_asset_id}/download")
        ).status_code == 404
        assert len(storage.downloads) == downloads_before_legacy_requests

        downloads_before_foreign_requests = len(storage.downloads)
        async with session_factory() as session:
            generations_before = await session.scalar(
                select(func.count()).select_from(SceneGeneration)
            )
            idempotency_before = await session.scalar(
                select(func.count()).select_from(IdempotencyKey)
            )
        await login(client, "editora@ownership.test")
        assert (await client.get(f"/api/v1/generations/{generation_id}")).status_code == 404
        assert (await client.get(f"/api/v1/assets/{output_id}")).status_code == 404
        assert (await client.get(f"/api/v1/assets/{output_id}/download")).status_code == 404
        assert (
            await client.get(f"/api/v1/final-versions/{final_id}/download")
        ).status_code == 404
        generation_replay = await client.post(
            f"/api/v1/scenes/{ids['editorb_scene']}/generations",
            headers={"Idempotency-Key": replay_key},
            json=generation_payload,
        )
        assert generation_replay.status_code == 404
        assert len(storage.downloads) == downloads_before_foreign_requests
        async with session_factory() as session:
            assert (
                await session.scalar(select(func.count()).select_from(SceneGeneration))
                == generations_before
            )
            assert (
                await session.scalar(select(func.count()).select_from(IdempotencyKey))
                == idempotency_before
            )

        await login(client, "adminy@ownership.test")
        assert (await client.get(f"/api/v1/generations/{generation_id}")).status_code == 200
        assert (await client.get(f"/api/v1/assets/{output_id}")).status_code == 200
        assert (await client.get(f"/api/v1/assets/{output_id}/download")).status_code == 200
        assert (await client.get(f"/api/v1/assets/{final_asset_id}")).status_code == 200
        assert (await client.get(f"/api/v1/final-versions/{final_id}/download")).status_code == 200


@pytest.mark.asyncio
async def test_upload_intent_rejects_foreign_project_and_product_before_claim_or_presign(
    session_factory, tmp_path
):
    ids = await seed_world(session_factory)
    storage = RecordingAssetStore()
    async with api_client(session_factory, tmp_path, storage) as client:
        await login(client, "editora@ownership.test")
        async with session_factory() as session:
            before_assets = await session.scalar(select(func.count()).select_from(Asset))
        for key, scope in (
            ("foreign-project", {"project_id": ids["editorb_project"], "product_id": None,
                                 "role": "PROJECT_REFERENCE"}),
            ("foreign-product", {"project_id": None, "product_id": ids["editorb_product"],
                                 "role": "PRODUCT_IMAGE"}),
        ):
            response = await client.post(
                "/api/v1/assets/upload-url",
                headers={"Idempotency-Key": key},
                json={
                    **scope,
                    "filename": "reference.png",
                    "content_type": "image/png",
                    "size_bytes": 12,
                },
            )
            assert response.status_code == 404, response.text

    async with session_factory() as session:
        after_assets = await session.scalar(select(func.count()).select_from(Asset))
        idempotency_count = await session.scalar(
            select(func.count()).select_from(IdempotencyKey)
        )
    assert after_assets == before_assets
    assert idempotency_count == 0
    assert storage.uploads == []


@pytest.mark.asyncio
async def test_upload_idempotency_replay_rechecks_asset_ownership(session_factory, tmp_path):
    ids = await seed_world(session_factory)
    storage = RecordingAssetStore()
    key = "replay-with-foreign-asset"
    request = UploadRequest(
        project_id=ids["editora_project"],
        filename="reference.png",
        content_type="image/png",
        size_bytes=12,
        role="PROJECT_REFERENCE",
    )
    async with session_factory() as session, session.begin():
        session.add(
            IdempotencyKey(
                user_id=ids["editora_user"],
                operation="asset-upload-url",
                key=key,
                request_hash=payload_hash(request.model_dump(mode="json")),
                response={"asset_id": ids["editorb_asset"]},
                status_code=201,
                expires_at=utcnow() + timedelta(hours=1),
            )
        )

    async with api_client(session_factory, tmp_path, storage) as client:
        await login(client, "editora@ownership.test")
        response = await client.post(
            "/api/v1/assets/upload-url",
            headers={"Idempotency-Key": key},
            json=request.model_dump(mode="json"),
        )
    assert response.status_code == 404
    assert storage.uploads == []


@pytest.mark.asyncio
async def test_assembly_routes_scope_video_final_versions_and_background_asset(
    session_factory, tmp_path, monkeypatch
):
    ids = await seed_world(session_factory)
    storage = RecordingAssetStore()
    calls: list[tuple[str, str, bool]] = []

    generation_id = ids["editorb_generation"]
    async with session_factory() as session, session.begin():
        generation = await session.get(SceneGeneration, generation_id)
        assert generation is not None
        generation.created_by = ids["admin_user"]
        final = await session.get(FinalVideo, ids["editorb_final"])
        assert final is not None
        final.created_by = ids["admin_user"]
        final_id = final.id
    worker_store = WorkerOutputStore()
    generation_asset_id = await save_output(
        session_factory,
        worker_store,
        owner_id=generation_id,
        role="GENERATED_VIDEO",
        project_id=ids["editorb_project"],
        created_by=ids["admin_user"],
        data=b"admin-generated-video-for-editor-b",
        metadata={"kind": "VIDEO", "has_video": True},
    )
    async with session_factory() as session, session.begin():
        generation = await session.get(SceneGeneration, generation_id)
        generation.output_asset_id = generation_asset_id
    final_asset_id = await save_output(
        session_factory,
        worker_store,
        owner_id=final_id,
        role="FINAL_VIDEO",
        project_id=ids["editorb_project"],
        created_by=ids["admin_user"],
        data=b"admin-assembled-video-for-editor-b",
        metadata={"kind": "VIDEO", "has_video": True},
    )
    async with session_factory() as session, session.begin():
        final = await session.get(FinalVideo, final_id)
        final.output_asset_id = final_asset_id
    async with session_factory() as session:
        final_asset = await session.get(Asset, final_asset_id)
        final_download = (final_asset.object_key, final_asset.filename)

    async def fake_create(
        self,
        session,
        *,
        video_id,
        request,
        expected_revision,
        user_id,
        request_id,
        is_admin=False,
    ):
        calls.append((video_id, user_id, is_admin))
        final_id = (
            ids["editorb_final"]
            if video_id == ids["editorb_video"]
            else ids["editora_final"]
        )
        return await session.get(FinalVideo, final_id)

    monkeypatch.setattr(AssemblyService, "create", fake_create)
    async with api_client(session_factory, tmp_path, storage) as client:
        await login(client, "editora@ownership.test")
        own_versions = await client.get(
            f"/api/v1/videos/{ids['editora_video']}/final-versions"
        )
        assert own_versions.status_code == 200
        assert [row["id"] for row in own_versions.json()] == [ids["editora_final"]]

        assert (
            await client.get(f"/api/v1/videos/{ids['editorb_video']}/final-versions")
        ).status_code == 404
        assert (
            await client.get(f"/api/v1/final-versions/{ids['editorb_final']}")
        ).status_code == 404
        assert (
            await client.get(f"/api/v1/final-versions/{ids['editorb_final']}/download")
        ).status_code == 404
        assert (
            await client.post(f"/api/v1/final-versions/{ids['editorb_final']}/cancel")
        ).status_code == 404

        foreign_video = await client.post(
            f"/api/v1/videos/{ids['editorb_video']}/assemble",
            headers={"If-Match": "1", "Idempotency-Key": "foreign-video"},
            json={},
        )
        assert foreign_video.status_code == 404
        foreign_audio = await client.post(
            f"/api/v1/videos/{ids['editora_video']}/assemble",
            headers={"If-Match": "1", "Idempotency-Key": "foreign-audio"},
            json={"background_audio_asset_id": ids["editorb_asset"]},
        )
        assert foreign_audio.status_code == 404
        assert calls == []
        assert storage.downloads == []

        await login(client, "editorb@ownership.test")
        assert (
            await client.get(f"/api/v1/final-versions/{ids['editorb_final']}")
        ).status_code == 200
        assert (
            await client.get(f"/api/v1/final-versions/{ids['editorb_final']}/download")
        ).status_code == 200

        await login(client, "admin@ownership.test")
        assert (
            await client.get(f"/api/v1/final-versions/{ids['editorb_final']}")
        ).status_code == 200
        assert (
            await client.get(f"/api/v1/final-versions/{ids['editorb_final']}/download")
        ).status_code == 200
        assembled = await client.post(
            f"/api/v1/videos/{ids['editorb_video']}/assemble",
            headers={"If-Match": "1", "Idempotency-Key": "admin-assembly"},
            json={},
        )
        assert assembled.status_code == 202, assembled.text
        assert calls == [(ids["editorb_video"], ids["admin_user"], True)]
        assert storage.downloads == [
            final_download,
            final_download,
        ]

    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(IdempotencyKey)) == 1
        final = await session.get(FinalVideo, ids["editorb_final"])
        assert final.status == "READY"


@pytest.mark.asyncio
async def test_assembly_replay_rechecks_final_parent_ownership(session_factory, tmp_path):
    ids = await seed_world(session_factory)
    key = "foreign-final-replay"
    request = AssemblyRequest()
    async with session_factory() as session, session.begin():
        session.add(
            IdempotencyKey(
                user_id=ids["editora_user"],
                operation=f"assemble:{ids['editora_video']}",
                key=key,
                request_hash=payload_hash({"revision": 1, **request.model_dump(mode="json")}),
                response={"id": ids["editorb_final"], "video_id": ids["editorb_video"]},
                status_code=202,
                expires_at=utcnow() + timedelta(hours=1),
            )
        )

    async with api_client(session_factory, tmp_path, RecordingAssetStore()) as client:
        await login(client, "editora@ownership.test")
        response = await client.post(
            f"/api/v1/videos/{ids['editora_video']}/assemble",
            headers={"If-Match": "1", "Idempotency-Key": key},
            json={},
        )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_assembly_preflight_rejects_foreign_output_before_idempotency_or_final_write(
    session_factory, tmp_path
):
    ids = await seed_world(session_factory)
    async with session_factory() as session, session.begin():
        other_project = Project(name="Second A Project", created_by=ids["editora_user"])
        session.add(other_project)
        await session.flush()
        unrelated_output = Asset(
            project_id=other_project.id,
            kind="VIDEO",
            role="GENERATED_VIDEO",
            filename="other-project-output.mp4",
            content_type="video/mp4",
            object_key="ownership/other-project-output.mp4",
            status="READY",
            size_bytes=20,
            checksum="8" * 64,
            created_by=ids["editora_user"],
        )
        session.add(unrelated_output)
        await session.flush()
        generation = await session.get(SceneGeneration, ids["editora_generation"])
        generation.output_asset_id = unrelated_output.id

    async with session_factory() as session:
        with pytest.raises(AppError) as error:
            await AssemblyService().authorize_inputs(
                session,
                video_id=ids["editora_video"],
                request=AssemblyRequest(),
                user_id=ids["editora_user"],
            )
    assert error.value.code == "ASSET_NOT_FOUND"
    async with session_factory() as session:
        idempotency_before = await session.scalar(
            select(func.count()).select_from(IdempotencyKey)
        )
        finals_before = await session.scalar(select(func.count()).select_from(FinalVideo))

    async with api_client(session_factory, tmp_path, RecordingAssetStore()) as client:
        await login(client, "editora@ownership.test")
        response = await client.post(
            f"/api/v1/videos/{ids['editora_video']}/assemble",
            headers={"If-Match": "1", "Idempotency-Key": "foreign-output-asset"},
            json={},
        )
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "ASSET_NOT_FOUND"

    async with session_factory() as session:
        idempotency_after = await session.scalar(
            select(func.count()).select_from(IdempotencyKey)
        )
        finals_after = await session.scalar(select(func.count()).select_from(FinalVideo))
        video = await session.get(Video, ids["editora_video"])
    assert idempotency_after == idempotency_before == 0
    assert finals_after == finals_before == 2
    assert video.status == "DRAFT"

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select

from apps.api.app.api.deps import require_csrf, require_editor
from apps.api.app.db.models import (
    Asset,
    Brand,
    GenerationAsset,
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


@asynccontextmanager
async def ownership_client(session_factory, principal):
    async def session_override():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except BaseException:
                await session.rollback()
                raise

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[require_editor] = lambda: principal["value"]
    app.dependency_overrides[require_csrf] = lambda: principal["value"]
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.clear()


async def seed_ownership_graph(session_factory):
    async with session_factory() as session, session.begin():
        users = {
            role: User(
                email=f"{role.lower()}-{uuid4().hex}@example.test",
                name=role.title(),
                password_hash="unused-in-this-test",
                role=role,
            )
            for role in ("EDITOR_A", "EDITOR_B", "ADMIN")
        }
        # Store the database role separately from the fixture key.
        users["EDITOR_A"].role = "EDITOR"
        users["EDITOR_B"].role = "EDITOR"
        session.add_all(users.values())
        await session.flush()

        projects = {
            key: Project(name=f"Project {key}", created_by=user.id)
            for key, user in (("A", users["EDITOR_A"]), ("B", users["EDITOR_B"]))
        }
        brands = {
            key: Brand(name=f"Brand {key}", created_by=user.id)
            for key, user in (("A", users["EDITOR_A"]), ("B", users["EDITOR_B"]))
        }
        session.add_all([*projects.values(), *brands.values()])
        await session.flush()
        products = {
            key: Product(
                name=f"Product {key}",
                brand_id=brands[key].id,
                created_by=users[f"EDITOR_{key}"].id,
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
                title=f"Video {key}",
                brief=f"Brief {key}",
                kind="LONG_VIDEO",
                target_duration=30,
                aspect_ratio="16:9",
                created_by=users[f"EDITOR_{key}"].id,
            )
            for key in ("A", "B")
        }
        session.add_all(videos.values())
        await session.flush()
        scenes = {
            key: Scene(
                video_id=videos[key].id,
                scene_order=0,
                prompt=f"Scene {key}",
                duration_seconds=5,
            )
            for key in ("A", "B")
        }
        assets = {
            key: Asset(
                project_id=projects[key].id,
                kind="IMAGE",
                role="REFERENCE",
                filename=f"reference-{key}.png",
                content_type="image/png",
                object_key=f"ownership-test/{key}/{uuid4().hex}.png",
                status="READY",
                size_bytes=1,
                created_by=users[f"EDITOR_{key}"].id,
            )
            for key in ("A", "B")
        }
        session.add_all([*scenes.values(), *assets.values()])
        await session.flush()
        workflow = WorkflowRecord(
            code=f"OWNERSHIP-{uuid4().hex}",
            mode="t2v",
            version="1",
            workflow={},
            slots={},
            required_slots=[],
            profile={},
            workflow_hash="0" * 64,
            slot_map_hash="0" * 64,
            enabled=False,
            created_by=users["EDITOR_A"].id,
        )
        session.add(workflow)
        await session.flush()
        generations = {
            key: SceneGeneration(
                video_id=videos[key].id,
                scene_id=scenes[key].id,
                mode="t2v",
                workflow_id=workflow.id,
                generation_no=1,
                status="COMPLETED",
                phase="COMPLETED",
                input_snapshot={},
                output_asset_id=assets[key].id,
                created_by=users[f"EDITOR_{key}"].id,
            )
            for key in ("A", "B")
        }
        session.add_all(generations.values())
        await session.flush()
        return {
            "users": {key: user.id for key, user in users.items()},
            "projects": {key: project.id for key, project in projects.items()},
            "brands": {key: brand.id for key, brand in brands.items()},
            "products": {key: product.id for key, product in products.items()},
            "videos": {key: video.id for key, video in videos.items()},
            "scenes": {key: scene.id for key, scene in scenes.items()},
            "assets": {key: asset.id for key, asset in assets.items()},
            "generations": {key: generation.id for key, generation in generations.items()},
        }


async def row_count(session_factory, model, **filters):
    async with session_factory() as session:
        query = select(func.count()).select_from(model)
        for field, value in filters.items():
            query = query.where(getattr(model, field) == value)
        return await session.scalar(query)


@pytest.mark.asyncio
async def test_video_reads_updates_and_list_counts_are_scoped_by_owner(session_factory):
    data = await seed_ownership_graph(session_factory)
    principal = {"value": SimpleNamespace(id=data["users"]["EDITOR_A"], role="EDITOR")}
    async with ownership_client(session_factory, principal) as client:
        own_list = await client.get("/api/v1/videos")
        own_video = await client.get(f"/api/v1/videos/{data['videos']['A']}")
        foreign_video = await client.get(f"/api/v1/videos/{data['videos']['B']}")
        foreign_update = await client.patch(
            f"/api/v1/videos/{data['videos']['B']}",
            headers={"If-Match": "1"},
            json={"title": "stolen"},
        )

        assert own_list.status_code == 200
        assert own_list.json()["total"] == 1
        assert [item["id"] for item in own_list.json()["items"]] == [data["videos"]["A"]]
        assert own_video.status_code == 200
        assert foreign_video.status_code == 404
        assert foreign_update.status_code == 404

        principal["value"] = SimpleNamespace(id=data["users"]["EDITOR_B"], role="EDITOR")
        reverse = await client.get(f"/api/v1/videos/{data['videos']['A']}")
        assert reverse.status_code == 404

        principal["value"] = SimpleNamespace(id=data["users"]["ADMIN"], role="ADMIN")
        admin_list = await client.get("/api/v1/videos")
        admin_read = await client.get(f"/api/v1/videos/{data['videos']['A']}")
        admin_patch = await client.patch(
            f"/api/v1/videos/{data['videos']['B']}",
            headers={"If-Match": "1"},
            json={"title": "Admin updated"},
        )

    assert admin_list.status_code == 200
    assert admin_list.json()["total"] == 2
    assert admin_read.status_code == 200
    assert admin_patch.status_code == 200
    async with session_factory() as session:
        video = await session.get(Video, data["videos"]["B"])
        assert video.title == "Admin updated"


@pytest.mark.asyncio
@pytest.mark.parametrize("foreign_field", ["project_id", "product_id", "brand_id"])
async def test_create_video_rejects_foreign_business_references_before_locking(
    session_factory, monkeypatch, foreign_field
):
    from apps.api.app.api import videos as videos_api

    data = await seed_ownership_graph(session_factory)
    principal = {"value": SimpleNamespace(id=data["users"]["EDITOR_A"], role="EDITOR")}
    lock_calls = []
    lock_creation = videos_api.lock_creation_dependencies

    async def observe_lock(*args, **kwargs):
        lock_calls.append(True)
        return await lock_creation(*args, **kwargs)

    monkeypatch.setattr(videos_api, "lock_creation_dependencies", observe_lock)
    payload = {
        "project_id": data["projects"]["A"],
        "product_id": data["products"]["A"],
        "brand_id": data["brands"]["A"],
        "title": "New video",
        "kind": "QUICK_CLIP",
        "target_duration": 5,
        "aspect_ratio": "16:9",
        "brief": "A short brief",
    }
    resource_name = {
        "project_id": "projects",
        "product_id": "products",
        "brand_id": "brands",
    }[foreign_field]
    payload[foreign_field] = data[resource_name]["B"]

    async with ownership_client(session_factory, principal) as client:
        rejected = await client.post("/api/v1/videos", json=payload)
        assert rejected.status_code == 404
        assert lock_calls == []
        assert await row_count(session_factory, Video) == 2

        principal["value"] = SimpleNamespace(id=data["users"]["ADMIN"], role="ADMIN")
        admin_payload = {
            **payload,
            "project_id": data["projects"]["B"],
            "product_id": data["products"]["B"],
            "brand_id": data["brands"]["B"],
        }
        accepted = await client.post("/api/v1/videos", json=admin_payload)

    assert accepted.status_code == 201
    assert lock_calls == [True]
    assert await row_count(session_factory, Video) == 3


@pytest.mark.asyncio
async def test_scene_routes_check_parent_video_owner_and_preserve_admin_access(session_factory):
    data = await seed_ownership_graph(session_factory)
    principal = {"value": SimpleNamespace(id=data["users"]["EDITOR_A"], role="EDITOR")}
    async with ownership_client(session_factory, principal) as client:
        own_scenes = await client.get(f"/api/v1/videos/{data['videos']['A']}/scenes")
        foreign_scenes = await client.get(f"/api/v1/videos/{data['videos']['B']}/scenes")
        foreign_scene = await client.get(f"/api/v1/scenes/{data['scenes']['B']}")
        assert own_scenes.status_code == 200
        assert len(own_scenes.json()) == 1
        assert foreign_scenes.status_code == 404
        assert foreign_scene.status_code == 404

        principal["value"] = SimpleNamespace(id=data["users"]["EDITOR_B"], role="EDITOR")
        create_denied = await client.post(
            f"/api/v1/videos/{data['videos']['A']}/scenes",
            headers={"If-Match": "1"},
            json={"prompt": "intrusion"},
        )
        patch_denied = await client.patch(
            f"/api/v1/scenes/{data['scenes']['A']}",
            headers={"If-Match": "1"},
            json={"prompt": "intrusion"},
        )
        disable_denied = await client.post(
            f"/api/v1/scenes/{data['scenes']['A']}/disable",
            headers={"If-Match": "1"},
        )
        enable_denied = await client.post(
            f"/api/v1/scenes/{data['scenes']['A']}/enable",
            headers={"If-Match": "1"},
        )
        reorder_denied = await client.post(
            f"/api/v1/videos/{data['videos']['A']}/scenes/reorder",
            headers={"If-Match": "1"},
            json={"scene_ids": [data["scenes"]["A"]]},
        )
        assert create_denied.status_code == 404
        assert patch_denied.status_code == 404
        assert disable_denied.status_code == 404
        assert enable_denied.status_code == 404
        assert reorder_denied.status_code == 404
        async with session_factory() as session:
            scene = await session.get(Scene, data["scenes"]["A"])
            video = await session.get(Video, data["videos"]["A"])
            assert scene.prompt == "Scene A"
            assert scene.revision == 1
            assert video.revision == 1

        principal["value"] = SimpleNamespace(id=data["users"]["ADMIN"], role="ADMIN")
        admin_read = await client.get(f"/api/v1/scenes/{data['scenes']['A']}")
        admin_create = await client.post(
            f"/api/v1/videos/{data['videos']['B']}/scenes",
            headers={"If-Match": "1"},
            json={"prompt": "admin scene"},
        )

    assert admin_read.status_code == 200
    assert admin_create.status_code == 201
    assert await row_count(session_factory, Scene, video_id=data["videos"]["A"]) == 1
    assert await row_count(session_factory, Scene, video_id=data["videos"]["B"]) == 2


@pytest.mark.asyncio
async def test_scene_reference_assets_are_owner_checked_before_mutation(session_factory):
    data = await seed_ownership_graph(session_factory)
    principal = {"value": SimpleNamespace(id=data["users"]["EDITOR_A"], role="EDITOR")}
    async with ownership_client(session_factory, principal) as client:
        create_denied = await client.post(
            f"/api/v1/videos/{data['videos']['A']}/scenes",
            headers={"If-Match": "1"},
            json={
                "prompt": "foreign reference",
                "generation_config": {"first_frame_asset_id": data["assets"]["B"]},
            },
        )
        patch_denied = await client.patch(
            f"/api/v1/scenes/{data['scenes']['A']}",
            headers={"If-Match": "1"},
            json={"generation_config": {"reference_image_asset_ids": [data["assets"]["B"]]}},
        )
        assert create_denied.status_code == patch_denied.status_code == 404
        assert await row_count(session_factory, Scene, video_id=data["videos"]["A"]) == 1
        async with session_factory() as session:
            scene = await session.get(Scene, data["scenes"]["A"])
            assert scene.revision == 1
            assert scene.generation_config == {}

        principal["value"] = SimpleNamespace(id=data["users"]["ADMIN"], role="ADMIN")
        admin_cross_owner_patch = await client.patch(
            f"/api/v1/scenes/{data['scenes']['A']}",
            headers={"If-Match": "1"},
            json={"generation_config": {"last_frame_asset_id": data["assets"]["B"]}},
        )
        admin_same_owner_patch = await client.patch(
            f"/api/v1/scenes/{data['scenes']['B']}",
            headers={"If-Match": "1"},
            json={"generation_config": {"last_frame_asset_id": data["assets"]["B"]}},
        )

    assert admin_cross_owner_patch.status_code == 404
    assert admin_same_owner_patch.status_code == 200
    async with session_factory() as session:
        scene_a = await session.get(Scene, data["scenes"]["A"])
        scene_b = await session.get(Scene, data["scenes"]["B"])
        assert scene_a.revision == 1
        assert scene_a.generation_config == {}
        assert scene_b.revision == 2
        assert scene_b.generation_config["last_frame_asset_id"] == data["assets"]["B"]


@pytest.mark.asyncio
async def test_scene_selection_rejects_foreign_generation_and_foreign_output_asset(session_factory):
    data = await seed_ownership_graph(session_factory)
    async with session_factory() as session, session.begin():
        foreign_output_generation = SceneGeneration(
            video_id=data["videos"]["A"],
            scene_id=data["scenes"]["A"],
            mode="t2v",
            workflow_id=(
                await session.scalar(select(WorkflowRecord.id).limit(1))
            ),
            generation_no=2,
            status="COMPLETED",
            phase="COMPLETED",
            input_snapshot={},
            output_asset_id=data["assets"]["B"],
            created_by=data["users"]["EDITOR_A"],
        )
        session.add(foreign_output_generation)
        await session.flush()
        foreign_output_generation_id = foreign_output_generation.id
        session.add(
            GenerationAsset(
                generation_id=data["generations"]["A"],
                asset_id=data["assets"]["B"],
                role="REFERENCE_IMAGE",
                order_index=0,
            )
        )
        await session.flush()

    principal = {"value": SimpleNamespace(id=data["users"]["EDITOR_A"], role="EDITOR")}
    async with ownership_client(session_factory, principal) as client:
        foreign_generation = await client.post(
            f"/api/v1/scenes/{data['scenes']['A']}/select-generation",
            headers={"If-Match": "1"},
            json={"generation_id": data["generations"]["B"]},
        )
        foreign_output = await client.post(
            f"/api/v1/scenes/{data['scenes']['A']}/select-generation",
            headers={"If-Match": "1"},
            json={"generation_id": foreign_output_generation_id},
        )
        foreign_reference = await client.post(
            f"/api/v1/scenes/{data['scenes']['A']}/select-generation",
            headers={"If-Match": "1"},
            json={"generation_id": data["generations"]["A"]},
        )

    assert foreign_generation.status_code == 404
    assert foreign_output.status_code == 404
    assert foreign_reference.status_code == 404
    async with session_factory() as session:
        scene = await session.get(Scene, data["scenes"]["A"])
        assert scene.selected_generation_id is None
        assert scene.revision == 1


@pytest.mark.asyncio
async def test_scene_selection_rejects_same_owner_intermediate_from_another_project(
    session_factory,
):
    data = await seed_ownership_graph(session_factory)
    async with session_factory() as session, session.begin():
        other_project = Project(
            name="Second Project A",
            created_by=data["users"]["EDITOR_A"],
        )
        session.add(other_project)
        await session.flush()
        artifact = Asset(
            project_id=other_project.id,
            kind="VIDEO",
            role="GENERATED_VIDEO",
            filename="intermediate.mp4",
            content_type="video/mp4",
            object_key=f"ownership-test/intermediate/{uuid4().hex}.mp4",
            status="READY",
            size_bytes=32,
            checksum="a" * 64,
            created_by=data["users"]["EDITOR_A"],
        )
        session.add(artifact)
        await session.flush()
        session.add(
            GenerationAsset(
                generation_id=data["generations"]["A"],
                asset_id=artifact.id,
                role="DIRECTOR_SEGMENT",
                order_index=0,
            )
        )

    principal = {"value": SimpleNamespace(id=data["users"]["EDITOR_A"], role="EDITOR")}
    async with ownership_client(session_factory, principal) as client:
        response = await client.post(
            f"/api/v1/scenes/{data['scenes']['A']}/select-generation",
            headers={"If-Match": "1"},
            json={"generation_id": data["generations"]["A"]},
        )
    assert response.status_code == 404
    async with session_factory() as session:
        scene = await session.get(Scene, data["scenes"]["A"])
        assert scene.selected_generation_id is None
        assert scene.revision == 1

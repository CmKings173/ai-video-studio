from __future__ import annotations

from uuid import NAMESPACE_URL, uuid5

import pytest
from sqlalchemy import func, select

from apps.api.app.api.ownership import get_video_output_asset
from apps.api.app.core.errors import AppError
from apps.api.app.db.models import Asset, FinalVideo, Scene, SceneGeneration, Video
from tests.integration.test_assets_assembly_ownership import (
    RecordingAssetStore,
    api_client,
    login,
    seed_world,
)


@pytest.mark.asyncio
async def test_unscoped_asset_with_conflicting_owner_is_hidden_without_download_url(
    session_factory, tmp_path
):
    ids = await seed_world(session_factory)
    async with session_factory() as session, session.begin():
        asset = Asset(
            kind="IMAGE",
            role="PROJECT_REFERENCE",
            filename="unscoped-owner-conflict.png",
            content_type="image/png",
            object_key="ownership/unscoped-owner-conflict.png",
            status="READY",
            size_bytes=12,
            checksum="c" * 64,
            created_by=ids["editora_user"],
            resource_owner_id=ids["editorb_user"],
            ownership_provenance="OWNER",
        )
        session.add(asset)
        await session.flush()
        asset_id = asset.id

    storage = RecordingAssetStore()
    async with api_client(session_factory, tmp_path, storage) as client:
        await login(client, "editora@ownership.test")
        read = await client.get(f"/api/v1/assets/{asset_id}")
        download = await client.get(f"/api/v1/assets/{asset_id}/download")

    assert read.status_code == 404
    assert download.status_code == 404
    assert storage.downloads == []


@pytest.mark.asyncio
async def test_legacy_generation_pointer_cannot_borrow_sibling_output(session_factory):
    ids = await seed_world(session_factory)
    async with session_factory() as session, session.begin():
        original = await session.get(SceneGeneration, ids["editora_generation"])
        assert original is not None
        scene = await session.get(Scene, ids["editora_scene"])
        assert scene is not None
        sibling = SceneGeneration(
            video_id=ids["editora_video"],
            scene_id=scene.id,
            mode=original.mode,
            workflow_id=original.workflow_id,
            generation_no=2,
            operation="ORIGINAL",
            status="COMPLETED",
            phase="COMPLETED",
            input_snapshot={},
            created_by=ids["editora_user"],
        )
        session.add(sibling)
        await session.flush()

        sibling_output_id = str(
            uuid5(NAMESPACE_URL, f"ai-video-studio:GENERATED_VIDEO:{sibling.id}")
        )
        session.add(
            Asset(
                id=sibling_output_id,
                project_id=ids["editora_project"],
                kind="VIDEO",
                role="GENERATED_VIDEO",
                filename="sibling-output.mp4",
                content_type="video/mp4",
                object_key="ownership/sibling-output.mp4",
                status="READY",
                size_bytes=32,
                checksum="d" * 64,
                created_by=ids["editora_user"],
            )
        )
        sibling.output_asset_id = sibling_output_id
        original.output_asset_id = sibling_output_id
        await session.flush()

    async with session_factory() as session:
        video = await session.get(Video, ids["editora_video"])
        assert video is not None
        with pytest.raises(AppError) as error:
            await get_video_output_asset(
                session,
                video,
                sibling_output_id,
                ids["editora_user"],
                role="GENERATED_VIDEO",
                generation_id=ids["editora_generation"],
            )
    assert error.value.status_code == 404
    assert error.value.code == "ASSET_NOT_FOUND"
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(FinalVideo)) == 2


@pytest.mark.asyncio
async def test_legacy_final_pointer_cannot_borrow_sibling_final_output(session_factory):
    ids = await seed_world(session_factory)
    async with session_factory() as session, session.begin():
        original = await session.get(FinalVideo, ids["editora_final"])
        assert original is not None
        sibling = FinalVideo(
            video_id=ids["editora_video"],
            version_no=2,
            status="READY",
            phase="READY",
            manifest={"video_id": ids["editora_video"]},
            manifest_hash="f" * 64,
            assembly_config={},
            created_by=ids["editora_user"],
        )
        session.add(sibling)
        await session.flush()

        sibling_output_id = str(uuid5(NAMESPACE_URL, f"ai-video-studio:FINAL_VIDEO:{sibling.id}"))
        session.add(
            Asset(
                id=sibling_output_id,
                project_id=ids["editora_project"],
                kind="VIDEO",
                role="FINAL_VIDEO",
                filename="sibling-final.mp4",
                content_type="video/mp4",
                object_key="ownership/sibling-final.mp4",
                status="READY",
                size_bytes=32,
                checksum="e" * 64,
                created_by=ids["editora_user"],
            )
        )
        sibling.output_asset_id = sibling_output_id
        original.output_asset_id = sibling_output_id
        await session.flush()

    async with session_factory() as session:
        video = await session.get(Video, ids["editora_video"])
        assert video is not None
        with pytest.raises(AppError) as error:
            await get_video_output_asset(
                session,
                video,
                sibling_output_id,
                ids["editora_user"],
                role="FINAL_VIDEO",
                final_video_id=ids["editora_final"],
            )
    assert error.value.status_code == 404
    assert error.value.code == "ASSET_NOT_FOUND"

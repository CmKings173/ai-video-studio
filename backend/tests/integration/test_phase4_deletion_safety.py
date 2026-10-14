"""Deletion safety against the isolated FK-enforced SQLite session fixture.

Cleanup only advances database state; no retention worker or object store runs.
"""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, func, select, text

from apps.api.app.core.errors import AppError
from apps.api.app.core.security import hash_password
from apps.api.app.db.models import (
    Asset,
    DeletionOperation,
    FinalVideo,
    Project,
    Scene,
    SceneGeneration,
    User,
    Video,
    WorkflowRecord,
    utcnow,
)
from apps.api.app.services.deletion_cleanup import DeletionCleanupService
from apps.api.app.services.final_version_deletion import (
    preview_final_video_deletion,
    request_final_video_deletion,
)
from apps.api.app.services.generation_deletion import (
    preview_generation_deletion,
    request_generation_deletion,
)
from apps.api.app.services.scene_deletion import (
    preview_scene_deletion,
    request_scene_deletion,
)
from apps.api.app.services.video_deletion import (
    preview_video_deletion,
    request_video_deletion,
)
from tests.integration.test_authz import api_client
from tests.postgres.test_asset_retention_concurrency import DATABASE_URL
from tests.postgres.test_asset_retention_concurrency import (
    retention_pg_factory as retention_pg_factory,
)


async def seed(session):
    user = User(email="deletion@example.test", password_hash="not-a-login-hash")
    session.add(user)
    await session.flush()
    project = Project(name="Deletion fixture", created_by=user.id)
    workflow = WorkflowRecord(
        code="DELETION_TEST",
        mode="t2v",
        version="1",
        workflow={},
        slots={},
        workflow_hash="a" * 64,
        slot_map_hash="b" * 64,
        created_by=user.id,
    )
    session.add_all([project, workflow])
    await session.flush()
    video = Video(project_id=project.id, title="Keep parent intact", created_by=user.id)
    session.add(video)
    await session.flush()
    scene = Scene(video_id=video.id, scene_order=1)
    session.add(scene)
    await session.flush()
    generation = SceneGeneration(
        video_id=video.id,
        scene_id=scene.id,
        workflow_id=workflow.id,
        mode="t2v",
        generation_no=1,
        status="COMPLETED",
        created_by=user.id,
    )
    session.add(generation)
    await session.flush()
    return user, project, video, scene, generation


async def assert_no_deletion_writes(session, video, scene):
    # Check before rollback: a savepoint rollback must not hide an early INSERT.
    await session.flush()
    assert await session.scalar(select(func.count()).select_from(DeletionOperation)) == 0
    await session.refresh(video)
    await session.refresh(scene)
    assert (video.revision, video.status, video.current_final_video_id) == (1, "DRAFT", None)
    assert (scene.revision, scene.enabled, scene.selected_generation_id) == (1, True, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("active_work", ["generation", "assembly"])
async def test_active_work_rejects_before_operation_or_parent_writes(session_factory, active_work):
    async with session_factory() as session, session.begin():
        user, _, video, scene, generation = await seed(session)
        if active_work == "generation":
            generation.status = "RUNNING"
        else:
            session.add(
                FinalVideo(
                    video_id=video.id,
                    version_no=1,
                    status="ASSEMBLING",
                    manifest={},
                    manifest_hash="c" * 64,
                    created_by=user.id,
                )
            )
        await session.flush()
        preview = await preview_video_deletion(session, video_id=video.id, user_id=user.id)
        assert preview["allowed"] is False
        assert preview["blockers"][0]["code"] == (
            "ACTIVE_GENERATIONS" if active_work == "generation" else "ACTIVE_FINAL_VIDEOS"
        )
        with pytest.raises(AppError) as caught:
            await request_video_deletion(
                session, video_id=video.id, user_id=user.id, expected_revision=1
            )
        assert (caught.value.code, caught.value.status_code) == ("VIDEO_DELETE_BLOCKED", 409)
        await assert_no_deletion_writes(session, video, scene)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["revision", "dependency"])
async def test_confirmation_rejects_changed_revision_or_dependencies(session_factory, change):
    async with session_factory() as session, session.begin():
        user, _, video, scene, generation = await seed(session)
        preview = await preview_video_deletion(session, video_id=video.id, user_id=user.id)
        assert preview["allowed"] is True
        if change == "revision":
            video.revision = 2
        else:
            generation.revision = 2
        await session.flush()
        with pytest.raises(AppError) as caught:
            await request_video_deletion(
                session,
                video_id=video.id,
                user_id=user.id,
                expected_revision=1,
                confirmation_token=preview["confirmation_token"],
            )
        assert caught.value.status_code == 412
        assert caught.value.code == (
            "REVISION_CONFLICT" if change == "revision" else "DELETION_CONFIRMATION_STALE"
        )
        await session.flush()
        assert await session.scalar(select(func.count()).select_from(DeletionOperation)) == 0
        await session.refresh(video)
        await session.refresh(scene)
        assert video.revision == (2 if change == "revision" else 1)
        assert video.status == "DRAFT"
        assert scene.revision == 1 and scene.enabled


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", ["scene", "generation", "final_video"])
async def test_child_deletion_previews_use_async_session_no_autoflush_safely(
    session_factory, resource
):
    async with session_factory() as session, session.begin():
        user, _, video, scene, generation = await seed(session)
        final_video = FinalVideo(
            video_id=video.id,
            version_no=1,
            status="READY",
            manifest={},
            manifest_hash="d" * 64,
            created_by=user.id,
        )
        session.add(final_video)
        await session.flush()

        if resource == "scene":
            preview = await preview_scene_deletion(session, scene_id=scene.id, user_id=user.id)
        elif resource == "generation":
            preview = await preview_generation_deletion(
                session, generation_id=generation.id, user_id=user.id
            )
        else:
            preview = await preview_final_video_deletion(
                session, final_video_id=final_video.id, user_id=user.id
            )

        assert isinstance(preview, dict)
        assert isinstance(preview.get("allowed"), bool)


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", ["video", "scene", "generation", "final_video"])
async def test_admin_can_delete_editor_resource_and_operation_tracks_actor_and_owner(
    session_factory, resource
):
    async with session_factory() as session, session.begin():
        owner, _, video, scene, generation = await seed(session)
        admin = User(
            email="deletion-admin@example.test",
            password_hash="not-a-login-hash",
            role="ADMIN",
        )
        session.add(admin)
        await session.flush()
        final_video = FinalVideo(
            video_id=video.id,
            version_no=1,
            status="READY",
            manifest={},
            manifest_hash="f" * 64,
            created_by=owner.id,
        )
        session.add(final_video)
        await session.flush()

        if resource == "video":
            preview = await preview_video_deletion(
                session, video_id=video.id, user_id=admin.id, is_admin=True
            )
            request = request_video_deletion
            identity = {"video_id": video.id}
        elif resource == "scene":
            preview = await preview_scene_deletion(
                session, scene_id=scene.id, user_id=admin.id, is_admin=True
            )
            request = request_scene_deletion
            identity = {"scene_id": scene.id}
        elif resource == "generation":
            preview = await preview_generation_deletion(
                session, generation_id=generation.id, user_id=admin.id, is_admin=True
            )
            request = request_generation_deletion
            identity = {"generation_id": generation.id}
        else:
            preview = await preview_final_video_deletion(
                session, final_video_id=final_video.id, user_id=admin.id, is_admin=True
            )
            request = request_final_video_deletion
            identity = {"final_video_id": final_video.id}

        assert preview["allowed"] is True
        operation = await request(
            session,
            **identity,
            user_id=admin.id,
            is_admin=True,
            expected_revision=preview["revision"],
            confirmation_token=preview["confirmation_token"],
        )
        assert operation.requested_by_id == admin.id
        assert operation.resource_owner_id == owner.id


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", ["video", "scene", "generation", "final_video"])
async def test_admin_deletion_http_routes_authorize_owner_resource_and_record_both_identities(
    session_factory, resource
):
    async with session_factory() as session, session.begin():
        owner, _, video, scene, generation = await seed(session)
        owner.password_hash = hash_password("editor-password-for-deletion")
        admin = User(
            email="deletion-admin@example.test",
            password_hash=hash_password("admin-password-for-deletion"),
            role="ADMIN",
        )
        outsider = User(
            email="deletion-outsider@example.test",
            password_hash=hash_password("outsider-password-for-deletion"),
            role="EDITOR",
        )
        session.add_all([admin, outsider])
        final_video = FinalVideo(
            video_id=video.id,
            version_no=1,
            status="READY",
            manifest={},
            manifest_hash="9" * 64,
            created_by=owner.id,
        )
        session.add(final_video)
        await session.flush()
        owner_id, admin_id = owner.id, admin.id
        resource_ids = {
            "video": video.id,
            "scene": scene.id,
            "generation": generation.id,
            "final_video": final_video.id,
        }

    resource_id = resource_ids[resource]
    routes = {
        "video": f"/api/v1/videos/{resource_id}",
        "scene": f"/api/v1/scenes/{resource_id}",
        "generation": f"/api/v1/generations/{resource_id}",
        "final_video": f"/api/v1/final-versions/{resource_id}",
    }
    route = routes[resource]
    async with api_client(session_factory) as client:
        login = await client.post(
            "/api/v1/auth/login",
            json={
                "email": "deletion-outsider@example.test",
                "password": "outsider-password-for-deletion",
            },
        )
        assert login.status_code == 200, login.text
        csrf = login.json()["csrf_token"]
        preview = await client.get(f"{route}/deletion-preview")
        assert preview.status_code == 404, preview.text
        denied = await client.request(
            "DELETE",
            route,
            json={"expected_revision": 1},
            headers={"X-CSRF-Token": csrf},
        )
        assert denied.status_code == 404, denied.text
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(DeletionOperation)) == 0

    async with api_client(session_factory) as client:
        login = await client.post(
            "/api/v1/auth/login",
            json={
                "email": "deletion-admin@example.test",
                "password": "admin-password-for-deletion",
            },
        )
        assert login.status_code == 200, login.text
        csrf = login.json()["csrf_token"]
        preview_response = await client.get(f"{route}/deletion-preview")
        assert preview_response.status_code == 200, preview_response.text
        preview = preview_response.json()
        assert preview["allowed"] is True
        deleted = await client.request(
            "DELETE",
            route,
            json={
                "expected_revision": preview["revision"],
                "confirmation_token": preview["confirmation_token"],
            },
            headers={"X-CSRF-Token": csrf},
        )
        assert deleted.status_code == 202, deleted.text
        operation_id = deleted.json()["id"]
        operation_url = f"/api/v1/deletion-operations/{operation_id}"
        assert (await client.get(operation_url)).status_code == 200

        owner_login = await client.post(
            "/api/v1/auth/login",
            json={"email": "deletion@example.test", "password": "editor-password-for-deletion"},
        )
        assert owner_login.status_code == 200, owner_login.text
        assert (await client.get(operation_url)).status_code == 200

        outsider_login = await client.post(
            "/api/v1/auth/login",
            json={
                "email": "deletion-outsider@example.test",
                "password": "outsider-password-for-deletion",
            },
        )
        assert outsider_login.status_code == 200, outsider_login.text
        assert (await client.get(operation_url)).status_code == 404

    async with session_factory() as session:
        operation = await session.get(DeletionOperation, operation_id)
        assert operation.requested_by_id == admin_id
        assert operation.resource_owner_id == owner_id


@pytest.mark.asyncio
@pytest.mark.parametrize("unsafe_output", ["live_reference", "wrong_project", "bad_provenance"])
async def test_cleanup_preserves_unsafe_output_and_records_blocked_state(
    session_factory, unsafe_output
):
    async with session_factory() as session, session.begin():
        user, project, video, _, generation = await seed(session)
        asset = Asset(
            project_id=project.id,
            resource_owner_id=user.id,
            ownership_provenance="VIDEO_PARENT",
            kind="VIDEO",
            role="GENERATED_VIDEO",
            filename="output.mp4",
            content_type="video/mp4",
            object_key="test-only/output.mp4",
            status="READY",
            created_by=user.id,
        )
        session.add(asset)
        await session.flush()
        generation.output_asset_id = asset.id
        await session.flush()
        # Seed an accepted operation independently of the request path, so a
        # preview failure cannot prevent exercising the real cleanup service.
        operation = DeletionOperation(
            resource_type="VIDEO",
            resource_id=video.id,
            video_id=video.id,
            resource_owner_id=user.id,
            requested_by_id=user.id,
            expected_revision=1,
            status="LOGICALLY_DELETED",
        )
        session.add(operation)
        await session.flush()
        assert operation.status == "LOGICALLY_DELETED"
        if unsafe_output == "bad_provenance":
            asset.ownership_provenance = None
        else:
            other_project = Project(name="Other", created_by=user.id)
            session.add(other_project)
            await session.flush()
            if unsafe_output == "wrong_project":
                asset.project_id = other_project.id
            else:
                # A separate visible video retains this output as a reference.
                other_video = Video(project_id=project.id, title="Live", created_by=user.id)
                session.add(other_video)
                await session.flush()
                other_scene = Scene(video_id=other_video.id, scene_order=1)
                session.add(other_scene)
                await session.flush()
                session.add(
                    SceneGeneration(
                        video_id=other_video.id,
                        scene_id=other_scene.id,
                        workflow_id=generation.workflow_id,
                        mode="t2v",
                        generation_no=1,
                        status="COMPLETED",
                        created_by=user.id,
                        output_asset_id=asset.id,
                    )
                )
        operation_id, asset_id, video_id = operation.id, asset.id, video.id

    cleanup = DeletionCleanupService(session_factory, SimpleNamespace())
    now = utcnow()
    claims = await cleanup._claim_batch(now)
    assert len(claims) == 1
    # Exercise the transaction boundary directly to expose the real exception
    # rather than run()'s intentionally generic transient-failure report.
    outcome = await cleanup._process(claims[0], now)
    expected = (
        "LIVE_REFERENCE_REMAINS"
        if unsafe_output == "live_reference"
        else "OUTPUT_PROVENANCE_UNRESOLVED"
    )
    assert outcome.status == "CLEANUP_BLOCKED"
    assert outcome.error_code == expected
    async with session_factory() as session:
        operation = await session.get(DeletionOperation, operation_id)
        asset = await session.get(Asset, asset_id)
        video = await session.get(Video, video_id)
        assert operation.status == "CLEANUP_BLOCKED"
        assert operation.last_error_code == expected
        assert operation.cleanup_completed_at is None
        assert operation.next_attempt_at is not None
        assert asset.status == "READY"
        assert asset.deleted_at is None and asset.purged_at is None
        assert video.revision == 1 and video.status == "DRAFT"


@pytest.mark.asyncio
@pytest.mark.parametrize("batch_size", [1, 2, 3])
@pytest.mark.parametrize(
    "unsafe_output,expected_code",
    [
        ("live_reference", "LIVE_REFERENCE_REMAINS"),
        ("bad_provenance", "OUTPUT_PROVENANCE_UNRESOLVED"),
        ("wrong_project", "OUTPUT_PROVENANCE_UNRESOLVED"),
        ("wrong_owner", "OUTPUT_PROVENANCE_UNRESOLVED"),
        ("wrong_kind", "OUTPUT_PROVENANCE_UNRESOLVED"),
        ("wrong_role", "OUTPUT_ROLE_MISMATCH"),
        ("claimed", "ASSET_OPERATION_CLAIMED"),
        ("conflicting_roles", "OUTPUT_ROLE_MISMATCH"),
        ("failed_timestamp", "FAILED_ASSET_TIMESTAMP_MISSING"),
        ("validation", "ASSET_VALIDATION_ACTIVE"),
        ("missing", "RETENTION_PENDING"),
    ],
)
async def test_cleanup_mixed_live_and_eligible_outputs_progress_on_retry(
    session_factory, batch_size, unsafe_output, expected_code
):
    if unsafe_output == "missing":
        # Deliberately model a dangling historical reference in this synthetic
        # database only; normal FK enforcement prevents creating this state.
        async with session_factory() as session:
            await session.execute(text("PRAGMA foreign_keys=OFF"))
            await session.commit()
    async with session_factory() as session, session.begin():
        user, project, video, scene, generation = await seed(session)
        outputs = [
            Asset(
                id=f"mixed-output-{index}",
                project_id=project.id,
                resource_owner_id=user.id,
                ownership_provenance="VIDEO_PARENT",
                kind="VIDEO",
                role="GENERATED_VIDEO",
                filename=f"output-{index}.mp4",
                content_type="video/mp4",
                object_key=f"test-only/mixed-output-{index}.mp4",
                status="READY",
                created_by=user.id,
            )
            for index in range(3)
        ]
        session.add_all(outputs)
        await session.flush()
        generation.output_asset_id = outputs[0].id
        for index in (1, 2):
            session.add(
                SceneGeneration(
                    video_id=video.id,
                    scene_id=scene.id,
                    workflow_id=generation.workflow_id,
                    mode="t2v",
                    generation_no=index + 1,
                    status="COMPLETED",
                    created_by=user.id,
                    output_asset_id=outputs[index].id,
                )
            )
        live_video = Video(project_id=project.id, title="Live", created_by=user.id)
        session.add(live_video)
        await session.flush()
        live_scene = Scene(video_id=live_video.id, scene_order=1)
        session.add(live_scene)
        await session.flush()
        session.add(
            SceneGeneration(
                video_id=live_video.id,
                scene_id=live_scene.id,
                workflow_id=generation.workflow_id,
                mode="t2v",
                generation_no=1,
                status="COMPLETED",
                created_by=user.id,
                output_asset_id=outputs[0].id if unsafe_output == "live_reference" else None,
            )
        )
        operation = DeletionOperation(
            resource_type="VIDEO",
            resource_id=video.id,
            video_id=video.id,
            resource_owner_id=user.id,
            requested_by_id=user.id,
            expected_revision=1,
            status="LOGICALLY_DELETED",
        )
        session.add(operation)
        if unsafe_output == "bad_provenance":
            outputs[0].ownership_provenance = None
        elif unsafe_output == "wrong_project":
            other_project = Project(name="Other", created_by=user.id)
            session.add(other_project)
            await session.flush()
            outputs[0].project_id = other_project.id
        elif unsafe_output == "wrong_owner":
            other_user = User(email="other@cleanup.test", password_hash="synthetic")
            session.add(other_user)
            await session.flush()
            outputs[0].resource_owner_id = other_user.id
        elif unsafe_output == "wrong_kind":
            outputs[0].kind = "IMAGE"
        elif unsafe_output == "wrong_role":
            outputs[0].role = "FINAL_VIDEO"
        elif unsafe_output == "claimed":
            outputs[0].operation_claim_id = "synthetic-claim"
        elif unsafe_output == "conflicting_roles":
            session.add(FinalVideo(
                video_id=video.id, version_no=1, status="READY", manifest={},
                manifest_hash="c" * 64, created_by=user.id,
                output_asset_id=outputs[0].id,
            ))
        elif unsafe_output == "failed_timestamp":
            outputs[0].status = "FAILED"
        elif unsafe_output == "validation":
            outputs[0].status = "VALIDATING"
            outputs[0].media_metadata = {"validation": {"state": "RUNNING"}}
        await session.flush()
        operation_id = operation.id
        if unsafe_output == "missing":
            await session.execute(delete(Asset).where(Asset.id == outputs[0].id))

    if unsafe_output == "missing":
        async with session_factory() as session:
            await session.execute(text("PRAGMA foreign_keys=ON"))
            await session.commit()

    cleanup = DeletionCleanupService(
        session_factory, SimpleNamespace(retention_batch_size=batch_size)
    )
    now = utcnow()
    deleted_at = {}
    for attempt in range(4):
        claims = await cleanup._claim_batch(now)
        assert len(claims) == 1
        outcome = await cleanup._process(claims[0], now)
        assert outcome.status != "CLEANUP_COMPLETE"
        # A pending claim replay may advance another bounded batch, but must
        # never complete while an unsafe output remains unresolved.
        replay = await cleanup._process(claims[0], now)
        assert replay is None or replay.status != "CLEANUP_COMPLETE"
        async with session_factory() as session:
            operation = await session.get(DeletionOperation, operation_id)
            shared = await session.get(Asset, "mixed-output-0")
            if unsafe_output == "missing":
                assert shared is None
            else:
                assert shared.status == {
                    "failed_timestamp": "FAILED", "validation": "VALIDATING"
                }.get(unsafe_output, "READY")
                assert shared.deleted_at is None and shared.purged_at is None
            assert operation.cleanup_completed_at is None
            for index in (1, 2):
                asset = await session.get(Asset, f"mixed-output-{index}")
                if attempt >= 1 or batch_size >= 2 or index == 1:
                    assert asset.status == "DELETED", outcome
                    assert asset.deleted_at is not None and asset.purged_at is None
                    if index in deleted_at:
                        assert asset.deleted_at == deleted_at[index]
                    deleted_at[index] = asset.deleted_at
            if batch_size >= 3:
                assert operation.status == (
                    "CLEANUP_PENDING" if unsafe_output == "missing" else "CLEANUP_BLOCKED"
                )
                assert operation.last_error_code == expected_code
            now = operation.next_attempt_at + timedelta(seconds=1)


@pytest.mark.asyncio
@pytest.mark.postgres
@pytest.mark.skipif(not DATABASE_URL, reason="requires disposable POSTGRES_TEST_DATABASE_URL")
async def test_cleanup_skips_locked_output_before_bounded_limit(retention_pg_factory):
    factory = retention_pg_factory
    async with factory() as session, session.begin():
        user, project, video, scene, generation = await seed(session)
        outputs = [Asset(
            id=f"locked-output-{index}", project_id=project.id,
            resource_owner_id=user.id, ownership_provenance="VIDEO_PARENT",
            kind="VIDEO", role="GENERATED_VIDEO", filename=f"output-{index}.mp4",
            content_type="video/mp4", object_key=f"test-only/locked-{index}.mp4",
            status="READY", created_by=user.id,
        ) for index in range(2)]
        session.add_all(outputs)
        await session.flush()
        generation.output_asset_id = outputs[0].id
        session.add(SceneGeneration(
            video_id=video.id, scene_id=scene.id, workflow_id=generation.workflow_id,
            mode="t2v", generation_no=2, status="COMPLETED", created_by=user.id,
            output_asset_id=outputs[1].id,
        ))
        operation = DeletionOperation(
            resource_type="VIDEO", resource_id=video.id, video_id=video.id,
            resource_owner_id=user.id, requested_by_id=user.id,
            expected_revision=1, status="LOGICALLY_DELETED",
        )
        session.add(operation)
        await session.flush()
        operation_id = operation.id
    cleanup = DeletionCleanupService(factory, SimpleNamespace(retention_batch_size=1))
    now = utcnow()
    async with factory() as locker, locker.begin():
        await locker.scalar(select(Asset).where(Asset.id == "locked-output-0").with_for_update())
        for _ in range(3):
            claims = await cleanup._claim_batch(now)
            assert len(claims) == 1
            outcome = await cleanup._process(claims[0], now)
            assert outcome.status == "CLEANUP_PENDING"
            async with factory() as session:
                assert (await session.get(Asset, "locked-output-0")).status == "READY"
                assert (await session.get(Asset, "locked-output-1")).status == "DELETED"
                operation = await session.get(DeletionOperation, operation_id)
                assert operation.cleanup_completed_at is None
                now = operation.next_attempt_at + timedelta(seconds=1)
    claims = await cleanup._claim_batch(now)
    assert len(claims) == 1
    assert (await cleanup._process(claims[0], now)).status == "CLEANUP_PENDING"
    async with factory() as session:
        assert (await session.get(Asset, "locked-output-0")).status == "DELETED"


@pytest.mark.asyncio
async def test_cleanup_waits_while_final_video_is_assembling(session_factory):
    async with session_factory() as session, session.begin():
        user, _, video, _, _ = await seed(session)
        final_video = FinalVideo(
            video_id=video.id,
            version_no=1,
            status="ASSEMBLING",
            manifest={},
            manifest_hash="e" * 64,
            created_by=user.id,
        )
        session.add(final_video)
        await session.flush()
        operation = DeletionOperation(
            resource_type="FINAL_VIDEO",
            resource_id=final_video.id,
            video_id=video.id,
            resource_owner_id=user.id,
            requested_by_id=user.id,
            expected_revision=1,
            status="LOGICALLY_DELETED",
        )
        session.add(operation)
        await session.flush()
        operation_id, final_video_id = operation.id, final_video.id

    cleanup = DeletionCleanupService(session_factory, SimpleNamespace())
    now = utcnow()
    claims = await cleanup._claim_batch(now)
    assert len(claims) == 1
    outcome = await cleanup._process(claims[0], now)
    assert outcome.status == "CLEANUP_BLOCKED"
    assert outcome.error_code == "ACTIVE_DEPENDENCY"
    async with session_factory() as session:
        operation = await session.get(DeletionOperation, operation_id)
        final_video = await session.get(FinalVideo, final_video_id)
        assert operation.status == "CLEANUP_BLOCKED"
        assert operation.cleanup_completed_at is None
        assert final_video.status == "ASSEMBLING"

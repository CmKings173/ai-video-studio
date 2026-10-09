from datetime import timedelta

import pytest

from apps.api.app.api.events import _dedupe_token, _encode, _snapshot
from apps.api.app.db.models import (
    FinalVideo,
    Project,
    Scene,
    SceneGeneration,
    User,
    Video,
    WorkflowRecord,
    utcnow,
)


def test_video_event_token_changes_when_worker_updates_status_without_revision():
    assert _dedupe_token("video:v1", 4, {"status": "GENERATING"}) != _dedupe_token(
        "video:v1", 4, {"status": "READY"}
    )


def test_generation_event_token_changes_for_worker_progress_without_revision():
    before = {
        "status": "RUNNING",
        "stage": "render",
        "progress": 20,
        "current": 2,
        "total": 10,
        "error_code": None,
        "output_asset_id": None,
    }
    after = {**before, "progress": 30, "current": 3}

    assert _dedupe_token("generation:g1", 1, before) != _dedupe_token(
        "generation:g1", 1, after
    )
    assert _dedupe_token("generation:g1", 1, before) != _dedupe_token(
        "generation:g1", 1, {**before, "status": "FAILED", "error_code": "RENDER_FAILED"}
    )


def test_final_event_token_changes_for_worker_progress_without_revision():
    before = {"status": "ASSEMBLING", "stage": "mux", "current": 1, "total": 4}
    after = {**before, "stage": "encode", "current": 2}

    assert _dedupe_token("final:f1", 1, before) != _dedupe_token("final:f1", 1, after)


@pytest.mark.asyncio
async def test_snapshot_bounds_terminal_history_and_pages_new_rows(session_factory):
    now = utcnow()
    history_at = now - timedelta(days=30)
    scan_from = now - timedelta(seconds=1)
    user_id, video_id, scene_id, workflow_id = "user-events", "video-events", "scene-events", "workflow-events"

    async with session_factory() as session:
        session.add(User(id=user_id, email="events@example.test", password_hash="test"))
        project = Project(name="events", created_by=user_id)
        session.add(project)
        await session.flush()
        session.add(
            Video(
                id=video_id,
                project_id=project.id,
                title="events",
                kind="QUICK_CLIP",
                target_duration=5,
                aspect_ratio="16:9",
                created_by=user_id,
            )
        )
        session.add(Scene(id=scene_id, video_id=video_id, scene_order=0))
        session.add(
            WorkflowRecord(
                id=workflow_id,
                code="events-test",
                mode="t2v",
                version="1",
                workflow={},
                slots={},
                required_slots=[],
                profile={},
                workflow_hash="a" * 64,
                slot_map_hash="b" * 64,
                created_by=user_id,
            )
        )
        await session.flush()

        generations = [
            SceneGeneration(
                id=f"g-{index:034d}",
                video_id=video_id,
                scene_id=scene_id,
                mode="t2v",
                workflow_id=workflow_id,
                generation_no=index,
                status="COMPLETED",
                created_at=history_at,
                created_by=user_id,
            )
            for index in range(1, 301)
        ]
        generations.extend(
            SceneGeneration(
                id=f"n-{index:034d}",
                video_id=video_id,
                scene_id=scene_id,
                mode="t2v",
                workflow_id=workflow_id,
                generation_no=300 + index,
                status="FAILED",
                error_code="RENDER_FAILED",
                created_at=now,
                created_by=user_id,
            )
            for index in range(1, 102)
        )
        generations.append(
            SceneGeneration(
                id="g-active".ljust(36, "0"),
                video_id=video_id,
                scene_id=scene_id,
                mode="t2v",
                workflow_id=workflow_id,
                generation_no=402,
                status="RUNNING",
                created_at=history_at,
                created_by=user_id,
            )
        )
        finals = [
            FinalVideo(
                id=f"f-{index:034d}",
                video_id=video_id,
                version_no=index,
                status="READY",
                manifest={},
                manifest_hash="c" * 64,
                created_at=history_at,
                created_by=user_id,
            )
            for index in range(1, 301)
        ]
        finals.extend(
            FinalVideo(
                id=f"r-{index:034d}",
                video_id=video_id,
                version_no=300 + index,
                status="FAILED",
                manifest={},
                manifest_hash="d" * 64,
                error_code="ASSEMBLY_FAILED",
                created_at=now,
                created_by=user_id,
            )
            for index in range(1, 102)
        )
        session.add_all([*generations, *finals])
        await session.commit()

    snapshot = await _snapshot(
        session_factory,
        video_id,
        generation_cursor=(scan_from, ""),
        final_cursor=(scan_from, ""),
        active_generation_ids=set(),
        active_final_ids=set(),
    )
    generation_events = [event for event in snapshot.events if event[0].startswith("generation:")]
    final_events = [event for event in snapshot.events if event[0].startswith("final:")]

    assert len(generation_events) == 101  # one active item plus one bounded page of new rows
    assert len(final_events) == 100
    assert len(snapshot.new_generation_ids) == 100
    assert len(snapshot.new_final_ids) == 100
    assert snapshot.generation_cursor[0] == now
    assert snapshot.final_cursor[0] == now
    active_id = "g-active".ljust(36, "0")
    assert f"generation:{active_id}" in {event[0] for event in generation_events}
    assert any(event[3]["status"] == "RUNNING" for event in generation_events)

    next_snapshot = await _snapshot(
        session_factory,
        video_id,
        generation_cursor=snapshot.generation_cursor,
        final_cursor=snapshot.final_cursor,
        active_generation_ids=snapshot.active_generation_ids,
        active_final_ids=snapshot.active_final_ids,
    )
    assert len(next_snapshot.new_generation_ids) == 1
    assert len(next_snapshot.new_final_ids) == 1


@pytest.mark.asyncio
async def test_snapshot_keeps_tracking_an_active_item_until_terminal_transition(session_factory):
    now = utcnow()
    user_id, video_id, scene_id, workflow_id = "user-live", "video-live", "scene-live", "workflow-live"

    async with session_factory() as session:
        session.add(User(id=user_id, email="live@example.test", password_hash="test"))
        project = Project(name="live", created_by=user_id)
        session.add(project)
        await session.flush()
        session.add_all(
            [
                Video(
                    id=video_id,
                    project_id=project.id,
                    title="live",
                    kind="QUICK_CLIP",
                    target_duration=5,
                    aspect_ratio="16:9",
                    created_by=user_id,
                ),
                Scene(id=scene_id, video_id=video_id, scene_order=0),
                WorkflowRecord(
                    id=workflow_id,
                    code="live-test",
                    mode="t2v",
                    version="1",
                    workflow={},
                    slots={},
                    required_slots=[],
                    profile={},
                    workflow_hash="e" * 64,
                    slot_map_hash="f" * 64,
                    created_by=user_id,
                ),
            ]
        )
        await session.flush()
        generation = SceneGeneration(
            id="generation-live",
            video_id=video_id,
            scene_id=scene_id,
            mode="t2v",
            workflow_id=workflow_id,
            generation_no=1,
            status="RUNNING",
            phase="render",
            progress_current=1,
            progress_total=10,
            created_at=now - timedelta(minutes=5),
            created_by=user_id,
        )
        session.add(generation)
        await session.commit()

    first = await _snapshot(
        session_factory,
        video_id,
        generation_cursor=(now, ""),
        final_cursor=(now, ""),
        active_generation_ids=set(),
        active_final_ids=set(),
    )
    assert first.active_generation_ids == {"generation-live"}

    async with session_factory() as session:
        generation = await session.get(SceneGeneration, "generation-live")
        generation.status = "FAILED"
        generation.phase = "failed"
        generation.progress_current = 4
        generation.error_code = "RENDER_FAILED"
        await session.commit()

    terminal = await _snapshot(
        session_factory,
        video_id,
        generation_cursor=first.generation_cursor,
        final_cursor=first.final_cursor,
        active_generation_ids=first.active_generation_ids,
        active_final_ids=first.active_final_ids,
    )
    generation_event = next(event for event in terminal.events if event[0] == "generation:generation-live")
    assert generation_event[3]["status"] == "FAILED"
    assert generation_event[3]["current"] == 4
    assert terminal.active_generation_ids == set()


def test_live_event_indexes_are_declared_for_bounded_queries():
    generation_indexes = {index.name: index for index in SceneGeneration.__table__.indexes}
    final_indexes = {index.name: index for index in FinalVideo.__table__.indexes}

    assert "ix_scene_generations_video_active" in generation_indexes
    assert "ix_final_videos_video_created" in final_indexes
    assert tuple(
        column.name for column in generation_indexes["ix_scene_generations_video_active"].columns
    ) == ("video_id", "status", "created_at", "id")


def test_sse_payload_contains_schema_and_resource_revision():
    payload = _encode(7, "generation.progress", 3, {"generation_id": "g1"})
    assert "id: 7" in payload
    assert '"schema_version":1' in payload
    assert '"resource_revision":3' in payload

from __future__ import annotations

import asyncio
import hashlib
import os
import subprocess
import sys
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from apps.api.app.db.models import (
    Asset,
    FinalVideo,
    Project,
    Scene,
    SceneGeneration,
    User,
    Video,
    WorkflowRecord,
)
from apps.api.app.services.asset_claims import (
    OUTPUT_WRITE_CLAIM,
    REPAIR_CLAIM,
    acquire_claim,
    owns_claim,
)
from apps.api.app.services.asset_service import upload_staging_key
from workers.assembler import Assembler
from workers.common import lock_scheduler, save_output
from workers.dispatcher import Dispatcher
from workers.reconciliation import AssetReconciler, ReconciliationReport

ROOT = Path(__file__).resolve().parents[2]
DATABASE_URL = os.getenv("POSTGRES_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not DATABASE_URL,
        reason="set POSTGRES_TEST_DATABASE_URL to run the PostgreSQL production lane",
    ),
]


@pytest.fixture(scope="module")
async def pg_engine():
    assert DATABASE_URL is not None
    env = os.environ.copy()
    env["DATABASE_URL"] = DATABASE_URL
    env["APP_ENV"] = "test"
    await asyncio.to_thread(
        subprocess.run(  # noqa: ASYNC221
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=ROOT,
            env=env,
            check=True,
        )
    )
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    yield engine
    await engine.dispose()


@pytest.mark.asyncio
async def test_postgres_migrations_create_partial_indexes_and_lock_primitives(pg_engine):
    async with pg_engine.connect() as connection:
        indexes = {
            row[0]: row[1]
            for row in (
                await connection.execute(
                    text(
                        "SELECT indexname, indexdef FROM pg_indexes "
                        "WHERE schemaname = current_schema() "
                        "AND indexname IN ("
                        "'uq_workflow_registry_enabled_mode', "
                        "'uq_final_videos_active_video')"
                    )
                )
            ).all()
        }
        assert "WHERE enabled" in indexes["uq_workflow_registry_enabled_mode"]
        assert (
            "status IN ('QUEUED', 'ASSEMBLING', 'CANCEL_REQUESTED')"
            in indexes["uq_final_videos_active_video"]
        )
        await connection.execute(text("SELECT pg_advisory_xact_lock(874321)"))
        await connection.execute(text("SELECT id FROM scene_generations FOR UPDATE SKIP LOCKED"))


@pytest.mark.asyncio
async def test_postgres_immutable_final_manifest_trigger_rejects_mutation(pg_engine):
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    suffix = uuid4().hex
    async with factory() as session, session.begin():
        user = User(email=f"pg-{suffix}@example.test", name="PG lane", password_hash="hash")
        session.add(user)
        await session.flush()
        project = Project(name=f"PG {suffix}", description="", created_by=user.id)
        session.add(project)
        await session.flush()
        video = Video(
            project_id=project.id,
            title="PG lane",
            kind="QUICK_CLIP",
            target_duration=5,
            aspect_ratio="16:9",
            brief="",
            created_by=user.id,
        )
        session.add(video)
        await session.flush()
        final = FinalVideo(
            video_id=video.id,
            version_no=1,
            status="QUEUED",
            manifest={"schema_version": 1},
            manifest_hash="a" * 64,
            assembly_config={},
            created_by=user.id,
        )
        session.add(final)
        await session.flush()
        final_id = final.id

    with pytest.raises(DBAPIError, match="immutable final assembly manifest"):
        async with factory() as session, session.begin():
            row = await session.get(FinalVideo, final_id)
            row.manifest = {"schema_version": 2}
            await session.flush()


async def _seed_pg_asset(
    factory,
    *,
    status: str = "PENDING_UPLOAD",
    kind: str = "VIDEO",
    content_type: str = "video/mp4",
    filename: str = "asset.mp4",
    role: str = "REFERENCE_VIDEO",
    size_bytes: int = 10,
    checksum: str | None = None,
) -> tuple[str, str, str, str]:
    suffix = uuid4().hex
    async with factory() as session, session.begin():
        user = User(email=f"asset-{suffix}@example.test", name="PG asset", password_hash="hash")
        session.add(user)
        await session.flush()
        project = Project(name=f"Asset {suffix}", description="", created_by=user.id)
        session.add(project)
        await session.flush()
        asset = Asset(
            project_id=project.id,
            kind=kind,
            role=role,
            filename=filename,
            content_type=content_type,
            object_key=f"assets/{suffix}/{filename}",
            status=status,
            size_bytes=size_bytes,
            checksum=checksum,
            created_by=user.id,
        )
        session.add(asset)
        await session.flush()
        return user.id, project.id, asset.id, asset.object_key


def _png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (8, 6), "red").save(buffer, "PNG")
    return buffer.getvalue()


class PgMemoryStore:
    def __init__(self):
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.put_count = 0

    async def get_bytes(self, key, max_bytes=None):
        data = self.objects[key][0]
        if max_bytes is not None and len(data) > max_bytes:
            raise RuntimeError("too large")
        return data

    async def put_bytes(self, key, data, content_type):
        self.put_count += 1
        self.objects[key] = (data, content_type)

    async def delete(self, key):
        self.objects.pop(key, None)


@pytest.mark.asyncio
async def test_postgres_asset_operation_claim_concurrent_acquire_is_single_owner(pg_engine):
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    _user_id, _project_id, asset_id, _object_key = await _seed_pg_asset(factory)
    start = asyncio.Event()

    async def contender(claim_type: str):
        await start.wait()
        async with factory() as session, session.begin():
            return await acquire_claim(
                session,
                asset_id,
                claim_type,
                timeout_seconds=60,
                allowed_statuses={"PENDING_UPLOAD"},
            )

    attempts = [
        asyncio.create_task(contender(REPAIR_CLAIM)),
        asyncio.create_task(contender(OUTPUT_WRITE_CLAIM)),
    ]
    start.set()
    claim_ids = await asyncio.gather(*attempts)
    winners = [claim_id for claim_id in claim_ids if claim_id is not None]
    assert len(winners) == 1

    async with factory() as session:
        asset = await session.get(Asset, asset_id)
        assert asset.operation_claim_id == winners[0]
        assert owns_claim(asset, winners[0], asset.operation_claim_type)


@pytest.mark.asyncio
async def test_postgres_save_output_concurrent_same_identity_stays_single_row(pg_engine):
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    user_id, project_id, _asset_id, _object_key = await _seed_pg_asset(factory)
    store = PgMemoryStore()
    start = asyncio.Event()
    data = b"postgres-output-bytes"

    async def collect():
        await start.wait()
        try:
            return "ok", await save_output(
                factory,
                store,
                owner_id="same-postgres-owner",
                role="GENERATED_VIDEO",
                project_id=project_id,
                created_by=user_id,
                data=data,
                metadata={"kind": "VIDEO", "has_video": True},
                claim_timeout_seconds=60,
            )
        except ValueError as exc:
            return "busy", str(exc)

    attempts = [asyncio.create_task(collect()), asyncio.create_task(collect())]
    start.set()
    results = await asyncio.gather(*attempts)
    successes = [value for status, value in results if status == "ok"]
    busy = [value for status, value in results if status == "busy"]
    assert successes
    assert len(set(successes)) == 1
    assert len(successes) + len(busy) == 2
    assert all(value == "ASSET_OPERATION_BUSY" for value in busy)
    assert store.put_count == 1

    async with factory() as session:
        rows = (
            await session.scalars(
                text(
                    "SELECT id FROM assets "
                    "WHERE role = 'GENERATED_VIDEO' "
                    "AND filename = 'same-postgres-owner.mp4'"
                )
            )
        ).all()
        asset = await session.get(Asset, successes[0])
        assert len(rows) == 1
        assert asset.status == "READY"
        assert asset.operation_claim_id is None
        assert asset.checksum == hashlib.sha256(data).hexdigest()


@pytest.mark.asyncio
async def test_postgres_save_output_loser_reuses_ready_after_insert_conflict(pg_engine):
    loser_initial_read = asyncio.Event()
    winner_initial_read = asyncio.Event()
    winner_ready = asyncio.Event()
    loser_conflicted = asyncio.Event()
    get_counts: dict[str, int] = {}

    class CoordinatedSession(AsyncSession):
        async def get(self, entity, ident, *args, **kwargs):
            task = asyncio.current_task()
            task_name = task.get_name() if task else ""
            if entity is Asset and task_name in {
                "save-output-winner",
                "save-output-loser",
            }:
                count = get_counts.get(task_name, 0) + 1
                get_counts[task_name] = count
                if count == 1:
                    if task_name == "save-output-winner":
                        winner_initial_read.set()
                        await loser_initial_read.wait()
                    else:
                        loser_initial_read.set()
                        await winner_initial_read.wait()
                elif task_name == "save-output-loser" and count == 2:
                    await winner_ready.wait()
            return await super().get(entity, ident, *args, **kwargs)

        async def flush(self, *args, **kwargs):
            task = asyncio.current_task()
            if task and task.get_name() == "save-output-loser":
                await winner_ready.wait()
            try:
                return await super().flush(*args, **kwargs)
            except IntegrityError:
                if task and task.get_name() == "save-output-loser":
                    loser_conflicted.set()
                raise

    factory = async_sessionmaker(
        pg_engine,
        expire_on_commit=False,
        autoflush=False,
        class_=CoordinatedSession,
    )
    user_id, project_id, _asset_id, _object_key = await _seed_pg_asset(factory)
    store = PgMemoryStore()
    data = b"postgres-ready-reread"

    async def collect_winner():
        result = await save_output(
            factory,
            store,
            owner_id="same-ready-reread-owner",
            role="GENERATED_VIDEO",
            project_id=project_id,
            created_by=user_id,
            data=data,
            metadata={"kind": "VIDEO", "has_video": True},
            claim_timeout_seconds=60,
        )
        winner_ready.set()
        return result

    async def collect_loser():
        return await save_output(
            factory,
            store,
            owner_id="same-ready-reread-owner",
            role="GENERATED_VIDEO",
            project_id=project_id,
            created_by=user_id,
            data=data,
            metadata={"kind": "VIDEO", "has_video": True},
            claim_timeout_seconds=60,
        )

    winner = asyncio.create_task(collect_winner(), name="save-output-winner")
    loser = asyncio.create_task(collect_loser(), name="save-output-loser")
    first_id, second_id = await asyncio.wait_for(
        asyncio.gather(winner, loser), timeout=10
    )

    assert loser_conflicted.is_set()
    assert first_id == second_id
    assert store.put_count == 1
    async with factory() as session:
        row_count = await session.scalar(
            text("SELECT count(*) FROM assets WHERE id = :asset_id"),
            {"asset_id": first_id},
        )
        asset = await session.get(Asset, first_id)
        assert row_count == 1
        assert asset.status == "READY"
        assert asset.operation_claim_id is None
        assert asset.object_key in store.objects


@pytest.mark.asyncio
async def test_postgres_reconciler_concurrent_repair_only_one_finalizes(pg_engine):
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    data = _png_bytes()
    checksum = hashlib.sha256(data).hexdigest()
    _user_id, _project_id, asset_id, object_key = await _seed_pg_asset(
        factory,
        kind="IMAGE",
        content_type="image/png",
        filename="repair.png",
        role="PRODUCT_IMAGE",
        size_bytes=len(data),
        checksum=checksum,
    )
    store = PgMemoryStore()
    store.objects[upload_staging_key(asset_id=asset_id)] = (data, "image/png")
    settings = SimpleNamespace(
        asset_operation_claim_timeout_seconds=60,
        ffprobe_binary="ffprobe",
        max_upload_bytes=1024 * 1024,
        upload_intent_grace_seconds=0,
        pending_asset_retention_hours=24,
        retention_failed_hours=24,
        deleted_asset_retention_hours=168,
        retention_deleting_retry_hours=1,
        retention_batch_size=100,
    )
    reconciler = AssetReconciler(factory, store, settings)
    snapshot = {
        "id": asset_id,
        "status": "PENDING_UPLOAD",
        "object_key": object_key,
        "checksum": checksum,
        "size_bytes": len(data),
        "content_type": "image/png",
        "filename": "repair.png",
        "created_at": None,
    }
    start = asyncio.Event()

    async def repair():
        report = ReconciliationReport()
        await start.wait()
        await reconciler._repair_pending(snapshot, report)
        return report

    attempts = [asyncio.create_task(repair()), asyncio.create_task(repair())]
    start.set()
    reports = await asyncio.gather(*attempts)
    assert sum(report.repaired_assets == [asset_id] for report in reports) == 1
    assert store.put_count == 1

    async with factory() as session:
        asset = await session.get(Asset, asset_id)
        assert asset.status == "READY"
        assert asset.operation_claim_id is None


async def _seed_dispatch_queue(factory):
    async with factory() as session, session.begin():
        suffix = uuid4().hex
        user = User(email=f"dispatch-{suffix}@example.test", name="PG", password_hash="hash")
        session.add(user)
        await session.flush()
        project = Project(name=f"Dispatch {suffix}", description="", created_by=user.id)
        workflow = WorkflowRecord(
            code=f"DISPATCH_{suffix}",
            mode="t2v",
            version="1",
            workflow={"1": {"class_type": "Text", "inputs": {"text": "prompt"}}},
            slots={},
            required_slots=[],
            profile={},
            workflow_hash="c" * 64,
            slot_map_hash="d" * 64,
            enabled=True,
            created_by=user.id,
        )
        session.add_all([project, workflow])
        await session.flush()
        ids = []
        for index in range(2):
            video = Video(
                project_id=project.id,
                title=f"Video {index}",
                kind="QUICK_CLIP",
                target_duration=5,
                aspect_ratio="16:9",
                brief="brief",
                created_by=user.id,
            )
            session.add(video)
            await session.flush()
            scene = Scene(video_id=video.id, scene_order=0, prompt="prompt", duration_seconds=5)
            session.add(scene)
            await session.flush()
            generation = SceneGeneration(
                video_id=video.id,
                scene_id=scene.id,
                mode="t2v",
                workflow_id=workflow.id,
                generation_no=1,
                operation="ORIGINAL",
                status="CREATED",
                phase="PENDING",
                input_snapshot={"workflow": workflow.workflow, "slots": {}, "assets": []},
                created_by=user.id,
            )
            session.add(generation)
            await session.flush()
            ids.append(generation.id)
        return ids


@pytest.mark.asyncio
async def test_postgres_advisory_scheduler_lock_blocks_second_transaction(pg_engine):
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    first_entered = asyncio.Event()
    second_entered = asyncio.Event()
    release = asyncio.Event()

    async def holder():
        async with factory() as session, session.begin():
            await lock_scheduler(session, 874322)
            first_entered.set()
            await release.wait()

    async def waiter():
        await first_entered.wait()
        async with factory() as session, session.begin():
            await lock_scheduler(session, 874322)
            second_entered.set()

    first = asyncio.create_task(holder())
    await first_entered.wait()
    second = asyncio.create_task(waiter())
    await asyncio.sleep(0.1)
    assert not second_entered.is_set()
    release.set()
    await asyncio.gather(first, second)
    assert second_entered.is_set()


@pytest.mark.asyncio
async def test_postgres_dispatcher_concurrent_claims_are_unique(pg_engine):
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    generation_ids = await _seed_dispatch_queue(factory)
    settings = SimpleNamespace(lease_seconds=30, max_generation_attempts=2)
    first = Dispatcher(factory, None, None, None, settings)
    second = Dispatcher(factory, None, None, None, settings)

    claimed = await asyncio.gather(first.claim(), second.claim())
    winners = [item for item in claimed if item is not None]
    assert len(winners) == 1
    assert winners[0] in generation_ids

    async with factory() as session, session.begin():
        generation = await session.get(SceneGeneration, winners[0], with_for_update=True)
        generation.status = "FAILED"
        generation.claimed_by = None
        generation.lease_expires_at = None

    next_claim = await second.claim()
    assert next_claim in set(generation_ids) - {winners[0]}


async def _seed_assembly_queue(factory):
    async with factory() as session, session.begin():
        suffix = uuid4().hex
        user = User(email=f"assembly-{suffix}@example.test", name="PG", password_hash="hash")
        session.add(user)
        await session.flush()
        project = Project(name=f"Assembly {suffix}", description="", created_by=user.id)
        session.add(project)
        await session.flush()
        ids = []
        for index in range(2):
            video = Video(
                project_id=project.id,
                title=f"Assembly {index}",
                kind="LONG_VIDEO",
                target_duration=30,
                aspect_ratio="16:9",
                brief="brief",
                created_by=user.id,
            )
            session.add(video)
            await session.flush()
            final = FinalVideo(
                video_id=video.id,
                version_no=1,
                status="QUEUED",
                manifest={"scenes": []},
                manifest_hash="e" * 64,
                assembly_config={},
                created_by=user.id,
            )
            session.add(final)
            await session.flush()
            ids.append(final.id)
        return ids


@pytest.mark.asyncio
async def test_postgres_assembler_concurrent_claims_are_unique(pg_engine):
    """Concurrent workers may claim different queued finals for distinct videos,
    never duplicate claims."""
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    final_ids = await _seed_assembly_queue(factory)
    settings = SimpleNamespace(lease_seconds=30)
    first = Assembler(factory, None, None, settings)
    second = Assembler(factory, None, None, settings)

    claimed = await asyncio.gather(first.claim(), second.claim())
    winners = [item for item in claimed if item is not None]
    assert len(winners) == 2
    assert len(set(winners)) == 2
    assert set(winners) == set(final_ids)


@pytest.mark.asyncio
async def test_postgres_assembler_cannot_double_claim_same_final(pg_engine):
    """When only one final is queued, concurrent claims must yield exactly one winner."""
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        suffix = uuid4().hex
        user = User(email=f"single-{suffix}@example.test", name="PG", password_hash="hash")
        session.add(user)
        await session.flush()
        project = Project(name=f"Single {suffix}", description="", created_by=user.id)
        session.add(project)
        await session.flush()
        video = Video(
            project_id=project.id,
            title="Single assembly",
            kind="LONG_VIDEO",
            target_duration=30,
            aspect_ratio="16:9",
            brief="brief",
            created_by=user.id,
        )
        session.add(video)
        await session.flush()
        final = FinalVideo(
            video_id=video.id,
            version_no=1,
            status="QUEUED",
            manifest={"scenes": []},
            manifest_hash="e" * 64,
            assembly_config={},
            created_by=user.id,
        )
        session.add(final)
        await session.flush()
        final_id = final.id

    settings = SimpleNamespace(lease_seconds=30)
    first = Assembler(factory, None, None, settings)
    second = Assembler(factory, None, None, settings)

    claimed = await asyncio.gather(first.claim(), second.claim())
    winners = [item for item in claimed if item is not None]
    assert len(winners) == 1
    assert winners[0] == final_id
    assert None in claimed

from __future__ import annotations

import time
from contextlib import nullcontext
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import sqlite

from apps.api.app.api import dashboard as dashboard_api
from apps.api.app.api import events as events_api
from apps.api.app.api import generations as generations_api
from apps.api.app.api.ownership import owner_scope
from apps.api.app.core.errors import AppError
from apps.api.app.db.models import (
    Asset,
    Brand,
    DirectorRun,
    DirectorRunMember,
    FinalVideo,
    GenerationAttempt,
    Product,
    Project,
    Scene,
    SceneGeneration,
    User,
    Video,
)
from apps.api.app.schemas.api import GenerateAll, GenerationRequest


def _sql(statement) -> str:
    return str(
        statement.compile(
            dialect=sqlite.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


def _entity(statement):
    descriptions = statement.column_descriptions
    return descriptions[0].get("entity") if descriptions else None


def _where_sql(statement) -> str:
    where = getattr(statement, "whereclause", None)
    return _sql(where) if where is not None else ""


class _Rows:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def all(self):
        return self._rows


class _MappingRows:
    def __init__(self, row):
        self.row = row

    def one_or_none(self):
        return self.row


class _Result:
    def __init__(self, row):
        self.row = row

    def mappings(self):
        return _MappingRows(self.row)


class _FakeSession:
    """Record and compile statements without opening a database connection."""

    def __init__(self, *, scalar=None, scalars=None, get=None, account_role="EDITOR"):
        self.account_role = account_role
        self.scalar_handler = scalar or (lambda _statement: None)
        self.scalars_handler = scalars or (lambda _statement: ())
        self.get_handler = get or (lambda _model, _resource_id: None)
        self.statements: list[tuple[object, str]] = []
        self.get_calls: list[tuple[object, str]] = []
        self.no_autoflush = nullcontext()

    async def scalar(self, statement):
        self.statements.append((statement, _sql(statement)))
        return self.scalar_handler(statement)

    async def scalars(self, statement):
        self.statements.append((statement, _sql(statement)))
        return _Rows(self.scalars_handler(statement))

    async def execute(self, statement):
        if _entity(statement) is User:
            return SimpleNamespace(
                one_or_none=lambda: SimpleNamespace(
                    role=self.account_role, is_active=True, auth_version=1
                )
            )
        self.statements.append((statement, _sql(statement)))
        table = statement.selected_columns[0].table
        model = next(
            candidate
            for candidate in (Asset, Brand, Product, Project, SceneGeneration, Video)
            if candidate.__table__ is table
        )
        record = self.scalar_handler(select(model))
        if record is None:
            return _Result(None)
        now = datetime.now(UTC)
        defaults = {
            "name": model.__name__,
            "description": "",
            "archived": False,
            "revision": 1,
            "created_at": now,
            "updated_at": now,
        }
        row = {
            column.key: getattr(record, column.key, defaults.get(column.key))
            for column in model.__table__.c
        }
        return _Result(row)

    async def get(self, model, resource_id):
        self.get_calls.append((model, resource_id))
        return self.get_handler(model, resource_id)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return None


def _editor(user_id: str = "editor-a"):
    return SimpleNamespace(id=user_id, role="EDITOR", auth_version=1)


def _has_owner_filter(statements, user) -> bool:
    return any(user.id in compiled for _statement, compiled in statements)


def _generation_record():
    now = datetime.now(UTC)
    return SimpleNamespace(
        id="generation-b",
        video_id="video-b",
        scene_id="scene-b",
        mode="t2v",
        workflow_id="workflow-id",
        generation_no=1,
        operation="ORIGINAL",
        parent_generation_id=None,
        status="COMPLETED",
        phase="COMPLETED",
        progress_current=1,
        progress_total=1,
        revision=1,
        input_snapshot={},
        request_id=None,
        comfy_prompt_id=None,
        output_asset_id=None,
        attempt_count=1,
        error_code=None,
        error_message=None,
        created_at=now,
        queued_at=now,
        started_at=now,
        progress_updated_at=now,
        finished_at=now,
    )


def _request():
    return SimpleNamespace(state=SimpleNamespace(request_id="ownership-test-request"))


def _settings():
    return SimpleNamespace(idempotency_hours=24)


def test_owner_scope_compiles_editor_filter_and_admin_bypass():
    editor = _editor()
    admin = SimpleNamespace(id="admin-id", role="ADMIN")

    for model in (
        Asset,
        Video,
        Scene,
        SceneGeneration,
        GenerationAttempt,
        DirectorRun,
        DirectorRunMember,
        FinalVideo,
    ):
        editor_sql = _sql(select(model.id).where(owner_scope(model, editor)))
        admin_sql = _sql(select(model.id).where(owner_scope(model, admin)))

        assert editor.id in editor_sql
        assert "created_by" in editor_sql
        assert admin.id not in admin_sql


@pytest.mark.asyncio
async def test_create_hides_foreign_scene_before_idempotency_or_generation_service(monkeypatch):
    user = _editor()
    session = _FakeSession()
    claim_calls = []
    service_constructions = []
    service_create_calls = []

    async def claim_spy(*args, **kwargs):
        claim_calls.append((args, kwargs))

    class ServiceSpy:
        def __init__(self, _settings):
            service_constructions.append(True)

        @staticmethod
        def effective_request(_scene, payload):
            return payload

        async def create(self, *_args, **_kwargs):
            service_create_calls.append(True)

    monkeypatch.setattr(generations_api, "claim", claim_spy)
    monkeypatch.setattr(generations_api, "GenerationService", ServiceSpy)

    with pytest.raises(AppError) as rejected:
        await generations_api._create(
            scene_id="foreign-scene",
            payload=GenerationRequest(),
            request=_request(),
            key="same-key",
            user=user,
            session=session,
            settings=_settings(),
            operation_name="create-generation",
        )

    assert rejected.value.status_code == 404
    assert rejected.value.code == "SCENE_NOT_FOUND"
    assert _has_owner_filter(session.statements, user)
    assert session.get_calls == []
    assert claim_calls == []
    assert service_constructions == []
    assert service_create_calls == []


@pytest.mark.asyncio
async def test_foreign_reference_asset_is_rejected_before_idempotency_or_create(monkeypatch):
    user = _editor()
    scene = SimpleNamespace(id="scene-a", video_id="video-a")
    video = SimpleNamespace(
        id="video-a", project_id="project-a", product_id=None, brand_id=None,
        created_by=user.id,
    )
    session = _FakeSession(
        scalar=lambda statement: {
            Scene: scene,
            Video: video,
            Project: SimpleNamespace(id="project-a", created_by=user.id),
        }.get(_entity(statement)),
        scalars=lambda _statement: (),
    )
    claim_calls = []
    create_calls = []
    service_constructions = []

    async def claim_spy(*args, **kwargs):
        claim_calls.append((args, kwargs))

    class ServiceSpy:
        def __init__(self, _settings):
            service_constructions.append(True)

        @staticmethod
        def effective_request(_scene, payload):
            return payload

        async def create(self, *_args, **_kwargs):
            create_calls.append(True)

    monkeypatch.setattr(generations_api, "claim", claim_spy)
    monkeypatch.setattr(generations_api, "GenerationService", ServiceSpy)

    with pytest.raises(AppError) as rejected:
        await generations_api._create(
            scene_id=scene.id,
            payload=GenerationRequest(reference_image_asset_ids=[uuid4()]),
            request=_request(),
            key="asset-key",
            user=user,
            session=session,
            settings=_settings(),
            operation_name="create-generation",
        )

    assert rejected.value.status_code == 404
    assert rejected.value.code == "ASSET_NOT_FOUND"
    asset_queries = [sql for _statement, sql in session.statements if "assets" in sql.lower()]
    assert asset_queries
    assert all(user.id in sql for sql in asset_queries)
    assert claim_calls == []
    assert service_constructions == []
    assert create_calls == []


@pytest.mark.asyncio
async def test_foreign_parent_generation_is_rejected_before_idempotency_or_create(monkeypatch):
    user = _editor()
    scene = SimpleNamespace(id="scene-a", video_id="video-a")
    video = SimpleNamespace(
        id="video-a", project_id="project-a", product_id=None, brand_id=None,
        created_by=user.id,
    )
    session = _FakeSession(
        scalar=lambda statement: {
            Scene: scene,
            Video: video,
            Project: SimpleNamespace(id="project-a", created_by=user.id),
        }.get(_entity(statement)),
        scalars=lambda _statement: (),
    )
    claim_calls = []
    create_calls = []
    service_constructions = []

    async def claim_spy(*args, **kwargs):
        claim_calls.append((args, kwargs))

    class ServiceSpy:
        def __init__(self, _settings):
            service_constructions.append(True)

        @staticmethod
        def effective_request(_scene, payload):
            return payload

        async def create(self, *_args, **_kwargs):
            create_calls.append(True)

    monkeypatch.setattr(generations_api, "claim", claim_spy)
    monkeypatch.setattr(generations_api, "GenerationService", ServiceSpy)

    with pytest.raises(AppError) as rejected:
        await generations_api._create(
            scene_id=scene.id,
            payload=GenerationRequest(
                operation="REGENERATE",
                parent_generation_id=uuid4(),
            ),
            request=_request(),
            key="parent-key",
            user=user,
            session=session,
            settings=_settings(),
            operation_name="create-regeneration",
        )

    assert rejected.value.status_code == 404
    assert rejected.value.code == "GENERATION_NOT_FOUND"
    generation_queries = [
        sql for _statement, sql in session.statements if "scene_generations" in sql.lower()
    ]
    assert generation_queries
    assert all(user.id in sql for sql in generation_queries)
    assert claim_calls == []
    assert service_constructions == []
    assert create_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("foreign_dependency", ["project", "product", "brand"])
async def test_video_foreign_dependencies_are_rejected_before_generation_idempotency(
    monkeypatch, foreign_dependency
):
    user = _editor()
    scene = SimpleNamespace(id="scene-a", video_id="video-a")
    video = SimpleNamespace(
        id="video-a",
        project_id="project-a",
        product_id="product-a",
        brand_id="brand-a",
        created_by=user.id,
    )
    records = {
        Scene: scene,
        Video: video,
        Project: (
            SimpleNamespace(id="project-a", created_by="editor-b")
            if foreign_dependency == "project"
            else SimpleNamespace(id="project-a", created_by=user.id)
        ),
        Product: (
            SimpleNamespace(id="product-a", created_by="editor-b", brand_id="brand-a")
            if foreign_dependency == "product"
            else SimpleNamespace(id="product-a", created_by=user.id, brand_id="brand-a")
        ),
        Brand: (
            SimpleNamespace(id="brand-a", created_by="editor-b")
            if foreign_dependency == "brand"
            else SimpleNamespace(id="brand-a", created_by=user.id)
        ),
    }
    session = _FakeSession(scalar=lambda statement: records.get(_entity(statement)))
    claim_calls = []

    async def claim_spy(*args, **kwargs):
        claim_calls.append((args, kwargs))

    monkeypatch.setattr(generations_api, "claim", claim_spy)

    with pytest.raises(AppError) as rejected:
        await generations_api._create(
            scene_id=scene.id,
            payload=GenerationRequest(),
            request=_request(),
            key="same-key",
            user=user,
            session=session,
            settings=_settings(),
            operation_name="create-generation",
        )

    assert rejected.value.status_code == 404
    assert claim_calls == []
    assert _has_owner_filter(session.statements, user)


@pytest.mark.asyncio
async def test_generation_list_hides_foreign_scene_and_scopes_count_and_rows():
    user = _editor()
    foreign_scene = SimpleNamespace(id="scene-b", video_id="video-b")
    denied_session = _FakeSession(get=lambda _model, _id: foreign_scene)

    with pytest.raises(AppError) as rejected:
        await generations_api.list_scene_generations(
            "scene-b", (1, 25), user, denied_session
        )

    assert rejected.value.status_code == 404
    assert rejected.value.code == "SCENE_NOT_FOUND"
    assert _has_owner_filter(denied_session.statements, user)
    assert denied_session.get_calls == []

    owned_scene = SimpleNamespace(id="scene-a", video_id="video-a")

    def own_scene_and_empty_count(statement):
        return owned_scene if _entity(statement) is Scene else 0

    allowed_session = _FakeSession(
        scalar=own_scene_and_empty_count,
        scalars=lambda _statement: (),
    )
    result = await generations_api.list_scene_generations(
        "scene-a", (1, 25), user, allowed_session
    )

    generation_queries = [
        sql
        for _statement, sql in allowed_session.statements
        if "scene_generations" in sql.lower()
    ]
    assert result.total == 0
    assert result.items == []
    assert len(generation_queries) == 2
    assert all(user.id in sql for sql in generation_queries)


@pytest.mark.asyncio
async def test_generation_detail_returns_404_for_foreign_row_under_owner_scope():
    user = _editor()
    session = _FakeSession(get=lambda _model, _id: _generation_record())

    with pytest.raises(AppError) as rejected:
        await generations_api.get_generation("generation-b", user, session)

    assert rejected.value.status_code == 404
    assert rejected.value.code == "GENERATION_NOT_FOUND"
    assert _has_owner_filter(session.statements, user)
    assert session.get_calls == []


@pytest.mark.asyncio
async def test_generation_cancel_rejects_foreign_row_before_locking(monkeypatch):
    user = _editor()

    def return_foreign_parent_only_for_unscoped_lookup(statement):
        compiled = _sql(statement)
        if user.id in compiled:
            return None
        expression = statement.column_descriptions[0].get("expr")
        if _entity(statement) is SceneGeneration and getattr(expression, "key", None) == "video_id":
            return "video-b"
        return None

    session = _FakeSession(scalar=return_foreign_parent_only_for_unscoped_lookup)
    lock_calls = []

    async def lock_spy(*args, **kwargs):
        lock_calls.append((args, kwargs))
        return None

    monkeypatch.setattr(generations_api, "lock_revisioned_row", lock_spy)

    with pytest.raises(AppError) as rejected:
        await generations_api.cancel_generation("generation-b", user, session)

    assert rejected.value.status_code == 404
    assert rejected.value.code == "GENERATION_NOT_FOUND"
    assert _has_owner_filter(session.statements, user)
    assert lock_calls == []


@pytest.mark.asyncio
async def test_generate_all_hides_foreign_video_before_idempotency_or_service(monkeypatch):
    user = _editor()
    session = _FakeSession()
    claim_calls = []
    service_constructions = []

    async def claim_spy(*args, **kwargs):
        claim_calls.append((args, kwargs))

    class ServiceSpy:
        def __init__(self, _settings):
            service_constructions.append(True)

    monkeypatch.setattr(generations_api, "claim", claim_spy)
    monkeypatch.setattr(generations_api, "GenerationService", ServiceSpy)

    with pytest.raises(AppError) as rejected:
        await generations_api.generate_all(
            "video-b",
            GenerateAll(),
            _request(),
            "same-key",
            user,
            session,
            _settings(),
        )

    assert rejected.value.status_code == 404
    assert rejected.value.code == "VIDEO_NOT_FOUND"
    assert _has_owner_filter(session.statements, user)
    assert claim_calls == []
    assert service_constructions == []


@pytest.mark.asyncio
async def test_generation_attempt_list_hides_foreign_generation_before_member_lookup():
    user = _editor()
    session = _FakeSession()

    with pytest.raises(AppError) as rejected:
        await generations_api.list_generation_attempts("generation-b", user, session)

    assert rejected.value.status_code == 404
    assert rejected.value.code == "GENERATION_NOT_FOUND"
    assert _has_owner_filter(session.statements, user)
    assert len(session.statements) == 1


@pytest.mark.asyncio
async def test_sse_rejects_foreign_video_before_constructing_stream(monkeypatch):
    user = _editor()
    session = _FakeSession(get=lambda _model, video_id: SimpleNamespace(id=video_id))
    stream_constructions = []

    monkeypatch.setattr(events_api, "principal_from_token", lambda *_args: user)

    def stream_spy(*args):
        stream_constructions.append(args)

        async def empty_stream():
            yield "event: should-not-be-created\n\n"

        return empty_stream()

    monkeypatch.setattr(events_api, "_counted_stream", stream_spy)
    request = SimpleNamespace(cookies={"studio_session": "opaque-test-token"})
    settings = SimpleNamespace(auth_cookie_name="studio_session")

    with pytest.raises(AppError) as rejected:
        await events_api.video_events(
            "video-b", request, lambda: session, settings
        )

    assert rejected.value.status_code == 404
    assert rejected.value.code == "VIDEO_NOT_FOUND"
    assert _has_owner_filter(session.statements, user)
    assert session.get_calls == []
    assert stream_constructions == []


@pytest.mark.asyncio
async def test_sse_generator_checks_video_ownership_before_first_emission(monkeypatch):
    user = SimpleNamespace(
        id="editor-a", role="EDITOR", auth_version=1, expires_at=time.time() + 30
    )
    session = _FakeSession()
    snapshot_calls = []

    monkeypatch.setattr(events_api, "principal_from_token", lambda *_args: user)

    async def unsafe_snapshot_spy(*_args, **_kwargs):
        snapshot_calls.append(True)
        now = datetime.now(UTC)
        return events_api.EventSnapshot(
            events=[("video:video-b", 1, "video.updated", {"status": "READY"})],
            generation_cursor=(now, ""),
            final_cursor=(now, ""),
            new_generation_ids=set(),
            new_final_ids=set(),
            active_generation_ids=set(),
            active_final_ids=set(),
        )

    monkeypatch.setattr(events_api, "_snapshot", unsafe_snapshot_spy)

    class RequestStub:
        cookies = {"studio_session": "opaque-test-token"}

        async def is_disconnected(self):
            return False

    request = RequestStub()
    settings = SimpleNamespace(
        auth_cookie_name="studio_session",
        sse_heartbeat_seconds=60,
        sse_poll_seconds=0.01,
    )
    stream = events_api._stream(request, lambda: session, "video-b", settings)

    try:
        try:
            first_event = await anext(stream)
        except StopAsyncIteration:
            first_event = None
        except AppError as rejected:
            assert rejected.status_code == 404
        else:
            pytest.fail(f"foreign video emitted an SSE event: {first_event!r}")
    finally:
        await stream.aclose()

    assert _has_owner_filter(session.statements, user)
    assert session.get_calls == []
    assert snapshot_calls == []


def _snapshot_rows():
    return SimpleNamespace(
        id="video-a",
        revision=1,
        status="READY",
        current_final_video_id=None,
    )


@pytest.mark.asyncio
async def test_sse_snapshot_scopes_all_row_queries_on_each_poll_and_admin_bypasses():
    editor = _editor()
    editor_sessions = []

    def editor_factory():
        session = _FakeSession(
            scalar=lambda statement: _snapshot_rows()
            if _entity(statement) is Video
            else None,
            scalars=lambda _statement: (),
        )
        editor_sessions.append(session)
        return session

    cursor = (datetime.now(UTC), "")
    for _poll in range(2):
        await events_api._snapshot(
            editor_factory,
            "video-a",
            principal=editor,
            generation_cursor=cursor,
            final_cursor=cursor,
            active_generation_ids=set(),
            active_final_ids=set(),
        )

    expected_entities = (Video, Scene, SceneGeneration, FinalVideo)
    assert len(editor_sessions) == 2
    for session in editor_sessions:
        for model in expected_entities:
            queries = [
                statement
                for statement, _sql_text in session.statements
                if _entity(statement) is model
            ]
            assert queries
            assert all(editor.id in _where_sql(statement) for statement in queries)

    admin = SimpleNamespace(id="admin-id", role="ADMIN", auth_version=1)
    admin_sessions = []

    def admin_factory():
        session = _FakeSession(
            account_role="ADMIN",
            scalar=lambda statement: _snapshot_rows()
            if _entity(statement) is Video
            else None,
            scalars=lambda _statement: (),
        )
        admin_sessions.append(session)
        return session

    await events_api._snapshot(
        admin_factory,
        "video-a",
        principal=admin,
        generation_cursor=cursor,
        final_cursor=cursor,
        active_generation_ids=set(),
        active_final_ids=set(),
    )

    assert len(admin_sessions) == 1
    for statement, _sql_text in admin_sessions[0].statements:
        if _entity(statement) in expected_entities:
            where_sql = _where_sql(statement)
            # Admin bypasses the per-user filter, while graph-consistency
            # predicates still bind child resources to their Video owner.
            assert "created_by" in where_sql.lower()
            assert admin.id not in where_sql


@pytest.mark.asyncio
async def test_sse_snapshot_fails_closed_for_foreign_video():
    editor = _editor()
    session = _FakeSession()
    cursor = (datetime.now(UTC), "")

    with pytest.raises(AppError) as rejected:
        await events_api._snapshot(
            lambda: session,
            "video-b",
            principal=editor,
            generation_cursor=cursor,
            final_cursor=cursor,
            active_generation_ids=set(),
            active_final_ids=set(),
        )

    assert rejected.value.status_code == 404
    assert rejected.value.code == "VIDEO_NOT_FOUND"
    assert len(session.statements) == 1
    assert _has_owner_filter(session.statements, editor)


@pytest.mark.asyncio
async def test_dashboard_count_queries_are_owner_scoped_with_admin_bypass():
    editor = _editor()
    editor_session = _FakeSession(scalar=lambda _statement: 0)
    editor_summary = await dashboard_api.dashboard_summary(editor, editor_session)

    assert editor_summary.projects == 0
    assert len(editor_session.statements) == 7
    assert all(editor.id in sql for _statement, sql in editor_session.statements)

    admin = SimpleNamespace(id="admin-id", role="ADMIN")
    admin_session = _FakeSession(scalar=lambda _statement: 0)
    admin_summary = await dashboard_api.dashboard_summary(admin, admin_session)

    assert admin_summary.projects == 0
    assert len(admin_session.statements) == 7
    assert all(admin.id not in sql for _statement, sql in admin_session.statements)

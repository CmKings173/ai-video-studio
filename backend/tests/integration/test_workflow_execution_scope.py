import json
from dataclasses import replace

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from apps.api.app.api.admin import approve_workflow, create_workflow
from apps.api.app.core.errors import AppError
from apps.api.app.db.models import User, WorkflowRecord
from apps.api.app.schemas.api import WorkflowApproval, WorkflowCreate
from apps.api.app.services.workflow_loader import seed_workflows
from apps.api.app.services.workflow_registry import (
    ApprovedWorkflow,
    WorkflowRegistry,
    WorkflowSlotError,
    ingest_execution_scope,
    require_execution_scope,
)
from tests.integration.test_generation_preparation import director_workflow_data


async def admin_user(factory):
    async with factory() as session, session.begin():
        user = User(email="scope@example.test", password_hash="test", role="ADMIN")
        session.add(user)
        await session.flush()
        return user


def payload(code, scope="single_scene", profile=None):
    graph, slots, qualified, _ = director_workflow_data(execution_scope=scope)
    return WorkflowCreate(
        code=code,
        mode="t2v",
        version="1",
        execution_scope=scope,
        workflow=graph,
        slots=slots,
        profile=qualified if profile is None else profile,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["single_scene", "aggregate"])
async def test_admin_approval_replaces_only_same_scope(session_factory, scope):
    admin = await admin_user(session_factory)
    opposite = "aggregate" if scope == "single_scene" else "single_scene"
    async with session_factory() as session, session.begin():
        first = await create_workflow(payload("old", scope), admin=admin, session=session)
        await approve_workflow(first.id, WorkflowApproval(enabled=True), admin, session)
        peer = await create_workflow(payload("peer", opposite), admin=admin, session=session)
        await approve_workflow(peer.id, WorkflowApproval(enabled=True), admin, session)
        newer = await create_workflow(payload("new", scope), admin=admin, session=session)
        dto = await approve_workflow(newer.id, WorkflowApproval(enabled=True), admin, session)
        await session.flush()
        assert dto.execution_scope == scope
        rows = {r.code: r.enabled for r in (await session.scalars(select(WorkflowRecord))).all()}
        assert rows == {"old": False, "new": True, "peer": True}


@pytest.mark.parametrize("scope", ["single_scene", "aggregate"])
@pytest.mark.asyncio
async def test_database_rejects_duplicate_enabled_scope(session_factory, scope):
    admin = await admin_user(session_factory)
    async with session_factory() as session, session.begin():
        for index in range(2):
            row = WorkflowRecord(
                **payload(f"scope-{index}", scope).model_dump(),
                workflow_hash="a" * 64,
                slot_map_hash="b" * 64,
                enabled=True,
                created_by=admin.id,
            )
            if index == 0:
                session.add(row)
                await session.flush()
            else:
                with pytest.raises(IntegrityError):
                    async with session.begin_nested():
                        session.add(row)
                        await session.flush()


@pytest.mark.asyncio
async def test_scope_profile_mismatch_cannot_enable(session_factory):
    admin = await admin_user(session_factory)
    async with session_factory() as session, session.begin():
        dto = await create_workflow(payload("mismatch"), admin=admin, session=session)
        row = await session.get(WorkflowRecord, dto.id)
        row.execution_scope = "aggregate"
        with pytest.raises(AppError) as error:
            await approve_workflow(row.id, WorkflowApproval(enabled=True), admin, session)
        assert error.value.code == "WORKFLOW_EXECUTION_SCOPE_INVALID"
        assert not row.enabled


@pytest.mark.parametrize(
    "scope,profile,expected",
    [
        (None, {}, "single_scene"),
        (None, {"export_mode": "segments"}, "aggregate"),
        ("aggregate", {"export_mode": "segments"}, "aggregate"),
        ("single_scene", {"export_mode": "segments"}, "single_scene"),
    ],
)
@pytest.mark.asyncio
async def test_registry_ingest_preserves_explicit_scope_and_legacy_fallback(
    session_factory,
    tmp_path,
    scope,
    profile,
    expected,
):
    admin = await admin_user(session_factory)
    item = payload("ingested").model_dump(mode="json")
    item.pop("execution_scope")
    if scope is not None:
        item["execution_scope"] = scope
    item["profile"] = profile
    item["auto_approve"] = True
    (tmp_path / "graph.json").write_text(json.dumps(item.pop("workflow")), encoding="utf-8")
    item["file"] = "graph.json"
    (tmp_path / "registry.json").write_text(json.dumps({"workflows": [item]}), encoding="utf-8")
    assert await seed_workflows(session_factory, tmp_path, admin) == 1
    async with session_factory() as session:
        row = await session.scalar(select(WorkflowRecord))
        assert row.execution_scope == expected
        assert not row.enabled  # Scope compatibility never grants qualification.


def test_in_memory_registry_and_manifest_preserve_scope():
    single = ApprovedWorkflow("t2v", "1", {"1": {"class_type": "Test", "inputs": {}}}, {})
    aggregate = replace(single, execution_scope="aggregate")
    registry = WorkflowRegistry()
    registry.register(single)
    registry.register(aggregate)
    assert registry.resolve("t2v", "1", execution_scope="aggregate") is aggregate
    assert registry.resolve("t2v", "1", execution_scope="single_scene") is single
    assert ApprovedWorkflow.from_manifest(aggregate.manifest()).execution_scope == "aggregate"


@pytest.mark.parametrize("scope", [None, "unknown", [], {}])
def test_invalid_explicit_ingested_scope_is_never_inferred(scope):
    with pytest.raises(WorkflowSlotError, match="execution_scope"):
        ingest_execution_scope({"execution_scope": scope, "profile": {"export_mode": "segments"}})


def test_runtime_scope_validation_never_infers_missing_identity():
    from types import SimpleNamespace

    with pytest.raises(AppError, match="execution scope"):
        require_execution_scope(SimpleNamespace(profile={"export_mode": "segments"}))


@pytest.mark.parametrize("scope", ["single_scene", "aggregate"])
@pytest.mark.asyncio
async def test_seed_disables_scope_mismatch_despite_qualified_evidence(
    session_factory, tmp_path, scope
):
    admin = await admin_user(session_factory)
    items = []
    for identity, execution_scope in (("single", "single_scene"), ("aggregate", "aggregate")):
        item = payload(identity, execution_scope).model_dump(mode="json")
        (tmp_path / f"{identity}.json").write_text(
            json.dumps(item.pop("workflow")), encoding="utf-8"
        )
        item.update(file=f"{identity}.json", auto_approve=True)
        if execution_scope == scope:
            item["execution_scope"] = "aggregate" if scope == "single_scene" else "single_scene"
        items.append(item)
    (tmp_path / "registry.json").write_text(json.dumps({"workflows": items}), encoding="utf-8")
    assert await seed_workflows(session_factory, tmp_path, admin) == 2
    async with session_factory() as session:
        rows = {row.code: row for row in (await session.scalars(select(WorkflowRecord))).all()}
        mismatched = "single" if scope == "single_scene" else "aggregate"
        assert rows[mismatched].enabled is False
        assert rows["aggregate" if mismatched == "single" else "single"].enabled is True


@pytest.mark.parametrize("aggregate,expected", [(False, "single_scene"), (True, "aggregate")])
def test_director_importer_emits_explicit_scope_and_preserves_existing_entries(
    tmp_path,
    monkeypatch,
    aggregate,
    expected,
):
    from apps.api.scripts import import_director_templates as importer
    from tests.unit.test_director_config_importer import source_checkout

    source = source_checkout(tmp_path, monkeypatch)
    examples = source / "example_workflows"
    widget_names = ["steps", "cfg", "sampler", "scheduler", "width", "height", "total_frames"]
    example = {
        "links": [[11, 3, 0, 1, 0, "MODEL"]],
        "nodes": [
            {
                "id": 1,
                "type": "MiniMaxH3Director",
                "inputs": [{"name": "model", "type": "MODEL", "link": 11}]
                + [{"name": name, "widget": {"name": name}} for name in widget_names],
                "widgets_values": [25, 1.0, "euler", "simple", 864, 480, 124],
            },
            {
                "id": 2,
                "type": "SaveVideo",
                "inputs": [{"name": "filename_prefix", "widget": {"name": "filename_prefix"}}],
                "widgets_values": ["output"],
            },
            {
                "id": 3,
                "type": "UNETLoader",
                "inputs": [],
                "outputs": [{"name": "MODEL", "type": "MODEL", "links": [11]}],
            },
        ],
    }
    for task in ("t2v", "fl2v", "r2v", "v2v", "rv2v"):
        (examples / f"minimax_h3_director_{task}.json").write_text(
            json.dumps(example), encoding="utf-8"
        )
    monkeypatch.setattr(
        importer.subprocess,
        "check_output",
        lambda args, **kwargs: importer.DIRECTOR_SOURCE["commit"] if args[-1] == "HEAD" else "",
    )
    output = tmp_path / "output"
    output.mkdir()
    existing = {"code": "existing", "version": "1", "execution_scope": "aggregate"}
    (output / "registry.json").write_text(json.dumps({"workflows": [existing]}), encoding="utf-8")
    imported = importer.import_templates(source, output, aggregate=aggregate)
    assert len(imported) == 6
    assert {entry["execution_scope"] for entry in imported} == {expected}
    registry = json.loads((output / "registry.json").read_text(encoding="utf-8"))
    assert registry["workflows"][0] == existing
    assert all(not entry["auto_approve"] for entry in imported)
    if aggregate:
        for entry in imported:
            assert entry["code"] == f"H3_DIRECTOR_{entry['mode'].upper()}_BASE_AGGREGATE"
            assert entry["file"] == f"director_{entry['mode']}_aggregate.api.json"
            assert entry["version"].endswith("-aggregate-dynamic-bridge-v1")
            assert entry["profile"]["output_artifacts"] == [
                {
                    "role": "segment",
                    "node_id": "1",
                    "required": True,
                    "coverage": "all_members",
                    "transport": "studio_native_segments_v1",
                }
            ]
    repeated = importer.import_templates(source, output, aggregate=aggregate)
    assert repeated == imported
    repeated_registry = json.loads((output / "registry.json").read_text(encoding="utf-8"))
    assert repeated_registry["workflows"] == [existing, *imported]


@pytest.mark.asyncio
async def test_selection_resolves_simultaneous_scopes_and_preserves_manifest_scope(session_factory):
    from apps.api.app.core.config import Settings
    from apps.api.app.services.generation_service import GenerationService

    admin = await admin_user(session_factory)
    async with session_factory() as session, session.begin():
        ids = {}
        for scope in ("single_scene", "aggregate"):
            dto = await create_workflow(payload(scope, scope), admin=admin, session=session)
            await approve_workflow(dto.id, WorkflowApproval(enabled=True), admin, session)
            ids[scope] = dto.id
        await session.flush()
        service = GenerationService(Settings(_env_file=None))
        for scope in ("single_scene", "aggregate"):
            row, approved = await service._workflow(
                session, "t2v", None, allow_aggregate=scope == "aggregate"
            )
            assert row.id == ids[scope]
            assert approved.execution_scope == scope
            assert approved.manifest()["execution_scope"] == scope
            opposite = "aggregate" if scope == "single_scene" else "single_scene"
            with pytest.raises(AppError) as error:
                await service._workflow(
                    session, "t2v", ids[opposite], allow_aggregate=scope == "aggregate"
                )
            assert error.value.code == "WORKFLOW_EXECUTION_SCOPE_CONFLICT"


@pytest.mark.asyncio
async def test_direct_database_scope_mismatch_rejected_at_runtime(session_factory):
    from apps.api.app.services.workflow_contracts import approved_record

    admin = await admin_user(session_factory)
    async with session_factory() as session, session.begin():
        dto = await create_workflow(payload("direct-write"), admin=admin, session=session)
        row = await session.get(WorkflowRecord, dto.id)
        row.execution_scope = "aggregate"
        row.enabled = True
        await session.flush()
        with pytest.raises(AppError) as error:
            approved_record(row)
        assert error.value.code == "WORKFLOW_EXECUTION_SCOPE_INVALID"

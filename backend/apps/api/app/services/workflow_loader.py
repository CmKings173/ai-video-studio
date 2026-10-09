from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import select

from apps.api.app.core.errors import AppError
from apps.api.app.db.models import User, WorkflowRecord, utcnow
from apps.api.app.services.workflow_contracts import require_contract
from apps.api.app.services.workflow_qualification import qualification_status
from apps.api.app.services.workflow_registry import (
    ApprovedWorkflow,
    ingest_execution_scope,
    require_execution_scope,
)
from apps.api.app.services.workflow_router import require_director_execution


def _read_workflow(directory: Path, entry: dict) -> dict:
    path = directory / entry["file"]
    return json.loads(path.read_text(encoding="utf-8"))


def load_manifests(directory: Path) -> list[dict]:
    registry = directory / "registry.json"
    if not registry.exists():
        return []
    value = json.loads(registry.read_text(encoding="utf-8"))
    entries = value.get("workflows")
    if not isinstance(entries, list):
        raise ValueError("workflow registry must contain a workflows list")
    result = []
    for entry in entries:
        current = dict(entry)
        current["execution_scope"] = ingest_execution_scope(current)
        current["workflow"] = _read_workflow(directory, current)
        require_director_execution(
            SimpleNamespace(workflow=current["workflow"], profile=current.get("profile", {}))
        )
        result.append(current)
    return result


async def seed_workflows(factory, directory: Path, admin: User | None) -> int:
    if admin is None:
        return 0
    manifests = load_manifests(directory)
    inserted = 0
    async with factory() as session, session.begin():
        for item in manifests:
            exists = await session.scalar(
                select(WorkflowRecord.id).where(
                    WorkflowRecord.code == item["code"],
                    WorkflowRecord.version == item["version"],
                )
            )
            if exists:
                continue
            slots = {
                name: (str(binding[0]), str(binding[1])) for name, binding in item["slots"].items()
            }
            approved = ApprovedWorkflow(
                mode=item["mode"],
                version=item["version"],
                workflow=item["workflow"],
                slots=slots,
                required_slots=frozenset(item.get("required_slots", [])),
                execution_scope=item["execution_scope"],
            )
            approved.validate()
            enabled = (
                bool(item.get("auto_approve", False))
                and qualification_status(
                    item.get("profile", {}), approved.workflow_hash, approved.slot_map_hash
                ).qualified
            )
            if enabled:
                try:
                    require_execution_scope(
                        SimpleNamespace(
                            execution_scope=item["execution_scope"], profile=item.get("profile", {})
                        )
                    )
                    require_contract(
                        SimpleNamespace(
                            profile=item.get("profile", {}),
                            mode=item["mode"],
                            quality_profile=item.get("quality_profile", "STANDARD"),
                            execution_scope=item["execution_scope"],
                        ),
                        approved,
                    )
                except AppError:
                    enabled = False
            session.add(
                WorkflowRecord(
                    code=item["code"],
                    quality_profile=item.get("quality_profile", "STANDARD"),
                    execution_scope=item["execution_scope"],
                    mode=item["mode"],
                    version=item["version"],
                    workflow=item["workflow"],
                    slots={name: list(binding) for name, binding in slots.items()},
                    required_slots=item.get("required_slots", []),
                    profile=item.get("profile", {}),
                    workflow_hash=approved.workflow_hash,
                    slot_map_hash=approved.slot_map_hash,
                    enabled=enabled,
                    approved_at=utcnow() if enabled else None,
                    created_by=admin.id,
                )
            )
            inserted += 1
    return inserted

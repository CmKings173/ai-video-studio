from __future__ import annotations

import copy
import json
from pathlib import Path

from sqlalchemy import select

from apps.api.app.db.models import User, WorkflowRecord, utcnow
from apps.api.app.services.workflow_registry import ApprovedWorkflow


def _read_workflow(directory: Path, entry: dict) -> dict:
    path = directory / entry["file"]
    if not path.exists() and entry.get("source"):
        path = Path(entry["source"])
    workflow = json.loads(path.read_text(encoding="utf-8"))
    derive = entry.get("derive") or {}
    if derive.get("type") == "first_last":
        workflow = copy.deepcopy(workflow)
        loader_node = str(derive["loader_node"])
        target_node = str(derive["target_node"])
        workflow[loader_node] = {
            "class_type": "LoadImage",
            "inputs": {"image": "last-frame.png"},
            "_meta": {"title": "Last Frame"},
        }
        workflow[target_node]["inputs"]["last_frame"] = [loader_node, 0]
    return workflow


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
        current["workflow"] = _read_workflow(directory, current)
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
            )
            approved.validate()
            enabled = bool(item.get("auto_approve", False))
            session.add(
                WorkflowRecord(
                    code=item["code"],
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

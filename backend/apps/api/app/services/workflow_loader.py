from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import select

from apps.api.app.core.errors import AppError
from apps.api.app.db.models import User, WorkflowRecord, utcnow
from apps.api.app.providers.minimax_h3_director.capabilities import DIRECTOR_SOURCE
from apps.api.app.providers.minimax_h3_director.workflow_builder import (
    validate_export_bindings,
)
from apps.api.app.services.workflow_contracts import WorkflowProfile, require_contract
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
    identities: set[tuple[str, str]] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("workflow registry entries must be objects")
        current = dict(entry)
        code, version = current.get("code"), current.get("version")
        if (
            not isinstance(code, str)
            or not code.strip()
            or not isinstance(version, str)
            or not version.strip()
        ):
            raise ValueError("workflow code and version are required")
        identity = (code, version)
        if identity in identities:
            raise ValueError(f"duplicate workflow code/version identity: {code}/{version}")
        identities.add(identity)

        current["execution_scope"] = ingest_execution_scope(current)
        profile = current.get("profile")
        if not isinstance(profile, dict):
            raise ValueError(f"workflow {code}/{version} profile must be an object")
        try:
            require_execution_scope(
                SimpleNamespace(execution_scope=current["execution_scope"], profile=profile)
            )
        except AppError as exc:
            raise ValueError(f"workflow {code}/{version} has an invalid execution scope") from exc

        is_h3_template = code.startswith("H3_DIRECTOR_") or profile.get("provider") == (
            DIRECTOR_SOURCE["provider"]
        )
        quality_profile = current.get("quality_profile")
        if is_h3_template and (
            not isinstance(quality_profile, str)
            or not quality_profile
            or profile.get("quality_profile") != quality_profile
        ):
            raise ValueError(f"workflow {code}/{version} quality profile is inconsistent")
        if is_h3_template and (
            profile.get("provider") != DIRECTOR_SOURCE["provider"]
            or profile.get("provider_source") != DIRECTOR_SOURCE
        ):
            raise ValueError(f"workflow {code}/{version} provider source pin is invalid")

        raw_slots = current.get("slots")
        required_slots = current.get("required_slots", [])
        if not isinstance(raw_slots, dict) or any(
            not isinstance(binding, list)
            or len(binding) != 2
            or any(not isinstance(value, str) or not value for value in binding)
            for binding in raw_slots.values()
        ):
            raise ValueError(f"workflow {code}/{version} slots must map to [node, input] strings")
        if not isinstance(required_slots, list) or any(
            not isinstance(name, str) or not name for name in required_slots
        ):
            raise ValueError(f"workflow {code}/{version} required_slots must be a string list")

        current["workflow"] = _read_workflow(directory, current)
        require_director_execution(
            SimpleNamespace(workflow=current["workflow"], profile=profile)
        )
        approved = ApprovedWorkflow(
            mode=current.get("mode"),
            version=version,
            workflow=current["workflow"],
            slots={name: (binding[0], binding[1]) for name, binding in raw_slots.items()},
            required_slots=frozenset(required_slots),
            execution_scope=current["execution_scope"],
        )
        approved.validate()
        for name, actual in (
            ("workflow_hash", approved.workflow_hash),
            ("slot_map_hash", approved.slot_map_hash),
        ):
            if (is_h3_template and name not in profile) or (
                name in profile and profile[name] != actual
            ):
                raise ValueError(f"workflow {code}/{version} {name} does not match content")
        if is_h3_template:
            validate_export_bindings(approved.workflow, profile)
            WorkflowProfile.model_validate(profile)
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

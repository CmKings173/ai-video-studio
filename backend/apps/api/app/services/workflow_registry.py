"""Approved ComfyUI workflow registry with symbolic input/output slots."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from apps.api.app.core.errors import AppError


class WorkflowSlotError(ValueError):
    """Raised when a workflow slot cannot be resolved or patched safely."""


SlotBinding = tuple[str, str]

EXECUTION_SCOPES = frozenset({"single_scene", "aggregate"})


def ingest_execution_scope(value: Mapping[str, Any]) -> str:
    """Infer scope only at the boundary importing a legacy manifest."""
    if "execution_scope" in value:
        scope = value["execution_scope"]
    else:
        profile = value.get("profile") or {}
        if not isinstance(profile, Mapping):
            raise WorkflowSlotError("Workflow profile must be an object")
        scope = "aggregate" if profile.get("export_mode") == "segments" else "single_scene"
    if not isinstance(scope, str) or scope not in EXECUTION_SCOPES:
        raise WorkflowSlotError("execution_scope must be single_scene or aggregate")
    return scope


def require_execution_scope(record: Any) -> str:
    """Validate persisted domain identity against provider transport, never infer it."""
    scope = getattr(record, "execution_scope", None)
    profile = record.profile
    if (
        not isinstance(scope, str)
        or scope not in EXECUTION_SCOPES
        or not isinstance(profile, Mapping)
        or ("execution_scope" in profile and profile["execution_scope"] != scope)
        or (profile.get("export_mode") == "segments") != (scope == "aggregate")
    ):
        raise AppError(
            "WORKFLOW_EXECUTION_SCOPE_INVALID",
            "Workflow execution scope does not match its provider profile",
            409,
        )
    return scope


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ApprovedWorkflow:
    mode: str
    version: str
    workflow: Mapping[str, Any]
    slots: Mapping[str, SlotBinding]
    required_slots: frozenset[str] = field(default_factory=frozenset)
    execution_scope: str = "single_scene"

    @property
    def workflow_hash(self) -> str:
        return _stable_hash(self.workflow)

    @property
    def slot_map_hash(self) -> str:
        return _stable_hash(self.slots)

    def patch(self, values: Mapping[str, Any]) -> dict[str, Any]:
        unknown = set(values) - set(self.slots)
        if unknown:
            names = ", ".join(sorted(unknown))
            raise WorkflowSlotError(f"Unknown workflow slot(s): {names}")

        missing = set(self.required_slots) - set(values)
        if missing:
            names = ", ".join(sorted(missing))
            raise WorkflowSlotError(f"Missing required workflow slot(s): {names}")

        patched = deepcopy(dict(self.workflow))
        for role, value in values.items():
            node_id, input_name = self.slots[role]
            try:
                node = patched[node_id]
                inputs = node["inputs"]
            except (KeyError, TypeError) as exc:
                raise WorkflowSlotError(
                    f"Slot {role} points to missing node/input container {node_id!r}"
                ) from exc
            if input_name not in inputs:
                raise WorkflowSlotError(
                    f"Slot {role} points to missing input {input_name!r} on node {node_id!r}"
                )
            inputs[input_name] = value
        return patched

    def validate(self) -> None:
        if (
            not isinstance(self.execution_scope, str)
            or self.execution_scope not in EXECUTION_SCOPES
        ):
            raise WorkflowSlotError("execution_scope must be single_scene or aggregate")
        if not self.mode or not self.version or not self.workflow:
            raise WorkflowSlotError("mode, version and workflow are required")
        if not isinstance(self.workflow, Mapping) or not isinstance(self.slots, Mapping):
            raise WorkflowSlotError("Workflow graph and slots must be objects")
        for node_id, node in self.workflow.items():
            if (
                not isinstance(node_id, str)
                or not isinstance(node, Mapping)
                or not isinstance(node.get("class_type"), str)
                or not isinstance(node.get("inputs"), Mapping)
            ):
                raise WorkflowSlotError("Workflow must be an API graph with class_type and inputs")
        for role, binding in self.slots.items():
            if (
                not isinstance(role, str)
                or not role
                or not isinstance(binding, (list, tuple))
                or len(binding) != 2
            ):
                raise WorkflowSlotError("Every slot needs a role and [node_id, input_name]")
            node_id, input_name = str(binding[0]), str(binding[1])
            node = self.workflow.get(node_id)
            if not isinstance(node, Mapping) or not isinstance(node.get("inputs"), Mapping):
                raise WorkflowSlotError(f"Slot {role} points to missing node {node_id!r}")
            if input_name not in node["inputs"]:
                raise WorkflowSlotError(
                    f"Slot {role} points to missing input {input_name!r} on node {node_id!r}"
                )
        missing = self.required_slots - set(self.slots)
        if missing:
            raise WorkflowSlotError(
                f"Workflow declares missing required slot(s): {', '.join(sorted(missing))}"
            )
        if len(set(tuple(binding) for binding in self.slots.values())) != len(self.slots):
            raise WorkflowSlotError(
                "Different symbolic slots cannot overwrite the same graph input"
            )

    def manifest(self) -> dict[str, Any]:
        self.validate()
        return {
            "mode": self.mode,
            "execution_scope": self.execution_scope,
            "version": self.version,
            "workflow": deepcopy(dict(self.workflow)),
            "slots": {name: list(binding) for name, binding in self.slots.items()},
            "required_slots": sorted(self.required_slots),
            "workflow_hash": self.workflow_hash,
            "slot_map_hash": self.slot_map_hash,
        }

    @classmethod
    def from_manifest(cls, value: Mapping[str, Any]) -> ApprovedWorkflow:
        if not isinstance(value.get("workflow"), Mapping) or not isinstance(
            value.get("slots"), Mapping
        ):
            raise WorkflowSlotError("Workflow graph and slots must be objects")
        if any(
            not isinstance(binding, (list, tuple)) or len(binding) != 2
            for binding in value["slots"].values()
        ):
            raise WorkflowSlotError("Every slot needs [node_id, input_name]")
        workflow = cls(
            mode=str(value["mode"]),
            version=str(value["version"]),
            workflow=value["workflow"],
            slots={
                name: (str(binding[0]), str(binding[1])) for name, binding in value["slots"].items()
            },
            required_slots=frozenset(value.get("required_slots", ())),
            execution_scope=ingest_execution_scope(value),
        )
        workflow.validate()
        for field_name, actual in (
            ("workflow_hash", workflow.workflow_hash),
            ("slot_map_hash", workflow.slot_map_hash),
        ):
            expected = value.get(field_name)
            if expected and expected != actual:
                raise WorkflowSlotError(f"{field_name} does not match manifest content")
        return workflow


class WorkflowRegistry:
    def __init__(self) -> None:
        self._workflows: dict[tuple[str, str, str], ApprovedWorkflow] = {}

    def register(self, workflow: ApprovedWorkflow) -> None:
        workflow.validate()
        key = (workflow.mode, workflow.version, workflow.execution_scope)
        if key in self._workflows:
            raise ValueError(f"Workflow already registered: {workflow.mode}@{workflow.version}")
        self._workflows[key] = workflow

    def resolve(
        self, mode: str, version: str, *, execution_scope: str = "single_scene"
    ) -> ApprovedWorkflow:
        return self._workflows[(mode, version, execution_scope)]

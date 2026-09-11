"""Approved ComfyUI workflow registry with symbolic input/output slots."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any


class WorkflowSlotError(ValueError):
    """Raised when a workflow slot cannot be resolved or patched safely."""


SlotBinding = tuple[str, str]


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
        if not self.mode or not self.version or not self.workflow:
            raise WorkflowSlotError("mode, version and workflow are required")
        for role, binding in self.slots.items():
            if not isinstance(role, str) or not role or len(binding) != 2:
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

    def manifest(self) -> dict[str, Any]:
        self.validate()
        return {
            "mode": self.mode,
            "version": self.version,
            "workflow": deepcopy(dict(self.workflow)),
            "slots": {name: list(binding) for name, binding in self.slots.items()},
            "required_slots": sorted(self.required_slots),
            "workflow_hash": self.workflow_hash,
            "slot_map_hash": self.slot_map_hash,
        }

    @classmethod
    def from_manifest(cls, value: Mapping[str, Any]) -> ApprovedWorkflow:
        workflow = cls(
            mode=str(value["mode"]),
            version=str(value["version"]),
            workflow=value["workflow"],
            slots={
                name: (str(binding[0]), str(binding[1])) for name, binding in value["slots"].items()
            },
            required_slots=frozenset(value.get("required_slots", ())),
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
        self._workflows: dict[tuple[str, str], ApprovedWorkflow] = {}

    def register(self, workflow: ApprovedWorkflow) -> None:
        workflow.validate()
        key = (workflow.mode, workflow.version)
        if key in self._workflows:
            raise ValueError(f"Workflow already registered: {workflow.mode}@{workflow.version}")
        self._workflows[key] = workflow

    def resolve(self, mode: str, version: str) -> ApprovedWorkflow:
        return self._workflows[(mode, version)]

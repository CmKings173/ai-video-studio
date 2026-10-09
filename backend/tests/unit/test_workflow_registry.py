import pytest

from apps.api.app.services.workflow_registry import (
    ApprovedWorkflow,
    WorkflowRegistry,
    WorkflowSlotError,
)


def sample_workflow() -> dict:
    return {
        "10": {"class_type": "Prompt", "inputs": {"value": "old prompt"}},
        "20": {"class_type": "SaveVideo", "inputs": {"filename_prefix": "old"}},
    }


def sample_approved() -> ApprovedWorkflow:
    return ApprovedWorkflow(
        mode="t2v",
        version="2026-09-10",
        workflow=sample_workflow(),
        slots={
            "PROMPT": ("10", "value"),
            "OUTPUT_PREFIX": ("20", "filename_prefix"),
        },
        required_slots=frozenset({"PROMPT", "OUTPUT_PREFIX"}),
    )


def test_patch_returns_copy_and_injects_symbolic_slots() -> None:
    approved = sample_approved()

    patched = approved.patch({"PROMPT": "a red bottle on a table", "OUTPUT_PREFIX": "gen-123"})

    assert patched["10"]["inputs"]["value"] == "a red bottle on a table"
    assert patched["20"]["inputs"]["filename_prefix"] == "gen-123"
    assert approved.workflow == sample_workflow()
    assert patched is not approved.workflow


def test_patch_rejects_missing_required_slot() -> None:
    approved = sample_approved()

    with pytest.raises(WorkflowSlotError, match="PROMPT"):
        approved.patch({"OUTPUT_PREFIX": "gen-123"})


def test_patch_rejects_unknown_slot() -> None:
    approved = sample_approved()

    with pytest.raises(WorkflowSlotError, match="UNKNOWN"):
        approved.patch({"PROMPT": "ok", "OUTPUT_PREFIX": "gen-123", "UNKNOWN": 1})


def test_registry_resolves_only_registered_mode_and_version() -> None:
    registry = WorkflowRegistry()
    approved = sample_approved()
    registry.register(approved)

    assert registry.resolve("t2v", "2026-09-10") is approved

    with pytest.raises(KeyError):
        registry.resolve("i2v", "2026-09-10")

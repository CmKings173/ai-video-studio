from pathlib import Path

import pytest

from apps.api.app.services.workflow_loader import load_manifests
from apps.api.scripts.h3_probe import H3Probe
from tests.historical_workflow_fixtures import historical_manifests

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
async def test_legacy_probe_rejects_director_before_submission():
    entry = next(
        entry
        for entry in load_manifests(ROOT / "workflows" / "h3")
        if entry.get("profile", {}).get("provider") == "minimax_h3_director"
    )
    result = await H3Probe().run(entry, execute=True)
    assert result.accepted is False
    assert result.executed is False
    assert result.error_code == "DIRECTOR_PROBE_REQUIRED"


@pytest.mark.asyncio
async def test_historical_workflows_retain_offline_audit_preflight():
    manifests = historical_manifests()

    results = [await H3Probe().run(entry, execute=False) for entry in manifests]

    assert {result.mode for result in results} == {
        "t2v",
        "i2v",
        "i2v_last",
        "i2v_first_last",
        "r2v",
    }
    assert all(result.accepted for result in results)
    assert all(result.executed is False for result in results)
    assert all(result.frames == 124 for result in results)
    assert all(len(result.workflow_hash) == 64 for result in results)
    assert all(len(result.slot_map_hash) == 64 for result in results)
    assert all(result.error_code is None for result in results)


@pytest.mark.asyncio
async def test_probe_rejects_manifest_with_missing_output_node():
    entry = historical_manifests()[0]
    entry["profile"] = {**entry["profile"], "output_node": "does-not-exist"}

    result = await H3Probe().run(entry, execute=False)

    assert result.accepted is False
    assert result.error_code == "WORKFLOW_OUTPUT_NODE_MISSING"

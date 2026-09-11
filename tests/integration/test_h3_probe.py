from pathlib import Path

import pytest

from apps.api.app.services.workflow_loader import load_manifests
from apps.api.scripts.h3_probe import H3Probe

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
async def test_reference_workflows_pass_offline_h3_preflight():
    manifests = load_manifests(ROOT / "workflows" / "h3")

    results = [await H3Probe().run(entry, execute=False) for entry in manifests]

    assert {result.mode for result in results} == {
        "t2v",
        "i2v",
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
    entry = load_manifests(ROOT / "workflows" / "h3")[0]
    entry["profile"] = {**entry["profile"], "output_node": "does-not-exist"}

    result = await H3Probe().run(entry, execute=False)

    assert result.accepted is False
    assert result.error_code == "WORKFLOW_OUTPUT_NODE_MISSING"

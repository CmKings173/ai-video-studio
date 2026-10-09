import json
from pathlib import Path

import pytest

from apps.api.app.providers.minimax_h3_director.contracts import DirectorExecutionSpec
from apps.api.app.providers.minimax_h3_director.qualification import qualified_features
from apps.api.app.providers.minimax_h3_director.workflow_builder import DirectorWorkflowBuilder
from apps.api.app.services.workflow_registry import ApprovedWorkflow
from tests.unit.test_director_execution_contract import candidate

DIRECTORY = Path(__file__).resolve().parents[2] / "workflows" / "h3"
ENTRIES = [
    entry
    for entry in json.loads((DIRECTORY / "registry.json").read_text())["workflows"]
    if entry["code"].startswith("H3_DIRECTOR_")
]


@pytest.mark.parametrize("entry", ENTRIES, ids=lambda entry: entry["code"])
def test_each_current_task_scope_has_static_paths_but_no_runtime_promotion(entry):
    graph = json.loads((DIRECTORY / entry["file"]).read_text())
    approved = ApprovedWorkflow(
        entry["mode"],
        entry["version"],
        graph,
        {key: tuple(value) for key, value in entry["slots"].items()},
        execution_scope=entry["execution_scope"],
    )
    approved.validate()
    assert approved.workflow_hash == entry["profile"]["workflow_hash"]
    assert approved.slot_map_hash == entry["profile"]["slot_map_hash"]
    assert entry["auto_approve"] is False
    assert entry["profile"]["poc_verified"] is False
    assert "execution_evidence" not in entry["profile"]
    assert not any(qualified_features(entry["profile"]).values())
    base, _ = candidate(entry["mode"])
    for refine, face in [(False, False), (True, False), (False, True), (True, True)]:
        values = {
            **base.model_dump(),
            "refine": {"enabled": refine},
            "face_refine": {"enabled": face},
            "provenance": {"workflow_profile": entry["profile"]},
        }
        if entry["execution_scope"] == "aggregate":
            values.update(
                member_count=1,
                timeline=[{"scene_id": "static", "prompt": "Exact", "frame_count": 124}],
            )
        spec = DirectorExecutionSpec.finalize(**values)
        built = DirectorWorkflowBuilder().build(base_workflow=graph, spec=spec, staged_assets={})
        director = next(
            node
            for node in built.values()
            if node["class_type"] in {"MiniMaxH3Director", "StudioMiniMaxH3Director"}
        )
        assert ("refine" in director["inputs"]) == refine
        assert ("face_refine" in director["inputs"]) == face


def test_registry_does_not_expand_into_feature_combinations():
    assert len(ENTRIES) == 12
    assert len({(entry["mode"], entry["execution_scope"]) for entry in ENTRIES}) == 12

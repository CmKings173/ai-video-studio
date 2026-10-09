from copy import deepcopy

import pytest

from apps.api.app.providers.minimax_h3_director.contracts import DirectorExecutionSpec
from apps.api.app.providers.minimax_h3_director.workflow_builder import (
    DirectorWorkflowBuilder,
    DirectorWorkflowError,
)
from tests.unit.test_director_execution_contract import candidate as base_candidate


def candidate(**settings):
    spec, graph = base_candidate()
    return DirectorExecutionSpec.finalize(**{**spec.model_dump(), **settings}), graph


@pytest.mark.parametrize(
    "feature,cls",
    [
        ("refine", "MiniMaxH3DirectorRefine"),
        ("face_refine", "MiniMaxH3DirectorFaceRefine"),
    ],
)
def test_actual_config_path_and_enabled_patch(feature, cls):
    spec, template = candidate(**{feature: {"enabled": True}})
    config_id = next(key for key, node in template.items() if node["class_type"] == cls)
    director = next(node for node in template.values() if node["class_type"] == "MiniMaxH3Director")
    assert director["inputs"][feature] == [config_id, 0]
    built = DirectorWorkflowBuilder().build(base_workflow=template, spec=spec, staged_assets={})
    assert built[config_id]["inputs"]["seed_mode"] == "inherit"


def test_base_disconnects_optional_features():
    spec, template = candidate()
    built = DirectorWorkflowBuilder().build(base_workflow=template, spec=spec, staged_assets={})
    director = next(node for node in built.values() if node["class_type"] == "MiniMaxH3Director")
    assert "refine" not in director["inputs"]
    assert "face_refine" not in director["inputs"]


@pytest.mark.parametrize(
    "feature,cls",
    [
        ("refine", "MiniMaxH3DirectorRefine"),
        ("face_refine", "MiniMaxH3DirectorFaceRefine"),
    ],
)
@pytest.mark.parametrize("damage", ["missing", "duplicate", "wrong_socket", "wrong_type"])
def test_enabled_path_rejects_damage(feature, cls, damage):
    spec, template = candidate(**{feature: {"enabled": True}})
    key = next(key for key, node in template.items() if node["class_type"] == cls)
    director = next(node for node in template.values() if node["class_type"] == "MiniMaxH3Director")
    if damage == "missing":
        del template[key]
    elif damage == "duplicate":
        template["duplicate"] = deepcopy(template[key])
    elif damage == "wrong_type":
        template[key]["class_type"] = "UnknownConfig"
    else:
        director["inputs"][feature] = [key, 1]
    with pytest.raises(DirectorWorkflowError):
        DirectorWorkflowBuilder().build(base_workflow=template, spec=spec, staged_assets={})


def test_combined_patches_independently():
    spec, template = candidate(
        refine={"enabled": True, "passes": 2},
        face_refine={"enabled": True, "steps": 12, "blend": 0.5},
    )
    built = DirectorWorkflowBuilder().build(base_workflow=template, spec=spec, staged_assets={})
    configs = {node["class_type"]: node["inputs"] for node in built.values()}
    assert configs["MiniMaxH3DirectorRefine"]["passes"] == 2
    assert configs["MiniMaxH3DirectorFaceRefine"]["steps"] == 12
    assert configs["MiniMaxH3DirectorFaceRefine"]["blend"] == 0.5


@pytest.mark.parametrize(
    "feature,values",
    [
        ("refine", {"mode": "guess"}),
        ("refine", {"aspect_ratio": "16:9"}),
        ("face_refine", {"select": "guess"}),
        ("face_refine", {"undeclared": True}),
    ],
)
def test_frozen_unknown_settings_rejected_without_object_info(feature, values):
    spec, template = candidate(**{feature: {"enabled": True, **values}})
    with pytest.raises(DirectorWorkflowError):
        DirectorWorkflowBuilder().build(base_workflow=template, spec=spec, staged_assets={})

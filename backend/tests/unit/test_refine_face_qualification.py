from copy import deepcopy

import pytest
from pydantic import ValidationError

from apps.api.app.core.errors import AppError
from apps.api.app.providers.minimax_h3_director.qualification import (
    director_settings_identity,
    qualified_features,
    require_settings,
)
from apps.api.app.schemas.api import (
    DirectorAudioPolicy,
    DirectorFaceRefine,
    DirectorMotionContext,
    DirectorRefine,
)
from apps.api.app.services.generation_intent import stable_hash
from tests.unit.test_director_execution_contract import candidate
from tests.unit.test_workflow_qualification import evidence_profile


def settings(refine=False, face=False):
    return {
        "motion_context": DirectorMotionContext().model_dump(),
        "refine": DirectorRefine(enabled=refine).model_dump(),
        "face_refine": DirectorFaceRefine(enabled=face).model_dump(),
        "audio_policy": DirectorAudioPolicy().model_dump(),
    }


@pytest.mark.parametrize("refine,face", [(True, False), (False, True), (True, True)])
def test_static_support_never_qualifies(refine, face):
    profile = {
        "feature_support": {
            "refine": {"statically_valid": True},
            "face_refine": {"statically_valid": True},
        }
    }
    assert qualified_features(profile) == {
        "refine": False,
        "face_refine": False,
        "motion_context": False,
    }
    with pytest.raises(AppError, match="executed evidence"):
        require_settings(profile, settings(refine, face))


def test_settings_cases_do_not_qualify_combination_or_changed_fields():
    profile = evidence_profile()  # Synthetic test data only, never a registry artifact.
    from apps.api.app.services.workflow_contracts import profile_hash

    profile.update(fps=24, weight_hashes={"face_yolov8m.pt": "a" * 64}, dependency_versions={})
    baseline = profile["execution_evidence"]
    baseline.update(
        profile_hash=profile_hash(profile),
        weight_hashes=deepcopy(profile["weight_hashes"]),
        dependency_versions={},
        combinations=[{"width": 864, "height": 480, "mode": "t2v"}],
    )
    baseline["director_settings"] = []
    for config in (settings(True, False), settings(False, True)):
        case = {
            key: deepcopy(value) for key, value in baseline.items() if key != "director_settings"
        }
        canvas = {"width": 864, "height": 480}
        case.update(
            settings=config,
            base_canvas=canvas,
            task="t2v",
            settings_hash=director_settings_identity(config, base_canvas=canvas, task="t2v"),
        )
        baseline["director_settings"].append(case)
        require_settings(profile, config)
    with pytest.raises(AppError):
        require_settings(profile, settings(True, True))
    for feature, field, value in [
        ("refine", "passes", 2),
        ("refine", "megapixels", 2.0),
        ("refine", "upscale_method", "lanczos"),
        ("face_refine", "steps", 12),
        ("face_refine", "canvas_width", 800),
        ("face_refine", "blend", 0.5),
    ]:
        changed = settings(feature == "refine", feature == "face_refine")
        changed[feature][field] = value
        with pytest.raises(AppError):
            require_settings(profile, changed)


def test_frozen_settings_are_signed_and_roundtrip():
    from apps.api.app.providers.minimax_h3_director.contracts import DirectorExecutionSpec

    spec, _ = candidate()
    signed = DirectorExecutionSpec.finalize(**{**spec.model_dump(), **settings(True, True)})
    assert signed.execution_hash != spec.execution_hash
    restored = DirectorExecutionSpec.model_validate(signed.model_dump(mode="json"))
    assert restored.refine == settings(True, True)["refine"]
    altered = signed.model_dump(mode="json")
    altered["face_refine"]["steps"] += 1
    with pytest.raises(ValidationError, match="hash"):
        DirectorExecutionSpec.model_validate(altered)


def test_intent_identity_canonicalizes_order_and_distinguishes_feature_settings():
    from apps.api.app.services.generation_intent import GenerationIntent

    spec, _ = candidate()
    frozen = spec.model_dump()
    values = {key: value for key, value in frozen.items() if key in GenerationIntent.model_fields}
    values["provider_task"] = spec.task
    values.update(settings())
    base = GenerationIntent.model_validate(values)
    assert (
        GenerationIntent.model_validate(dict(reversed(list(values.items())))).semantic_hash
        == base.semantic_hash
    )
    hashes = {base.semantic_hash}
    for refine, face in [(True, False), (False, True), (True, True)]:
        modified = {**values, **settings(refine, face)}
        hashes.add(GenerationIntent.model_validate(modified).semantic_hash)
    assert len(hashes) == 4
    modified = {**values, **settings(True, True)}
    before = GenerationIntent.model_validate(modified).semantic_hash
    modified["face_refine"]["canvas_width"] = 800
    assert GenerationIntent.model_validate(modified).semantic_hash != before


def test_aggregate_qualification_identity_contains_exact_optional_settings():
    from apps.api.app.providers.minimax_h3_director.contracts import DirectorExecutionSpec
    from apps.api.app.services.director_run_service import director_aggregate_settings

    spec, _ = candidate()
    identities = set()
    for refine, face in [(False, False), (True, False), (False, True), (True, True)]:
        frozen = DirectorExecutionSpec.finalize(**{**spec.model_dump(), **settings(refine, face)})
        config = director_aggregate_settings(
            frozen,
            member_count=3,
            continuities=["CUT", "CONTINUOUS", "CONTINUOUS"],
            output_artifacts=[],
        )
        assert config["refine"] == frozen.refine
        assert config["face_refine"] == frozen.face_refine
        identities.add(stable_hash(config))
    assert len(identities) == 4


@pytest.mark.parametrize(
    "values",
    [
        {"aspect_ratio": "Custom"},
        {"enabled": True, "width": 1280},
        {"enabled": True, "height": 736},
    ],
)
def test_ignored_upstream_geometry_rejected(values):
    with pytest.raises(ValidationError):
        DirectorRefine.model_validate(values)

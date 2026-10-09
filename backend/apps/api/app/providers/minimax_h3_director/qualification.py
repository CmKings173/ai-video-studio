"""Optional Director behavior needs evidence for the exact frozen settings."""

from typing import Any

from pydantic import ValidationError

from apps.api.app.core.errors import AppError
from apps.api.app.schemas.api import (
    DirectorAudioPolicy,
    DirectorFaceRefine,
    DirectorMotionContext,
    DirectorRefine,
)
from apps.api.app.services.generation_intent import stable_hash
from apps.api.app.services.qualification_binding import optional_weights_bound, provenance_matches
from apps.api.app.services.workflow_qualification import ExecutionEvidence, require_qualified

from .output_canvas import resolve_director_output_canvas


def director_settings_identity(settings, *, base_canvas, task) -> str:
    return stable_hash({"settings": settings, "base_canvas": base_canvas, "task": task})


def matches_output_canvas(profile, case, settings, base_canvas=None, task=None) -> bool:
    """Bind a settings case to an executed input canvas, including frozen dispatch."""
    combinations = (profile.get("execution_evidence") or {}).get("combinations", [])
    if not isinstance(combinations, list):
        return False
    declared = case.get("base_canvas")
    if not isinstance(declared, dict) or set(declared) != {"width", "height"}:
        return False
    declared_canvas = (declared["width"], declared["height"])
    if not isinstance(case.get("task"), str):
        return False
    for combination in combinations:
        if not isinstance(combination, dict):
            continue
        canvas = (combination.get("width"), combination.get("height"))
        mode = combination.get("mode")
        if (
            declared_canvas != canvas
            or case["task"] != mode
            or base_canvas is not None
            and tuple(base_canvas) != canvas
            or task is not None
            and task != mode
        ):
            continue
        try:
            expected = resolve_director_output_canvas(canvas, settings["refine"], task=mode)
        except (ValueError, TypeError):
            continue
        output = case.get("output") or {}
        if (output.get("width"), output.get("height")) == expected:
            return True
    return False


def verified_settings(
    profile: dict[str, Any], *, base_canvas=None, task=None
) -> list[dict[str, Any]]:
    baseline = profile.get("execution_evidence") or {}
    if not isinstance(baseline, dict):
        return []
    try:
        require_qualified(profile, baseline.get("workflow_hash"), baseline.get("slot_map_hash"))
    except AppError:
        return []
    cases = baseline.get("director_settings", [])
    if not isinstance(cases, list):
        cases = []
    verified = []
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("settings"), dict):
            continue
        if case.get("settings_hash") != director_settings_identity(
            case["settings"], base_canvas=case.get("base_canvas"), task=case.get("task")
        ):
            continue
        try:
            evidence = ExecutionEvidence.model_validate(case)
            models = {
                "motion_context": DirectorMotionContext,
                "refine": DirectorRefine,
                "face_refine": DirectorFaceRefine,
                "audio_policy": DirectorAudioPolicy,
            }
            normalized = {
                name: model.model_validate(case["settings"].get(name, {})).model_dump(mode="json")
                for name, model in models.items()
            }
            if normalized != case["settings"]:
                continue
        except ValidationError:
            continue
        if (
            not evidence.executed
            or not evidence.output.has_video
            or not provenance_matches(profile, case)
            or not optional_weights_bound(profile, normalized)
            or evidence.output.fps != profile.get("fps")
            or (normalized["audio_policy"]["mode"] != "mute" and not evidence.output.has_audio)
            or not matches_output_canvas(profile, case, normalized, base_canvas, task)
        ):
            continue
        verified.append(case)
    return verified


def qualified_features(profile: dict[str, Any], *, base_canvas=None, task=None) -> dict[str, bool]:
    verified = verified_settings(profile, base_canvas=base_canvas, task=task)
    return {
        feature: any(bool(case["settings"].get(feature, {}).get("enabled")) for case in verified)
        for feature in ("motion_context", "refine", "face_refine")
    }


def qualified_audio_modes(profile: dict[str, Any], *, base_canvas=None, task=None) -> list[str]:
    return sorted(
        {
            "generate",
            *(
                case["settings"].get("audio_policy", {}).get("mode", "generate")
                for case in verified_settings(profile, base_canvas=base_canvas, task=task)
            ),
        }
        & {"generate", "source", "mute"}
    )


def require_settings(
    profile: dict[str, Any], settings: dict[str, Any], *, base_canvas=None, task=None
) -> None:
    special = any(
        settings[name].get("enabled") for name in ("motion_context", "refine", "face_refine")
    )
    special = special or settings["audio_policy"].get("mode", "generate") != "generate"
    if not special:
        return
    if any(
        case.get("settings") == settings
        and matches_output_canvas(profile, case, settings, base_canvas, task)
        for case in verified_settings(profile)
    ):
        return
    raise AppError(
        "DIRECTOR_SETTINGS_NOT_QUALIFIED",
        "Exact Director Motion Context, Refine, Face Refine and audio settings "
        "need executed evidence",
        409,
    )

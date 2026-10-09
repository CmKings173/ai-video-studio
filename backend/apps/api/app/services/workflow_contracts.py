"""One graph-bound profile contract for enable, capabilities and execution."""

from __future__ import annotations

import copy
import math
from dataclasses import fields
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool

from apps.api.app.core.errors import AppError
from apps.api.app.providers.minimax_h3_director.graph_identity import (
    DIRECTOR_CLASS_TYPES,
    is_director_graph,
)
from apps.api.app.providers.minimax_h3_director.output_canvas import resolve_director_output_canvas
from apps.api.app.schemas.api import GenerationMode, GenerationRatio, QualityProfile
from apps.api.app.services.executable_weights import collect_executable_weights
from apps.api.app.services.h3_validator import H3Profile
from apps.api.app.services.qualification_binding import execution_profile_hash
from apps.api.app.services.workflow_qualification import SHA256, MeasuredOutput, require_qualified
from apps.api.app.services.workflow_registry import (
    ApprovedWorkflow,
    _stable_hash,
    require_execution_scope,
)

SELECTOR_SHA256 = "3fe5c3f9aeceed343725a91c6feaa13377b5ebad2c95fd633e3806d69d10bcc8"
DURATION_EXPRESSION = "max(5, round(a * 24)) + (5 - (max(5, round(a * 24)) % 17)) % 17"
DIRECTOR_SOURCE_MAX_DIMENSION = 8192
SELECTOR_LABELS = {
    "1:1": "1:1 (Square)",
    "2:3": "2:3 (Portrait Photo)",
    "3:2": "3:2 (Photo)",
    "3:4": "3:4 (Portrait Standard)",
    "4:3": "4:3 (Standard)",
    "9:16": "9:16 (Portrait Widescreen)",
    "16:9": "16:9 (Widescreen)",
    "21:9": "21:9 (Ultrawide)",
}


class WorkflowProfile(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True, allow_inf_nan=False)
    quality_profile: QualityProfile
    steps: int = Field(ge=1, le=100)
    fps: Literal[24]
    sampler: str = ""
    scheduler: str = ""
    turbo: StrictBool = False
    resolution: dict[str, Any]
    duration_resolver: dict[str, Any]
    dependency_versions: dict[str, str]
    setting_bindings: dict[str, list[str]] = Field(default_factory=dict)
    weight_hashes: dict[str, SHA256] = Field(default_factory=dict)


class ExecutedCombination(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    mode: GenerationMode
    quality_profile: QualityProfile
    aspect_ratio: GenerationRatio
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    steps: int = Field(gt=0)
    output: MeasuredOutput


def profile_hash(profile: dict) -> str:
    return execution_profile_hash(profile)


def invalid(message: str) -> AppError:
    return AppError("WORKFLOW_NOT_QUALIFIED", message, 409)


def resolve_canvas(resolution: dict, approved: ApprovedWorkflow, ratio: str) -> tuple[int, int]:
    if not isinstance(resolution, dict):
        raise invalid("Canvas resolver must be an object")
    kind = resolution.get("kind")
    if kind == "explicit_slots":
        if not {"WIDTH", "HEIGHT"} <= set(approved.slots):
            raise invalid("Explicit canvas requires WIDTH and HEIGHT slots")
        canvases = resolution.get("canvases")
        if not isinstance(canvases, dict):
            raise invalid("Declared canvases must be an object")
        value = canvases.get(ratio)
        if not isinstance(value, list) or len(value) != 2 or any(type(v) is not int for v in value):
            raise invalid("No declared canvas for requested aspect ratio")
        width, height = value
    elif kind == "resolution_selector":
        if resolution.get("source_sha256") != SELECTOR_SHA256:
            raise invalid("ResolutionSelector requires the pinned resolver source hash")
        node_id = resolution.get("node_id")
        if not isinstance(node_id, str):
            raise invalid("ResolutionSelector needs an actual graph node id")
        node = approved.workflow.get(node_id, {})
        inputs = node.get("inputs", {})
        if node.get("class_type") != "ResolutionSelector" or approved.slots.get("ASPECT_RATIO") != (
            node_id,
            "aspect_ratio",
        ):
            raise invalid("ResolutionSelector must be bound to the patched aspect-ratio slot")
        labels = resolution.get("aspect_ratio_values", {})
        if not isinstance(labels, dict):
            raise invalid("Selector labels must be an object")
        if ratio not in SELECTOR_LABELS or labels.get(ratio) != SELECTOR_LABELS[ratio]:
            raise invalid("No accurate selector label for requested ratio")
        mp, multiple = inputs.get("megapixels"), inputs.get("multiple")
        if type(mp) not in {int, float} or not math.isfinite(mp) or mp <= 0 or multiple != 32:
            raise invalid("Invalid pinned selector parameters")
        a, b = (int(part) for part in ratio.split(":"))
        scale = math.sqrt(mp * 1024 * 1024 / (a * b))
        width, height = (
            round(a * scale / multiple) * multiple,
            round(b * scale / multiple) * multiple,
        )
        # The legacy native selector has a separate post-rounding safety envelope.
        # Director explicit canvases are governed by their qualified profile instead.
        if width * height > 1032192:
            raise invalid("Resolved legacy selector canvas exceeds native H3 pixel limit")
    else:
        raise invalid("Workflow has no authoritative canvas resolver")
    if (
        width <= 0
        or height <= 0
        or width % 32
        or height % 32
        or width > DIRECTOR_SOURCE_MAX_DIMENSION
        or height > DIRECTOR_SOURCE_MAX_DIMENSION
    ):
        raise invalid("Resolved canvas exceeds H3 source bounds")
    return width, height


def resolve_frames(profile: WorkflowProfile, approved: ApprovedWorkflow, seconds: float) -> int:
    if type(seconds) not in {int, float} or not math.isfinite(seconds) or not 4 <= seconds <= 15:
        raise invalid("Generation duration must be finite and between 4 and 15 seconds")
    resolver = profile.duration_resolver
    if resolver.get("kind") == "frames_slots":
        if not {"FRAMES", "LENGTH"} & set(approved.slots):
            raise invalid("Frame resolver requires a frame slot")
    elif resolver.get("kind") == "template_expression":
        if not isinstance(resolver.get("node_id"), str):
            raise invalid("Duration expression needs an actual graph node id")
        node = approved.workflow.get(resolver.get("node_id"), {})
        if (
            node.get("class_type") != "ComfyMathExpression"
            or node.get("inputs", {}).get("expression") != DURATION_EXPRESSION
        ):
            raise invalid("Duration expression differs from the pinned template")
        if "DURATION" not in approved.slots:
            raise invalid("Duration expression requires a duration slot")
        duration_node, field = approved.slots["DURATION"]
        if node.get("inputs", {}).get("values.a") != [duration_node, 0] or field != "value":
            raise invalid("Duration expression does not consume the duration slot")
    elif resolver.get("kind") == "director_frames":
        nodes = [
            node
            for node in approved.workflow.values()
            if node.get("class_type") in DIRECTOR_CLASS_TYPES
        ]
        if len(nodes) != 1 or "total_frames" not in nodes[0].get("inputs", {}):
            raise invalid("Director frame resolver requires the Director total_frames input")
    else:
        raise invalid("Workflow has no verified duration resolver")
    target = max(5, round(seconds * 24))
    return target + (5 - target % 17) % 17


def upstream_nodes(approved: ApprovedWorkflow, output: str) -> set[str]:
    pending, result = [output], set()
    while pending:
        current = pending.pop()
        if current in result:
            continue
        result.add(current)
        for value in approved.workflow[current]["inputs"].values():
            if (
                isinstance(value, list)
                and len(value) == 2
                and isinstance(value[0], str)
                and type(value[1]) is int
                and value[0] in approved.workflow
            ):
                pending.append(value[0])
    return result


def native_inputs(approved: ApprovedWorkflow) -> dict | None:
    classes = {"MiniMaxH3ImageToVideo", "MiniMaxH3ReferenceToVideo"}
    nodes = [node["inputs"] for node in approved.workflow.values() if node["class_type"] in classes]
    if len(nodes) > 1:
        raise invalid("Multiple H3 conditioning paths require separate qualification")
    return nodes[0] if nodes else None


def native_asset_slots(approved: ApprovedWorkflow) -> set[str]:
    inputs = native_inputs(approved)
    if inputs is None:
        return set()
    roles = {"first_frame": "FIRST_FRAME", "last_frame": "LAST_FRAME"}
    result = {role for key, role in roles.items() if key in inputs}
    for prefix, role, maximum in (
        ("ref_images.ref_image_", "REFERENCE_IMAGE", 9),
        ("ref_videos.ref_video_", "REFERENCE_VIDEO", 3),
        ("ref_audios.ref_audio_", "REFERENCE_AUDIO", 3),
    ):
        indices = []
        for key in inputs:
            if key.startswith(prefix):
                number = key.removeprefix(prefix)
                if not number.isdecimal() or int(number) >= maximum:
                    raise invalid("Native reference input exceeds published slot limits")
                indices.append(int(number))
                result.add(f"{role}_{int(number) + 1}")
        if sorted(indices) != list(range(len(indices))):
            raise invalid("Native reference slots must be contiguous and zero-based")
    return result


def asset_slot_capacity(approved: ApprovedWorkflow, role: str, maximum: int) -> int:
    count = 0
    while count < maximum and f"{role}_{count + 1}" in approved.slots:
        require_asset_slot(approved, f"{role}_{count + 1}")
        count += 1
    return count


def require_asset_slot(approved: ApprovedWorkflow, name: str) -> None:
    if name not in approved.slots:
        raise invalid(f"Requested asset slot is absent: {name}")
    inputs = native_inputs(approved)
    if inputs is None:
        # Other executed graph providers still need hash-bound slot/evidence contracts.
        return
    node_id, field = approved.slots[name]
    node = approved.workflow[node_id]
    if name in {"FIRST_FRAME", "LAST_FRAME"}:
        key = "first_frame" if name == "FIRST_FRAME" else "last_frame"
        valid = (
            node["class_type"] == "LoadImage"
            and field == "image"
            and inputs.get(key) == [node_id, 0]
        )
    else:
        role, number = name.rsplit("_", 1)
        index = int(number) - 1
        if role == "REFERENCE_IMAGE":
            valid = (
                node["class_type"] == "LoadImage"
                and field == "image"
                and inputs.get(f"ref_images.ref_image_{index}") == [node_id, 0]
            )
        elif role == "REFERENCE_AUDIO":
            valid = (
                node["class_type"] == "LoadAudio"
                and field == "audio"
                and inputs.get(f"ref_audios.ref_audio_{index}") == [node_id, 0]
            )
        elif role == "REFERENCE_VIDEO":
            link = inputs.get(f"ref_videos.ref_video_{index}")
            components = (
                approved.workflow.get(link[0], {})
                if isinstance(link, list) and len(link) == 2
                else {}
            )
            valid = (
                node["class_type"] == "LoadVideo"
                and field == "file"
                and components.get("class_type") == "GetVideoComponents"
                and components.get("inputs", {}).get("video") == [node_id, 0]
                and link[1] == 0
            )
        else:
            valid = False
    if not valid:
        raise invalid(f"Asset slot {name} does not feed the actual H3 conditioning input")


def require_contract(
    record, approved: ApprovedWorkflow, quality: str | None = None, ratio: str | None = None
) -> WorkflowProfile:
    require_qualified(record.profile, approved.workflow_hash, approved.slot_map_hash)
    try:
        approved.validate()
        profile = WorkflowProfile.model_validate(record.profile)
        allowed = {item.name for item in fields(H3Profile)}
        limits = H3Profile(
            **{key: value for key, value in record.profile.items() if key in allowed}
        )
        combinations = [
            ExecutedCombination.model_validate(value)
            for value in record.profile["execution_evidence"].get("combinations", [])
        ]
    except (ValueError, KeyError, TypeError) as exc:
        raise invalid(
            "Complete graph-bound profile and executed combinations are required"
        ) from exc
    evidence = record.profile["execution_evidence"]
    if evidence.get("profile_hash") != profile_hash(record.profile):
        raise invalid("Executed settings differ from profile hash")
    if profile.quality_profile != record.quality_profile or quality not in (
        None,
        profile.quality_profile,
    ):
        raise invalid("Requested quality does not match workflow profile")
    try:
        weights = collect_executable_weights(approved.workflow)
    except ValueError as exc:
        raise invalid(str(exc)) from exc
    if (
        set(profile.weight_hashes) != weights
        or evidence.get("weight_hashes", {}) != profile.weight_hashes
    ):
        raise invalid(
            "Executed weight hashes must cover every executable "
            "model/encoder/VAE/LoRA/auxiliary filename"
        )
    adapters = [
        node for node in approved.workflow.values() if node["class_type"].startswith("LoraLoader")
    ]
    if adapters:
        binding = profile.setting_bindings.get("turbo", [])
        if (
            len(binding) != 2
            or approved.workflow.get(binding[0], {}).get("inputs", {}).get(binding[1])
            is not profile.turbo
        ):
            raise invalid("Adapter graphs require an explicit graph-bound Turbo switch")
        declared = record.profile.get("adapter_settings")
        actual = {
            node_id: node["inputs"]
            for node_id, node in approved.workflow.items()
            if node["class_type"].startswith("LoraLoader")
        }
        if declared != actual:
            raise invalid("Every adapter/strength must be explicit in the qualified profile")
    elif profile.turbo:
        raise invalid("Turbo profile has no explicit adapter graph")
    classes = {node.get("class_type") for node in approved.workflow.values()}
    director_nodes = [
        node
        for node in approved.workflow.values()
        if node.get("class_type") in DIRECTOR_CLASS_TYPES
    ]
    is_director = bool(director_nodes)
    if (
        not all(isinstance(name, str) for name in classes)
        or set(profile.dependency_versions) != classes
    ):
        raise invalid("Dependency provenance must cover every executable graph class")
    if evidence.get("dependency_versions") != profile.dependency_versions or any(
        not name.strip() or not version.strip()
        for name, version in profile.dependency_versions.items()
    ):
        raise invalid("Executed dependency provenance differs from graph")
    if is_director:
        if len(director_nodes) != 1:
            raise invalid("Director workflows require exactly one MiniMaxH3Director node")
        if record.profile.get("provider") != "minimax_h3_director":
            raise invalid("Director workflow profile must declare provider=minimax_h3_director")
        source = record.profile.get("provider_source") or {}
        if (
            source.get("repository") != "AIMixer/ComfyUI_MiniMaxH3_Director"
            or source.get("commit") != "a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb"
        ):
            raise invalid("Director workflow must pin the approved provider source commit")
        inputs = director_nodes[0].get("inputs", {})
        required_inputs = {
            "task_type",
            "global_prompt",
            "cfg",
            "seed",
            "frame_rate",
            "width",
            "height",
            "ref_max_size",
            "total_frames",
            "timeline_data",
        }
        if not required_inputs <= set(inputs):
            raise invalid("Director workflow is missing required semantic inputs")
        try:
            from apps.api.app.providers.minimax_h3_director.workflow_builder import (
                validate_export_bindings,
            )

            validate_export_bindings(approved.workflow, record.profile)
            from apps.api.app.providers.minimax_h3_director.config_graph import (
                CONFIG_CLASSES,
                validate_config_topology,
            )
            from apps.api.app.providers.minimax_h3_director.qualification import verified_settings

            for feature, config_class in CONFIG_CLASSES.items():
                has_config = any(
                    node.get("class_type") == config_class for node in approved.workflow.values()
                )
                advertised = any(
                    case["settings"][feature].get("enabled")
                    for case in verified_settings(record.profile)
                )
                if has_config or inputs.get(feature) is not None or advertised:
                    validate_config_topology(approved.workflow, director_nodes[0], feature)
        except ValueError as exc:
            raise invalid(str(exc)) from exc
    elif not {"PROMPT", "SEED", "OUTPUT_PREFIX"} <= set(approved.slots):
        raise invalid("Workflow must patch prompt, seed and output prefix")
    if not is_director:
        for setting in ("sampler", "scheduler"):
            binding = profile.setting_bindings.get(setting, [])
            if len(binding) != 2 or approved.workflow.get(binding[0], {}).get("inputs", {}).get(
                binding[1]
            ) != getattr(profile, setting):
                raise invalid(f"Graph {setting} differs from profile")
    if not is_director and "STEPS" not in approved.slots:
        raise invalid("Profile steps require a steps slot")
    inputs = None if is_director else native_inputs(approved)
    if inputs is not None:
        conditioning = next(
            node
            for node in approved.workflow.values()
            if node["class_type"] in {"MiniMaxH3ImageToVideo", "MiniMaxH3ReferenceToVideo"}
        )
        expected = "MiniMaxH3ReferenceToVideo" if record.mode == "r2v" else "MiniMaxH3ImageToVideo"
        if conditioning["class_type"] != expected:
            raise invalid("H3 model family conflicts with requested mode")
        output_id = record.profile.get("output_node")
        if (
            not isinstance(output_id, str)
            or approved.workflow.get(output_id, {}).get("class_type") != "SaveVideo"
        ):
            raise invalid("Native H3 requires an explicit SaveVideo output node")
        if approved.slots["OUTPUT_PREFIX"] != (output_id, "filename_prefix"):
            raise invalid("Output prefix must bind the collected SaveVideo output")
        ancestors = upstream_nodes(approved, output_id)
        if any(node not in ancestors for node, _ in approved.slots.values()):
            raise invalid("Every patched slot must participate in the collected output graph")
        condition_id = next(
            key for key, value in approved.workflow.items() if value is conditioning
        )
        prompt_binding = approved.slots["PROMPT"]
        if prompt_binding != (condition_id, "prompt") and not (
            approved.workflow[prompt_binding[0]]["class_type"] == "PrimitiveString"
            and prompt_binding[1] == "value"
            and inputs.get("prompt") == [prompt_binding[0], 0]
        ):
            raise invalid("Prompt must feed native H3 directly without hidden composition")
        sched_id, sched_field = profile.setting_bindings["scheduler"]
        if (
            approved.workflow[sched_id]["class_type"] != "BasicScheduler"
            or sched_field != "scheduler"
            or approved.slots["STEPS"] != (sched_id, "steps")
        ):
            raise invalid("Steps must bind the actual sampling scheduler")
        seed_node, seed_field = approved.slots["SEED"]
        if (
            approved.workflow[seed_node]["class_type"] != "RandomNoise"
            or seed_field != "noise_seed"
        ):
            raise invalid("Seed must bind the native sampling noise")
        samplers = [
            node
            for key, node in approved.workflow.items()
            if key in ancestors and node["class_type"] == "SamplerCustomAdvanced"
        ]
        sampler_id, sampler_field = profile.setting_bindings["sampler"]
        if (
            len(samplers) != 1
            or samplers[0]["inputs"].get("sigmas") != [sched_id, 0]
            or samplers[0]["inputs"].get("noise") != [seed_node, 0]
            or samplers[0]["inputs"].get("sampler") != [sampler_id, 0]
            or approved.workflow[sampler_id]["class_type"] != "KSamplerSelect"
            or sampler_field != "sampler_name"
        ):
            raise invalid("Qualified sampling settings must control the actual H3 sampler")
        if any(key.startswith("ref_video_audios.") for key in inputs):
            raise invalid(
                "Paired soundtrack binding requires an independently qualified "
                "normalization contract"
            )
        actual_asset_slots = native_asset_slots(approved)
        if not actual_asset_slots <= set(approved.slots):
            raise invalid("Every actual conditioning asset requires a symbolic slot")
        for role, maximum in (
            ("REFERENCE_IMAGE", min(9, limits.max_reference_images)),
            ("REFERENCE_VIDEO", min(3, limits.max_reference_videos)),
            ("REFERENCE_AUDIO", min(3, limits.max_reference_audio)),
        ):
            if sum(name.startswith(f"{role}_") for name in actual_asset_slots) > maximum:
                raise invalid("Required native reference inputs exceed qualified profile limits")
        if record.mode == "r2v" and (
            {"FIRST_FRAME", "LAST_FRAME"} & actual_asset_slots
            or len(actual_asset_slots) > 12
            or not any(
                name.startswith(("REFERENCE_IMAGE_", "REFERENCE_VIDEO_"))
                for name in actual_asset_slots
            )
        ):
            raise invalid("Native Ref2VA graph has no valid mixed visual reference envelope")
        if record.mode != "r2v":
            frames = {
                "i2v": {"first_frame"},
                "i2v_last": {"last_frame"},
                "i2v_first_last": {"first_frame", "last_frame"},
                "t2v": set(),
            }[record.mode]
            if {key for key in {"first_frame", "last_frame"} if key in inputs} != frames:
                raise invalid("Graph contains unrequested first/last frame inputs")
        if profile.resolution.get("kind") == "resolution_selector":
            selector = profile.resolution["node_id"]
            if inputs.get("width") != [selector, 0] or inputs.get("height") != [selector, 1]:
                raise invalid("ResolutionSelector does not control the actual H3 canvas")
        else:
            for name, field in (("WIDTH", "width"), ("HEIGHT", "height")):
                binding = approved.slots.get(name)
                if binding is None or (
                    binding != (condition_id, field)
                    and not (
                        binding[1] == "value"
                        and inputs.get(field) == [binding[0], 0]
                        and approved.workflow[binding[0]]["class_type"] == "PrimitiveInt"
                    )
                ):
                    raise invalid("Dimension slots do not control the actual H3 canvas")
        if profile.duration_resolver.get("kind") == "template_expression" and inputs.get(
            "length"
        ) != [profile.duration_resolver["node_id"], 1]:
            raise invalid("Frame expression does not control the actual H3 length")
        if profile.duration_resolver.get("kind") == "frames_slots":
            binding = approved.slots.get("FRAMES", approved.slots.get("LENGTH"))
            if binding != (condition_id, "length") and not (
                binding is not None
                and binding[1] == "value"
                and approved.workflow[binding[0]]["class_type"] == "PrimitiveInt"
                and inputs.get("length") == [binding[0], 0]
            ):
                raise invalid("Frame slot must control the actual H3 length")
        for node in approved.workflow.values():
            if node["class_type"] == "CreateVideo":
                node_id = next(key for key, value in approved.workflow.items() if value is node)
                if "FPS" in approved.slots and approved.slots["FPS"] != (node_id, "fps"):
                    raise invalid("FPS slot must control the actual video encoder")
                if "FPS" not in approved.slots and node["inputs"].get("fps") != 24:
                    raise invalid("Actual video encoder FPS differs from native H3 profile")
    for name in approved.slots:
        if name in {"FIRST_FRAME", "LAST_FRAME"} or name.startswith("REFERENCE_"):
            require_asset_slot(approved, name)
    if not is_director:
        # Arbitrary prefix composition after PROMPT would change accepted text.
        prompt_node, prompt_field = approved.slots["PROMPT"]
        consumers = [
            node
            for node in approved.workflow.values()
            if [prompt_node, 0] in list(node.get("inputs", {}).values())
        ]
        if approved.workflow[prompt_node].get("class_type") == "StringConcatenate" or any(
            node.get("class_type") == "StringConcatenate" for node in consumers
        ):
            raise invalid("Hidden prompt concatenation must be removed and requalified")
        if prompt_field not in approved.workflow[prompt_node]["inputs"]:
            raise invalid("Prompt binding is invalid")
    resolve_frames(profile, approved, 5)
    valid_ratios = []
    measured_canvases = []
    for combination in combinations:
        canvas = resolve_canvas(profile.resolution, approved, combination.aspect_ratio)
        expected_canvas = (
            resolve_director_output_canvas(canvas, {}, task=record.mode) if is_director else canvas
        )
        if canvas[0] * canvas[1] > limits.max_pixels:
            raise invalid("Advertised canvas exceeds qualified profile pixel limit")
        if (
            combination.mode != record.mode
            or combination.quality_profile != profile.quality_profile
            or combination.steps != profile.steps
            or canvas != (combination.width, combination.height)
        ):
            raise invalid("Executed combination differs from graph/profile settings")
        if (
            (combination.output.width, combination.output.height) != expected_canvas
            or combination.output.fps != profile.fps
            or not combination.output.has_video
            or not combination.output.has_audio
        ):
            raise invalid(
                "Each advertised combination requires matching measured video/audio output"
            )
        valid_ratios.append(combination.aspect_ratio)
        measured_canvases.append(expected_canvas)
    if (
        is_director
        and (evidence["output"]["width"], evidence["output"]["height"]) not in measured_canvases
    ):
        raise invalid("Director baseline output differs from every executed canvas")
    if not valid_ratios or ratio is not None and ratio not in valid_ratios:
        raise invalid("Requested mode/profile/ratio has no executed qualification")
    return profile


def approved_record(record) -> ApprovedWorkflow:
    require_execution_scope(record)
    try:
        return ApprovedWorkflow.from_manifest(
            {
                "mode": record.mode,
                "execution_scope": record.execution_scope,
                "version": record.version,
                "workflow": record.workflow,
                "slots": record.slots,
                "required_slots": record.required_slots,
                "workflow_hash": record.workflow_hash,
                "slot_map_hash": record.slot_map_hash,
            }
        )
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        raise invalid("Workflow graph/slot integrity failed") from exc


def require_frozen(snapshot: dict, record) -> ApprovedWorkflow:
    """Revalidate frozen inputs for execution; historical reads need no execution gate."""
    from apps.api.app.services.workflow_router import require_director_execution

    require_director_execution(record)
    approved = approved_record(record)
    is_director = is_director_graph(approved.workflow)
    contract = require_contract(
        record,
        approved,
        snapshot.get("requested_quality_profile", record.quality_profile),
        frozen_ratio(snapshot, record, approved),
    )
    if (
        snapshot.get("workflow_hash", record.workflow_hash) != record.workflow_hash
        or snapshot.get("slot_map_hash", record.slot_map_hash) != record.slot_map_hash
    ):
        raise invalid("Frozen workflow no longer matches qualified workflow version")
    if snapshot.get("schema_version", 1) >= 2:
        if snapshot.get("semantic_hash") != _stable_hash(
            {key: value for key, value in snapshot.items() if key != "semantic_hash"}
        ):
            raise invalid("Frozen semantic inputs changed")
        if snapshot.get("profile_hash") != profile_hash(record.profile):
            raise invalid("Frozen profile differs from current qualified profile")
    normalized = copy.deepcopy(snapshot.get("workflow", {}))
    try:
        for node_id, field in approved.slots.values():
            normalized[node_id]["inputs"][field] = approved.workflow[node_id]["inputs"][field]
    except (KeyError, TypeError) as exc:
        raise invalid("Frozen graph is missing qualified inputs") from exc
    if _stable_hash(normalized) != approved.workflow_hash:
        raise invalid("Frozen graph contains unqualified structural changes")
    graph = snapshot["workflow"]
    ratio = frozen_ratio(snapshot, record, approved)
    width, height = resolve_canvas(contract.resolution, approved, ratio)
    frames = resolve_frames(contract, approved, snapshot.get("duration_seconds"))
    for name, expected in (
        ("width", width),
        ("height", height),
        ("resolved_width", width),
        ("resolved_height", height),
        ("frames", frames),
        ("resolved_duration_seconds", frames / contract.fps),
    ):
        if name in snapshot and snapshot[name] != expected:
            raise invalid(f"Frozen {name} differs from qualified resolver")
    if is_director:
        try:
            from apps.api.app.providers.minimax_h3_director.contracts import DirectorExecutionSpec

            spec = DirectorExecutionSpec.model_validate(snapshot.get("director_execution"))
        except (TypeError, ValueError) as exc:
            raise invalid("Frozen Director execution contract is missing or invalid") from exc
        if (
            spec.workflow_id != record.id
            or spec.workflow_hash != record.workflow_hash
            or spec.slot_map_hash != record.slot_map_hash
            or spec.profile_hash != profile_hash(record.profile)
            or (spec.canvas.width, spec.canvas.height) != (width, height)
            or spec.canvas.aspect_ratio != ratio
            or spec.steps != contract.steps
            or spec.fps != contract.fps
            or spec.frames != frames
            or spec.seed != snapshot.get("seed")
            or spec.prompt != snapshot.get("prompt")
        ):
            raise invalid("Frozen Director execution differs from qualified snapshot")
        from apps.api.app.providers.minimax_h3_director.qualification import require_settings

        if approved.execution_scope == "single_scene":
            require_settings(
                record.profile,
                {
                    name: getattr(spec, name)
                    for name in ("motion_context", "refine", "face_refine", "audio_policy")
                },
                base_canvas=(spec.canvas.width, spec.canvas.height),
                task=spec.task,
            )
        from apps.api.app.services.generation_intent import GenerationIntent

        try:
            intent = GenerationIntent.model_validate(snapshot.get("generation_intent"))
        except (TypeError, ValueError) as exc:
            raise invalid("Frozen Director generation intent is missing or invalid") from exc
        if spec.intent_hash != intent.semantic_hash or spec.assets != intent.assets:
            raise invalid("Frozen Director execution differs from generation intent")
        frozen_assets = {
            (asset["role"], asset["order_index"]): (
                asset["id"],
                asset["checksum"],
                asset["object_key"],
            )
            for asset in snapshot.get("assets", [])
        }
        execution_assets = {
            (asset.role, asset.order_index): (asset.id, asset.checksum, asset.object_key)
            for asset in spec.assets
        }
        if frozen_assets != execution_assets or len(frozen_assets) != len(spec.assets):
            raise invalid("Frozen Director materialization manifest differs from execution inputs")
        return approved
    if "ASPECT_RATIO" in approved.slots:
        node, field = approved.slots["ASPECT_RATIO"]
        expected = contract.resolution.get("aspect_ratio_values", {}).get(ratio, ratio)
        if graph[node]["inputs"][field] != expected:
            raise invalid("Frozen aspect-ratio input differs from qualified resolver")
    if "FPS" in approved.slots:
        node, field = approved.slots["FPS"]
        if graph[node]["inputs"][field] != contract.fps:
            raise invalid("Frozen FPS differs from qualified profile")
    step_node, step_field = approved.slots["STEPS"]
    if graph[step_node]["inputs"][step_field] != contract.steps:
        raise invalid("Frozen graph steps differ from qualified profile")
    for role, field in (
        ("PROMPT", "prompt"),
        ("NEGATIVE_PROMPT", "negative_prompt"),
        ("SEED", "seed"),
        ("STEPS", "steps"),
        ("WIDTH", "resolved_width"),
        ("HEIGHT", "resolved_height"),
        ("FRAMES", "frames"),
        ("LENGTH", "frames"),
        ("DURATION", "duration_seconds"),
    ):
        if role in approved.slots and field in snapshot:
            node, input_name = approved.slots[role]
            if graph[node]["inputs"][input_name] != snapshot[field]:
                raise invalid(f"Frozen {field} differs from actual graph input")
    return approved


def frozen_ratio(snapshot: dict, record, approved: ApprovedWorkflow) -> str:
    if snapshot.get("requested_aspect_ratio"):
        return snapshot["requested_aspect_ratio"]
    if is_director_graph(approved.workflow):
        canvas = (snapshot.get("generation_intent") or {}).get("canvas") or {}
        if canvas.get("aspect_ratio"):
            return canvas["aspect_ratio"]
    resolution = record.profile.get("resolution", {})
    graph = snapshot.get("workflow", {})
    if resolution.get("kind") == "resolution_selector" and "ASPECT_RATIO" in approved.slots:
        node, field = approved.slots["ASPECT_RATIO"]
        value = graph.get(node, {}).get("inputs", {}).get(field)
        for ratio, label in resolution.get("aspect_ratio_values", {}).items():
            if value == label:
                return ratio
    if resolution.get("kind") == "explicit_slots" and {"WIDTH", "HEIGHT"} <= set(approved.slots):
        canvas = [
            graph.get(node, {}).get("inputs", {}).get(field)
            for node, field in (approved.slots["WIDTH"], approved.slots["HEIGHT"])
        ]
        matches = [
            ratio for ratio, value in resolution.get("canvases", {}).items() if value == canvas
        ]
        if len(matches) == 1:
            return matches[0]
    raise invalid("Historical frozen canvas has no unambiguous qualified ratio")

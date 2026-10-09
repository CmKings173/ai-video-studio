from __future__ import annotations

from typing import Any

DIRECTOR_SOURCE = {
    "provider": "minimax_h3_director",
    "repository": "AIMixer/ComfyUI_MiniMaxH3_Director",
    "commit": "a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb",
    "license": "Apache-2.0",
    "pipeline_id": "minimax_h3_motion_context_v9",
}

SOURCE_RATIOS = ("16:9", "9:16", "1:1", "4:3", "3:4", "3:2", "2:3", "21:9", "Custom")


def source_capabilities() -> dict[str, Any]:
    return {
        **DIRECTOR_SOURCE,
        "tasks": ["t2v", "i2v", "fl2v", "r2v", "v2v", "rv2v"],
        "legacy_mode_mapping": {
            "t2v": "t2v",
            "i2v": "i2v",
            "i2v_last": "fl2v",
            "i2v_first_last": "fl2v",
            "fl2v": "fl2v",
            "r2v": "r2v",
            "v2v": "v2v",
            "rv2v": "rv2v",
        },
        "ratios": list(SOURCE_RATIOS),
        "custom_canvas": {"min": 32, "max": 8192, "multiple": 32},
        "reference_limits": {"images": 9, "videos": 3, "audio": 3, "total": 12},
        "supports": {
            "source_video": True,
            "first_frame": True,
            "last_frame": True,
            "motion_context": True,
            "refine": True,
            "face_refine": True,
            "audio": True,
        },
        "motion_context": {
            "context_frames": [5, 22, 39, 56],
            "default_context_frames": 22,
            "default_audio_context_frames": 24,
        },
        "face_refine": {"default_detector": "face_yolov8m.pt"},
        "file_types": {
            "image": ["image/png", "image/jpeg", "image/webp"],
            "video": ["video/mp4", "video/quicktime", "video/webm"],
            "audio": ["audio/flac", "audio/wav", "audio/mpeg"],
        },
        "quality_profiles": [
            "DRAFT",
            "STANDARD",
            "HIGH",
            "BASE",
            "HD",
            "FULL_HD_REFINED",
            "CUSTOM",
        ],
        "runtime_qualification_required": True,
    }


def qualified_capabilities(combinations: list[dict[str, Any]]) -> dict[str, Any]:
    runnable = [item for item in combinations if item.get("runnable")]
    return {
        "combinations": runnable,
        "modes": sorted({str(item.get("mode")) for item in runnable if item.get("mode")}),
        "ratios": sorted(
            {str(item.get("aspect_ratio")) for item in runnable if item.get("aspect_ratio")}
        ),
        "quality_profiles": sorted(
            {str(item.get("quality_profile")) for item in runnable if item.get("quality_profile")}
        ),
    }

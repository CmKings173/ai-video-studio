from __future__ import annotations

from typing import Any

from .contracts import DirectorExecutionSpec


def _media(path: str) -> dict[str, Any]:
    return {"fileName": path.rsplit("/", 1)[-1], "videoFile": path, "type": "input"}


def _image(path: str, index: int) -> dict[str, Any]:
    return {"index": index, "imageFile": path, "fileName": path.rsplit("/", 1)[-1]}


def _audio(path: str, index: int) -> dict[str, Any]:
    return {"index": index, "audioFile": path, "fileName": path.rsplit("/", 1)[-1], "type": "input"}


def _ref_video(path: str, index: int) -> dict[str, Any]:
    return {"index": index, "videoFile": path, "fileName": path.rsplit("/", 1)[-1], "type": "input"}


def build_timeline(
    spec: DirectorExecutionSpec, staged: dict[tuple[str, int], str]
) -> dict[str, Any]:
    source_path = staged.get(("SOURCE_VIDEO", 0), "")
    expected = {(item.role, item.order_index) for item in spec.assets}
    if set(staged) != expected:
        raise ValueError("Staged Director assets differ from the frozen input manifest")
    source = (
        _media(source_path)
        if source_path
        else {
            "fileName": "",
            "videoFile": "",
            "subfolder": "",
            "type": "input",
            "frames": [],
            "frameMap": [],
        }
    )
    declared = list(spec.timeline)
    if not declared:
        declared = [
            {
                "scene_id": None,
                "prompt": spec.prompt,
                "start_frame": 0,
                "frame_count": spec.frames,
                "continuity_from_previous": False,
            }
        ]
    segments = []
    for index, item in enumerate(declared):
        if hasattr(item, "model_dump"):
            item = item.model_dump(mode="json")
        bindings = item.get("asset_indices") or {}

        def paths(role: str, bindings=bindings) -> list[str]:
            if bindings:
                indices = bindings.get(role, [])
                return [staged[(role, ordinal)] for ordinal in indices]
            return [
                path
                for (asset_role, _ordinal), path in sorted(staged.items())
                if asset_role == role
            ]

        images = [_image(path, ordinal) for ordinal, path in enumerate(paths("REFERENCE_IMAGE"))]
        videos = [
            _ref_video(path, ordinal) for ordinal, path in enumerate(paths("REFERENCE_VIDEO"))
        ]
        audios = [_audio(path, ordinal) for ordinal, path in enumerate(paths("REFERENCE_AUDIO"))]
        first_images, last_images = paths("FIRST_FRAME"), paths("LAST_FRAME")
        if (
            spec.motion_context.get("enabled")
            and item.get("continuity_from_previous")
            and first_images
            and spec.task == "i2v"
        ):
            raise ValueError("DIRECTOR_CONTINUITY_CONFLICTS_WITH_EXPLICIT_FIRST_FRAME")
        segment = {
            "id": item.get("scene_id") or f"studio-segment-{index}",
            "start": int(item.get("start_frame", 0)),
            "length": int(item.get("frame_count", spec.frames)),
            "frameCount": int(item.get("frame_count", spec.frames)),
            "prompt": item.get("prompt") or spec.prompt,
            "taskType": spec.task,
            "refs": images,
            "refVideos": videos,
            "refAudios": audios,
            "referenceVideo": {},
            "continuityFromPrev": bool(item.get("continuity_from_previous", False)),
        }
        if first_images:
            segment["startImage"] = _image(first_images[0], 0)
            segment["genImage"] = _image(first_images[0], 0)
        if last_images:
            segment["endImage"] = _image(last_images[0], 1)
        segments.append(segment)
    audio_mode = str(spec.audio_policy.get("mode") or "generate")
    timeline = {
        "version": 4,
        "timelineMode": "video" if source_path else "prompt_batch",
        "editMode": "segment",
        "totalFrames": spec.frames,
        "frameRate": spec.fps,
        "width": spec.canvas.width,
        "height": spec.canvas.height,
        "refMaxSize": max(spec.canvas.width, spec.canvas.height),
        "video": source,
        "videoClips": ([{**source, "start": 0, "length": spec.frames}] if source_path else []),
        "global": {
            "taskType": spec.task,
            "prompt": spec.prompt,
            "refs": [],
            "refVideos": [],
            "refAudios": [],
            "referenceVideo": {},
        },
        "segments": segments,
        "output": {
            "mode": "fixed",
            "longEdge": max(spec.canvas.width, spec.canvas.height),
            "width": spec.canvas.width,
            "height": spec.canvas.height,
            "maxExportFrames": 0,
            "exportMode": "all",
            "audioMode": audio_mode,
            "refImageSize": "match",
            "continuityEnabled": bool(spec.motion_context.get("enabled"))
            and any(segment["continuityFromPrev"] for segment in segments),
            "continuityOverlapFrames": int(spec.motion_context.get("context_frames", 22)),
            "continuityKeepTail": bool(spec.motion_context.get("keep_tail", False)),
        },
    }
    if spec.task == "fl2v":
        timeline["timelineMode"] = "fl2v"
        timeline["shots"] = [
            {
                **segment,
                "durationSec": segment["frameCount"] / spec.fps,
                "negativePrompt": spec.negative_prompt,
            }
            for segment in segments
        ]
        timeline.pop("segments")
    return timeline

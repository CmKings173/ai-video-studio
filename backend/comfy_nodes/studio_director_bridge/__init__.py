"""Pinned Director bridge: expose native segment files as structured history output.

Install this directory in ComfyUI/custom_nodes. The upstream source remains intact.
An execution-scoped capture observes its plan at the existing finalize seam; no
report parsing, output-directory scanning or filename-based member guessing occurs.
"""

from __future__ import annotations

import copy
import hashlib
import importlib
import threading
from contextvars import ContextVar
from pathlib import Path

VERSION = "studio-director-bridge-v1"
SOURCE_FILES = {
    "nodes/director.py": "640090027e562004d8cb0e486812a122432624a247683d88a9dc781a1f2b0185",
    "nodes/director_common.py": "be4782b420f2722cbc7b17131b96c5823ecbb16320806b2b819fe8ea2128b001",
    "director/executor_core.py": "72dae4eb1cd87a9ad1036ac61ca8ec8ade140036935f0ba4c5495b1d6965c1d2",
    "director/segment_mp4_export.py": (
        "df35f95710d4130d9fb12feb660aaf6409f34074aa089e83e7b0cd6b0ca67082"
    ),
}
_capture = ContextVar("studio_director_plan", default=None)
_install_lock = threading.Lock()


def source_class():
    import nodes

    source = nodes.NODE_CLASS_MAPPINGS.get("MiniMaxH3Director")
    if source is None:
        raise RuntimeError("Install the pinned MiniMax H3 Director before using the studio bridge")
    module = importlib.import_module(source.__module__)
    root = Path(module.__file__).resolve().parents[1]
    for relative, expected in SOURCE_FILES.items():
        if hashlib.sha256((root / relative).read_bytes()).hexdigest() != expected:
            raise RuntimeError(f"Director source drift: {relative}")
    # Install once; ordinary Director executions have no capture context and keep
    # their original result. ContextVars isolate concurrent execution threads.
    with _install_lock:
        original = module.finalize_director_outputs
        if not getattr(original, "_studio_capture", False):

            def observe(plan, *args, **kwargs):
                capture = _capture.get()
                if capture is not None:
                    capture.append(plan)
                return original(plan, *args, **kwargs)

            observe._studio_capture = True
            module.finalize_director_outputs = observe
    return source


def native_manifest(plan, output_directory: Path) -> list[dict]:
    if plan.export_mode != "segments" or plan.run_indices is not None:
        raise ValueError("Studio aggregate exports require a complete segments run")
    root = output_directory.resolve()
    run_directory = Path(plan.segment_mp4_run_dir).resolve()
    if not run_directory.is_relative_to(root / "minimax_seg_export"):
        raise ValueError("Native segment output escaped the ComfyUI output directory")
    artifacts = []
    for member_index, segment in enumerate(plan.segments):
        if segment.index != member_index:
            raise ValueError("Native segment indices must be contiguous")
        for role, suffix, required in (
            ("segment", "", True),
            ("pre-refine", "_pre", False),
            ("pre-face", "_facepre", False),
        ):
            path = run_directory / f"seg_{segment.index:04d}{suffix}.mp4"
            if not path.is_file():
                if required:
                    raise ValueError("Director did not persist every required native segment")
                continue
            resolved = path.resolve()
            if not resolved.is_relative_to(run_directory) or resolved.stat().st_size == 0:
                raise ValueError("Invalid native segment artifact")
            artifacts.append(
                {
                    "role": role,
                    "member_index": member_index,
                    "filename": path.name,
                    "subfolder": run_directory.relative_to(root).as_posix(),
                    "type": "output",
                }
            )
    if not artifacts:
        raise ValueError("Native segment manifest is empty")
    return artifacts


class StudioMiniMaxH3Director:
    CATEGORY = "MiniMaxH3/Studio"
    RETURN_TYPES = ("IMAGE", "AUDIO", "FLOAT", "INT", "IMAGE", "STRING", "IMAGE", "IMAGE")
    RETURN_NAMES = (
        "images",
        "audio",
        "fps",
        "frame_count",
        "source_images",
        "report",
        "images_pre_refine",
        "images_pre_face_refine",
    )
    OUTPUT_IS_LIST = (True, True, False, False, True, False, True, True)
    OUTPUT_NODE = True
    FUNCTION = "execute"

    @classmethod
    def INPUT_TYPES(cls):
        inputs = copy.deepcopy(source_class().INPUT_TYPES())
        inputs.setdefault("optional", {})["studio_output_prefix"] = (
            "STRING",
            {"default": "studio/director"},
        )
        return inputs

    @classmethod
    def VALIDATE_INPUTS(cls, **kwargs):
        kwargs.pop("studio_output_prefix", None)
        return source_class().VALIDATE_INPUTS(**kwargs)

    def execute(self, studio_output_prefix="studio/director", **kwargs):
        import folder_paths

        source = source_class()
        captured = []
        token = _capture.set(captured)
        try:
            result = source().execute(**kwargs)
        finally:
            _capture.reset(token)
        if len(captured) != 1:
            raise ValueError("Director bridge requires one complete execution plan")
        manifest = native_manifest(captured[0], Path(folder_paths.get_output_directory()))
        return {
            "result": result,
            "ui": {
                "studio_director_artifacts": manifest,
                "studio_director_bridge": [VERSION],
                "studio_output_prefix": [studio_output_prefix],
            },
        }


NODE_CLASS_MAPPINGS = {"StudioMiniMaxH3Director": StudioMiniMaxH3Director}
NODE_DISPLAY_NAME_MAPPINGS = {
    "StudioMiniMaxH3Director": "MiniMax H3 Director · Studio native manifest"
}

"""Synthetic executed assertions for isolated tests only, never release evidence."""

from apps.api.app.services.workflow_contracts import profile_hash
from apps.api.app.services.workflow_registry import ApprovedWorkflow


def synthetic_profile(graph, slots, *, mode="t2v", steps=12, quality="STANDARD"):
    approved = ApprovedWorkflow(
        mode, "1", graph, {key: tuple(value) for key, value in slots.items()}
    )
    node_id = slots["PROMPT"][0]
    graph[node_id]["inputs"].update(sampler="test-only", scheduler="test-only")
    profile = {
        "quality_profile": quality,
        "steps": steps,
        "fps": 24,
        "turbo": False,
        "sampler": "test-only",
        "scheduler": "test-only",
        "setting_bindings": {"sampler": [node_id, "sampler"], "scheduler": [node_id, "scheduler"]},
        "resolution": {
            "kind": "explicit_slots",
            "canvases": {"9:16": [480, 864], "16:9": [864, 480], "1:1": [640, 640]},
        },
        "duration_resolver": {"kind": "frames_slots"},
        "dependency_versions": {
            node["class_type"]: "synthetic-test-only" for node in graph.values()
        },
        "output_node": "2",
        "studio_revision": "1" * 40,
        "comfyui_version": "0.3.50-test",
        "provider_source": {
            "repository": "test/Director",
            "commit": "a" * 40,
        },
        "execution_spec_hash": "2" * 64,
    }
    profile.update(
        poc_verified=True,
        execution_evidence={
            "executed": True,
            "workflow_hash": approved.workflow_hash,
            "slot_map_hash": approved.slot_map_hash,
            "profile_hash": profile_hash(profile),
            "tested_at": "2026-10-05T00:00:00Z",
            "prompt_id": "synthetic-test-only",
            "model_hash": "c" * 64,
            "lora_hashes": {},
            "comfyui_commit": "d" * 40,
            "studio_revision": profile["studio_revision"],
            "director_repository": profile["provider_source"]["repository"],
            "director_commit": profile["provider_source"]["commit"],
            "comfyui_version": profile["comfyui_version"],
            "execution_spec_hash": "2" * 64,
            "gpu_model": "synthetic-test-only",
            "gpu_count": 1,
            "driver_version": "synthetic-test-only",
            "cuda_runtime": "synthetic-test-only",
            "custom_node_versions": {},
            "dependency_versions": profile["dependency_versions"],
            "combinations": [
                {
                    "mode": mode,
                    "quality_profile": quality,
                    "aspect_ratio": ratio,
                    "width": canvas[0],
                    "height": canvas[1],
                    "steps": steps,
                    "output": {
                        "width": canvas[0],
                        "height": canvas[1],
                        "fps": 24,
                        "duration_seconds": 124 / 24,
                        "frame_count": 124,
                        "video_codec": "h264",
                        "container": "mp4",
                        "has_video": True,
                        "has_audio": True,
                        "audio_sample_rate": 48000,
                        "audio_channels": 2,
                        "audio_codec": "aac",
                        "checksum": "f" * 64,
                        "size_bytes": 1000,
                    },
                }
                for ratio, canvas in profile["resolution"]["canvases"].items()
            ],
            "output": {
                "width": 480,
                "height": 864,
                "fps": 24,
                "duration_seconds": 124 / 24,
                "frame_count": 124,
                "video_codec": "h264",
                "container": "mp4",
                "has_audio": True,
                "has_video": True,
                "audio_sample_rate": 48000,
                "audio_channels": 2,
                "audio_codec": "aac",
                "checksum": "f" * 64,
                "size_bytes": 1000,
            },
        },
    )
    return profile, approved

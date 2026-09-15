from __future__ import annotations

import argparse
import asyncio
import json
import mimetypes
import time
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any
from uuid import uuid4

from apps.api.app.integrations.comfy_adapter import ComfyAdapter
from apps.api.app.services.h3_validator import H3Profile, H3Request, H3Validator
from apps.api.app.services.workflow_loader import load_manifests
from apps.api.app.services.workflow_registry import ApprovedWorkflow


class ProbeFailure(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ProbeResult:
    code: str
    mode: str
    version: str
    accepted: bool
    executed: bool
    workflow_hash: str
    slot_map_hash: str
    prompt_id: str | None
    output_kind: str | None
    width: int
    height: int
    frames: int
    elapsed_seconds: float | None
    gpu_memory_mb: float | None
    error_code: str | None
    error_message: str | None
    details: dict[str, Any]


class H3Probe:
    ASSET_SLOTS = frozenset(
        {
            "FIRST_FRAME",
            "LAST_FRAME",
            "REFERENCE_IMAGE_1",
            "REFERENCE_AUDIO_1",
            "REFERENCE_VIDEO_1",
        }
    )

    def __init__(self, adapter: Any | None = None):
        self.adapter = adapter

    @staticmethod
    def _workflow(entry: dict[str, Any]) -> ApprovedWorkflow:
        workflow = ApprovedWorkflow(
            mode=entry["mode"],
            version=entry["version"],
            workflow=entry["workflow"],
            slots={
                name: (str(binding[0]), str(binding[1]))
                for name, binding in entry["slots"].items()
            },
            required_slots=frozenset(entry.get("required_slots", [])),
        )
        workflow.validate()
        return workflow

    @staticmethod
    def _profile(entry: dict[str, Any]) -> H3Profile:
        allowed = {item.name for item in fields(H3Profile)}
        return H3Profile(
            **{key: value for key, value in entry.get("profile", {}).items() if key in allowed}
        )

    @staticmethod
    def _request(mode: str, width: int, height: int, duration: float) -> H3Request:
        values: dict[str, Any] = {}
        if mode == "i2v":
            values["first_frame_asset_id"] = "probe-first-frame"
        elif mode == "i2v_first_last":
            values["first_frame_asset_id"] = "probe-first-frame"
            values["last_frame_asset_id"] = "probe-last-frame"
        elif mode == "r2v":
            values["reference_image_asset_ids"] = ["probe-reference-image"]
        return H3Request(
            mode=mode,
            width=width,
            height=height,
            duration_seconds=duration,
            **values,
        )

    @staticmethod
    def _gpu_memory_mb(stats: dict[str, Any]) -> float | None:
        devices = stats.get("devices")
        if not isinstance(devices, list):
            return None
        used = []
        for device in devices:
            if not isinstance(device, dict):
                continue
            total = device.get("vram_total")
            free = device.get("vram_free")
            if isinstance(total, (int, float)) and isinstance(free, (int, float)):
                used.append(max(0.0, float(total) - float(free)) / 1024**2)
        return max(used) if used else None

    async def _upload_media(
        self,
        workflow: ApprovedWorkflow,
        values: dict[str, Any],
        media: dict[str, Path],
    ) -> None:
        required_assets = workflow.required_slots & self.ASSET_SLOTS
        missing = required_assets - set(media)
        if missing:
            raise ProbeFailure(
                "PROBE_MEDIA_REQUIRED",
                f"Execution requires media for: {', '.join(sorted(missing))}",
            )
        for slot in sorted(required_assets):
            path = media[slot].resolve()
            if not path.is_file():
                raise ProbeFailure("PROBE_MEDIA_MISSING", f"Media file does not exist: {path}")
            data = await asyncio.to_thread(path.read_bytes)
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            values[slot] = await self.adapter.upload(data, path.name, content_type)

    async def run(
        self,
        entry: dict[str, Any],
        *,
        execute: bool = False,
        media: dict[str, Path] | None = None,
        width: int = 480,
        height: int = 864,
        duration_seconds: float = 5,
        steps: int = 8,
        timeout_seconds: float = 3600,
    ) -> ProbeResult:
        workflow_hash = slot_map_hash = ""
        frames_count = 0
        started = time.monotonic()
        try:
            workflow = self._workflow(entry)
            workflow_hash, slot_map_hash = workflow.workflow_hash, workflow.slot_map_hash
            output_node = str(entry.get("profile", {}).get("output_node", ""))
            if not output_node or output_node not in workflow.workflow:
                raise ProbeFailure(
                    "WORKFLOW_OUTPUT_NODE_MISSING",
                    "Configured output node does not exist in the workflow",
                )
            profile = self._profile(entry)
            validated = H3Validator(profile).validate(
                self._request(entry["mode"], width, height, duration_seconds)
            )
            frames_count = validated.frames
            aspect_values = entry.get("profile", {}).get("aspect_ratio_values", {})
            common = {
                "PROMPT": "Cinematic commercial product reveal on a clean studio set",
                "NEGATIVE_PROMPT": "distorted product, unreadable label",
                "SEED": 1,
                "WIDTH": width,
                "HEIGHT": height,
                "FRAMES": frames_count,
                "LENGTH": frames_count,
                "DURATION": duration_seconds,
                "STEPS": steps,
                "FPS": profile.fps,
                "ASPECT_RATIO": aspect_values.get("9:16", "9:16"),
                "OUTPUT_PREFIX": f"h3-probe/{entry['mode']}/{uuid4().hex}",
                "FIRST_FRAME": "probe-first-frame.png",
                "LAST_FRAME": "probe-last-frame.png",
                "REFERENCE_IMAGE_1": "probe-reference-image.png",
                "REFERENCE_AUDIO_1": "probe-reference-audio.wav",
                "REFERENCE_VIDEO_1": "probe-reference-video.mp4",
            }
            values = {name: common[name] for name in workflow.slots if name in common}
            if execute:
                if self.adapter is None:
                    raise ProbeFailure("COMFY_ADAPTER_REQUIRED", "Execution needs a Comfy adapter")
                await self._upload_media(workflow, values, media or {})
            patched = workflow.patch(values)
            details = {
                "workflow_nodes": len(patched),
                "required_slots": sorted(workflow.required_slots),
                "output_node": output_node,
                "profile_poc_verified": bool(entry.get("profile", {}).get("poc_verified")),
                "provenance": entry.get("profile", {}).get("provenance"),
            }
            if not execute:
                return ProbeResult(
                    entry["code"],
                    entry["mode"],
                    entry["version"],
                    True,
                    False,
                    workflow_hash,
                    slot_map_hash,
                    None,
                    None,
                    width,
                    height,
                    frames_count,
                    None,
                    None,
                    None,
                    None,
                    details,
                )
            stats = await self.adapter.system_stats()
            gpu_memory = self._gpu_memory_mb(stats)
            client_id = f"studio-h3-probe-{uuid4()}"
            prompt_id = await self.adapter.submit(patched, client_id)
            while time.monotonic() - started <= timeout_seconds:
                history = await self.adapter.get_history(prompt_id)
                if history:
                    status = history.get("status", {})
                    if status.get("status_str") == "error":
                        raise ProbeFailure("COMFY_EXECUTION_FAILED", str(status))
                    if status.get("completed") or status.get("status_str") == "success":
                        outputs = self.adapter.outputs(history, output_node)
                        if not outputs:
                            raise ProbeFailure(
                                "COMFY_OUTPUT_MISSING", "Workflow completed without output"
                            )
                        return ProbeResult(
                            entry["code"],
                            entry["mode"],
                            entry["version"],
                            True,
                            True,
                            workflow_hash,
                            slot_map_hash,
                            prompt_id,
                            outputs[0].get("kind"),
                            width,
                            height,
                            frames_count,
                            round(time.monotonic() - started, 3),
                            gpu_memory,
                            None,
                            None,
                            details,
                        )
                await asyncio.sleep(1)
            raise ProbeFailure("PROBE_TIMEOUT", "Timed out waiting for ComfyUI history")
        except Exception as exc:
            return ProbeResult(
                str(entry.get("code", "unknown")),
                str(entry.get("mode", "unknown")),
                str(entry.get("version", "unknown")),
                False,
                execute,
                workflow_hash,
                slot_map_hash,
                None,
                None,
                width,
                height,
                frames_count,
                round(time.monotonic() - started, 3) if execute else None,
                None,
                getattr(exc, "code", exc.__class__.__name__.upper()),
                str(exc),
                {},
            )


def _report(results: list[ProbeResult], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    rows = [asdict(result) for result in results]
    (destination.parent / "results.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# H3 / ComfyUI compatibility gate",
        "",
        "| Mode | Workflow | Preflight | Executed | Frames | Result |",
        "| --- | --- | --- | --- | ---: | --- |",
    ]
    for result in results:
        outcome = "PASS" if result.accepted else f"FAIL: {result.error_code}"
        lines.append(
            f"| {result.mode} | `{result.code}@{result.version}` | "
            f"{'yes' if result.workflow_hash else 'no'} | "
            f"{'yes' if result.executed else 'no'} | {result.frames} | {outcome} |"
        )
    lines.extend(
        [
            "",
            "Reference workflows stay disabled until every enabled mode has an executed PASS "
            "on the target H3/ComfyUI workstation and its model/custom-node hashes are recorded.",
            "",
        ]
    )
    destination.write_text("\n".join(lines), encoding="utf-8")


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate or execute H3 reference workflows")
    parser.add_argument("--mode", choices=("t2v", "i2v", "i2v_first_last", "r2v"))
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--media",
        action="append",
        default=[],
        metavar="SLOT=PATH",
        help="Media for required workflow slots, for example FIRST_FRAME=C:/frame.png",
    )
    return parser.parse_args()


async def _main() -> int:
    from apps.api.app.core.config import get_settings

    args = _arguments()
    if not args.all and not args.mode:
        raise SystemExit("Select --all or --mode")
    settings = get_settings()
    entries = load_manifests(Path(settings.workflow_dir))
    selected = entries if args.all else [item for item in entries if item["mode"] == args.mode]
    media = {}
    for item in args.media:
        name, separator, value = item.partition("=")
        if not separator or not name or not value:
            raise SystemExit(f"Invalid --media value: {item}")
        media[name] = Path(value)
    adapter = ComfyAdapter(settings) if args.execute else None
    results = [
        await H3Probe(adapter).run(entry, execute=args.execute, media=media)
        for entry in selected
    ]
    root = await asyncio.to_thread(lambda: Path(__file__).resolve().parents[3])
    _report(results, root / "docs" / "h3-poc" / "results.md")
    return 0 if all(result.accepted for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))

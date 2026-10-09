"""Static validation and honest, byte-inspected target-runtime PoC records."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import mimetypes
import re
import time
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from apps.api.app.integrations.comfy_adapter import ComfyAdapter
from apps.api.app.integrations.media import inspect_media
from apps.api.app.services.h3_validator import H3Profile, H3Request, H3Validator
from apps.api.app.services.workflow_contracts import (
    SELECTOR_SHA256,
    profile_hash,
    resolve_canvas,
    upstream_nodes,
)
from apps.api.app.services.workflow_loader import load_manifests
from apps.api.app.services.workflow_registry import ApprovedWorkflow, _stable_hash
from apps.api.scripts.probe_media import measure_probe_output


class ProbeFailure(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class ProbeResult:
    code: str
    mode: str
    version: str
    accepted: bool = False
    executed: bool = False
    stage: str = "NOT_RUN"
    qualified: bool = False
    workflow_hash: str = ""
    slot_map_hash: str = ""
    prompt_id: str | None = None
    output_kind: str | None = None
    width: int | None = None
    height: int | None = None
    resolved_width: int | None = None
    resolved_height: int | None = None
    frames: int = 0
    elapsed_seconds: float | None = None
    gpu_memory_mb: float | None = None
    output_metadata: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    details: dict[str, Any] | None = None


def is_asset_slot(name: str) -> bool:
    return name in {"FIRST_FRAME", "LAST_FRAME"} or bool(
        re.fullmatch(r"REFERENCE_(?:IMAGE_[1-9]|VIDEO_[1-3]|AUDIO_[1-3])", name)
    )


def unresolved_submission(result: ProbeResult | dict[str, Any]) -> bool:
    """One admission guard for live results and persisted (including legacy) rows."""
    value = asdict(result) if isinstance(result, ProbeResult) else result
    return value.get("stage") in {"SUBMISSION_PENDING", "SUBMITTED"} or value.get("error_code") in {
        "COMFY_SUBMISSION_UNCERTAIN",
        "PROBE_INTERRUPTED_RECONCILE",
    }


class H3Probe:
    def __init__(self, adapter: Any | None = None, *, ffprobe_binary: str = "ffprobe"):
        self.adapter = adapter
        self.ffprobe_binary = ffprobe_binary

    @staticmethod
    def _workflow(entry: dict[str, Any]) -> ApprovedWorkflow:
        return ApprovedWorkflow.from_manifest(entry)

    @staticmethod
    def _profile(entry: dict[str, Any]) -> H3Profile:
        allowed = {item.name for item in fields(H3Profile)}
        return H3Profile(
            **{key: value for key, value in entry.get("profile", {}).items() if key in allowed}
        )

    @staticmethod
    def _resolution(entry, workflow):
        declared = entry.get("profile", {}).get("resolution")
        if declared:
            return declared
        binding = workflow.slots.get("ASPECT_RATIO")
        if binding and workflow.workflow[binding[0]].get("class_type") == "ResolutionSelector":
            # Source-pinned static interpretation; runtime compatibility is checked separately.
            return {
                "kind": "resolution_selector",
                "node_id": binding[0],
                "source_sha256": SELECTOR_SHA256,
                "aspect_ratio_values": entry.get("profile", {}).get("aspect_ratio_values", {}),
            }
        raise ProbeFailure("CANVAS_RESOLVER_REQUIRED", "Declare the graph-bound canvas resolver")

    @staticmethod
    def _gpu_memory_mb(stats: dict[str, Any]) -> float | None:
        devices = stats.get("devices")
        if not isinstance(devices, list):
            return None
        used = []
        for device in devices:
            if not isinstance(device, dict):
                continue
            total, free = device.get("vram_total"), device.get("vram_free")
            if (
                type(total) in {int, float}
                and type(free) in {int, float}
                and math.isfinite(total)
                and math.isfinite(free)
                and 0 <= free <= total
            ):
                used.append((total - free) / 1024**2)
        return max(used) if used else None

    async def _runtime_interfaces(self, workflow):
        inventory = await self.adapter.object_info()
        if not isinstance(inventory, dict):
            raise ProbeFailure("RUNTIME_NODE_INFO_INVALID", "Runtime did not return node inventory")
        for node in workflow.workflow.values():
            name = node.get("class_type")
            schema = inventory.get(name)
            if not isinstance(schema, dict):
                raise ProbeFailure("RUNTIME_NODE_MISSING", f"Runtime lacks executable class {name}")
            inputs = schema.get("input", {})
            supported = {
                key for group in ("required", "optional", "hidden") for key in inputs.get(group, {})
            }
            if set(node.get("inputs", {})) - supported:
                raise ProbeFailure(
                    "RUNTIME_INPUT_UNVERIFIED", f"Unverified runtime inputs on {name}"
                )
        return _stable_hash(inventory)

    async def _assets(self, workflow, media, *, execute):
        slots = {name for name in workflow.slots if is_asset_slot(name)}
        if set(media) - slots:
            raise ProbeFailure(
                "PROBE_MEDIA_UNSUPPORTED", "Media includes an unsupported symbolic slot"
            )
        requested = (workflow.required_slots & slots) | set(media)
        if execute and requested - set(media):
            raise ProbeFailure(
                "PROBE_MEDIA_REQUIRED", "Every requested frame/reference needs a file"
            )
        values, measured = {}, {}
        for slot in sorted(requested):
            if not execute:
                values[slot] = f"static-{slot.lower()}"
                continue
            path = media[slot].resolve()
            if not path.is_file() or not 0 < path.stat().st_size <= 512 * 1024 * 1024:
                raise ProbeFailure(
                    "PROBE_MEDIA_MISSING", "Media file is absent, empty or oversized"
                )

            def read_bounded(media_path=path):
                with media_path.open("rb") as source:
                    return source.read(512 * 1024 * 1024 + 1)

            data = await asyncio.to_thread(read_bounded)
            if len(data) > 512 * 1024 * 1024:
                raise ProbeFailure("PROBE_MEDIA_OVERSIZED", "Media exceeds the upload bound")
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            metadata = await inspect_media(data, content_type, path.name, self.ffprobe_binary)
            expected = "VIDEO" if "VIDEO_" in slot else "AUDIO" if "AUDIO_" in slot else "IMAGE"
            if metadata["kind"] != expected:
                raise ProbeFailure("PROBE_MEDIA_KIND_MISMATCH", "Media kind differs from slot role")
            if expected == "VIDEO" and metadata.get("fps") != 24:
                raise ProbeFailure(
                    "REFERENCE_VIDEO_FPS", "Normalize reference video to 24 FPS first"
                )
            measured[slot] = {
                **metadata,
                "checksum": hashlib.sha256(data).hexdigest(),
                "size_bytes": len(data),
            }
            values[slot] = await self.adapter.upload(data, path.name, content_type)
        return values, measured

    @staticmethod
    def _request(mode, width, height, duration, assets, metadata, *, static=False):
        def ordered(prefix):
            return sorted(
                (name for name in assets if name.startswith(prefix)),
                key=lambda name: int(name.rsplit("_", 1)[1]),
            )

        images, videos, audio = (
            ordered(f"REFERENCE_{kind}_") for kind in ("IMAGE", "VIDEO", "AUDIO")
        )
        return H3Request(
            mode=mode,
            width=width,
            height=height,
            duration_seconds=duration,
            first_frame_asset_id=assets.get("FIRST_FRAME"),
            last_frame_asset_id=assets.get("LAST_FRAME"),
            reference_image_asset_ids=images,
            reference_video_asset_ids=videos,
            reference_audio_asset_ids=audio,
            reference_video_durations=[
                2 if static else metadata.get(name, {}).get("video_duration_seconds")
                for name in videos
            ],
            reference_audio_durations=[
                2 if static else metadata.get(name, {}).get("audio_duration_seconds")
                for name in audio
            ],
        )

    async def run(
        self,
        entry: dict[str, Any],
        *,
        execute: bool = False,
        media: dict[str, Path] | None = None,
        width: int | None = None,
        height: int | None = None,
        aspect_ratio: str = "9:16",
        duration_seconds: float = 5,
        steps: int | None = None,
        timeout_seconds: float = 3600,
        poll_interval_seconds: float = 1,
        vram_sample_interval_seconds: float = 1,
        runtime_provenance: dict | None = None,
        client_id: str | None = None,
    ) -> ProbeResult:
        result = ProbeResult(
            str(entry.get("code", "unknown")),
            str(entry.get("mode", "unknown")),
            str(entry.get("version", "unknown")),
            details={},
        )
        details = result.details
        started = time.monotonic()
        stop_sampling = asyncio.Event()
        sampling_task = None
        samples = []

        async def sample():
            try:
                stats = await asyncio.wait_for(self.adapter.system_stats(), timeout=3)
                value = self._gpu_memory_mb(stats)
                if value is not None:
                    samples.append(value)
            except Exception:
                details["vram_sampling_incomplete"] = True

        async def sampling_loop():
            while not stop_sampling.is_set():
                try:
                    await asyncio.wait_for(
                        stop_sampling.wait(), timeout=vram_sample_interval_seconds
                    )
                except TimeoutError:
                    await sample()

        async def bounded(operation):
            remaining = timeout_seconds - (time.monotonic() - started)
            if remaining <= 0:
                operation.close()
                raise ProbeFailure("PROBE_TIMEOUT", "Target runtime probe timed out")
            return await asyncio.wait_for(operation, timeout=remaining)

        try:
            if entry.get("profile", {}).get("provider") == "minimax_h3_director":
                raise ProbeFailure(
                    "DIRECTOR_PROBE_REQUIRED", "Use director_probe for Director workflows"
                )
            if any(
                type(value) not in {int, float} or not math.isfinite(value) or value <= 0
                for value in (timeout_seconds, poll_interval_seconds, vram_sample_interval_seconds)
            ):
                raise ProbeFailure("PROBE_ARGUMENT_INVALID", "Probe time bounds must be positive")
            workflow = self._workflow(entry)
            result.workflow_hash, result.slot_map_hash = (
                workflow.workflow_hash,
                workflow.slot_map_hash,
            )
            profile = entry.get("profile", {})
            output_node = str(profile.get("output_node", ""))
            if not output_node or output_node not in workflow.workflow:
                raise ProbeFailure(
                    "WORKFLOW_OUTPUT_NODE_MISSING", "Configured output node is absent"
                )
            resolution = self._resolution(entry, workflow)
            resolved_width, resolved_height = resolve_canvas(resolution, workflow, aspect_ratio)
            result.resolved_width, result.resolved_height = resolved_width, resolved_height
            if (
                width is not None
                and width != resolved_width
                or height is not None
                and height != resolved_height
            ):
                raise ProbeFailure(
                    "CANVAS_OVERRIDE_MISMATCH", "Requested canvas differs from graph resolver"
                )
            if steps is None:
                if "steps" in profile:
                    steps = profile["steps"]
                elif "STEPS" in workflow.slots:
                    node_id, field = workflow.slots["STEPS"]
                    steps = workflow.workflow[node_id]["inputs"][field]
            if type(steps) is not int or not 1 <= steps <= 100:
                raise ProbeFailure("PROBE_STEPS_INVALID", "Declare candidate step count explicitly")
            mutable_steps = workflow.slots.get("STEPS")
            fixed_binding = profile.get("setting_bindings", {}).get("steps")
            binding = mutable_steps or fixed_binding
            if (
                not isinstance(binding, (list, tuple))
                or len(binding) != 2
                or binding[0] not in upstream_nodes(workflow, output_node)
                or binding[1] not in workflow.workflow[binding[0]]["inputs"]
                or fixed_binding is not None
                and tuple(fixed_binding) != tuple(binding)
            ):
                raise ProbeFailure(
                    "PROBE_STEPS_BINDING_REQUIRED",
                    "Candidate steps need an explicit binding on the collected output path",
                )
            if not mutable_steps and workflow.workflow[binding[0]]["inputs"][binding[1]] != steps:
                raise ProbeFailure(
                    "PROBE_FIXED_STEPS_MISMATCH", "Fixed graph steps differ from candidate steps"
                )
            details.update(
                {
                    "requested_aspect_ratio": aspect_ratio,
                    "quality_profile": profile.get("quality_profile"),
                    "candidate_steps": steps,
                    "candidate_steps_binding": list(binding),
                    "candidate_steps_mutable": bool(mutable_steps),
                    "runtime_provenance": runtime_provenance,
                    "profile_hash": profile_hash({**profile, "steps": steps}),
                    "tested_at": datetime.now(UTC).isoformat(),
                    "release_gate": (
                        "NOT_APPROVED: separate runtime/profile/benchmark qualification required"
                    ),
                }
            )
            assets, metadata = await self._assets(workflow, media or {}, execute=False)
            validated = H3Validator(self._profile(entry)).validate(
                self._request(
                    entry["mode"],
                    resolved_width,
                    resolved_height,
                    duration_seconds,
                    assets,
                    metadata,
                    static=True,
                )
            )
            result.frames = validated.frames
            common = {
                "PROMPT": "Cinematic commercial product reveal on a clean studio set",
                "NEGATIVE_PROMPT": "distorted product, unreadable label",
                "SEED": 1,
                "WIDTH": resolved_width,
                "HEIGHT": resolved_height,
                "FRAMES": result.frames,
                "LENGTH": result.frames,
                "DURATION": duration_seconds,
                "STEPS": steps,
                "FPS": 24,
                "ASPECT_RATIO": resolution.get("aspect_ratio_values", {}).get(
                    aspect_ratio, aspect_ratio
                ),
                "OUTPUT_PREFIX": f"h3-probe/{entry['mode']}/{uuid4().hex}",
                **assets,
            }
            patched = workflow.patch(
                {key: value for key, value in common.items() if key in workflow.slots}
            )
            details["candidate_steps"] = patched[binding[0]]["inputs"][binding[1]]
            details["static_graph_hash"] = _stable_hash(patched)
            result.stage = "STATIC_VALIDATED"
            if not execute:
                result.accepted = True
                details["reference_media_inspected"] = False
                return result
            if self.adapter is None:
                raise ProbeFailure("COMFY_ADAPTER_REQUIRED", "Execution needs a Comfy adapter")
            details["runtime_node_info_hash"] = await bounded(self._runtime_interfaces(workflow))
            assets, metadata = await bounded(self._assets(workflow, media or {}, execute=True))
            H3Validator(self._profile(entry)).validate(
                self._request(
                    entry["mode"],
                    resolved_width,
                    resolved_height,
                    duration_seconds,
                    assets,
                    metadata,
                )
            )
            details["reference_media"] = metadata
            common.update(assets)
            patched = workflow.patch(
                {key: value for key, value in common.items() if key in workflow.slots}
            )
            details["submitted_graph_hash"] = _stable_hash(patched)
            details["client_id"] = client_id or f"studio-h3-probe-{uuid4().hex}"
            queue = await bounded(self.adapter.get_queue())
            if not isinstance(queue, dict) or any(
                not isinstance(queue.get(key), list) for key in ("queue_running", "queue_pending")
            ):
                raise ProbeFailure("COMFY_QUEUE_INVALID", "Runtime queue response is invalid")
            if queue["queue_running"] or queue["queue_pending"]:
                raise ProbeFailure(
                    "COMFY_QUEUE_BUSY", "Benchmark requires an isolated idle runtime"
                )
            await sample()
            sampling_task = asyncio.create_task(sampling_loop())
            submit_started = time.monotonic()

            async def submit():
                # Mark entry inside the bounded operation: exhausted preflight budget
                # must not claim that the adapter's submission boundary was entered.
                result.stage = "SUBMISSION_PENDING"
                details["submission_started"] = True
                return await self.adapter.submit(patched, details["client_id"])

            result.prompt_id = await bounded(submit())
            result.stage = "SUBMITTED"
            details["submit_seconds"] = time.monotonic() - submit_started
            execution_observed = time.monotonic()
            while True:
                history = await bounded(self.adapter.get_history(result.prompt_id))
                if history:
                    status = history.get("status", {})
                    terminal = status.get("completed") is True or status.get("status_str") in {
                        "success",
                        "error",
                    }
                    if terminal:
                        result.executed = True
                        result.stage = "EXECUTED"
                        details["execution_observation_seconds"] = (
                            time.monotonic() - execution_observed
                        )
                        details["queue_wait_seconds"] = None
                        details["execution_seconds"] = None
                        if status.get("status_str") == "error":
                            raise ProbeFailure(
                                "COMFY_EXECUTION_FAILED", "Runtime reported execution failure"
                            )
                        outputs = self.adapter.outputs(history, output_node)
                        if not outputs:
                            raise ProbeFailure(
                                "COMFY_OUTPUT_MISSING", "Workflow completed without output"
                            )
                        collection_started = time.monotonic()
                        data = await bounded(self.adapter.download(outputs[0]))
                        measured = await bounded(
                            measure_probe_output(
                                data,
                                expected_width=resolved_width,
                                expected_height=resolved_height,
                                expected_fps=24,
                                expected_frames=result.frames,
                                ffprobe_binary=self.ffprobe_binary,
                            )
                        )
                        if (
                            type(measured.get("decoded_video_frames")) is not int
                            or measured["decoded_video_frames"] != result.frames
                            or type(measured.get("decoded_audio_frames")) is not int
                            or measured["decoded_audio_frames"] <= 0
                        ):
                            raise ProbeFailure(
                                "OUTPUT_DECODE_EVIDENCE_REQUIRED",
                                "Actual decoded AV evidence required",
                            )
                        result.output_metadata = measured
                        result.width, result.height = measured["width"], measured["height"]
                        result.output_kind = outputs[0].get("kind", "video")
                        details["collection_seconds"] = time.monotonic() - collection_started
                        result.stage = "VERIFIED"
                        result.accepted = True
                        return result
                await bounded(asyncio.sleep(poll_interval_seconds))
        except Exception as exc:
            result.error_code = getattr(
                exc,
                "code",
                "PROBE_TIMEOUT"
                if isinstance(exc, TimeoutError)
                else exc.__class__.__name__.upper(),
            )
            result.error_message = str(exc)
            if result.stage == "SUBMISSION_PENDING":
                if result.error_code == "COMFY_REJECTED":
                    result.stage = "STATIC_VALIDATED"
                    details["submission_rejected"] = True
                else:
                    details["cause_error_code"] = result.error_code
                    result.error_code = "COMFY_SUBMISSION_UNCERTAIN"
            return result
        finally:
            stop_sampling.set()
            if sampling_task:
                await sampling_task
            if execute:
                result.elapsed_seconds = time.monotonic() - started
            details["vram_samples"] = len(samples)
            details["vram_peak_kind"] = "SAMPLED_MAX" if samples else "NOT_MEASURED"
            result.gpu_memory_mb = max(samples) if samples else None


def _report(results: list[ProbeResult], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    result_path = destination.parent / "results.json"
    temporary = result_path.with_name(f"results.{uuid4().hex}.part")
    temporary.write_text(
        json.dumps([asdict(result) for result in results], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(result_path)
    lines = [
        "# H3 / ComfyUI probe record",
        "",
        "| Mode | Stage | Executed | Media verified | Actual canvas | Error |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for result in results:
        lines.append(
            f"| {result.mode} | {result.stage} | {result.executed} | "
            f"{result.stage == 'VERIFIED'} | {result.width}x{result.height} | "
            f"{result.error_code or '-'} |"
        )
    lines.extend(
        ["", "Static validation and media verification do not approve a production workflow.", ""]
    )
    destination.write_text("\n".join(lines), encoding="utf-8")


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("t2v", "i2v", "i2v_last", "i2v_first_last", "r2v"))
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--aspect-ratio", default="9:16", choices=("9:16", "16:9", "1:1"))
    parser.add_argument("--duration", type=float, default=5)
    parser.add_argument("--steps", type=int)
    parser.add_argument("--media", action="append", default=[], metavar="SLOT=PATH")
    parser.add_argument("--runtime-provenance", type=Path)
    parser.add_argument("--output-dir", type=Path)
    return parser.parse_args()


async def _main() -> int:
    from apps.api.app.core.config import get_settings

    args = _arguments()
    if not args.all and not args.mode:
        raise SystemExit("Select --all or --mode")
    settings = get_settings()
    entries = load_manifests(Path(settings.workflow_dir))
    entries = [
        item for item in entries if item.get("profile", {}).get("provider") != "minimax_h3_director"
    ]
    selected = entries if args.all else [item for item in entries if item["mode"] == args.mode]
    if not selected:
        raise SystemExit("No matching workflow manifest")
    media = {}
    for item in args.media:
        name, separator, value = item.partition("=")
        if not separator or not is_asset_slot(name) or not value:
            raise SystemExit(f"Invalid --media value: {item}")
        media[name] = Path(value)
    provenance = (
        json.loads(args.runtime_provenance.read_text("utf-8")) if args.runtime_provenance else None
    )
    adapter = ComfyAdapter(settings) if args.execute else None
    output = args.output_dir or Path(settings.workspace_root) / "h3-poc" / uuid4().hex
    if (output / "results.json").exists():
        raise SystemExit("Existing probe evidence requires a new output directory")
    results = []
    probe = H3Probe(adapter, ffprobe_binary=settings.ffprobe_binary)
    for entry in selected:
        correlation = f"studio-h3-probe-{uuid4().hex}"
        results.append(
            ProbeResult(
                str(entry["code"]),
                str(entry["mode"]),
                str(entry["version"]),
                stage="SUBMISSION_PENDING" if args.execute else "NOT_RUN",
                error_code="PROBE_INTERRUPTED_RECONCILE" if args.execute else None,
                details={"client_id": correlation},
            )
        )
        _report(results, output / "results.md")
        results[-1] = await probe.run(
            entry,
            execute=args.execute,
            media=media,
            aspect_ratio=args.aspect_ratio,
            duration_seconds=args.duration,
            steps=args.steps,
            runtime_provenance=provenance,
            timeout_seconds=settings.comfy_timeout_seconds,
            client_id=correlation,
        )
        _report(results, output / "results.md")
        if unresolved_submission(results[-1]):
            break
    print(output.resolve())
    return 0 if results and all(result.accepted for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))

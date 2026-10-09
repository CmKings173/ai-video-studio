"""Repeatable H3 candidate matrix; observed timings never imply release approval."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx

from apps.api.app.integrations.comfy_adapter import ComfyAdapter
from apps.api.app.services.workflow_loader import load_manifests
from apps.api.app.services.workflow_registry import _stable_hash
from apps.api.scripts.h3_probe import H3Probe, unresolved_submission


@dataclass(frozen=True)
class BenchmarkCase:
    name: str
    mode: str
    media: dict[str, Path]
    required_categories: tuple[str, ...] = ()


CASE_CATEGORIES = {
    "first_frame": ("first_frame",),
    "last_frame": ("last_frame",),
    "first_last": ("first_frame", "last_frame"),
    "ref_image": ("image",),
    "ref_video": ("video",),
    "ref_audio_image": ("image", "audio"),
    "ref_mixed": ("image", "video", "audio"),
}


def _missing_categories(case):
    required = set(case.required_categories) | set(CASE_CATEGORIES.get(case.name, ()))
    present = {
        category
        for category in required
        if any(
            name == category.upper()
            if category.endswith("frame")
            else name.startswith(f"REFERENCE_{category.upper()}_")
            for name in case.media
        )
    }
    return sorted(required - present)


def _percentile(values, fraction):
    if not values:
        return None
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)]


def _write_report(destination, report):
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + "." + uuid4().hex + ".part")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(destination)


def _media_identity(cases):
    values = []
    for case in cases:
        media = {}
        for slot, path in sorted(case.media.items()):
            digest = None
            if path.is_file():
                with path.open("rb") as source:
                    digest = hashlib.file_digest(source, "sha256").hexdigest()
            media[slot] = {"path": str(path.resolve()), "checksum": digest}
        values.append(
            {
                "name": case.name,
                "mode": case.mode,
                "media": media,
                "required_categories": sorted(
                    set(case.required_categories) | set(CASE_CATEGORIES.get(case.name, ()))
                ),
            }
        )
    return values


async def run_matrix(
    *,
    adapter,
    entries,
    cases,
    ratios,
    durations,
    profiles,
    repeats,
    execute,
    destination,
    resume=False,
    cold_runtime_confirmed=False,
    ffprobe_binary="ffprobe",
    runtime_provenance=None,
    timeout_seconds=3600,
    probe_factory=H3Probe,
):
    if type(repeats) is not int or not 3 <= repeats <= 10:
        raise ValueError("At least three and at most ten warmed repeats are required")
    if not cases or not ratios or not durations or not profiles:
        raise ValueError("Benchmark matrix must not be empty")
    if any(len(set(values)) != len(values) for values in (ratios, durations, profiles)):
        raise ValueError("Benchmark axis values must be unique")
    if any(ratio not in {"9:16", "16:9", "1:1"} for ratio in ratios):
        raise ValueError("Unsupported benchmark ratio")
    if any(
        type(value) not in {int, float} or not math.isfinite(value) or not 4 <= value <= 15
        for value in durations
    ):
        raise ValueError("Benchmark duration must be finite and between 4 and 15 seconds")
    if any(profile not in {"DRAFT", "STANDARD", "HIGH"} for profile in profiles):
        raise ValueError("Unknown quality profile")
    if len({case.name for case in cases}) != len(cases):
        raise ValueError("Benchmark case names must be unique")
    destination = Path(destination)
    identity = {
        "entries": entries,
        "cases": _media_identity(cases),
        "ratios": ratios,
        "durations": durations,
        "profiles": profiles,
        "repeats": repeats,
        "execute": execute,
        "runtime_provenance": runtime_provenance,
        "ffprobe_binary": ffprobe_binary,
        "cold_runtime_confirmed": cold_runtime_confirmed,
    }
    plan_hash = _stable_hash(identity)
    if resume:
        report = json.loads(destination.read_text("utf-8"))
        if report.get("schema_version") != 1 or report.get("plan_hash") != plan_hash:
            raise ValueError("Resume inputs are different from the saved matrix")
        if any(unresolved_submission(row) for row in report["rows"]):
            report["halt_reason"] = "Reconcile uncertain prompt before resume"
            report.setdefault("summary", {})["complete"] = False
            _write_report(destination, report)
            return report
        report.pop("halt_reason", None)
    else:
        if destination.exists():
            raise ValueError("Existing results require explicit resume; choose a new destination")
        report = {
            "schema_version": 1,
            "plan_hash": plan_hash,
            "plan": identity,
            "concurrency": 1,
            "cold_runtime_confirmed": cold_runtime_confirmed,
            "release_gate": "NOT_APPROVED",
            "segments": [],
            "rows": [],
        }
    segment_id = uuid4().hex
    report["segments"].append(
        {"id": segment_id, "started_at": datetime.now(UTC).isoformat(), "resumed": resume}
    )
    probe = probe_factory(adapter, ffprobe_binary=ffprobe_binary)
    previous_family = None
    halt = False
    cell_ids = []

    async def measured(entry, case, cell, phase, repetition=None):
        started = time.monotonic()
        client_id = f"studio-h3-benchmark-{uuid4().hex}"
        row = {
            **cell,
            "phase": phase,
            "repetition": repetition,
            "segment_id": segment_id,
            "client_id": client_id,
            "stage": "SUBMISSION_PENDING",
            "executed": False,
            "accepted": False,
            "error_code": "PROBE_INTERRUPTED_RECONCILE",
        }
        report["rows"].append(row)
        _write_report(destination, report)
        result = await probe.run(
            entry,
            execute=True,
            media=case.media,
            aspect_ratio=cell["aspect_ratio"],
            duration_seconds=cell["duration_seconds"],
            runtime_provenance=runtime_provenance,
            timeout_seconds=timeout_seconds,
            client_id=client_id,
        )
        row.update({**asdict(result), "wall_seconds": time.monotonic() - started})
        _write_report(destination, report)
        return row

    for case in cases:
        family = "REF2VA" if case.mode == "r2v" else "FL2VA"
        for quality in profiles:
            matches = [
                entry
                for entry in entries
                if entry["mode"] == case.mode
                and entry.get("profile", {}).get("quality_profile") == quality
            ]
            if len(matches) > 1:
                raise ValueError("Select exactly one candidate version per mode/quality")
            entry = matches[0] if matches else None
            for ratio in ratios:
                for duration in durations:
                    cell = {
                        "case": case.name,
                        "mode": case.mode,
                        "quality_profile": quality,
                        "aspect_ratio": ratio,
                        "duration_seconds": duration,
                        "family": family,
                    }
                    cell["cell_id"] = _stable_hash(cell)
                    cell_ids.append(cell["cell_id"])
                    previous = [
                        row for row in report["rows"] if row.get("cell_id") == cell["cell_id"]
                    ]
                    warm = {
                        row["repetition"]
                        for row in previous
                        if row.get("phase") == "WARM"
                        and row.get("stage") == "VERIFIED"
                        and row.get("accepted") is True
                    }
                    if len(warm) >= repeats:
                        continue
                    missing = _missing_categories(case)
                    if not execute or entry is None or missing:
                        report["rows"].append(
                            {
                                **cell,
                                "stage": "NOT_RUN",
                                "executed": False,
                                "accepted": False,
                                "phase": "NOT_RUN",
                                "error_code": "EXECUTION_NOT_REQUESTED"
                                if not execute
                                else "BENCHMARK_MEDIA_CATEGORY_MISSING"
                                if missing
                                else "CANDIDATE_PROFILE_MISSING",
                                "error_message": "Supply required media categories: "
                                + ", ".join(missing)
                                if missing
                                else None,
                                "segment_id": segment_id,
                            }
                        )
                        _write_report(destination, report)
                        continue
                    if previous_family is None:
                        phase = (
                            "COLD_BASELINE"
                            if cold_runtime_confirmed and not resume
                            else "FIRST_OBSERVED_REQUEST"
                        )
                        first = await measured(entry, case, cell, phase)
                    elif previous_family != family:
                        first = await measured(entry, case, cell, "FAMILY_SWITCH_OBSERVED")
                        first["previous_family"] = previous_family
                        _write_report(destination, report)
                    else:
                        first = None
                    previous_family = family
                    if first is not None and first["stage"] != "VERIFIED":
                        if unresolved_submission(first):
                            halt = True
                            report["halt_reason"] = "Reconcile uncertain prompt before continuing"
                            break
                        continue
                    warmed = await measured(entry, case, cell, "WARMUP")
                    if warmed["stage"] != "VERIFIED":
                        if unresolved_submission(warmed):
                            halt = True
                            report["halt_reason"] = "Reconcile uncertain prompt before continuing"
                            break
                        continue
                    for repetition in range(1, repeats + 1):
                        if repetition in warm:
                            continue
                        row = await measured(entry, case, cell, "WARM", repetition)
                        if unresolved_submission(row):
                            halt = True
                            report["halt_reason"] = "Reconcile uncertain prompt before continuing"
                            break
                    if halt:
                        break
                if halt:
                    break
            if halt:
                break
        if halt:
            break
    rows = [row for row in report["rows"] if row.get("phase") == "WARM"]
    verified = [
        row for row in rows if row.get("stage") == "VERIFIED" and row.get("accepted") is True
    ]
    timings = [row["wall_seconds"] for row in verified]
    complete_cells = sum(
        len({row["repetition"] for row in verified if row["cell_id"] == cell_id}) >= repeats
        for cell_id in cell_ids
    )
    required_cells = len(cases) * len(profiles) * len(ratios) * len(durations)
    report["summary"] = {
        "warm_verified_count": len(verified),
        "warm_failure_count": len(rows) - len(verified),
        "warm_p50_wall_seconds": _percentile(timings, 0.50),
        "warm_p95_wall_seconds": _percentile(timings, 0.95),
        "percentile_method": "nearest-rank; warm verified rows only",
        "required_cells": required_cells,
        "complete_cells": complete_cells,
        "complete": not halt and complete_cells == required_cells,
    }
    report["segments"][-1]["finished_at"] = datetime.now(UTC).isoformat()
    _write_report(destination, report)
    return report


def _cases(media):
    def subset(*prefixes):
        return {
            name: path for name, path in media.items() if any(name.startswith(p) for p in prefixes)
        }

    return [
        BenchmarkCase("text", "t2v", {}),
        BenchmarkCase("first_frame", "i2v", subset("FIRST_FRAME")),
        BenchmarkCase("last_frame", "i2v_last", subset("LAST_FRAME")),
        BenchmarkCase("first_last", "i2v_first_last", subset("FIRST_FRAME", "LAST_FRAME")),
        BenchmarkCase("ref_image", "r2v", subset("REFERENCE_IMAGE_")),
        BenchmarkCase("ref_video", "r2v", subset("REFERENCE_VIDEO_")),
        BenchmarkCase("ref_audio_image", "r2v", subset("REFERENCE_IMAGE_", "REFERENCE_AUDIO_")),
        BenchmarkCase(
            "ref_mixed", "r2v", subset("REFERENCE_IMAGE_", "REFERENCE_VIDEO_", "REFERENCE_AUDIO_")
        ),
    ]


def _arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("t2v", "i2v", "i2v_last", "i2v_first_last", "r2v"))
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--repeat", type=int, default=3, choices=range(3, 11))
    parser.add_argument("--ratio", action="append", choices=("9:16", "16:9", "1:1"))
    parser.add_argument("--duration", action="append", type=float)
    parser.add_argument("--profile", action="append", choices=("DRAFT", "STANDARD", "HIGH"))
    parser.add_argument("--media", action="append", default=[], metavar="SLOT=PATH")
    parser.add_argument("--manifest-dir", type=Path)
    parser.add_argument("--runtime-provenance", type=Path)
    parser.add_argument("--cold-runtime-confirmed", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args()


async def run():
    from apps.api.app.core.config import get_settings

    args = _arguments()
    if not args.all and not args.mode:
        raise SystemExit("Select --all or --mode")
    if args.resume and args.output is None:
        raise SystemExit("--resume requires the exact existing --output path")
    settings = get_settings()
    media = {}
    for value in args.media:
        name, separator, path = value.partition("=")
        if not separator or not name or not path or name in media:
            raise SystemExit("Each --media needs a unique SLOT=PATH")
        media[name] = Path(path)
    cases = _cases(media)
    if not args.all:
        cases = [case for case in cases if case.mode == args.mode]
    entries = load_manifests(args.manifest_dir or Path(settings.workflow_dir))
    provenance = (
        json.loads(args.runtime_provenance.read_text("utf-8")) if args.runtime_provenance else None
    )
    destination = (
        args.output
        or Path(settings.workspace_root) / "h3-benchmark" / uuid4().hex / "benchmark.json"
    )
    async with httpx.AsyncClient(
        timeout=settings.comfy_timeout_seconds, follow_redirects=False
    ) as client:
        adapter = ComfyAdapter(settings, client=client)
        report = await run_matrix(
            adapter=adapter,
            entries=entries,
            cases=cases,
            ratios=args.ratio or ["9:16", "16:9", "1:1"],
            durations=args.duration or [5, 10, 15],
            profiles=args.profile or ["DRAFT", "STANDARD", "HIGH"],
            repeats=args.repeat,
            execute=args.execute,
            destination=destination,
            resume=args.resume,
            cold_runtime_confirmed=args.cold_runtime_confirmed,
            ffprobe_binary=settings.ffprobe_binary,
            runtime_provenance=provenance,
            timeout_seconds=settings.comfy_timeout_seconds,
        )
    print(destination.resolve())
    return 0 if report["summary"]["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))

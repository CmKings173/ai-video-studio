"""Run a repeatable H3/ComfyUI benchmark matrix on the target workstation."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import asdict
from pathlib import Path

from apps.api.app.integrations.comfy_adapter import ComfyAdapter
from apps.api.app.services.workflow_loader import load_manifests
from apps.api.scripts.h3_probe import H3Probe


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("t2v", "i2v", "i2v_first_last", "r2v"))
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--execute", action="store_true", help="Submit jobs to ComfyUI")
    parser.add_argument("--repeat", type=int, default=3, choices=range(1, 11))
    parser.add_argument("--resolution", action="append", default=[])
    parser.add_argument("--duration", action="append", type=float, default=[])
    parser.add_argument("--steps", action="append", type=int, default=[])
    parser.add_argument("--media", action="append", default=[], metavar="SLOT=PATH")
    return parser.parse_args()


def _parse_resolution(value: str) -> tuple[int, int]:
    width, separator, height = value.lower().partition("x")
    if not separator or not width.isdigit() or not height.isdigit():
        raise ValueError(f"Invalid resolution: {value}")
    return int(width), int(height)


async def run() -> int:
    from apps.api.app.core.config import get_settings

    args = _arguments()
    if not args.all and not args.mode:
        raise SystemExit("Select --all or --mode")
    if not args.execute:
        raise SystemExit("Benchmark execution requires --execute on the target ComfyUI host")
    settings = get_settings()
    resolutions = args.resolution or ["480x864"]
    durations = args.duration or [5.0]
    steps_values = args.steps or [8]
    entries = load_manifests(Path(settings.workflow_dir))
    selected = entries if args.all else [item for item in entries if item["mode"] == args.mode]
    media: dict[str, Path] = {}
    for item in args.media:
        name, separator, value = item.partition("=")
        if not separator or not name or not value:
            raise SystemExit(f"Invalid --media value: {item}")
        media[name] = Path(value)
    adapter = ComfyAdapter(settings)
    rows: list[dict] = []
    for entry in selected:
        for resolution in resolutions:
            width, height = _parse_resolution(resolution)
            for duration in durations:
                for steps in steps_values:
                    for repetition in range(1, args.repeat + 1):
                        started = time.monotonic()
                        result = await H3Probe(adapter).run(
                            entry,
                            execute=True,
                            media=media,
                            width=width,
                            height=height,
                            duration_seconds=duration,
                            steps=steps,
                            timeout_seconds=settings.comfy_timeout_seconds,
                        )
                        row = asdict(result)
                        row.update(
                            {
                                "resolution": resolution,
                                "duration_seconds_requested": duration,
                                "steps_requested": steps,
                                "repetition": repetition,
                                "wall_seconds": round(time.monotonic() - started, 3),
                            }
                        )
                        rows.append(row)
    root = await asyncio.to_thread(lambda: Path(__file__).resolve().parents[3])
    destination = root / "docs" / "h3-poc" / "benchmark.json"
    await asyncio.to_thread(
        destination.write_text,
        json.dumps(rows, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        "utf-8",
    )
    return 0 if rows and all(row["accepted"] and row["executed"] for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))

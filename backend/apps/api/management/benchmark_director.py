"""Benchmark a frozen Director candidate through the same durable probe contract.

Every run has a separate ledger and output prefix. An unresolved run ends the
benchmark; it must be reconciled before any additional execution is submitted.
No workflow approval or production qualification is written by this command.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import statistics
from pathlib import Path

from apps.api.app.providers.minimax_h3_director.contracts import DirectorExecutionSpec
from apps.api.scripts.director_probe import run, save_ledger


async def benchmark(snapshot, media, output, *, repetitions=3, execute=False):
    if type(repetitions) is not int or not 1 <= repetitions <= 50:
        raise ValueError("Benchmark repetitions must be between 1 and 50")
    # Exclusive directory creation protects prior evidence and unresolved work.
    await asyncio.to_thread(output.mkdir, parents=True, exist_ok=False)
    spec = DirectorExecutionSpec.model_validate(snapshot["director_execution"])
    summary = {
        "execution_hash": spec.execution_hash,
        "task": spec.task,
        "settings": {
            "canvas": spec.canvas.model_dump(mode="json"),
            "motion_context": spec.motion_context,
            "refine": spec.refine,
            "face_refine": spec.face_refine,
            "audio_policy": spec.audio_policy,
        },
        "release_gate": "NOT_APPROVED",
        "runs": [],
        "executed": False,
    }
    for index in range(repetitions):
        candidate = copy.deepcopy(snapshot)
        values = spec.model_dump(mode="json", exclude={"execution_hash"})
        values["output_prefix"] = f"{spec.output_prefix}/benchmark-{index:03d}"
        candidate["director_execution"] = DirectorExecutionSpec.finalize(**values).model_dump(
            mode="json"
        )
        try:
            result = await run(candidate, media, output / f"run-{index:03d}", execute=execute)
        except Exception:
            summary["stage"] = "RECONCILIATION_REQUIRED"
            save_ledger(output / "benchmark.json", summary)
            raise
        summary["runs"].append(
            {
                "stage": result["stage"],
                "client_id": result["client_id"],
                "elapsed_seconds": result.get("elapsed_seconds"),
                "prompt_id": result.get("prompt_id"),
            }
        )
        summary["executed"] = summary["executed"] or result.get("executed", False)
        save_ledger(output / "benchmark.json", summary)
        if result["stage"] not in {"MEASURED", "STATIC_VALIDATED"}:
            summary["stage"] = result["stage"]
            break
    else:
        summary["stage"] = "MEASURED" if execute else "STATIC_VALIDATED"
    durations = [
        item["elapsed_seconds"]
        for item in summary["runs"]
        if item["elapsed_seconds"] is not None and item["stage"] == "MEASURED"
    ]
    if durations:
        summary["latency_seconds"] = {
            "min": min(durations),
            "max": max(durations),
            "median": statistics.median(durations),
            "mean": statistics.mean(durations),
        }
    save_ledger(output / "benchmark.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--media", action="append", default=[], metavar="ROLE:INDEX=PATH")
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    media = {}
    for binding in args.media:
        role_index, path = binding.split("=", 1)
        role, index = role_index.split(":", 1)
        key = (role, int(index))
        if key in media:
            parser.error("Duplicate media ordinal")
        media[key] = Path(path)
    snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
    result = asyncio.run(
        benchmark(
            snapshot, media, args.output_dir, repetitions=args.repetitions, execute=args.execute
        )
    )
    print(json.dumps({"stage": result["stage"], "runs": len(result["runs"])}))


if __name__ == "__main__":
    main()

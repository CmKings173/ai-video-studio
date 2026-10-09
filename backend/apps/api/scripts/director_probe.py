"""Probe a frozen Director candidate without approving or scheduling production jobs.

Use --snapshot with a candidate snapshot containing base_workflow, slots and
director_execution. --media ROLE:INDEX=PATH supplies local frozen asset bytes.
--execute is explicit; --resume reconciles an existing ledger and never resubmits.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path
from uuid import uuid4

from apps.api.app.core.config import get_settings
from apps.api.app.integrations.comfy_adapter import ComfyAdapter
from apps.api.app.integrations.media import inspect_media
from apps.api.app.providers.minimax_h3_director.collector import (
    artifact_manifest,
    report_artifacts,
    validate_artifact,
)
from apps.api.app.providers.minimax_h3_director.contracts import DirectorExecutionSpec
from apps.api.app.providers.minimax_h3_director.workflow_builder import DirectorWorkflowBuilder
from apps.api.app.services.generation_intent import stable_hash
from apps.api.app.services.workflow_contracts import profile_hash
from apps.api.app.services.workflow_registry import ApprovedWorkflow
from workers.common import check_checksum
from workers.dispatcher import history_status


def save_ledger(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


async def run(
    snapshot: dict,
    media: dict[tuple[str, int], Path],
    output: Path,
    *,
    execute=False,
    resume=False,
    timeout_seconds=3600,
):
    spec = DirectorExecutionSpec.model_validate(snapshot["director_execution"])
    workflow = ApprovedWorkflow(
        mode=spec.task,
        version=spec.workflow_version,
        workflow=snapshot["base_workflow"],
        slots={key: tuple(value) for key, value in snapshot["slots"].items()},
        required_slots=frozenset(snapshot.get("required_slots", [])),
    )
    workflow.validate()
    profile = spec.provenance["workflow_profile"]
    if (
        workflow.workflow_hash != spec.workflow_hash
        or workflow.slot_map_hash != spec.slot_map_hash
        or profile_hash(profile) != spec.profile_hash
    ):
        raise ValueError("Candidate graph or profile differs from frozen execution")
    await asyncio.to_thread(output.mkdir, parents=True, exist_ok=True)
    ledger_path = output / "director-probe.json"
    if resume:
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        if ledger["execution_hash"] != spec.execution_hash:
            raise ValueError("Cannot resume a different execution")
        if not execute:
            raise ValueError("Resume requires --execute")
        if ledger["stage"] in {"MEASURED", "FAILED"}:
            return ledger
    else:
        # Exclusive creation prevents a second process from replacing correlation.
        with ledger_path.open("x", encoding="utf-8") as stream:
            ledger = {
                "execution_hash": spec.execution_hash,
                "client_id": str(uuid4()),
                "stage": "PREPARING",
                "executed": False,
                "release_gate": "NOT_APPROVED",
                "snapshot_hash": stable_hash(snapshot),
            }
            json.dump(ledger, stream)
            stream.flush()
            os.fsync(stream.fileno())
    settings = get_settings()
    adapter = ComfyAdapter(settings) if execute else None
    started = time.monotonic()
    try:
        if not resume:
            expected = {(asset.role, asset.order_index) for asset in spec.assets}
            if set(media) != expected:
                raise ValueError("Media must exactly cover frozen asset roles and ordinals")
            staged = {}
            for asset in spec.assets:
                path = media[(asset.role, asset.order_index)]
                if (
                    path.stat().st_size != asset.size_bytes
                    or asset.size_bytes > settings.max_upload_bytes
                ):
                    raise ValueError("Frozen media size differs or exceeds upload bound")
                data = await asyncio.to_thread(path.read_bytes)
                check_checksum(data, asset.checksum)
                await inspect_media(
                    data, asset.content_type, asset.filename, settings.ffprobe_binary
                )
                name = (
                    f"{spec.execution_hash[:24]}_{asset.role.lower()}_"
                    f"{asset.order_index}{path.suffix}"
                )
                staged[(asset.role, asset.order_index)] = (
                    await adapter.upload(data, name, asset.content_type) if execute else name
                )
            graph = DirectorWorkflowBuilder().build(
                base_workflow=workflow.workflow,
                spec=spec,
                staged_assets=staged,
                object_info=await adapter.object_info() if execute else None,
            )
            ledger.update(stage="STATIC_VALIDATED", graph_hash=stable_hash(graph))
            save_ledger(ledger_path, ledger)
            if not execute:
                return ledger
            ledger["stage"] = "SUBMISSION_PENDING"
            save_ledger(ledger_path, ledger)
            # From here, exceptions leave durable correlation and require resume.
            ledger["prompt_id"] = await adapter.submit(graph, ledger["client_id"])
            ledger.update(stage="SUBMITTED", executed=True)
            save_ledger(ledger_path, ledger)
        prompt_id = ledger.get("prompt_id") or await adapter.find_by_client_id(ledger["client_id"])
        if not prompt_id:
            ledger["stage"] = "RECONCILIATION_REQUIRED"
            save_ledger(ledger_path, ledger)
            return ledger
        ledger.update(prompt_id=prompt_id, executed=True)
        save_ledger(ledger_path, ledger)
        while time.monotonic() - started < timeout_seconds:
            history = await adapter.get_history(prompt_id)
            if history and history_status(history) == "FAILED":
                ledger.update(stage="FAILED", history_status=history.get("status"))
                break
            if history and history_status(history) == "COMPLETED":
                manifest = []
                for index, artifact in enumerate(
                    artifact_manifest(
                        history,
                        profile,
                        require_final=snapshot.get("kind") != "director_aggregate",
                    )
                ):
                    data = await adapter.download(artifact.locator)
                    if not data or len(data) > settings.max_upload_bytes:
                        raise ValueError("Probe output exceeds media bounds")
                    measured = await inspect_media(
                        data, "video/mp4", artifact.locator["filename"], settings.ffprobe_binary
                    )
                    validate_artifact(measured, artifact.expected)
                    destination = output / f"artifact-{index:03d}.mp4"
                    await asyncio.to_thread(destination.write_bytes, data)
                    manifest.append(
                        {
                            "role": artifact.role,
                            "member_index": artifact.member_index,
                            "locator": artifact.locator,
                            "measured": measured,
                            "checksum": check_checksum(data, None),
                            "size_bytes": len(data),
                        }
                    )
                ledger.update(
                    stage="MEASURED",
                    artifacts=manifest,
                    reports=report_artifacts(history, profile),
                    elapsed_seconds=time.monotonic() - started,
                )
                break
            await asyncio.sleep(1)
        else:
            ledger["stage"] = "RECONCILIATION_REQUIRED"
        save_ledger(ledger_path, ledger)
        return ledger
    finally:
        if adapter:
            await adapter.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--media", action="append", default=[], metavar="ROLE:INDEX=PATH")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--timeout", type=int, default=3600)
    arguments = parser.parse_args()
    if arguments.timeout < 1:
        parser.error("Timeout must be positive")
    media = {}
    for binding in arguments.media:
        role_index, path = binding.split("=", 1)
        role, ordinal = role_index.split(":", 1)
        key = (role, int(ordinal))
        if key in media:
            parser.error("Duplicate media ordinal")
        media[key] = Path(path)
    snapshot = json.loads(arguments.snapshot.read_text(encoding="utf-8"))
    result = asyncio.run(
        run(
            snapshot,
            media,
            arguments.output_dir,
            execute=arguments.execute,
            resume=arguments.resume,
            timeout_seconds=arguments.timeout,
        )
    )
    print(json.dumps({"stage": result["stage"], "execution_hash": result["execution_hash"]}))


if __name__ == "__main__":
    main()

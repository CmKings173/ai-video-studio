"""Measured repeats follow an excluded warmup, with no invented cold state."""

import asyncio
import json
from pathlib import Path

import pytest

from apps.api.management import benchmark_h3
from apps.api.scripts.h3_probe import ProbeResult


def candidate(mode="t2v"):
    return {
        "mode": mode,
        "code": f"fixture-{mode}",
        "version": "fixture-only",
        "workflow": {"1": {"class_type": "FixtureOnly", "inputs": {"x": 1}}},
        "slots": {},
        "profile": {"quality_profile": "STANDARD", "steps": 20},
    }


class Probe:
    calls = []
    fail = False

    def __init__(self, adapter, **kwargs):
        self.adapter = adapter

    async def run(self, entry, **kwargs):
        self.calls.append((self.adapter, entry, kwargs))
        return ProbeResult(
            entry["code"],
            entry["mode"],
            entry["version"],
            accepted=not self.fail,
            executed=True,
            stage="EXECUTED" if self.fail else "VERIFIED",
            elapsed_seconds=2.0,
            gpu_memory_mb=100,
            output_metadata={"width": 480, "height": 864, "fps": 24},
        )


@pytest.fixture(autouse=True)
def reset_probe():
    Probe.calls = []
    Probe.fail = False


async def run(tmp_path, **overrides):
    args = {
        "adapter": object(),
        "entries": [candidate()],
        "cases": [benchmark_h3.BenchmarkCase("text", "t2v", {})],
        "ratios": ["9:16"],
        "durations": [5],
        "profiles": ["STANDARD"],
        "repeats": 3,
        "execute": True,
        "destination": tmp_path / "benchmark.json",
        "probe_factory": Probe,
    }
    return await benchmark_h3.run_matrix(**{**args, **overrides})


async def test_warmup_is_excluded_and_three_repeats_share_adapter(tmp_path):
    adapter = object()
    report = await run(tmp_path, adapter=adapter)
    phases = [row["phase"] for row in report["rows"]]
    assert phases == ["FIRST_OBSERVED_REQUEST", "WARMUP", "WARM", "WARM", "WARM"]
    assert report["summary"]["warm_verified_count"] == 3
    assert report["summary"]["warm_p50_wall_seconds"] >= 0
    assert all(call[0] is adapter for call in Probe.calls)
    assert report["concurrency"] == 1
    assert report["cold_runtime_confirmed"] is False
    assert (tmp_path / "benchmark.json").is_file()


async def test_failed_warmup_never_yields_warm_measurements(tmp_path):
    Probe.fail = True
    report = await run(tmp_path)
    assert report["summary"]["warm_verified_count"] == 0
    assert all(row["phase"] != "WARM" for row in report["rows"])
    assert report["summary"]["complete"] is False


@pytest.mark.parametrize("repeat", [0, 1, 2, True])
async def test_fewer_than_three_warm_repeats_are_rejected(tmp_path, repeat):
    with pytest.raises(ValueError, match="three"):
        await run(tmp_path, repeats=repeat)
    assert Probe.calls == []


async def test_missing_profile_records_not_run_without_submitting(tmp_path):
    report = await run(tmp_path, profiles=["DRAFT"])
    assert len(report["rows"]) == 1
    assert report["rows"][0]["stage"] == "NOT_RUN"
    assert report["rows"][0]["error_code"] == "CANDIDATE_PROFILE_MISSING"
    assert Probe.calls == []
    assert report["summary"]["complete"] is False


async def test_family_switch_is_separate_from_warmed_repeats(tmp_path):
    report = await run(
        tmp_path,
        entries=[candidate(), candidate("r2v")],
        cases=[
            benchmark_h3.BenchmarkCase("text", "t2v", {}),
            benchmark_h3.BenchmarkCase(
                "ref_image", "r2v", {"REFERENCE_IMAGE_1": Path("fixture.png")}
            ),
        ],
    )
    switched = [row for row in report["rows"] if row["phase"] == "FAMILY_SWITCH_OBSERVED"]
    assert len(switched) == 1
    assert switched[0]["previous_family"] == "FL2VA"
    assert switched[0]["family"] == "REF2VA"
    assert report["summary"]["warm_verified_count"] == 6


async def test_nonexecute_matrix_is_not_run(tmp_path):
    report = await run(tmp_path, execute=False)
    assert all(row["stage"] == "NOT_RUN" for row in report["rows"])
    assert Probe.calls == []


async def test_resume_skips_complete_cells_but_rejects_changed_inputs(tmp_path):
    first = await run(tmp_path)
    count = len(Probe.calls)
    resumed = await run(tmp_path, resume=True)
    assert len(Probe.calls) == count
    assert resumed["rows"] == first["rows"]
    with pytest.raises(ValueError, match="different"):
        await run(tmp_path, resume=True, durations=[10])


async def test_uncertain_submission_halts_entire_matrix_and_resume(tmp_path):
    class UncertainProbe(Probe):
        async def run(self, entry, **kwargs):
            self.calls.append((self.adapter, entry, kwargs))
            return ProbeResult(
                entry["code"],
                entry["mode"],
                entry["version"],
                stage="SUBMITTED",
                error_code="COMFY_SUBMISSION_UNCERTAIN",
            )

    report = await run(tmp_path, probe_factory=UncertainProbe, durations=[5, 10])
    assert len(Probe.calls) == 1
    assert "Reconcile" in report["halt_reason"]
    assert report["summary"]["complete"] is False
    await run(tmp_path, probe_factory=UncertainProbe, durations=[5, 10], resume=True)
    assert len(Probe.calls) == 1


async def test_interrupted_probe_is_journalled_and_not_resubmitted_on_resume(tmp_path):
    class InterruptedProbe(Probe):
        async def run(self, entry, **kwargs):
            self.calls.append((self.adapter, entry, kwargs))
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await run(tmp_path, probe_factory=InterruptedProbe)
    report = json.loads((tmp_path / "benchmark.json").read_text("utf-8"))
    assert report["rows"][0]["stage"] == "SUBMISSION_PENDING"
    assert report["rows"][0]["client_id"]
    assert report["rows"][0]["executed"] is False
    resumed = await run(tmp_path, probe_factory=InterruptedProbe, resume=True)
    assert resumed["summary"]["complete"] is False
    assert len(Probe.calls) == 1


async def test_resume_scans_later_unresolved_cell_before_retrying_earlier_failure(tmp_path):
    class LaterUncertain(Probe):
        async def run(self, entry, **kwargs):
            self.calls.append((self.adapter, entry, kwargs))
            return ProbeResult(
                entry["code"],
                entry["mode"],
                entry["version"],
                stage="EXECUTED" if kwargs["duration_seconds"] == 5 else "SUBMITTED",
                executed=kwargs["duration_seconds"] == 5,
                error_code="COMFY_EXECUTION_FAILED"
                if kwargs["duration_seconds"] == 5
                else "COMFY_SUBMISSION_UNCERTAIN",
                details={"client_id": kwargs["client_id"]},
            )

    initial = await run(tmp_path, durations=[5, 10], probe_factory=LaterUncertain)
    assert len(Probe.calls) == 2
    Probe.calls.clear()
    resumed = await run(tmp_path, durations=[5, 10], resume=True)
    assert Probe.calls == []
    assert resumed["rows"] == initial["rows"]
    assert "Reconcile" in resumed["halt_reason"]
    assert resumed["summary"]["complete"] is False


async def test_actual_probe_submit_timeout_blocks_continuation_and_resume(tmp_path):
    from apps.api.scripts.h3_probe import H3Probe
    from tests.unit.test_h3_probe_evidence import Adapter, entry

    class SlowSubmit(Adapter):
        async def submit(self, graph, client_id):
            self.submitted.append((graph, client_id))
            await asyncio.sleep(10)

    adapter = SlowSubmit()
    report = await run(
        tmp_path,
        adapter=adapter,
        entries=[entry()],
        durations=[5, 10],
        timeout_seconds=0.05,
        probe_factory=H3Probe,
    )
    assert len(adapter.submitted) == 1
    assert len(report["rows"]) == 1
    assert report["rows"][0]["details"]["client_id"] == report["rows"][0]["client_id"]
    resumed = await run(
        tmp_path,
        adapter=adapter,
        entries=[entry()],
        durations=[5, 10],
        timeout_seconds=0.05,
        probe_factory=H3Probe,
        resume=True,
    )
    assert len(adapter.submitted) == 1
    assert resumed["summary"]["complete"] is False
    assert "Reconcile" in resumed["halt_reason"]


@pytest.mark.parametrize(
    "case_name,missing",
    [
        ("ref_audio_image", "audio"),
        ("ref_audio_image", "image"),
        ("ref_mixed", "image"),
        ("ref_mixed", "video"),
        ("ref_mixed", "audio"),
    ],
)
async def test_named_reference_case_cannot_substitute_missing_category(
    tmp_path, case_name, missing
):
    media = {
        f"REFERENCE_{kind.upper()}_1": Path(f"fixture.{kind}")
        for kind in ("image", "video", "audio")
        if kind != missing
    }
    case = next(case for case in benchmark_h3._cases(media) if case.name == case_name)
    report = await run(tmp_path, entries=[candidate("r2v")], cases=[case])
    assert Probe.calls == []
    assert len(report["rows"]) == 1
    row = report["rows"][0]
    assert row["stage"] == "NOT_RUN"
    assert row["error_code"] == "BENCHMARK_MEDIA_CATEGORY_MISSING"
    assert missing in row["error_message"]
    assert not report["summary"]["complete"]

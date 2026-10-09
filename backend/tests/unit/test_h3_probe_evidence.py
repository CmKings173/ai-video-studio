"""Protocol boundary tests: a requested execution is not an executed result."""

import asyncio
import copy
import hashlib
import json
from types import SimpleNamespace

import pytest

from apps.api.app.integrations.comfy_adapter import ComfyError, SubmissionUncertain
from apps.api.scripts import h3_probe


def entry():
    return {
        "code": "SYNTHETIC_TEST_ONLY",
        "mode": "t2v",
        "version": "fixture",
        "workflow": {
            "1": {
                "class_type": "FixtureOnly",
                "inputs": {
                    "prompt": "",
                    "seed": 1,
                    "width": 480,
                    "height": 864,
                    "frames": 124,
                    "steps": 20,
                    "prefix": "",
                },
            }
        },
        "slots": {
            name: ["1", field]
            for name, field in {
                "PROMPT": "prompt",
                "SEED": "seed",
                "WIDTH": "width",
                "HEIGHT": "height",
                "FRAMES": "frames",
                "STEPS": "steps",
                "OUTPUT_PREFIX": "prefix",
            }.items()
        },
        "profile": {
            "fps": 24,
            "output_node": "1",
            "quality_profile": "STANDARD",
            "steps": 20,
            "resolution": {
                "kind": "explicit_slots",
                "canvases": {
                    "9:16": [480, 864],
                    "16:9": [864, 480],
                },
            },
        },
    }


class Adapter:
    def __init__(self, *, submit_error=None, history_error=False, absent_node=False):
        self.submit_error = submit_error
        self.history_error = history_error
        self.absent_node = absent_node
        self.submitted = []
        self.samples = 0

    async def object_info(self):
        if self.absent_node:
            return {}
        return {
            "FixtureOnly": {
                "input": {
                    "required": {field: ["STRING"] for field in entry()["workflow"]["1"]["inputs"]}
                },
                "output": ["VIDEO"],
            }
        }

    async def system_stats(self):
        self.samples += 1
        used = min(self.samples, 3) * 1024**2
        return {"devices": [{"vram_total": 10 * 1024**2, "vram_free": 10 * 1024**2 - used}]}

    async def get_queue(self):
        return {"queue_running": [], "queue_pending": []}

    async def submit(self, graph, client_id):
        self.submitted.append(copy.deepcopy(graph))
        if self.submit_error:
            raise self.submit_error
        return "fixture-prompt"

    async def get_history(self, prompt_id):
        assert prompt_id == "fixture-prompt"
        await asyncio.sleep(0.04)
        return {
            "status": {
                "completed": True,
                "status_str": "error" if self.history_error else "success",
            },
            "outputs": {"1": {"videos": [{"filename": "x.mp4"}]}},
        }

    @staticmethod
    def outputs(history, output_node):
        return history["outputs"][output_node]["videos"]

    async def download(self, output):
        assert output["filename"] == "x.mp4"
        return b"fixture-downloaded-media"


async def measured(data, **contract):
    assert data == b"fixture-downloaded-media"
    return {
        "width": contract["expected_width"],
        "height": contract["expected_height"],
        "fps": contract["expected_fps"],
        "duration_seconds": 124 / 24,
        "frames": 124,
        "has_audio": True,
        "has_video": True,
        "decoded_video_frames": 124,
        "decoded_audio_frames": 20,
        "checksum": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
    }


async def test_execute_without_adapter_never_claims_execution():
    result = await h3_probe.H3Probe().run(entry(), execute=True)
    assert result.executed is False
    assert result.accepted is False
    assert result.stage == "STATIC_VALIDATED"
    assert result.output_metadata is None


@pytest.mark.parametrize(
    "error",
    [
        ComfyError("rejected", "COMFY_REJECTED"),
        SubmissionUncertain("lost response", "COMFY_SUBMISSION_UNCERTAIN"),
    ],
)
async def test_submit_failure_keeps_correlation_without_claiming_execution(error):
    adapter = Adapter(submit_error=error)
    result = await h3_probe.H3Probe(adapter).run(entry(), execute=True)
    assert result.executed is False
    assert result.accepted is False
    assert result.prompt_id is None
    assert result.details["client_id"]
    assert result.error_code == error.code
    assert len(adapter.submitted) == 1


async def test_runtime_missing_node_stops_before_submission():
    adapter = Adapter(absent_node=True)
    result = await h3_probe.H3Probe(adapter).run(entry(), execute=True)
    assert result.executed is False
    assert result.error_code == "RUNTIME_NODE_MISSING"
    assert adapter.submitted == []


async def test_runtime_execution_error_is_execution_but_never_verified():
    result = await h3_probe.H3Probe(Adapter(history_error=True)).run(entry(), execute=True)
    assert result.executed is True
    assert result.stage == "EXECUTED"
    assert result.accepted is False
    assert result.prompt_id == "fixture-prompt"
    assert result.output_metadata is None


async def test_downloaded_measurement_and_sampled_peak_are_separate_from_provenance(monkeypatch):
    monkeypatch.setattr(h3_probe, "measure_probe_output", measured)
    adapter = Adapter()
    source = entry()
    original = copy.deepcopy(source)
    result = await h3_probe.H3Probe(adapter).run(
        source,
        execute=True,
        aspect_ratio="16:9",
        poll_interval_seconds=0.01,
        vram_sample_interval_seconds=0.01,
    )
    assert result.stage == "VERIFIED"
    assert result.executed and result.accepted
    assert result.qualified is False  # No genuine runtime/model qualification in this fixture.
    assert (result.width, result.height) == (864, 480)
    assert (
        result.output_metadata["checksum"]
        == hashlib.sha256(b"fixture-downloaded-media").hexdigest()
    )
    assert result.details["vram_samples"] >= 3
    assert result.gpu_memory_mb == 3
    assert adapter.submitted[0]["1"]["inputs"]["width"] == 864
    assert adapter.submitted[0]["1"]["inputs"]["height"] == 480
    assert source == original


async def test_container_only_measurements_do_not_establish_verified_media(monkeypatch):
    async def declarations(data, **contract):
        value = await measured(data, **contract)
        del value["decoded_video_frames"]
        return value

    monkeypatch.setattr(h3_probe, "measure_probe_output", declarations)
    result = await h3_probe.H3Probe(Adapter()).run(entry(), execute=True)
    assert result.executed and not result.accepted
    assert result.stage == "EXECUTED"
    assert result.error_code == "OUTPUT_DECODE_EVIDENCE_REQUIRED"


async def test_static_preflight_has_resolved_canvas_but_no_measured_output():
    result = await h3_probe.H3Probe().run(entry(), aspect_ratio="16:9")
    assert result.accepted and not result.executed
    assert result.stage == "STATIC_VALIDATED"
    assert result.width is result.height is result.output_metadata is None
    assert (result.resolved_width, result.resolved_height) == (864, 480)


async def test_static_preflight_rejects_required_unpatched_setting():
    value = entry()
    value["workflow"]["1"]["inputs"]["unpatched"] = "default"
    value["slots"]["UNBOUND"] = ["1", "unpatched"]
    value["required_slots"] = ["UNBOUND"]
    result = await h3_probe.H3Probe().run(value)
    assert not result.accepted
    assert result.error_code


async def test_probe_does_not_add_another_job_to_busy_runtime():
    class BusyAdapter(Adapter):
        async def get_queue(self):
            return {"queue_running": [[1, "other-editor"]], "queue_pending": []}

    adapter = BusyAdapter()
    result = await h3_probe.H3Probe(adapter).run(entry(), execute=True)
    assert not result.executed
    assert result.error_code == "COMFY_QUEUE_BUSY"
    assert adapter.submitted == []


async def test_candidate_step_override_changes_settings_hash():
    original = await h3_probe.H3Probe().run(entry(), steps=20)
    changed = await h3_probe.H3Probe().run(entry(), steps=25)
    assert original.workflow_hash == changed.workflow_hash
    assert original.details["profile_hash"] != changed.details["profile_hash"]
    assert changed.details["candidate_steps"] == 25


async def test_submit_deadline_preserves_unresolved_correlation():
    class SlowSubmit(Adapter):
        async def submit(self, graph, client_id):
            self.submitted.append((graph, client_id))
            await asyncio.sleep(10)

    adapter = SlowSubmit()
    result = await h3_probe.H3Probe(adapter).run(
        entry(), execute=True, timeout_seconds=0.05, client_id="deadline-correlation"
    )
    assert len(adapter.submitted) == 1
    assert result.stage == "SUBMISSION_PENDING"
    assert result.error_code == "COMFY_SUBMISSION_UNCERTAIN"
    assert result.details["cause_error_code"] == "PROBE_TIMEOUT"
    assert result.details["client_id"] == "deadline-correlation"
    assert result.prompt_id is None
    assert not result.executed and not result.accepted


async def test_pre_submit_deadline_does_not_claim_uncertain_admission():
    class SlowInventory(Adapter):
        async def object_info(self):
            await asyncio.sleep(10)

    adapter = SlowInventory()
    result = await h3_probe.H3Probe(adapter).run(entry(), execute=True, timeout_seconds=0.05)
    assert result.stage == "STATIC_VALIDATED"
    assert result.error_code == "PROBE_TIMEOUT"
    assert adapter.submitted == []


@pytest.mark.parametrize("steps", [None, 25])
async def test_unbound_steps_are_rejected_before_submission(steps):
    source = entry()
    del source["slots"]["STEPS"]
    adapter = Adapter()
    result = await h3_probe.H3Probe(adapter).run(source, execute=True, steps=steps)
    assert not result.accepted
    assert result.error_code == "PROBE_STEPS_BINDING_REQUIRED"
    assert adapter.submitted == []


async def test_step_binding_must_reach_collected_output():
    source = entry()
    source["workflow"]["2"] = {"class_type": "FixtureOnly", "inputs": {"steps": 20}}
    source["slots"]["STEPS"] = ["2", "steps"]
    result = await h3_probe.H3Probe().run(source, steps=25)
    assert not result.accepted
    assert result.error_code == "PROBE_STEPS_BINDING_REQUIRED"


@pytest.mark.parametrize("declared", [20, 25])
async def test_explicit_fixed_steps_must_match_graph(declared):
    source = entry()
    del source["slots"]["STEPS"]
    source["profile"]["setting_bindings"] = {"steps": ["1", "steps"]}
    source["profile"]["steps"] = declared
    result = await h3_probe.H3Probe().run(source)
    assert result.accepted is (declared == 20)
    if declared == 20:
        assert result.details["candidate_steps"] == 20
        assert result.details["candidate_steps_binding"] == ["1", "steps"]
    else:
        assert result.error_code == "PROBE_FIXED_STEPS_MISMATCH"


@pytest.mark.parametrize("stream", ["VIDEO", "AUDIO"])
@pytest.mark.parametrize("span", [None, 0.5, 2.5])
async def test_reference_limits_use_stream_duration_not_container(stream, span):
    slot = f"REFERENCE_{stream}_1"
    metadata = {slot: {"duration_seconds": 5, f"{stream.lower()}_duration_seconds": span}}
    request = h3_probe.H3Probe._request(
        "r2v", 480, 864, 5, {slot: "uploaded", "REFERENCE_IMAGE_1": "visual"}, metadata
    )
    durations = getattr(request, f"reference_{stream.lower()}_durations")
    assert durations == [span]
    if span is None or span < 2:
        with pytest.raises(ValueError):
            h3_probe.H3Validator(h3_probe.H3Profile()).validate(request)
    else:
        h3_probe.H3Validator(h3_probe.H3Profile()).validate(request)


@pytest.mark.parametrize("outcome", ["uncertain", "history", "cancel"])
async def test_cli_stops_and_persists_partial_correlation(monkeypatch, tmp_path, outcome):
    from apps.api.app.core import config

    args = SimpleNamespace(
        all=True,
        mode=None,
        execute=True,
        media=[],
        runtime_provenance=None,
        aspect_ratio="9:16",
        duration=5,
        steps=None,
        output_dir=tmp_path,
    )
    settings = SimpleNamespace(
        workflow_dir="fixture",
        workspace_root=tmp_path,
        ffprobe_binary="ffprobe",
        comfy_timeout_seconds=1,
    )
    calls = []

    class CLIProbe:
        def __init__(self, *args, **kwargs):
            pass

        async def run(self, source, **kwargs):
            calls.append(source["code"])
            persisted = json.loads((tmp_path / "results.json").read_text("utf-8"))
            assert persisted[-1]["details"]["client_id"] == kwargs["client_id"]
            if outcome == "cancel":
                raise asyncio.CancelledError()
            return h3_probe.ProbeResult(
                source["code"],
                source["mode"],
                source["version"],
                stage="STATIC_VALIDATED" if outcome == "uncertain" else "SUBMITTED",
                error_code="COMFY_SUBMISSION_UNCERTAIN"
                if outcome == "uncertain"
                else "COMFY_ERROR",
                prompt_id=None if outcome == "uncertain" else "accepted-id",
                details={"client_id": kwargs["client_id"]},
            )

    second = {**entry(), "code": "SECOND_FIXTURE"}
    monkeypatch.setattr(h3_probe, "_arguments", lambda: args)
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    monkeypatch.setattr(h3_probe, "load_manifests", lambda path: [entry(), second])
    monkeypatch.setattr(h3_probe, "ComfyAdapter", lambda settings: object())
    monkeypatch.setattr(h3_probe, "H3Probe", CLIProbe)
    if outcome == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await h3_probe._main()
    else:
        assert await h3_probe._main() == 1
    assert calls == ["SYNTHETIC_TEST_ONLY"]
    saved = json.loads((tmp_path / "results.json").read_text("utf-8"))
    assert len(saved) == 1
    assert saved[0]["details"]["client_id"]
    assert not saved[0]["executed"]


@pytest.mark.parametrize(
    "stage,error,unresolved",
    [
        ("SUBMISSION_PENDING", "PROBE_TIMEOUT", True),
        ("SUBMITTED", "COMFY_ERROR", True),
        ("STATIC_VALIDATED", "COMFY_SUBMISSION_UNCERTAIN", True),
        ("STATIC_VALIDATED", "COMFY_REJECTED", False),
        ("STATIC_VALIDATED", "PROBE_TIMEOUT", False),
        ("EXECUTED", "PROBEOUTPUTERROR", False),
        ("VERIFIED", None, False),
    ],
)
def test_one_unresolved_predicate_handles_persisted_and_live_results(stage, error, unresolved):
    result = h3_probe.ProbeResult("fixture", "t2v", "fixture", stage=stage, error_code=error)
    assert h3_probe.unresolved_submission(result) is unresolved
    assert h3_probe.unresolved_submission({"stage": stage, "error_code": error}) is unresolved

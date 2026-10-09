"""Probe output evidence must describe downloaded bytes, not request assumptions."""

import asyncio
import hashlib
import sys

import pytest

from apps.api.scripts import probe_media


def metadata(**overrides):
    return {
        "width": 480,
        "height": 864,
        "fps": 24.0,
        "frames": 124,
        "duration_seconds": 124 / 24,
        "has_video": True,
        "has_audio": True,
        **overrides,
    }


def mock_inspection(monkeypatch, value):
    async def inspect(path, binary):
        assert path.read_bytes() == b"downloaded-output"
        assert binary == "specific-ffprobe"
        return value

    monkeypatch.setattr(probe_media, "inspect_path", inspect)

    async def decode(path, binary, timeout_seconds):
        assert path.read_bytes() == b"downloaded-output"
        return {
            "decoded_video_frames": 124,
            "decoded_audio_frames": 240,
            "decoded_video_duration_seconds": 124 / 24,
            "decoded_audio_duration_seconds": 124 / 24,
            "decoded_video_start_seconds": 0,
            "decoded_audio_start_seconds": 0,
        }

    monkeypatch.setattr(probe_media, "_decode_output", decode)


async def measure():
    return await probe_media.measure_probe_output(
        b"downloaded-output",
        ffprobe_binary="specific-ffprobe",
        expected_width=480,
        expected_height=864,
        expected_fps=24,
        expected_frames=124,
    )


async def test_measured_output_hashes_exact_download_and_keeps_actual_duration(monkeypatch):
    mock_inspection(monkeypatch, metadata(duration_seconds=5.187))
    value = await measure()
    assert value["duration_seconds"] == 5.187
    assert value["checksum"] == hashlib.sha256(b"downloaded-output").hexdigest()
    assert value["size_bytes"] == len(b"downloaded-output")
    assert value["frames"] == 124


@pytest.mark.parametrize(
    "overrides",
    [
        {"width": 864},
        {"height": 480},
        {"fps": 30.0},
        {"has_audio": False},
        {"has_video": False},
        {"has_audio": 1},
        {"width": True},
        {"fps": True},
        {"duration_seconds": True},
        {"duration_seconds": float("nan")},
        {"duration_seconds": float("inf")},
        {"duration_seconds": 0},
        {"frames": 123},
        {"frames": True},
        {"duration_seconds": 10},
    ],
)
async def test_invalid_or_mismatched_output_cannot_be_verified(monkeypatch, overrides):
    mock_inspection(monkeypatch, metadata(**overrides))
    with pytest.raises(probe_media.ProbeOutputError):
        await measure()


async def test_decode_verifies_frames_when_container_omits_declared_count(monkeypatch):
    mock_inspection(monkeypatch, metadata(frames=None))
    result = await measure()
    assert result["frames"] is None  # Never fabricate a measured frame count.
    assert result["decoded_video_frames"] == 124


async def test_inspection_tool_failure_stays_an_error(monkeypatch):
    async def inspect(path, binary):
        raise RuntimeError("tool unavailable")

    monkeypatch.setattr(probe_media, "inspect_path", inspect)
    with pytest.raises(RuntimeError, match="tool unavailable"):
        await measure()


async def test_empty_download_is_rejected_before_inspection():
    with pytest.raises(probe_media.ProbeOutputError):
        await probe_media.measure_probe_output(
            b"",
            expected_width=480,
            expected_height=864,
            expected_fps=24,
            expected_frames=124,
        )


@pytest.mark.parametrize("empty_stream", ["video", "audio"])
def test_decoder_must_produce_both_streams(empty_stream):
    lines = ["#tb 0: 1/24", "#tb 1: 1/48000", "#media_type 0: video", "#media_type 1: audio"]
    if empty_stream != "video":
        lines.append("0, 0, 0, 1, 196608, hash")
    if empty_stream != "audio":
        lines.append("1, 0, 0, 1024, 2048, hash")
    with pytest.raises(probe_media.ProbeOutputError, match=f"No decoded {empty_stream}"):
        probe_media._framehash_evidence("\n".join(lines).encode())


@pytest.mark.parametrize("output", [b"", b"#tb 0: 0/1", b"not framehash", b"\xff"])
def test_broken_decoder_output_is_a_tool_error(output):
    with pytest.raises(probe_media.MediaInspectionError):
        probe_media._framehash_evidence(output)


@pytest.mark.parametrize("mode", ["timeout", "cancel", "output_bound", "tool_failure"])
async def test_decoder_bounds_and_cancellation_reap_actual_process(monkeypatch, tmp_path, mode):
    subprocess_factory = asyncio.create_subprocess_exec
    processes = []
    process_started = asyncio.Event()
    programs = {
        "timeout": "import time; time.sleep(10)",
        "cancel": "import time; time.sleep(10)",
        "output_bound": "import sys; sys.stdout.buffer.write(b'x' * (3 * 1024 * 1024))",
        "tool_failure": "import sys; sys.stderr.write('Unrecognized option'); sys.exit(2)",
    }

    async def process(*args, **kwargs):
        actual = await subprocess_factory(sys.executable, "-u", "-c", programs[mode], **kwargs)
        processes.append(actual)
        process_started.set()
        return actual

    monkeypatch.setattr(asyncio, "create_subprocess_exec", process)
    task = asyncio.create_task(
        probe_media._decode_output(tmp_path / "fixture", "decoder", 0.1 if mode == "timeout" else 5)
    )
    if mode == "cancel":
        await process_started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(probe_media.MediaInspectionError):
            await task
    assert len(processes) == 1
    assert processes[0].returncode is not None


@pytest.mark.parametrize(
    "overrides",
    [
        {"decoded_video_frames": 123},
        {"decoded_video_duration_seconds": 5},
        {"decoded_audio_duration_seconds": 6},
        {"decoded_audio_start_seconds": 1},
    ],
)
async def test_decoded_contract_is_independent_of_matching_declarations(monkeypatch, overrides):
    mock_inspection(monkeypatch, metadata())
    decode = probe_media._decode_output

    async def changed(*args):
        return {**await decode(*args), **overrides}

    monkeypatch.setattr(probe_media, "_decode_output", changed)
    with pytest.raises(probe_media.ProbeOutputError):
        await measure()

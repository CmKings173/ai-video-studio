"""CPU-generated media verifies the actual PoC inspection boundary."""

import hashlib
import json
import struct

import httpx
import pytest

from apps.api.app.integrations.comfy_adapter import ComfyAdapter
from apps.api.app.integrations.media import inspect_path
from apps.api.app.services.workflow_contracts import profile_hash
from apps.api.app.services.workflow_registry import _stable_hash
from apps.api.scripts import probe_media
from apps.api.scripts.h3_probe import H3Probe
from apps.api.scripts.probe_media import ProbeOutputError, measure_probe_output
from tests.integration.test_delivery_geometry import find_binaries, run
from tests.unit.test_h3_probe_evidence import entry


@pytest.fixture
def binaries():
    return find_binaries()


async def test_probe_media_measures_real_av_bytes_and_rejects_wrong_canvas(binaries, tmp_path):
    ffmpeg, ffprobe = binaries
    path = tmp_path / "synthetic-av.mp4"
    run(
        ffmpeg,
        "-v",
        "error",
        "-nostdin",
        "-f",
        "lavfi",
        "-i",
        "color=red:size=256x256:rate=24:duration=1",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:sample_rate=48000:duration=1",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-shortest",
        "-y",
        str(path),
    )
    data = path.read_bytes()
    value = await measure_probe_output(
        data,
        expected_width=256,
        expected_height=256,
        expected_fps=24,
        expected_frames=24,
        ffprobe_binary=ffprobe,
    )
    assert value["has_audio"] is True
    assert value["has_video"] is True
    assert value["checksum"] == hashlib.sha256(data).hexdigest()
    assert value["size_bytes"] == path.stat().st_size
    assert value["fps"] == 24
    assert value["frames"] == 24
    assert value["decoded_video_frames"] == 24
    assert value["decoded_audio_frames"] > 0
    assert value["decoded_video_duration_seconds"] == pytest.approx(1)
    assert value["decoded_audio_duration_seconds"] == pytest.approx(1, abs=1 / 24)
    with pytest.raises(ProbeOutputError, match="width"):
        await measure_probe_output(
            data,
            expected_width=480,
            expected_height=864,
            expected_fps=24,
            expected_frames=24,
            ffprobe_binary=ffprobe,
        )


def make_av(binaries, path, *, video_seconds=1, audio_seconds=1, frames=None, video_filter=None):
    ffmpeg, _ = binaries
    arguments = [
        ffmpeg,
        "-v",
        "error",
        "-nostdin",
        "-f",
        "lavfi",
        "-i",
        f"color=red:size=256x256:rate=24:duration={video_seconds}",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:sample_rate=48000:duration={audio_seconds}",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "pcm_s16le" if path.suffix == ".mkv" else "aac",
    ]
    if frames is not None:
        arguments += ["-frames:v", str(frames)]
    if video_filter:
        arguments += ["-vf", video_filter, "-fps_mode", "passthrough"]
    if path.suffix == ".mp4":
        arguments += ["-movflags", "+faststart"]
    run(*arguments, "-y", str(path))
    return path.read_bytes()


def zero_mdat(data):
    damaged = bytearray(data)
    offset = 0
    while offset + 8 <= len(data):
        size, kind = struct.unpack_from(">I4s", data, offset)
        header = 8
        if size == 1:
            size = struct.unpack_from(">Q", data, offset + 8)[0]
            header = 16
        if size == 0:
            size = len(data) - offset
        assert size >= header
        if kind == b"mdat":
            damaged[offset + header : offset + size] = bytes(size - header)
            return bytes(damaged)
        offset += size
    raise AssertionError("fixture has no mdat")


async def measure(data, ffprobe, expected_frames=24):
    return await measure_probe_output(
        data,
        expected_width=256,
        expected_height=256,
        expected_fps=24,
        expected_frames=expected_frames,
        ffprobe_binary=ffprobe,
    )


async def test_zeroed_mdat_is_rejected_despite_valid_declarations(binaries, tmp_path):
    data = make_av(binaries, tmp_path / "readable.mp4")
    damaged = zero_mdat(data)
    path = tmp_path / "damaged.mp4"
    path.write_bytes(damaged)
    declarations = await inspect_path(path, binaries[1])
    assert declarations["frames"] == 24
    assert declarations["has_video"] and declarations["has_audio"]
    with pytest.raises(ProbeOutputError, match="decode"):
        await measure(damaged, binaries[1])


async def test_short_video_long_audio_cannot_hide_missing_decoded_frames(binaries, tmp_path):
    path = tmp_path / "short-video.mkv"
    data = make_av(binaries, path, video_seconds=0.5)
    declarations = await inspect_path(path, binaries[1])
    assert declarations["frames"] is None
    assert declarations["duration_seconds"] == pytest.approx(1)
    with pytest.raises(ProbeOutputError, match="decoded video"):
        await measure(data, binaries[1])


async def test_full_mkv_keeps_missing_declared_count_and_distinct_decoded_count(binaries, tmp_path):
    data = make_av(binaries, tmp_path / "complete.mkv")
    value = await measure(data, binaries[1])
    assert value["frames"] is None
    assert value["decoded_video_frames"] == 24
    assert value["decoded_video_duration_seconds"] == pytest.approx(1)
    assert value["decoded_audio_frames"] > 0


async def test_video_timestamp_gap_is_not_repaired_into_verified_output(binaries, tmp_path):
    data = make_av(
        binaries,
        tmp_path / "gap.mkv",
        audio_seconds=2,
        video_filter="setpts=PTS+if(gte(N\\,12)\\,24\\,0)",
    )
    with pytest.raises(ProbeOutputError):
        await measure(data, binaries[1])


async def test_audio_padding_is_checked_independently(binaries, tmp_path):
    data = make_av(binaries, tmp_path / "long-audio.mkv", audio_seconds=1.5)
    with pytest.raises(ProbeOutputError, match="duration"):
        await measure(data, binaries[1])


async def test_corrupt_audio_is_rejected_even_when_video_decodes(binaries, tmp_path):
    path = tmp_path / "audio-corrupt.mp4"
    data = bytearray(make_av(binaries, path))
    packets = json.loads(
        run(binaries[1], "-v", "error", "-show_packets", "-of", "json", str(path))
    )["packets"]
    audio_packets = [packet for packet in packets if packet["codec_type"] == "audio"]
    assert audio_packets
    for packet in audio_packets:
        start, size = int(packet["pos"]), int(packet["size"])
        data[start : start + size] = bytes(size)
    path.write_bytes(data)
    # The undamaged selected video stream really decodes; AV still must fail.
    run(
        binaries[0],
        "-v",
        "error",
        "-nostdin",
        "-xerror",
        "-i",
        str(path),
        "-map",
        "0:v:0",
        "-f",
        "null",
        "-",
    )
    with pytest.raises(ProbeOutputError, match="decode"):
        await measure(bytes(data), binaries[1])


async def test_real_decoded_span_cannot_be_replaced_by_container_duration(
    binaries, tmp_path, monkeypatch
):
    path = tmp_path / "timestamp-gap.mkv"
    data = make_av(
        binaries, path, audio_seconds=2, video_filter="setpts=PTS+if(gte(N\\,12)\\,24\\,0)"
    )
    real_inspect = probe_media.inspect_path

    async def forged_container(path, binary):
        return {**await real_inspect(path, binary), "duration_seconds": 1}

    monkeypatch.setattr(probe_media, "inspect_path", forged_container)
    with pytest.raises(ProbeOutputError, match="decoded video duration"):
        await measure(data, binaries[1])


async def test_missing_ffmpeg_remains_tool_error_for_valid_av(binaries, tmp_path):
    data = make_av(binaries, tmp_path / "valid.mp4")
    with pytest.raises(probe_media.MediaInspectionError, match="unavailable"):
        await measure_probe_output(
            data,
            expected_width=256,
            expected_height=256,
            expected_fps=24,
            expected_frames=24,
            ffprobe_binary=binaries[1],
            ffmpeg_binary=str(tmp_path / "missing-ffmpeg.exe"),
        )


@pytest.mark.parametrize("corrupt", [False, True])
async def test_actual_adapter_download_decode_and_graph_correlation(binaries, tmp_path, corrupt):
    data = make_av(
        binaries,
        tmp_path / "graph-output.mp4",
        video_seconds=124 / 24,
        audio_seconds=124 / 24,
        frames=124,
    )
    if corrupt:
        data = zero_mdat(data)
    source = entry()
    source["profile"]["resolution"]["canvases"]["1:1"] = [256, 256]
    submitted = []

    def boundary(request):
        if request.url.path == "/object_info":
            return httpx.Response(
                200,
                json={
                    "FixtureOnly": {
                        "input": {
                            "required": {
                                field: ["STRING"] for field in source["workflow"]["1"]["inputs"]
                            }
                        }
                    }
                },
            )
        if request.url.path == "/queue":
            return httpx.Response(200, json={"queue_running": [], "queue_pending": []})
        if request.url.path == "/system_stats":
            return httpx.Response(200, json={"devices": []})
        if request.url.path == "/prompt":
            submitted.append(json.loads(request.content))
            return httpx.Response(200, json={"prompt_id": "cpu-fixture-prompt"})
        if request.url.path == "/history/cpu-fixture-prompt":
            return httpx.Response(
                200,
                json={
                    "cpu-fixture-prompt": {
                        "status": {"completed": True, "status_str": "success"},
                        "outputs": {"1": {"videos": [{"filename": "graph-output.mp4"}]}},
                    }
                },
            )
        if request.url.path == "/view":
            return httpx.Response(200, content=data)
        raise AssertionError(str(request.url))

    async with httpx.AsyncClient(transport=httpx.MockTransport(boundary)) as client:
        result = await H3Probe(
            ComfyAdapter("http://fixture", client=client), ffprobe_binary=binaries[1]
        ).run(source, execute=True, aspect_ratio="1:1", client_id="cpu-graph-correlation")
    assert len(submitted) == 1
    assert submitted[0]["client_id"] == result.details["client_id"] == "cpu-graph-correlation"
    assert submitted[0]["extra_data"]["studio_generation_id"] == "cpu-graph-correlation"
    assert result.prompt_id == "cpu-fixture-prompt"
    assert result.details["submitted_graph_hash"] == _stable_hash(submitted[0]["prompt"])
    assert result.workflow_hash == _stable_hash(source["workflow"])
    assert result.details["profile_hash"] == profile_hash(source["profile"])
    assert result.executed and not result.qualified
    if corrupt:
        assert result.stage == "EXECUTED" and not result.accepted
        assert result.output_metadata is None
        assert result.width is result.height is None
    else:
        assert result.stage == "VERIFIED" and result.accepted
        assert (result.width, result.height) == (256, 256)
        assert result.output_metadata["decoded_video_frames"] == 124
        assert result.output_metadata["decoded_audio_frames"] > 0
        assert result.output_metadata["checksum"] == hashlib.sha256(data).hexdigest()
        assert result.gpu_memory_mb is None

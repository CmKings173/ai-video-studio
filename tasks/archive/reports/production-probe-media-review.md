# Independent probe media review

Reviewed 2026-10-05 against `tasks/archive/reports/production-runtime-brief.md`. Scope: `backend/apps/api/scripts/probe_media.py`, `backend/tests/unit/test_probe_media.py`, and `backend/tests/integration/test_probe_media.py`. Read `integrations/media.py` only to trace the helper's actual inspection boundary. H3Probe/benchmark integration, existing qualification issues, and final runtime qualification are excluded. No implementation edits, subagents, commits, or GPU execution.

## Actionable findings

1. **[P1] Reject undecodable payloads before returning accepted output evidence** — `backend/apps/api/scripts/probe_media.py:54`. `inspect_path` reads stream/container declarations without decoding media; the subsequent checks accept those declarations as measured evidence. **Actual-byte proof:** created a 256×256/24 FPS, one-second H.264/AAC MP4 with `+faststart`, then zeroed all 10,114 bytes of its `mdat` payload while preserving atom headers and `moov`. `measure_probe_output` still returned success with `frames=24`, `duration_seconds=1.0`, both streams present, and checksum `4ee8b782a94db50ab521fe8eaf75a0c6c1ba1743d7e72a4e8a85ef9a6c1ecaed` (12,241 bytes). Actual `ffprobe -count_frames` produced no `nb_read_frames` for either stream; `ffmpeg -xerror` decoding failed with “Invalid data found when processing input” and nothing written. The hash is truthful, but the accepted AV/frame evidence describes unreadable payloads. Add bounded decode validation for both selected streams and reject decode errors/absent decoded output; preserve the distinction between invalid media and unavailable/failed tooling. Add a real corrupted-payload regression test.

2. **[P2] Bound video duration rather than allowing audio to mask missing video** — `backend/apps/api/scripts/probe_media.py:62–72`. `duration_seconds` comes from format/container duration, and missing frame counts bypass the count comparison. **Actual-byte proof:** a Matroska file containing 12 H.264 frames at 24 FPS (0.5 seconds) plus one second of PCM audio was accepted against `expected_frames=24`, `expected_fps=24`: returned `frames=None`, `duration_seconds=1.0`, and both streams present. Independent `ffprobe -count_frames` measured 12 video frames; the video stream DURATION tag was `00:00:00.500000000`, audio/container duration `1.000000`. Audio extended the container by 12 video frames, exceeding the permitted one-frame padding, yet the expected video run passed. No container restriction in the helper prevents this case. Validate the video stream's actual temporal span when frame count is unavailable, and check container/audio padding separately. Keep the unavailable metadata frame count missing. Add a short-video/long-audio regression test.

## Actual independent verification

From `D:/project/ai-video-studio/backend`:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_probe_media.py tests/integration/test_probe_media.py -q -p no:cacheprovider
ruff check --no-cache apps/api/scripts/probe_media.py tests/unit/test_probe_media.py tests/integration/test_probe_media.py
ruff format --check --no-cache apps/api/scripts/probe_media.py tests/unit/test_probe_media.py tests/integration/test_probe_media.py
```

Results: **20 passed in 0.44s** (19 unit, 1 actual AV CPU test; no skips); Ruff **All checks passed**, **3 files already formatted**, exit 0. These tests do not exercise either defect above.

Both reproductions ran via Python stdin using `TemporaryDirectory`, actual FFmpeg subprocesses, and the unmodified helper with contract 256×256/24 FPS/24 frames. Original MP4 source: lavfi `color=red:size=256x256:rate=24:duration=1` plus `sine=frequency=440:sample_rate=48000:duration=1`; libx264/yuv420p, AAC, `+faststart`. Corruption replaced the `mdat` payload with zero bytes. Short-video source used color duration 0.5, audio duration 1, libx264/yuv420p and pcm_s16le in MKV, without `-shortest`. Independent checks used `ffprobe -v error -count_frames -show_streams -show_format -of json` and `ffmpeg -v error -nostdin -xerror -i damaged.mp4 -f null -`. The reproduction harness exited 0 and recorded the expected nonzero damaged-file decoder exit (3199971767). Temporary media was removed automatically.

Binaries: `workspace/tools/ffmpeg-9.0.2/ffmpeg-9.0.2-essentials_build/bin/{ffmpeg,ffprobe}.exe`. No provenance or GPU execution is inferred from these CPU checks. File hashes were unchanged through review:

```text
probe_media.py                  20E9F77F2E6F06094F7FC67BB3BE5F14AFFD09FD3A695B4DE53E3684FF4E67F3
tests/unit/test_probe_media.py   33ADA52CA860CF9147BB09A2BA53DF453200CA4DF705A7567F72A267CEC3AB94
tests/integration/test_probe_media.py 6670DCF18F878710E262EB701F1923002401B6B613C5598F2855EB6C2A731BA2
```

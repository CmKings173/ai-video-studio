# Running the production-generation qualification tools

Implementation status and actual evidence are in
`tasks/archive/reports/production-runtime-report.md`, the historical controller checkpoint, and
`tasks/archive/reports/h3-qualification-report.md`. This document supplies commands; it does not
claim GPU execution. Checked-in unexecuted workflows remain disabled.

## Local static verification

From the repository backend, set process-local paths and choose a new output folder:

```powershell
Set-Location D:\project\ai-video-studio\backend
$env:WORKFLOW_DIR='D:\project\ai-video-studio\backend\workflows\h3'
$env:WORKSPACE_ROOT='D:\project\ai-video-studio\workspace\qualification'
.\.venv\Scripts\python.exe -m apps.api.scripts.h3_probe --all --aspect-ratio 16:9 `
  --output-dir D:\project\ai-video-studio\workspace\qualification\static-landscape
.\.venv\Scripts\python.exe -m apps.api.management.benchmark_h3 --all `
  --output D:\project\ai-video-studio\workspace\qualification\matrix-not-run.json
```

The probe above submits no jobs. The second command records the requested matrix as
NOT_RUN and returns exit1 because no execution was requested. It must not report warm
timings. Default matrix: eight media cases x three profiles x three ratios x three
durations =216cells. Actual controller execution on2026-10-06: five graph preflights
STATIC_VALIDATED with actual width/height null;216cells NOT_RUN; zero warm verified.
Artifacts are in `workspace/runtime-tool-verification/offline-probe-20261006-a/` and
`workspace/runtime-tool-verification/offline-matrix-20261006-a.json`.

## Target prerequisites

The Oct 6 fixes address all five controller findings and both media findings with
scoped regressions and actual portable FFmpeg CPU media. Exact observed binaries,
hashes, counts and spans are in `runtime-cpu-verification-20261006.json` in this
directory. CPU evidence does not qualify the target GPU or approve a release.

Downloaded output passes FFprobe declaration checks and a full CPU FFmpeg decode of
the first video and audio streams. The decoder uses rawvideo/PCM framehash output,
strict input-error handling, preserved timestamps and passthrough video timing.
`decoded_video_frames` must equal the graph frame count; decoded video span must
match frames/FPS within 1ms. Positive `decoded_audio_frames` counts decoded PCM output
frames (packets), not audio samples. Audio span, AV start alignment and container
padding are checked independently within one video frame plus 1ms. Missing declared
`frames`, `video_duration_seconds` and `audio_duration_seconds` stay missing; distinct
`decoded_*` fields carry the actual decode evidence.

The helper bounds input to 512MiB, decoder time to 30s, stdout to 2MiB and stderr to
64KiB. The probe's outer deadline may cancel it earlier. Timeout, output overflow,
unavailable decoder, invalid tool output and tool startup/configuration failures
remain inspection errors; confirmed undecodable media is rejected as invalid output.
Cancellation/error cleanup terminates and drains the process, including Windows
backpressure. FFmpeg is resolved alongside configured FFprobe (`ffmpeg.exe` beside
`ffprobe.exe`, or `ffmpeg` on PATH); the Python helper also accepts an explicit
`ffmpeg_binary` and `decode_timeout_seconds`. No automatic install occurs.

Use the intended worker namespace's configured ComfyUI URL. Capture live
`/system_stats` and `/object_info`, actual API-format exports (including expanded
dynamic reference inputs), core/node versions and actual weight/LoRA SHA256 digests.
Populate graph-bound candidate profiles using the generation-contract report. Missing
Draft/High or media-capable graph candidates are NOT_RUN, not aliases for Standard or
a one-image graph. Keep candidate graphs disabled during qualification.

Prepare an input directory with a first frame, last frame, ordered reference images,
24FPS reference videos and audio clips. Video/audio clips must individually be2-15s,
each category's total <=15s; <=9images, <=3videos, <=3audio, <=12mixed. Audio needs
visual references. A requested symbolic slot must exist and reach the actual graph.
Preserve input byte hashes; the benchmark plan binds them for resume. Reference
limits use `video_duration_seconds` / `audio_duration_seconds` from the final
generation contracts. Missing stream duration is rejected during actual preflight;
a longer container duration never substitutes for it. Static placeholder durations
are explicitly uninspected and cannot establish execution evidence.

Named benchmark cases have mandatory categories: `ref_audio_image` requires image
and audio, and `ref_mixed` requires image, video and audio. Other named reference/frame
cases likewise require their media roles. Missing categories produce NOT_RUN with
`BENCHMARK_MEDIA_CATEGORY_MISSING` and zero probe calls, rather than a differently
conditioned workload under the requested label. Category requirements bind the plan
hash. Old plans whose identity changed are rejected; historical evidence is retained.

Step evidence must have an explicit graph binding on the collected output's ancestry.
Mutable candidates require the `STEPS` slot. A static candidate may instead declare
`profile.setting_bindings.steps = [node_id, field]`, provided its fixed literal equals
the candidate count; unsupported overrides fail before submission. The probe records
the binding, actual patched value and candidate profile hash. A fixed candidate is
still subject to the generation release gate's stricter contract before promotion.

Store non-secret runtime/model/node provenance in a JSON file. The probe records it
without approving it automatically. Do not substitute research-source hashes for
installed model hashes. All metrics require actual measurement.

## Example target commands (NOT_RUN here)

Set FFprobe to the target installation, or use the verified portable binary for host
execution. Select the real candidate-manifest directory and runtime configuration:

```powershell
$env:WORKFLOW_DIR='D:\qualification\candidate-h3'
$env:FFPROBE_BINARY='D:\project\ai-video-studio\workspace\tools\ffmpeg-9.0.2\ffmpeg-9.0.2-essentials_build\bin\ffprobe.exe'
.\.venv\Scripts\python.exe -m apps.api.scripts.h3_probe --mode i2v_last --execute `
  --aspect-ratio 9:16 --duration 5 --media LAST_FRAME=D:\qualification\media\last.png `
  --runtime-provenance D:\qualification\runtime-provenance.json `
  --output-dir D:\qualification\evidence\last-frame-run01
.\.venv\Scripts\python.exe -m apps.api.management.benchmark_h3 --all --execute --repeat 3 `
  --manifest-dir D:\qualification\candidate-h3 `
  --media FIRST_FRAME=D:\qualification\media\first.png `
  --media LAST_FRAME=D:\qualification\media\last.png `
  --media REFERENCE_IMAGE_1=D:\qualification\media\reference01.png `
  --media REFERENCE_VIDEO_1=D:\qualification\media\reference01.mp4 `
  --media REFERENCE_AUDIO_1=D:\qualification\media\reference01.wav `
  --runtime-provenance D:\qualification\runtime-provenance.json `
  --output D:\qualification\evidence\benchmark-run01.json
```

Add further numbered references explicitly for multi-reference capacity tests. The
tool refuses unsupported slots rather than dropping them. Run with other producers
isolated and queue idle; an idle queue snapshot alone is not a distributed lock.
Concurrency stays1. No unload/reload request is made between benchmark cells.

Warmup, first-observed and family-switch requests are excluded from warm percentiles.
`--cold-runtime-confirmed` is only for an operator-established cold runtime; ordinary
first observations do not prove cold start. Switching wall time includes that request's
execution and is not claimed to isolate model-loading time. Unknown timing remains null.
VRAM is a sampled maximum with sample count, not an exact hardware peak.

To resume, repeat the exact matrix, media and provenance arguments with `--resume`
and the same `--output`. Completed cells are skipped. Changed inputs are rejected.
SUBMISSION_PENDING/SUBMITTED/uncertain records halt resume: inspect the saved client
correlation against ComfyUI queue/history before deciding whether the job was accepted.
Do not delete that evidence to force a second submission.

One shared predicate guards CLI continuation, matrix continuation and the entire
saved resume journal. Resume scans every row before skipping completed cells or
retrying an earlier failure. Entering submit is recorded separately from receiving
a prompt ID. An outer deadline during submit yields SUBMISSION_PENDING and
COMFY_SUBMISSION_UNCERTAIN with the original cause and correlation. A definite
COMFY_REJECTED response remains terminal and pre-submit timeouts stay distinct.
The probe CLI journals correlation before awaiting each run, atomically persists
partial JSON after each result and stops on unresolved admission. Use a fresh probe
output directory; existing results are protected from overwrite. Neither tool
resubmits/reconciles automatically, treats an idle queue as proof of absence, or
promotes a workflow. Use the existing adapter's `find_prompt(client_id)` against both
queue and history, then establish terminal history/output or a definite rejection
before recording an operator-reviewed reconciliation. Preserve the original row and
correlation; retries must never erase uncertainty evidence.

For a configured, isolated Compose target with candidate/media/provenance files
already placed in the shared worker workspace, equivalent commands are below.
These operator commands were NOT_RUN in this slice; stop other runtime producers
before executing them.

```powershell
docker compose --env-file .env -f infra/compose.yaml exec -T `
  -e WORKFLOW_DIR=/workspace/qualification/candidate-h3 -e FFPROBE_BINARY=ffprobe `
  dispatcher python -m apps.api.scripts.h3_probe --mode i2v_last --execute `
  --media LAST_FRAME=/workspace/qualification/media/last.png `
  --runtime-provenance /workspace/qualification/runtime-provenance.json `
  --output-dir /workspace/qualification/evidence/last-frame-run01
docker compose --env-file .env -f infra/compose.yaml exec -T dispatcher `
  python -m apps.api.management.benchmark_h3 --all --execute --repeat 3 `
  --manifest-dir /workspace/qualification/candidate-h3 `
  --media FIRST_FRAME=/workspace/qualification/media/first.png `
  --media LAST_FRAME=/workspace/qualification/media/last.png `
  --media REFERENCE_IMAGE_1=/workspace/qualification/media/reference01.png `
  --media REFERENCE_VIDEO_1=/workspace/qualification/media/reference01.mp4 `
  --media REFERENCE_AUDIO_1=/workspace/qualification/media/reference01.wav `
  --runtime-provenance /workspace/qualification/runtime-provenance.json `
  --output /workspace/qualification/evidence/benchmark-run01.json
```

## Promotion

VERIFIED means the downloaded output satisfied the probe's media contract. It does
not enable a workflow. Persist genuine graph/slot/profile/runtime/model/node/output
evidence in the release contract, add benchmark and creative acceptance, then use the
admin approval gate. Target generation, PostgreSQL/MinIO restore and five-editor
deployment UAT remain required before PRODUCTION_READY.

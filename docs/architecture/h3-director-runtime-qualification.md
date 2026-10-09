# H3 Director runtime qualification

Source support is not a PASS. Every PASS in this document requires an executed provider run on a pinned release identity.

## Qualification identity

Record for every run:

- ai-video-studio revision;
- Director repository + commit;
- ComfyUI version;
- graph hash + slot contract hash;
- model/LoRA/VAE/CLIP hashes;
- GPU model/count, driver/CUDA/runtime;
- dependency fingerprint, including FaceRefine detector when used;
- exact DirectorExecutionSpec hash.

## Core matrix

| Case | Required variants | Status before execution |
|---|---|---|
| T2V | short/nominal duration; portrait/landscape | REQUIRES_RUNTIME_VERIFICATION |
| I2V | first-frame, at least two ratios | REQUIRES_RUNTIME_VERIFICATION |
| FL2V first+last | endpoints with matching/mixed source sizes | REQUIRES_RUNTIME_VERIFICATION |
| FL2V last-only | last-only legacy mapping | REQUIRES_RUNTIME_VERIFICATION |
| R2V image | 1 ref and max qualified count | REQUIRES_RUNTIME_VERIFICATION |
| R2V video | 1 ref and selected multi-video case | REQUIRES_RUNTIME_VERIFICATION |
| R2V image+audio | representative audio durations | REQUIRES_RUNTIME_VERIFICATION |
| R2V mixed | selected image/video/audio combination | REQUIRES_RUNTIME_VERIFICATION |
| V2V | source video nominal + boundary duration | REQUIRES_RUNTIME_VERIFICATION |
| RV2V | source video + representative refs | REQUIRES_RUNTIME_VERIFICATION |
| Motion Context | 2+ continuous segments, default 22; hard-cut control | REQUIRES_RUNTIME_VERIFICATION |
| Refine | each production candidate mode/config | REQUIRES_RUNTIME_VERIFICATION |
| FaceRefine | face detected, no-face behavior, detector missing negative case | REQUIRES_RUNTIME_VERIFICATION |
| Custom canvas | square/portrait/landscape + selected non-preset 32-grid dimensions | REQUIRES_RUNTIME_VERIFICATION |

For every mode, vary at least one duration, ratio and production profile relevant to that mode. Do not extrapolate a PASS from another resolution or enhancement configuration.

## Resolution ladder

The source exposes large generic bounds, but production qualification is explicit. Candidate steps should be tested incrementally rather than jumping from baseline to 4K.

Example qualification ladder:

1. current known-small smoke canvas;
2. current product baseline canvas;
3. 720-class candidate if desired;
4. 1080-class candidate;
5. 2K candidate;
6. 4K candidate.

Each step records PASS/FAIL independently. A failure does not reduce the source capability; it limits this release's qualified production envelope.

## Evidence captured per run

Required output measurements:

- actual width and height;
- actual FPS;
- frame count and decoded duration;
- video codec/container;
- audio stream presence;
- audio sample rate/channels/codec;
- per-artifact checksum and byte size;
- Director report and provider job identifiers.

Required runtime measurements:

- submit/start/end timestamps and latency;
- GPU allocation/peak memory when instrumentation is available;
- GPU utilization sampling where available;
- host RAM and disk/cache pressure where material;
- retry/reconcile events;
- error category and provider traceback for failures.

## PASS criteria

A case can be PASS only when:

1. provider identity matches the qualification target;
2. submitted graph/spec hash matches the frozen case;
3. provider completes without hidden fallback to another mode;
4. every required artifact is collected and checksum-stable;
5. media probe matches expected dimensions/FPS/frame-duration contract;
6. required audio exists and measured metadata is recorded;
7. no fatal worker/reconciliation inconsistency remains;
8. repeated run count required by the release policy succeeds.

Quality review may be an additional gate, but it must not replace machine contract verification.

## Motion Context checks

For the continuity case verify:

- aggregate run contains ordered member scenes;
- CONTINUOUS boundary uses the qualified context window (default source value 22 unless release overrides);
- CUT boundary has no previous-reference context;
- output scene/member mapping stays stable;
- retry/reconciliation does not duplicate or reorder members;
- exported duration accounts for context trimming correctly.

## Refine and FaceRefine checks

Record generation canvas, refine target/config and final delivery canvas separately.

FaceRefine evidence also records detector filename/hash, confidence/crop/canvas policy, frames with detected face, no-face behavior and output stitch result.

## Audio discrepancy check

Never encode “32 kHz stereo” as expected output solely from the master spec. Record actual generated/internal AUDIO metadata when observable and actual muxed/exported stream metadata. The inspected Director export source normalizes reference audio to 44.1 kHz stereo in that path.

## Benchmark claims

The following master-spec numbers remain UNVERIFIED until measured on the target release/hardware:

- ~43.6 GB static memory;
- ~91.6 GB peak memory;
- 35-45 seconds for a 5-second 1080p shot;
- ~700 videos/day.

Throughput must be derived from measured latency, concurrency, retry rate and scheduler utilization rather than copied into capabilities.

## Evidence persistence

Store qualification evidence as immutable records/artifacts linked to release identity. Revoking qualification hides a combination from new generation but does not erase prior evidence or generation history.
# Implementation tooling checkpoint — 2026-10-06

`python -m apps.api.scripts.director_probe --snapshot candidate.json --output-dir evidence`
validates a candidate frozen Director spec/graph and its explicitly supplied media.
`--execute` is required for submission; `--resume --execute` reconciles an existing
ledger without submitting another job. Media arguments use `ROLE:INDEX=PATH` and
must match frozen checksums, byte sizes and ordinals.

`python -m apps.api.management.benchmark_director --snapshot candidate.json --output-dir benchmark`
uses the same contract for every task and enhancement combination. `--execute`
is explicit, and an unresolved run stops the benchmark. Reported latency includes
preparation, upload, execution and collection; it is not GPU-only sample latency.
VRAM, target hardware and runtime dependency provenance still need the existing
runtime monitoring/evidence procedure.

Native aggregate candidate templates for two members are present but disabled.
Install `backend/comfy_nodes/studio_director_bridge` in target ComfyUI and generate
other member counts with the source template importer. Qualify exact task, member
count, boundaries, optional settings and exporter geometry before enabling any
profile. Enhancements require source-defined config nodes and their qualified
connections/model dependencies in the candidate graph.

No live ComfyUI/GPU qualification was performed in this implementation checkpoint.

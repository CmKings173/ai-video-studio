# H3 Director target architecture

Status: DIRECTOR-ONLY EXECUTION; PRODUCTION QUALIFICATION FAILS CLOSED\
Date: 2026-10-06\
Director pin: `AIMixer/ComfyUI_MiniMaxH3_Director@a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb`

## Evidence rules

- **CURRENT_AI_VIDEO_STUDIO_SOURCE**: verified in the current dirty workspace.
- **DIRECTOR_SOURCE**: verified in the pinned Director checkout.
- **MASTER_SPEC**: proposed by SPEC-ENG-H3-UNCONSTRAINED-V3.
- **INFERENCE**: architecture decision derived from source evidence.
- **RUNTIME_EVIDENCE**: measured on an installed runtime.
- **REQUIRES_RUNTIME_VERIFICATION**: source supports the path but production behavior is unproven.

The dirty ai-video-studio working tree is authoritative. This design does not rewrite existing history or production code.

## Decision

**INFERENCE:** ai-video-studio remains the management and orchestration source of truth. MiniMax H3 Director is the only H3 execution provider inside ComfyUI. New generations, frozen retries, and derivatives reject non-Director workflows. There is no legacy fallback.

```text
Browser
  -> FastAPI generation request
  -> GenerationIntent
  -> DirectorPlanBuilder
  -> Frozen DirectorExecutionSpec
  -> durable Generation/DirectorRun records
  -> worker Dispatcher
  -> DirectorAdapter / DirectorWorkflowBuilder
  -> ComfyUI prompt
  -> MiniMaxH3Director
       -> first pass
       -> optional Motion Context
       -> optional Refine
       -> optional FaceRefine
       -> audio/export
  -> collector
  -> MinIO assets + measured metadata
  -> SSE/status
  -> optional existing FFmpeg delivery/assembly
```

## Responsibilities

### ai-video-studio owns

**CURRENT_AI_VIDEO_STUDIO_SOURCE:** authentication/authorization, projects/products/videos/scenes, PostgreSQL records, immutable generation snapshots, asset lifecycle, MinIO, idempotency, worker leases/retries/reconciliation, SSE, qualification metadata, final delivery presets, FFmpeg assembly, and generation history already exist and should be preserved.

**INFERENCE:** ai-video-studio also owns business intent, provider selection, release qualification, reference-asset authorization, deterministic materialization into ComfyUI input space, durable multi-scene run state, collector validation, and all user-visible capability gating.

### Director owns

**DIRECTOR_SOURCE:** Director owns H3-specific timeline planning/execution, task conditioning, external i2v/r2v groups, source-video timeline modes, Motion Context continuity, optional Refine, optional FaceRefine, H3 AV handling, segment export, and its runtime report.

ai-video-studio should consume these capabilities instead of reimplementing the algorithms.

## Business intent versus Director task key

| Business intent | Director task | Notes |
|---|---|---|
| Text-to-Video | `t2v` | prompt-only |
| First Frame | `i2v` | first keyframe |
| Last Frame | `fl2v` | legacy last-only maps to FL2V group shape |
| First + Last Frame | `fl2v` | both endpoints |
| Reference-to-Video | `r2v` | image/video/audio refs |
| Video-to-Video | `v2v` | source video from timeline/source mode |
| Reference-Video-to-Video | `rv2v` | source video plus references |
| Mixed | `mixed` | provider capability; not first-class product intent until qualified |

Legacy persisted values remain readable:

```text
t2v             -> t2v
i2v             -> i2v
i2v_last        -> fl2v
i2v_first_last  -> fl2v
r2v             -> r2v
v2v             -> v2v
rv2v            -> rv2v
```

## Geometry boundaries

**DIRECTOR_SOURCE:** the generation canvas is 32-pixel aligned; Director has its own Refine configuration and FaceRefine crop/canvas settings.

**CURRENT_AI_VIDEO_STUDIO_SOURCE:** final delivery already has resize/pad/crop/FPS/assembly logic.

Freeze three distinct geometries:

1. `generation_canvas`: H3 first-pass width/height.
2. `director_refine`: optional Director Refine target/configuration.
3. `delivery`: final FFmpeg delivery width/height/FPS/fit mode.

A 4K delivery request does not imply 4K H3 generation.

## Multi-scene continuity boundary

**DIRECTOR_SOURCE:** Motion Context consumes the previous segment AV tail inside one Director plan. Default context is 22 frames and continuity covers `t2v/i2v/fl2v/r2v/v2v/rv2v`.

**CURRENT_AI_VIDEO_STUDIO_SOURCE:** durability is currently centered on per-scene `SceneGeneration`.

**INFERENCE:** introduce a durable aggregate Director run that owns an ordered set of scene-generation members and one frozen `DirectorExecutionSpec`. Existing per-scene history remains intact, while the aggregate run owns shared timeline execution, continuity state, provider job identity, collector manifest, and retry policy.

Retry rules:

- before provider submission, rebuild from the same frozen spec;
- after submission, reconcile a recoverable provider job before creating another attempt;
- partial multi-segment resume may use Director cache only after release qualification proves it safe;
- never emulate native continuity with unrelated per-scene Comfy prompts.

## Asset path

```text
MinIO Asset
 -> authorized asset/revision/checksum in frozen snapshot
 -> worker materialization
 -> deterministic run-scoped Comfy input filename
 -> ordered group/timeline slot
 -> Director
```

Required roles are `FIRST_FRAME`, `LAST_FRAME`, `REFERENCE_IMAGE`, `REFERENCE_VIDEO`, `REFERENCE_AUDIO`, and `SOURCE_VIDEO`.

Each frozen reference stores asset ID, revision/checksum, role, stable ordinal, media metadata, and deterministic materialized filename. Filenames include run identity + ordinal + checksum prefix so retries cannot collide on user basenames.

## Capabilities

**INFERENCE:** the frontend consumes backend-served capabilities assembled from:

1. pinned provider metadata/source contract;
2. installed ComfyUI `/object_info`;
3. ai-video-studio release qualification;
4. runtime probe/benchmark evidence.

UI/schema capability and production-qualified capability are separate. Unqualified combinations are not advertised as runnable.

## Release identity

A qualified H3 release records at least:

- Director repository + commit/node version;
- ComfyUI version;
- workflow graph hash and slot/contract hash;
- model/LoRA/VAE/text-encoder hashes;
- dependency fingerprint;
- tested modes/reference combinations;
- tested generation canvases/ratios/durations;
- Refine, FaceRefine and Motion Context configuration;
- measured output metadata;
- benchmark/runtime evidence IDs.

Changing execution-significant identity invalidates qualification for affected combinations.

## Output ownership

Director may return image/audio lists, report data, combined output, and per-segment exports. The collector must be manifest-based and must not assume one filename or one image batch. Every accepted artifact is measured and recorded before MinIO finalization.

Director report text is diagnostic evidence, not the canonical generation contract.

## Non-goals for the first refactor

- no rewrite of auth, asset lifecycle, MinIO, PostgreSQL history, SSE or delivery pipeline;
- no migration that rewrites legacy generation-mode history;
- no production claim for 1080p/2K/4K, throughput, latency or VRAM without executed qualification;
- no hard-coded Comfy node IDs copied from the master-spec example graph.

## Director-only retirement (2026-10-08)

The bundled active registry contains six tasks in each of `single_scene` and `aggregate`.
`i2v_last` and `i2v_first_last` remain business aliases for Director `fl2v`.
The standalone PostgreSQL queue still executes single-scene Director plans; aggregate
DirectorRun orchestration remains separate. No queue redesign is required.

Historical workflow rows retain their graphs, hashes, versions, approval evidence and
foreign-key identities. Historical generation snapshots and reports remain readable.
`require_director_execution(record)` rejects legacy execution at selection and frozen
validation boundaries, regardless of an old enabled flag. Historical rows never appear
in generation capabilities. Admin approval cannot enable a non-Director workflow.

`python -m apps.api.management.retire_legacy_h3` is a dry-run inventory.
After the application database backup, the operator may run it with `--apply`. It locks
exactly the five legacy codes and rechecks reflected foreign keys and JSON identity
dependencies. Unreferenced rows are deleted; referenced rows are kept intact and disabled.
It never repoints history or rewrites snapshots. PostgreSQL row locks protect foreign-key
inserts; SHARE locks on JSON-bearing tables protect JSON-only history during recheck.
Apply requires READ COMMITTED isolation. Repeated apply is idempotent.
Application database audit and apply evidence are separate from isolated test evidence.
The active registry contains exactly 12 Director graphs: six single-scene and six aggregate.
The six historical `_aggregate_2` drafts have no active registry, builder, probe, or test
references; they were removed from `backend/workflows/h3`. Their verified SHA-256 values
remain in the archived retirement review. The upstream pin is unchanged.
Qualification and advertisement remain false until target runtime evidence exists.

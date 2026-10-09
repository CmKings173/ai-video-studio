# H3 Director execution contract

## Contract layers

1. API request: mutable user/business input.
2. `GenerationIntent`: normalized business semantics.
3. `DirectorExecutionSpec`: immutable provider execution contract.
4. Rendered ComfyUI graph: provider prompt derived from the frozen spec.

Layer 3 is persisted as the semantic source of truth for retries. The rendered graph is reproducible evidence with its own hash.

## DirectorExecutionSpec

Illustrative typed schema:

```text
DirectorExecutionSpec
  schema_version
  provider
    id, repository, commit, comfyui_version
    release_qualification_id
    workflow_graph_hash, slot_contract_hash
    model_hashes
  intent
    business_mode, director_task
    accepted_prompt
    prompt_transform_policy
    effective_prompt_metadata
  generation
    width, height, fps, frame_count, duration_seconds
    seed, steps, cfg, sampler, scheduler
    shift_video, shift_audio
  inputs[]
    role, asset_id, asset_revision, checksum, ordinal
    media_metadata, materialized_filename
  groups[]
  timeline
    canonical_json, source_video_input, segment_members[]
  continuity
    enabled, mode, context_frames, hard_cut_boundaries[]
  audio
    policy, required, expected_export_contract
  refine
  face_refine
  export
    mode, requested_artifacts[], collector_manifest_policy
  delivery
    separate_delivery_profile_id
```

Every execution-significant field must serialize deterministically and participate in the semantic hash.

## Prompt immutability

**CURRENT_AI_VIDEO_STUDIO_SOURCE:** accepted generation semantics are already frozen.

**DIRECTOR_SOURCE:** Director may transform group prompts, including FL2V/R2V reinforcement and automatic reference-tag handling.

Store both:

- `accepted_prompt`: exact normalized user/business prompt accepted by API;
- `effective_prompt_metadata`: deterministic transform policy/version and, when knowable, resulting provider prompt.

Never overwrite `accepted_prompt` with Director-conditioned text.

## Asset contract

Before freezing:

- verify project/product/video scope;
- verify asset state and revision/checksum;
- reject duplicate role/ordinal combinations;
- enforce business-mode requirements;
- record measured media metadata.

Worker materialization uses a deterministic run-scoped filename such as:

```text
{run_id}/{role}_{ordinal}_{checksum12}.{normalized_ext}
```

The worker verifies bytes against the frozen checksum and refuses silent substitution.

Reference ordering is zero-based internally and one-based in Director tags:

```text
REFERENCE_IMAGE ordinal 0 -> <Picture 1>
REFERENCE_VIDEO ordinal 0 -> <Video 1>
REFERENCE_AUDIO ordinal 0 -> <Audio 1>
```

## Mode invariants

- `t2v`: no keyframe/source requirement.
- `i2v`: first frame required.
- `fl2v` last-only: last frame required.
- `fl2v` first+last: both required.
- `r2v`: at least one qualified reference.
- `v2v`: source video required through timeline/source-video contract.
- `rv2v`: source video plus qualified references.
- external `i2v_groups` and `r2v_groups` are mutually exclusive.
- `mixed`: provider/internal until explicitly productized.

## Continuity contract

`SceneSpec.continuity = CUT` maps to a hard boundary where the next segment must not consume previous Motion Context.

`SceneSpec.continuity = CONTINUOUS` maps to Director continuity metadata with a qualified context window, default 22 frames unless release policy overrides it.

Continuity is frozen on the aggregate execution spec; retries cannot silently change cut boundaries or context length.

## Refine and FaceRefine

Refine and FaceRefine are provider execution configuration, separate from FFmpeg delivery transforms.

`refine` stores exact qualified mode and target/config values. `face_refine` stores detector identity and all execution-significant crop/canvas/sampling/paste settings. Detector dependencies belong in release qualification.

## Rendered graph contract

`DirectorWorkflowBuilder` consumes only a validated frozen spec plus a qualified release descriptor. It may resolve exact ComfyUI combo labels from `/object_info`, but cannot mutate business semantics.

Checks include:

- required Director class types exist;
- model/VAE/CLIP connections are compatible;
- exact installed task combo is used;
- required group/timeline nodes exist;
- no forbidden group combination;
- frozen values match rendered slots;
- graph and slot hashes match qualification identity.

No architecture contract assigns hard-coded node IDs such as 10/20/30/95.

## Collector contract

The collector consumes a provider artifact manifest instead of guessed filenames.

Each artifact records:

- role: final / segment / pre-refine / pre-face / report;
- scene/member index when applicable;
- provider locator;
- checksum and byte size;
- measured width/height/FPS/frame count/duration;
- measured audio stream metadata;
- generation run/attempt identity.

For segment export, prior segment tensors may be released while MP4 files remain authoritative. Collector logic therefore prefers explicit exported artifacts and measured media metadata over remaining IMAGE tensors.

A run is VERIFIED only when all required outputs pass the frozen contract and media probes.

## Persistence and compatibility

Existing `SceneGeneration.input_snapshot` remains readable. New snapshots are versioned and embed/reference the Director execution spec.

Legacy generation modes remain readable. New `v2v` and `rv2v` values are additive; no history rewrite is required.

## Failure categories

- invalid/frozen contract;
- asset authorization or checksum mismatch;
- provider release unavailable/unqualified;
- Comfy graph build/slot mismatch;
- provider submit/execution failure;
- collector artifact missing/corrupt;
- output contract mismatch;
- partial aggregate-run failure;
- retry exhausted.

Provider report strings are diagnostics, never the only machine-readable failure classifier.
# Implementation checkpoint — 2026-10-06

Aggregate execution now uses DirectorRun/DirectorRunAttempt under the standalone
dispatcher admission lock. Member generations never share a comfy_prompt_id
(that column is unique); attempt lookup resolves through DirectorRunMember.
Progress is projected to member counters, and completion publishes all required
READY segment assets in one transaction. Retry/reconciliation retains the run
snapshot and durable submission correlation.

Native `exportMode=segments` may release image tensors after persisting MP4.
The companion `backend/comfy_nodes/studio_director_bridge` observes the source
finalize seam using a ContextVar and emits structured `studio_director_artifacts`
from that run's plan. Profiles explicitly bind transport
`studio_native_segments_v1`, role, node identity and member coverage. These native
profiles currently require one timeline segment per scene member. The bridge
checks pinned execution/export source file hashes; there is no report parsing,
directory scanning or modification of the source checkout.

DirectorProbe consumes a signed frozen spec and the exact candidate graph/profile.
It records a client correlation ledger before submission. Resume reconciles the
same job without resubmission; missing correlation or timeouts stay unresolved.
Director benchmark uses separate ledgers/output prefixes and stops on any
unresolved run. Neither command writes production approval.

Delivery presets/fit mode are now resolved into the immutable assembly manifest
and consumed by FFmpeg. Mute delivery retains silent AAC. Generation canvas,
refine target and delivery canvas remain separate contracts.

**Runtime status: NOT VERIFIED.** Bridge installation, real ComfyUI/GPU exports,
enhancement combinations, migrations and broader regression suites remain for
the verification task.

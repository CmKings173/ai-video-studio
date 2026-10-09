# H3 Director refactor implementation plan

Status: ready for phased implementation after review of the architecture docs. Do not implement runtime claims before qualification.

## Preconditions

- preserve the current dirty working tree;
- use `AIMixer/ComfyUI_MiniMaxH3_Director@a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb` as the design pin until intentionally updated;
- treat the current ai-video-studio source, not HEAD alone, as the baseline;
- do not invent Comfy node IDs;
- keep legacy generation records readable.

## Work packages

### WP0 Baseline and regression boundary

- identify current tests that cover snapshots, workflow qualification, dispatcher retry/reconcile, asset retention/finalize, output probing and delivery;
- add only missing behavior-level regression coverage required to protect these seams;
- record baseline status before production edits.

Exit: baseline is trustworthy enough to attribute later failures.

### WP1 Provider identity

- add Director provider descriptor and pinned revision;
- capture ComfyUI `/object_info` required class types;
- extend qualification identity with Director/Comfy/model/dependency hashes;
- keep legacy provider selectable for rollback.

Exit: runtime can be identified as matching/mismatching a qualified release.

### WP2 Typed execution contract

- implement `GenerationIntent` normalization;
- implement deterministic immutable `DirectorExecutionSpec` serialization/hash;
- separate accepted prompt from Director-effective prompt metadata;
- add generation/refine/delivery geometry fields;
- add explicit input roles and stable ordinals.

Exit: spec can be frozen and compared without ComfyUI.

### WP3 Schema/API migration

- add versioned Director snapshot/run representation;
- add `v2v` and `rv2v` API/DB/frontend-readable values;
- preserve `i2v_last` and `i2v_first_last` history;
- add source-video input role;
- extend generation-capabilities response with source versus qualified capabilities.

Exit: new API shape is backward-readable and old records still load.

### WP4 Director workflow builder

- build model/VAE/CLIP/Director graph from frozen spec;
- resolve installed combo/class contracts from object info;
- build i2v/fl2v or r2v groups and timeline/source-video data as appropriate;
- attach Refine/FaceRefine config nodes when enabled;
- calculate graph/slot hash and verify frozen values.

Exit: static graph-contract tests pass for all source-supported task families.

### WP5 Worker input materialization

- fetch authorized MinIO revisions referenced by the frozen spec;
- checksum bytes before submit;
- write deterministic run/attempt-scoped Comfy input names;
- map stable ordinals to Director tags/group slots;
- clean attempt-scoped inputs according to existing lifecycle rules.

Exit: retries are deterministic and cannot silently substitute/collide assets.

### WP6 Dispatcher and collector

- submit Director graph through existing Comfy transport/reconciliation;
- persist provider job identity;
- collect explicit final/segment/pre-pass artifacts;
- probe every required output;
- finalize accepted assets to MinIO and record manifest/checksums.

Exit: one Director smoke run has durable end-to-end accounting.

### WP7 Enable T2V/I2V/FL2V

- route only qualified combinations;
- keep legacy FL2V aliases in business layer;
- validate required first/last roles.

Exit: executed qualification matrix passes selected production profiles.

### WP8 Enable R2V

- support up to source maxima 9 image / 3 video / 3 audio but advertise only qualified limits;
- verify mixed-reference ordering and prompt transforms;
- reject unsupported combinations before freezing.

Exit: image, video, image+audio and selected mixed cases are qualified.

### WP9 Enable V2V/RV2V

- implement source-video timeline contract;
- add frontend source-video selection;
- qualify source-video duration/codec/profile combinations.

Exit: V2V and RV2V are independently capability-gated.

### WP10 Aggregate Director run and Motion Context

- add aggregate run/attempt + ordered scene members;
- map CUT/CONTINUOUS boundaries;
- freeze context window and continuity pipeline ID;
- support status/reconcile/retry without member duplication;
- map segment outputs back to scene-generation history.

Exit: multi-scene native Motion Context survives retry/reconcile tests and executed qualification.

### WP11 Refine/FaceRefine/audio

- implement exact qualified Refine config;
- implement FaceRefine detector identity/config and dependency checks;
- collect optional pre-refine/pre-face artifacts;
- measure actual audio output metadata and stop assuming 32 kHz export.

Exit: enhancements can be enabled/disabled independently by capability.

### WP12 Frontend capability editor

- remove runnable-mode/reference/resolution assumptions that duplicate backend policy;
- consume qualified capabilities;
- expose V2V/RV2V and optional enhancement controls conditionally;
- preserve rendering of historic generation records.

Exit: frontend cannot request an unqualified combination through normal UI.

### WP13 Delivery/assembly regression

- feed verified Director outputs into existing delivery presets/FFmpeg assembly;
- prove generation canvas, Refine target and delivery canvas can differ safely;
- retain CUT/CROSSFADE and audio policies.

Exit: existing delivery integration tests plus Director-specific geometry cases pass.

### WP14 Runtime qualification and rollout

- execute `docs/architecture/h3-director-runtime-qualification.md` on target hardware;
- persist evidence per release identity;
- advertise only PASS combinations;
- benchmark latency/VRAM/throughput instead of copying master-spec estimates;
- stage rollout with provider/mode capability kill switches.

Exit: production capability set is evidence-backed.

### WP15 Cleanup

- remove obsolete graph-specific paths only after rollback window;
- retain historic readers/migrations;
- update runbook and troubleshooting docs.

Exit: no dead path is needed for rollback or historic record access.

## Required review gates

Before merging each work package, review:

- semantic snapshot stability;
- authorization/asset lifecycle regression risk;
- worker idempotency/reconciliation;
- provider qualification invalidation;
- migration/rollback path;
- tests proving behavior rather than implementation details.

## Final implementation acceptance

Implementation is complete only when the runtime-qualified capability response matches executed evidence and the original production-hardening guarantees still pass their regression suite.

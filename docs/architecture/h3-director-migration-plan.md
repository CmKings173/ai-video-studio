# H3 Director migration plan

This migration is additive and preserves the current dirty production-hardening work. Each phase has an independent rollback boundary. No phase rewrites legacy generation history.

## Phase 0 — Preserve baseline and blockers

Scope:

- freeze the current behavioral baseline with targeted tests around generation snapshots, asset lifecycle, worker dispatch/reconciliation, output probing and delivery;
- document existing dirty-work ownership before implementation starts;
- resolve any currently failing tests that would make Director regressions ambiguous.

Acceptance:

- baseline tests pass before Director production code changes;
- no reset/clean/revert of unrelated work.

Rollback: documentation/tests only.

## Phase 1 — Provider source pin and release identity

Likely files:

- workflow/provider registry and qualification services;
- deployment/runtime configuration;
- docs/runbook/qualification metadata.

Changes:

- add provider ID `minimax_h3_director`;
- pin repository commit/node version;
- record ComfyUI version, model/VAE/CLIP hashes, dependency fingerprint;
- query installed `/object_info` and verify required Director class types.

Tests: provider identity hashing, source/runtime mismatch rejection.

Acceptance: an installed runtime can be identified unambiguously and marked qualified/unqualified without submitting generation.

Rollback: disable generation while Director is unavailable; legacy execution is retired.

## Phase 2 — DirectorExecutionSpec and backend capabilities

Likely files:

- `backend/apps/api/app/schemas/api.py`;
- `generation_service.py`;
- new Director contract/plan-builder module;
- generation-capabilities endpoint/service;
- model/migration files if a versioned aggregate run is introduced here.

Changes:

- add typed immutable `DirectorExecutionSpec`;
- add business modes `v2v`, `rv2v` while retaining legacy values;
- represent accepted/effective prompt metadata separately;
- add generation/refine/delivery geometry separation;
- serve source capabilities and qualified capabilities separately.

Tests: deterministic serialization/hash, legacy mapping, capability gating, invalid combination rejection.

Acceptance: API can freeze a Director spec without building/submitting a Comfy graph.

Rollback: do not route requests to Director yet.

## Phase 3 — Director workflow builder and registry

Likely files:

- `workflow_loader.py`;
- `workflow_contracts.py`;
- `registry.json` or successor registry;
- new `DirectorWorkflowBuilder`.

Changes:

- construct graph from frozen spec and installed object info;
- wire model/video VAE/audio VAE/CLIP to `MiniMaxH3Director`;
- build exact external groups/timeline contract;
- bind optional Refine/FaceRefine nodes from the frozen spec;
- hash rendered graph and slot contract.

Tests: graph contract per mode, forbidden i2v+r2v group combination, frozen-slot immutability, missing node/combo detection.

Acceptance: graphs validate statically for every source-supported mode without using invented node IDs.

Rollback: fail closed; never select a legacy graph/provider.

## Phase 4 — Dispatcher, asset materialization and collector

Likely files:

- worker `dispatcher.py`;
- `comfy_adapter.py`;
- asset/storage helpers;
- output probe/collector modules.

Changes:

- materialize frozen MinIO inputs into run-scoped deterministic Comfy filenames;
- verify checksum and media metadata before submit;
- translate stable reference ordinals into Director group/tag ordering;
- submit/reconcile Director prompt;
- collect explicit artifact manifests, including segment exports;
- probe all required outputs before finalizing MinIO assets.

Tests: filename collision prevention, retry determinism, checksum mismatch, missing/corrupt segment artifact, multi-output collection.

Acceptance: one qualified Director run can round-trip through worker durability and asset finalization.

Rollback: disable Director provider routing; existing worker path remains.

## Phase 5 — T2V / I2V / FL2V

Changes:

- enable `t2v`, first-frame `i2v`, last-only FL2V and first+last FL2V;
- map legacy `i2v_last` and `i2v_first_last` during planning only;
- enforce group duration/frame alignment.

Tests: mode/reference invariants, parent regeneration/variation semantics, measured output contract.

Acceptance: runtime qualification passes the target profile matrix for these modes.

Rollback: per-mode feature flags/capability suppression.

## Phase 6 — R2V multi-reference

Changes:

- add ordered image/video/audio references;
- expose qualified combinations only;
- preserve stable `<Picture N>/<Video N>/<Audio N>` mapping;
- record Director prompt transform policy.

Tests: max counts, mixed reference ordering, duplicate refs, prompt-tag determinism, audio-duration/media validation.

Acceptance: qualified image, video, image+audio and selected mixed-reference probes pass.

Rollback: capability endpoint removes failing combinations.

## Phase 7 — V2V / RV2V

Changes:

- add explicit `SOURCE_VIDEO` role;
- build source-video timeline data rather than external i2v/r2v group misuse;
- add public/API/frontend values `v2v` and `rv2v`.

Tests: source video authorization/checksum, timeline serialization, mode requirements.

Acceptance: qualified V2V and RV2V profiles pass media probes.

Rollback: keep enum readable but unadvertised/unroutable.

## Phase 8 — Custom canvas and resolution capability

Changes:

- expose 32-grid custom dimensions from backend qualification;
- keep raw source bounds separate from runnable production limits;
- enforce qualified dimension/ratio/profile combinations.

Tests: snapping/validation boundaries and capability mismatch rejection.

Acceptance: selected portrait/landscape/square/custom profiles have executed evidence.

Rollback: advertise only previously qualified presets.

## Phase 9 — Motion Context and aggregate durability

Likely changes:

- add durable aggregate Director run/attempt record plus ordered scene members;
- map `CUT` and `CONTINUOUS` scene boundaries;
- freeze context window and continuity pipeline identity;
- expose member/aggregate progress over existing status/SSE conventions.

Tests: hard-cut boundary, 22-frame continuous boundary, retry/reconcile of aggregate attempt, failure of one member, idempotent resubmission.

Acceptance: a multi-scene runtime run proves native continuity with correct member artifacts and durable recovery behavior.

Rollback: disable continuity capability and fall back to independent scene generation with explicit CUT semantics; never claim Motion Context when disabled.

## Phase 10 — Refine, FaceRefine and audio/export contract

Changes:

- add qualified Director Refine config independent of delivery resize;
- add FaceRefine detector/dependency identity and config;
- collect pre-refine/pre-face artifacts only when requested;
- measure exported audio sample rate/channels rather than assume 32 kHz.

Tests: config hashing, detector missing, optional-output collection, measured AV metadata.

Acceptance: runtime matrix records Refine and FaceRefine outputs plus actual audio stream metadata.

Rollback: enhancement capabilities can be disabled independently.

## Phase 11 — Frontend capability-driven editor

Likely files:

- `frontend/lib/api/types.ts`;
- generation editor/page components;
- capability helpers such as `generationInputProblems`.

Changes:

- derive runnable modes/reference counts/resolutions/enhancements from backend capabilities;
- support V2V/RV2V source-video inputs;
- show accepted business intent without exposing raw provider graph details.

Tests: capability gating, invalid input UX, legacy generation rendering.

Acceptance: frontend has no hardcoded claim that exceeds backend qualification.

Rollback: server suppresses Director capabilities; existing editor behavior remains.

## Phase 12 — Delivery and assembly integration

Changes:

- keep Director generation output as source media;
- apply existing delivery presets/fit/FPS after collection;
- preserve CUT/CROSSFADE assembly behavior as a delivery concern.

Tests: generation canvas differs from delivery canvas, audio preservation/mute, assembly regressions.

Acceptance: delivery metadata matches requested preset without mutating provider execution history.

Rollback: existing delivery implementation remains the baseline.

## Phase 13 — Runtime qualification and benchmark

Run the matrix in `h3-director-runtime-qualification.md` on the target deployment. Persist evidence per provider release.

Acceptance: only executed combinations become `qualified_capabilities`.

Rollback: qualification revocation hides combinations without deleting history.

## Phase 14 — Deprecate obsolete paths

Only after usage/rollback window:

- remove replaced graph-specific assumptions;
- remove obsolete legacy-only validators that have equivalent Director contract coverage;
- keep readers/migrations for historic records.

Acceptance: clean-checkout tests, migration tests and rollback documentation pass.

Rollback: this phase should be delayed until no rollback needs old implementation code.

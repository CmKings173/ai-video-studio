# Director implementation checkpoint — 2026-10-06

Workspace: `D:/project/ai-video-studio`; branch `codex/production-safety-fixes`;
HEAD `01b740d8be4d58d3bd15bc46dd9842afb48af8bb`. The authoritative working tree
remains dirty. No commit, push, reset, clean, merge or deployment was performed.
Existing production-hardening work was retained.

Director source: `AIMixer/ComfyUI_MiniMaxH3_Director`, commit
`a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb`, Apache-2.0. Source-defined
`prompt_batch` is supported at this pin and remains in the timeline adapter.

## Implemented in this continuation

- **Domain/API:** Generate All decides aggregate execution before preparing
  generations; optional singleton qualification is deferred only for aggregate
  preparation. CONTINUOUS members require their immediate predecessor in the
  batch. Standalone continuous scenes fail with DIRECTOR_AGGREGATE_REQUIRED.
  Capability output distinguishes aggregate/single-scene scope and exposes typed,
  normalized qualified optional settings. OpenAPI was regenerated.
- **Dispatcher:** DirectorRun/DirectorRunAttempt execution shares scheduler
  admission with standalone generations. Submission, client correlation, uncertain
  submission recovery, cancellation and polling reuse the existing transport
  behavior. Run progress/errors/attempt state project to members. Members never
  duplicate the run's unique prompt ID. Every required segment must be READY
  before all member completions and automatic selections publish atomically.
- **Provider/workflows:** native segment collection uses an explicit manifest;
  bridge source seam hashes fail closed on drift. The bridge captures the exact
  source plan through a ContextVar and preserves upstream execution/results.
  Six disabled two-member candidate templates were imported from pinned examples.
  Other counts are supported by the importer CLI. Native candidates require one
  timeline segment per scene member. Refine's business follow_director alias maps
  to the exact source combo. Refined output geometry requires an explicit measured
  contract rather than assuming generation dimensions.
- **Frontend/history:** optional qualified Director presets, Refine mode/upscale
  target and Face confidence controls; exact optional combination checks; history
  shows aggregate identity/resolved global seed. FL2V aliases retain regenerate
  availability when the matching single-scene workflow is available.
- **Delivery/FFmpeg:** delivery_preset and fit_mode flow through request, service,
  immutable manifest, worker and actual normalization. Ultrawide 2560×1080 is
  usable. FIT_PAD/CENTER_CROP retain aspect ratio. Scene streams are explicitly
  selected and audio normalized to 48 kHz stereo; mute delivery retains silent
  AAC. Geometry remains separate from Director generation/Refine.
- **Qualification tooling:** DirectorProbe accepts signed candidate snapshots and
  exact role/ordinal/checksum/size media bindings. It saves correlation before
  submission, and resume never resubmits. A separate Director benchmark uses the
  same contract for all tasks/settings and stops on unresolved execution. Neither
  script writes approval. Architecture/runtime docs and bridge installation notes
  describe these boundaries.

Earlier working-tree implementation retained: typed intent/spec, provider-aware
routing, V2V/RV2V/source-video roles, ratio/custom canvas schemas, migration
contracts, expanded media formats, trusted ingress guards, safe seed range,
saved-reference hydration, semantic batch identity and historic snapshot readers.
Those retained changes are not newly verified by this checkpoint.

## Tests and lightweight checks

Executed before the final review edits:

- `test_director_dispatcher.py`: 4 tests passed (shared admission/reclaim,
  progress, cancellation, all-or-none completion).
- Initial `test_delivery_wiring.py`: 2 tests passed (preset validation and actual
  preset/fit-mode forwarding).
- `test_director_native_manifest.py`: 3 tests passed (member identity, missing or
  escaped exports, duplicate coverage).

Final source checks: selected changed Python files passed Ruff; frontend
`tsc --noEmit --incremental false` passed; OpenAPI export completed; `git diff
--check` found no whitespace errors. These are not runtime qualification.

Added but **NOT EXECUTED IN THIS TASK**: five execution-contract tests for signed
spec roundtrip/tampering, seed/custom canvas boundaries, frozen prompt/template,
ordered references/source video and FL2V; muted AAC delivery regression; Generate
All missing-predecessor regression. Rerun the earlier nine tests as part of final
verification because source changed after those checkpoints.

## Verification still required

Backend regression suite, frontend tests/lint/build, PostgreSQL migrations and
race tests, Docker startup, bridge installation/runtime loading, real ComfyUI
execution for all six tasks, source/reference materialization, Motion Context
trim/audio synchronization, Refine/FaceRefine dependencies and output geometry,
final delivery geometry/audio, GPU memory/latency/throughput, UAT and backup/restore.

Candidate templates remain disabled and features without exact executed evidence
remain unavailable. GPU runtime access/qualification is an external verification
dependency, not evidence of successful execution. This report does not approve a
merge or declare production readiness.

Verdict for this checkpoint: **IMPLEMENTATION_COMPLETE_PENDING_VERIFICATION**.
This refers to the code paths described above; full plan acceptance still requires
the explicit runtime/regression evidence from the approved qualification plan.

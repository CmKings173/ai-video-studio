# Runtime tooling checkpoint (controller-owned slice)

Specification: production-generation plan I/J/S and phases1/5. Workspace
`D:/project/ai-video-studio`, branch `codex/production-safety-fixes`, baseline
`01b740d`. Source edits are uncommitted. This report covers the probe/benchmark
orchestration, not completed qualification of the media helper or target GPU.

## Changes

- `backend/apps/api/scripts/h3_probe.py`: stages NOT_RUN/STATIC_VALIDATED/SUBMITTED/
  EXECUTED/VERIFIED; execute flag is no longer evidence of actual execution.
  Terminal runtime errors are EXECUTED failures. A failed or uncertain submit retains
  correlation and never claims execution. Download bytes are inspected; actual
  width/height stay null until verified. Requested ratio resolves through the shared
  source-pinned graph resolver; contradictory dimensions are rejected. Static preflight
  now patches required settings too. Last-frame mode and all numbered image/video/audio
  symbolic roles are recognized, unsupported media is rejected, counts/durations use
  H3Validator, supplied media is inspected before uploading. Reference videos need
  pre-normalization to24FPS; the tool does not silently trim/resample inputs.
- The runtime `/object_info` inventory is checked against every graph class/input;
  absent or unverified inputs stop submission. Native dynamic descriptors require a
  verified expanded export/schema before acceptance; this is not an invented Ref2VA
  graph. Graph/slot/profile/submitted-graph/node-inventory hashes are recorded. Explicit
  step overrides change the candidate settings hash. Target model/runtime provenance
  supplied by an operator is recorded, not promoted to approved evidence automatically.
- `backend/apps/api/app/integrations/comfy_adapter.py`: public read-only `object_info()`.
- Runtime queue must be observed idle before submit. Timing/memory polling stays
  sequential; the CLI benchmark keeps one adapter/HTTP client. Sampled VRAM maximum
  records sample count and incomplete sampling; absent metrics remain null. Queue wait
  and execution time remain unknown where no trustworthy timing evidence was observed;
  observation and collection wall times have distinct fields. Isolate workers/other
  clients during a target benchmark: an idle queue check cannot exclude a later race.
- `backend/apps/api/management/benchmark_h3.py`: explicit candidate mode/profile rows;
  missing candidates become NOT_RUN. Eight media cases, durations5/10/15 and ratios
  9:16/16:9/1:1; >=3 warmed repeats after excluded warmup, separate first observed
  request and family switch requests. A cold label requires an explicit operator
  assertion; ordinary first requests do not pretend to prove cold state. Concurrency1.
  Nearest-rank p50/p95 summarize verified warm wall times only. Creative acceptance
  and profile promotion remain separate qualification gates.
- Result destination is unique by default; historical docs results are preserved.
  Resume verifies a plan hash including candidate graph/profile, media byte hashes,
  matrix/provenance. Each call writes a SUBMISSION_PENDING journal/correlation BEFORE
  awaiting the probe. Interrupted/submitted/uncertain rows halt the entire resume until
  reconciled; completed cells are skipped, unfinished terminal cells warm up anew.
- Added 12 benchmark and11 probe boundary tests; updated offline mode set to include
  the newly added last-frame manifest. Added five-client HTTP acceptance2tests separately.

## Actual verification

Latest command, from `backend`, with command-local PYTHONPYCACHEPREFIX in ignored workspace:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_h3_benchmark.py tests/unit/test_h3_probe_evidence.py tests/integration/test_h3_probe.py tests/integration/test_five_editor_login.py -q -p no:cacheprovider
.\.venv\Scripts\ruff.exe check --no-cache apps/api/scripts/h3_probe.py apps/api/management/benchmark_h3.py apps/api/app/integrations/comfy_adapter.py tests/unit/test_h3_benchmark.py tests/unit/test_h3_probe_evidence.py tests/integration/test_h3_probe.py tests/integration/test_five_editor_login.py
.\.venv\Scripts\ruff.exe format --check --no-cache apps/api/scripts/h3_probe.py apps/api/management/benchmark_h3.py apps/api/app/integrations/comfy_adapter.py tests/unit/test_h3_benchmark.py tests/unit/test_h3_probe_evidence.py tests/integration/test_h3_probe.py tests/integration/test_five_editor_login.py
```

**27 passed in4.81s; Ruff check PASS; 7files format PASS.**
TDD: original probe8fail->8pass; benchmark10fail->10pass; subsequent concrete safety
regressions (uncertain continuation, interrupted journal, static required patch,
busy queue, overridden settings hash) each failed before their fix and now pass.
Runtime/model/inspection boundaries are faked in orchestration tests; offline graphs
are real repository files. The HTTP acceptance uses actual ASGI routes and isolated
SQLite. No target generation, actual benchmark timings, browser ingress or GPU PASS
is inferred. Full backend verification is owned by the active contract implementer.

## Remaining work in this phase

- Actual CLI checkpoint2026-10-06: five repository graphs STATIC_VALIDATED at16:9,
  actual width/height null, no submissions;216matrix cells explicitly NOT_RUN and
  zero warm verified. CLI probe exit0; nonexecute matrix exit1 as designed. Output
  `workspace/runtime-tool-verification/offline-probe-20261006-a/` and
  `workspace/runtime-tool-verification/offline-matrix-20261006-a.json`; command guide
  `docs/h3-poc/runtime-tooling.md`. This evidence is static, not executed target PASS.

- FIRST fix/re-review the two findings in `tasks/archive/reports/production-probe-media-review.md`.
  `probe_media.py` remains unapproved. A VERIFIED probe now specifically requires
  actual `decoded_video_frames == expected_frames` and positive `decoded_audio_frames`
  in addition to matched measured metadata. The current helper cannot supply them,
  so an actual successful runtime job remains EXECUTED/unverified until that fix.
- Capture actual target schemas/export, runtime/model/node hashes and media. Core
  candidate/profile contracts still receive independent review separately.
- Final probe/runtime worker should test actual downloaded CPU media through this
  orchestrator after helper repairs, support trustworthy target execution timing where
  available, and update run instructions/evidence. Do not rerun GPU jobs here unless
  runtime is available and the intended namespace is verified.

**Checkpoint verdict: scoped checks PASS; slice review pending; full phase NOT_READY.**

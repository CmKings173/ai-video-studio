# Production acceptance evidence matrix

Specification: `docs/ai-video-studio-production-generation-plan.md`, master prompt A-S.
Workspace: `D:/project/ai-video-studio`; branch `codex/production-safety-fixes`;
HEAD `01b740d8be4d58d3bd15bc46dd9842afb48af8bb`. Changes remain uncommitted.

This is a live execution ledger, not a release approval. `PENDING` means feasible work
is still in progress. `NOT_RUN` means the named external verification was unavailable.
CPU/synthetic-fixture results never establish H3 execution, production database, or UAT PASS.

## Required contracts and evidence

| Requirement | Code/local verification | External/release verification | Evidence owner |
| --- | --- | --- | --- |
| A: T2V, first, last, first+last, references | Implemented/shared-gate tests PASS; independent core review PENDING | NOT_RUN: target ComfyUI/GPU, including last-frame visual result | generation contracts, H3 research/runtime |
| B: ordered full references and official envelope | Counts/stream durations/scope/actual slot capacity tests PASS; unsupported exports disabled; core review PENDING | NOT_RUN: image/video/audio+visual/mixed target execution | generation contracts, runtime |
| C: Draft/Standard/High profiles | Hash-bound contracts/resolvers implemented and tested; review PENDING; candidate values unmeasured | NOT_RUN: creative review and warmed profile benchmark | generation contracts, runtime |
| D: truthful requested/resolved/measured properties | Immutable v2/legacy snapshots and output declarations separate;338-test checkpoint PASS, review PENDING | NOT_RUN: measured target output matches each approved combination | generation contracts, runtime |
| E: exact approved prompt and stale rejection | Backend exact freeze/stale rejection and frontend helper/component/mutation tests PASS; independent reviews PENDING | Actual unavailable-state browser PASS; qualified prompt browser execution NOT_RUN | generation contracts, frontend |
| F: technical retry/regenerate/variation | Frozen-parent semantics/explicit overrides/uncertain reconciliation tests PASS; parent-only UI requests PASS; review PENDING | NOT_RUN: target restart/cancel/retry recovery | generation contracts, runtime/UAT |
| G: per-scene generation config | Typed config, paired revisions and semantic batch implemented/tests PASS, review PENDING | Actual IAB+API+SQLite HIGH/16:9/FIXED42 save/reopen PASS; other scenes unchanged | generation contracts, frontend |
| H: semantic Generate All | Phase0 replay approved; contracts and deterministic ordered per-scene summary tests PASS | Independent updated core/UI reviews PENDING; real mixed H3 batch NOT_RUN | Phase0 reports, contracts/frontend |
| I: persistent runtime, conservative concurrency, timings | PENDING: honest probe/benchmark tooling; scheduler admission remains one prompt | NOT_RUN: cold/warm/switching/VRAM/queue evidence, >=3 measured warmed repeats | runtime |
| J: release qualification gate | Admin/seed/create/capabilities/fresh dispatch integration and original P2 regression fixes implemented/tested; review PENDING | NOT_RUN: genuine model/runtime/node/workflow/output evidence | generation contracts, runtime |
| K: selected enhancement, immutable lineage | PENDING: truthful unavailable-provider capability/API and lineage/retention | NOT_RUN: real configured H3 2K/local enhancement provider | delivery |
| L: seven delivery presets, ratio-safe MP4 | Isolated helper 26 unit +2 CPU tests PASS; anamorphic review finding OPEN | PENDING: integrated real CPU codec/FPS/audio/faststart/preset outputs | delivery |
| M: immutable CUT/CROSSFADE/audio/background | Current CPU baseline executed, mute has no audio; integrated fix/metadata/scope PENDING | NOT_RUN: MinIO-backed final download/restart | delivery, UAT |
| N: capability-driven editor and history actions | Generation editor/history/batch checkpoint implemented;53tests/typecheck/lint/build PASS; review PENDING | Empty-capability/editor save/reopen actual browser PASS; delivery/history target flows pending | frontend |
| O: arbitrary Product.context preservation | Phase0 PASS: regressions, actual browser update/clear and read-only SQLite proof | No broader UAT implied | Phase0 browser/review reports |
| P: backup/restore paths, mounts, manifests | Phase0 scoped PASS; actual script/daemon-free Compose env checks | NOT_RUN: PostgreSQL+MinIO isolated restore/download/reconciliation | Phase0 report, UAT |
| Q: trusted identity and five-editor login isolation | Phase0 regressions PASS; five-client real HTTP acceptance 2 PASS | NOT_RUN: deployed ingress/Next/browser five-editor UAT | Phase0, five-editor report |
| R: deterministic Yarn frontend dependencies | Phase0 frozen offline install/typecheck/lint/production build PASS | PENDING: final frontend build after remaining edits; Docker build NOT_RUN | Phase0, frontend |
| S: tests, migrations, API alignment, final review | Phase0 independent reviews closed; subsequent slices/reviews PENDING | NOT_RUN: live PostgreSQL/MinIO/GPU deployment checks | all slices, final qualification |

## Completed checkpoint evidence

- Phase0 independent review and fix rounds1-2: `tasks/archive/reports/production-phase0-report.md`,
  `tasks/archive/reports/production-phase0-review.md`, `tasks/archive/reports/production-phase0-rereview-1.md`,
  `tasks/archive/reports/production-phase0-rereview-2.md`.
- Latest full backend contract checkpoint: **338 passed, 11 skipped**;
  ten PostgreSQL cases need a disposable database, one UID boundary needs POSIX.
  Passing isolated helper tests do not close their independently reproduced findings.
- Latest generation frontend checkpoint: **53 passed**, typecheck/lint PASS, preserving production build
  PASS. Preserve the user's pre-existing `frontend/next-env.d.ts` bytes.
- Browser Product edit/clear: `tasks/archive/reports/production-phase0-browser-report.md`; two actual
  screenshots, persisted unrelated nested keys and no browser error log.
- Generation editor actual scene-config save/reopen: `tasks/archive/reports/production-frontend-controller-report.md`;
  IAB real API and read-only SQLite proof; scene/video revision1->2, other scenes unchanged.
  Generation contract report `tasks/archive/reports/production-generation-contracts-report.md`; pending independent review.
- Five-client HTTP auth checkpoint: **2 passed in 4.12s**, command below. Five distinct
  users/cookies/CSRF tokens; one throttled editor leaves other logins and sessions usable;
  logout revokes only that session; failures from five trusted IPs still throttle a
  shared attacked identity. ASGI HTTP + isolated SQLite; ingress/Next is outside scope.
- H3 upstream research and runtime inventory: `tasks/archive/reports/h3-qualification-report.md` and
  `tasks/h3-reference-artifacts/`. Runtime unreachable from observed host vantage;
  Docker Linux daemon unavailable; no generated target media or executed PoC.

```powershell
Set-Location D:\project\ai-video-studio\backend
$env:PYTHONPYCACHEPREFIX='D:\project\ai-video-studio\workspace\test-pycache'
.\.venv\Scripts\python.exe -m pytest tests/integration/test_five_editor_login.py -q -p no:cacheprovider
.\.venv\Scripts\ruff.exe check --no-cache tests/integration/test_five_editor_login.py
.\.venv\Scripts\ruff.exe format --check --no-cache tests/integration/test_five_editor_login.py
```

## Open local review findings

- Delivery source SAR/display-aspect distortion: `tasks/archive/reports/production-delivery-helper-review.md`;
  first fix in delivery brief, real anamorphic CPU regression required.
- Probe accepts unreadable payload / audio masks short video: `tasks/archive/reports/production-probe-media-review.md`;
  first fixes in runtime brief, real decode/count/span regressions required.
- Generation gate's prior malformed-map/boolean findings have implementer narrow fixes;
  close only after the generation-contract independent review.
- Runtime controller independent review REQUEST CHANGES with five orchestration findings:
  submit deadline uncertainty, global resume scan, probe CLI halt, graph-bound steps and
  reference workload categories. Maxwell dedicated fix task owns these plus the two decoder findings.
- Frontend generation checkpoint and backend core independent reviews active; no approval inferred
  from their implementer test results. Delivery/enhancement integration still pending.

## Release verdict

**NOT_READY while local implementation and review gates are pending.** Completion of
feasible local work may support READY_FOR_TARGET_GPU_POC; PRODUCTION_READY additionally
requires actual qualified target generation/benchmarks, PostgreSQL/MinIO restore and
five-editor deployment UAT evidence. Do not infer any of those from fixture tests.

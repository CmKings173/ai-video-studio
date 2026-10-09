# Production reliability report — 2026-10-07

## A. Repository state

- Workspace: `D:\project\ai-video-studio`, verified with `Codex with ChatGPT · ai-video-studio` live workspace_info/git_status.
- Branch: `codex/production-safety-fixes`.
- HEAD: `01b740d8be4d58d3bd15bc46dd9842afb48af8bb`.
- Staged: 0; unstaged: 66; untracked entries: 204; conflicted: 0. Counts include historical work and this report; directory entries are not individual file counts.
- No commit, push, reset, checkout, clean or stash. Existing dirty/untracked work preserved.

## B. Findings and implementation

All paths below are relative to the workspace above.

| Issue / severity | Root cause | Files changed | Fix and validation | Result |
|---|---|---|---|---|
| Asset validation in HTTP / P1 | Complete read/hash/inspect/promote held the request; request dependency could commit after response send | `backend/apps/api/app/api/assets.py`, `services/asset_service.py`, `workers/asset_validation.py`, `workers/reconciliation.py`, `services/asset_retention.py`, `infra/compose.yaml` | Transactional enqueue and explicit commit before HTTP 202, uploader ownership, durable JSON envelope, claim/heartbeat/recovery, retention/reconciliation fencing; worker/HTTP/PG tests including send-time publication and commit failure | PASS |
| Upload/output limit coupling / P1 | Comfy/storage/dispatch output limits reused browser upload entitlement; full buffers during validation and verification | `backend/apps/api/app/core/config.py`, `integrations/minio.py`, `integrations/media.py`, `integrations/comfy_adapter.py`, `backend/workers/common.py`, `dispatcher.py`, `director_dispatcher.py`, `assembler.py`, `reconciliation.py`, `.env.example` | Separate positive validated limits, streaming upload validation/checksum/assembly materialization, final output size check before read, bounded Comfy bytes path; streaming limit tests and real MinIO smoke | PASS |
| Scheduler fairness / P1 | Director-first priority and standalone prior-video preference could bypass global age; unlocked routing could select younger work | `backend/workers/common.py`, `dispatcher.py`, `director_dispatcher.py`, scheduler/race tests | Oldest admissible heads of both queues compared under advisory lock 971031 and SKIP LOCKED; routing and claims revalidate; active recovery first; no in-memory toggle; locked-head/replica PG regressions | PASS |
| Sidebar / P2 | Small viewport changed sidebar into a top block | `frontend/components/studio-shell.tsx`, `frontend/app/globals.css` | 248px expanded, 64px left rail at <=1024, 100dvh, hidden labels, title/aria/current/focus/avatar/logout and admin permission; responsive tests | PASS, source/CSS |
| Responsive forms / P2 | Fixed two-column forms did not account for available panel width | Video/assembly pages, upload page, globals.css | Panel-aware form collapse, wrapping headers, constrained main; source tests | PASS, source/CSS |
| Tables / P2 | Wide tables could expand page viewport | Admin/dashboard/project/product detail pages and globals.css | Independent horizontal scroll wrappers, preserved readable columns | PASS, source/CSS |
| Tabs / P2 | Long labels overflowed narrow views | `frontend/components/ui/tabs.tsx` | No-wrap horizontal scrolling, shrink-0 triggers, keyboard behavior retained | PASS |
| Drag/drop and upload limit / P2 | Drop handlers absent and displayed limit incorrect | `frontend/app/assets/upload/page.tsx`, `frontend/lib/api/assets.ts`, API policy endpoint | Shared picker/drop single-file MIME/size validation, keyboard zone, drag state, actual policy in MiB; bounded TanStack polling plus abortable retry/unmount | PASS |
| Aggregate audio scope / P2 | Audio options read single-scene capability | `frontend/components/generation/generation-editor.tsx` | Audio availability uses exact qualified settingsCap; aggregate-only/single-only/standalone regressions | PASS |
| Face detector API / P2 | Arbitrary filenames advertised unsupported customization | `backend/apps/api/app/schemas/api.py`, detector/config graph tests, regenerated `docs/openapi.yaml` | Literal face_yolov8m.pt product option, per-detector digest guard retained; unsupported names rejected even while disabled | PASS |

Independent review also found and resolved: canonical recovery when replay changes staging after promotion, Pillow cancellation/temp-file ownership, PUT transfer deadline, retry request timeout, long brand-filter sizing, and missing select label associations. Final scoped reviewers reported no remaining P1/P2.

### Memory boundary

Browser bytes still go directly to MinIO. Validation downloads in <=1 MiB chunks into a bounded temporary file, hashes the exact GET bytes, checks disk headroom, inspects the path and conditionally streams the same validated file into an immutable destination. Ordinary S3 CopyObject was not used because it does not provide the required portable destination non-overwrite condition. Cancellation drains transfer/inspection ownership before temporary cleanup; PUT uses a total deadline and cancellation-aware reader.

Generated and final video paths remain bounded bytes paths. Comfy bytearray-to-bytes conversion briefly holds about 2x configured output size plus allocator/chunk/HTTP/SDK overhead (about 1 GiB of payload at the default 500 MiB limit). This is a source-derived buffer estimate, not measured RSS. Persisted checksum verification and assembly input materialization now stream. No unbounded-memory claim is made; no separate final-video limit was necessary.

## C. Asset worker state machine

Before: `PENDING_UPLOAD -> VALIDATING` inside HTTP -> read/hash/inspect/promote -> READY/FAILED.

After: `PENDING_UPLOAD -> VALIDATING/QUEUED` committed before HTTP202 -> worker `VALIDATING/RUNNING` -> READY or FAILED. Transient failures return to QUEUED with persisted exponential backoff; after three default attempts they fail. Permanent invalid media/checksum/type/size/canonical collision fail deterministically. FAILED requires explicit retry_validation; expected and already-verified checksum identity survive retry. Duplicate pending/running completion does not enqueue extra work; conflicting checksum intent is rejected; READY replays current state.

Claims use existing OUTPUT_WRITE ownership, row locks, SKIP LOCKED and heartbeat. A stale RUNNING claim can be replaced; old owners cannot publish READY or delete replacement-owned canonical data. Validated checksum is persisted before promotion; recovery first adopts matching canonical bytes when available. Deletion-owned late promotions reopen durable retention cleanup. Request rollback publishes no queue job. No new generic task framework or Redis/Celery was added.

Failure coverage includes missing staging, unavailable MinIO, checksum/type/size mismatch, corrupt media, missing inspection tooling, promotion errors, lease loss, crash/reclaim/exhaustion, duplicate complete, READY replay, explicit FAILED retry and immutable collision. Dedicated PostgreSQL tests cover actual contention, locked heads and replica admission.

## D. Database

`APPLICATION_DB_MIGRATION: NOT_REQUIRED`.

- Local identity: localhost:5432 / studio; container alias postgres:5432 / studio.
- Alembic current/heads and post-start database revision: b5d9f1a3c7e2.
- Existing Asset media_metadata and operation_claim_id/type/claimed_at columns verified in the real application DB.
- No new migration or application backup needed for this round. Compose's migration service ran the existing head successfully as a no-op. No application DB was dropped or recreated. PostgreSQL test databases were newly isolated.

## E. Tests and builds

| Command / lane | Result |
|---|---|
| backend .venv Python `-m pytest -q` with isolated POSTGRES_TEST_DATABASE_URL | PASS: 963 passed, 1 skipped, 219.03s |
| backend `.venv` Python `-m pytest tests/postgres -q`, separate fresh DB | PASS: 23 passed, 20.16s |
| Backend targeted streaming/lifecycle, worker/HTTP, qualification and fairness tests | PASS; final reviewer focused slice 47 passed, production thread/reader regressions included |
| backend `.venv` Python `-m ruff check .` | PASS |
| backend `.venv` Python `-m compileall -q apps workers tests` | PASS |
| `git diff --check` | PASS |
| frontend `npm test` | PASS: 152/152 |
| frontend `npm run typecheck` | PASS |
| frontend `npm run lint` | PASS |
| frontend `npm run build` | PASS |
| final frontend `docker build --no-cache` | PASS: ai-video-studio-frontend:reliability-final-20261007 |
| final backend Compose build/start API + workers | PASS |

The single backend skip is the Linux UID/mode boundary on this Windows host. Earlier intermediate failures (OpenAPI regeneration, unsupported detector fixture, old fairness-policy assertion and signal argument assertion) were corrected; the final suite above passed.

Evidence: `.artifacts/final-ui-media-closeout/reliability-backend-verified.log`, `.artifacts/final-ui-media-closeout/reliability-postgres.log`, `.artifacts/final-ui-media-closeout/reliability-docker-frontend-final.log`, `.artifacts/final-ui-media-closeout/reliability-runtime-final.log`. Final frontend commands additionally verified by the scoped implementer; independent rereview ran 38 focused frontend tests.

## F. Runtime and UI acceptance

- API startup: PASS, Application startup complete; container healthy and live endpoint HTTP200.
- AssetValidationWorker startup: PASS, asset_validation_worker_started; final container running.
- PostgreSQL and MinIO: healthy. Actual local MinIO streaming GET + conditional PUT + identical replay + byte checksum verification: PASS with 75-byte PNG; temporary objects removed.
- Frontend production/Docker builds: PASS.
- END_TO_END_LARGE_MEDIA: NOT_RUN. Small storage smoke is not a large upload or GPU video run.
- GPU runtime qualification: NOT_RUN; no qualification/advertising flags or evidence fabricated. Upstream pin unchanged: a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb.

| Width | Sidebar | Content behavior | Evidence |
|---:|---|---|---|
| 1440 | Expanded ~248px | Multi-column when panel width permits | SOURCE/CSS VERIFIED |
| 1280 | Expanded ~248px | Multi-column when panel width permits | SOURCE/CSS VERIFIED |
| 1024 | Left rail ~64px | Narrow panels collapse; no top sidebar block | SOURCE/CSS VERIFIED |
| 768 | Left rail ~64px | One-column forms, independent tabs/tables scroll | SOURCE/CSS VERIFIED |
| 480 | Left rail ~64px | Main constrained, forms collapse, filters wrap | SOURCE/CSS VERIFIED |

BROWSER VERIFIED: NOT_RUN. No claim of measured geometry or pixel-perfect overflow verification.

## G. Remaining issues

- BLOCKER: none identified in this scoped round after independent rereview and final validation.
- DEFERRED: real browser geometry and end-to-end large media; broader promotion/takeover stress remains additional coverage. Generated bytes buffering is bounded but still significant as documented above.
- FEATURE GAP: SelfLift, Semantic Bridge, Segmented LoRA and target GPU runtime qualification remain outside scope.
- RELEASE RISK: Untracked production source will not exist when cloning/checking out another repository. Important entries include `backend/apps/api/app/providers/`, `backend/workers/asset_validation.py`, `backend/workers/director_dispatcher.py`, the four existing untracked migration files (a4c8e0f2b6d1, b5d9f1a3c7e2, e2b6c8d0f4a1, f3a7c9e1d2b4), Director workflow templates, new worker/HTTP/PG/fairness/streaming tests and `frontend/tests/`. This is a release risk, not evidence of a runtime failure. No commit/push performed.

## H. Verdict

`PRODUCTION_RELIABILITY_ROUND_PASS`

This verdict covers the requested reliability corrections and local validations. It does not certify target GPU generation or large-media end-to-end execution.

# AI Video Studio — Final Reliability, UI & Repository Cleanup

Date: 2026-10-08
Workspace: D:\project\ai-video-studio
Branch: codex/production-safety-fixes
HEAD: 01b740d8be4d58d3bd15bc46dd9842afb48af8bb

## Verdicts

- FUNCTIONAL_CLOSEOUT: NOT_VERIFIED
- UI_BROWSER: NOT_RUN
- CLEANUP: FAIL
- RELEASE_TRACKING: BLOCKED
- GPU_RUNTIME: NOT_RUN

Backend and frontend source/test gates passed. The closeout remains unverified because the required real-browser pass and independent rereview did not run. Cleanup is incomplete because the correction-suite files were not semantically consolidated; no test was deleted on a title-only heuristic.

## Repository and safety

At final inventory capture, Git reported 0 staged paths, 76 unstaged tracked paths, 283 untracked paths, and 0 conflicts. The exact path lists are in tasks/evidence/final-ui-media-closeout-git-manifest.json. Of the untracked paths, 182 are source, migration, workflow, test, UI, infrastructure, or documentation files that must be reviewed and tracked before release. No file was staged, committed, pushed, reset, stashed, or cleaned.

The working tree was treated as source of truth. No application DB or MinIO records were modified. One test command was initially launched from the repository root and produced async-plugin errors because it missed backend/pyproject.toml. The full suite was rerun from backend/ with the configured asyncio auto mode and passed; the incorrect invocation is not counted as a product failure.

## Confirmed findings and fixes

- Reference dispatch previously risked buffering complete media objects in memory. The production route now downloads each READY reference through AssetStore.download_to_path, verifies size and SHA-256 against the frozen input, then streams the local file through ComfyAdapter.upload_file. Temporary files are scoped to a staging directory and cleaned after transfer. Legacy get_bytes paths remain only for injected compatibility stores.
- Pending asset reconciliation now downloads into a bounded staging file before validation. Its compatibility-store fallback uses get_bytes(key) for old test doubles and rejects data above the configured bound before writing.
- Generated and final outputs are persisted from files and verified through streamed object checksums in production. Remaining get_bytes calls in assembler and output-verification code sit behind compatibility-store branches; the real AssetStore uses file streaming/checksum APIs. No output.read_bytes path was found in the reviewed worker paths.
- The reconciler now checks actual object-body bytes rather than trusting matching checksum metadata. Regression coverage includes same-size corrupt bodies with stale metadata.
- Assembly reserves space for all bounded inputs, output, and the configured free-space reserve before downloading/combining. It checks actual sizes and uses streaming production downloads.
- Director derivative creation validates the plan before adding/flushing generation rows; GenerationAsset associations are added after the gate. The rejection regression now includes a bound READY reference image and asserts no generation or association rows persist.
- Upload roles follow MIME and scope, and server policy is fail-closed. fallbackUploadPolicy has no remaining frontend definition or consumer.
- Project/product/brand, asset, audio, admin-user, and admin-workflow selectors use paginated queries. Existing selected IDs are preserved when outside the current page.
- Video download actions reserve the browser tab synchronously, close it on request failure, navigate directly to the presigned URL, and guard duplicate clicks. Unit behavior tests pass; popup-blocker behavior was not verified in a real browser.
- SSE fallback synchronizes active work while disconnected, stops polling after terminal state or reconnection, resynchronizes on visibility return, and cleans up timers/EventSource on unmount.

## Streaming and persistence path matrix

| Path | Production behavior | Evidence |
|---|---|---|
| MinIO reference to ComfyUI | Bounded file download, frozen size/hash validation, streamed multipart upload | backend/tests/unit/test_storage_streaming_limits.py; backend/tests/unit/test_dispatcher_reference_streaming.py |
| Pending asset repair | File-based staging and validation | backend/workers/reconciliation.py; asset lifecycle tests |
| ComfyUI output | Stream to bounded temporary file | backend/apps/api/app/integrations/comfy_adapter.py |
| Generated/final persistence | Immutable file upload followed by stored-body verification | backend/workers/common.py; storage streaming tests |
| Assembly clips/audio | Bounded streaming to staging; aggregate disk reserve | backend/workers/assembler.py; backend/tests/integration/test_assembly_manifest.py |
| Compatibility fakes | May return bytes, with explicit size checks | Reconciliation, assembler, and output-store fallback branches |

## Director-only workflow inventory and cleanup

The active registry has 12 graphs: six single-scene and six aggregate across T2V, I2V, FL2V, R2V, V2V, and RV2V. A read-only DB query found 12 workflow_registry rows, all Director rows; six are aggregate and six single-scene. No row is enabled, and no enabled non-Director row exists.

The six aggregate_2 drafts had no active registry, builder, probe, or test references. Their current SHA-256 values matched the historical hashes in tasks/archive/reports/director-closeout-retirement-review.md. This cleanup removed exactly those six files from backend/workflows/h3. The target architecture document now states the 12-graph active inventory. The historical retirement evidence remains unchanged.

The three retired legacy graph files were already deleted in the working tree and remain deleted. No legacy workflow fallback was restored. Runtime qualification and advertisement remain disabled.

frontend/lib/api/assets.ts no longer contains fallbackUploadPolicy. The frontend contains 25 test files and 181 passing cases. A static-title scan found no exact duplicate names among 113 literal titles. No tests were deleted because no duplicate behavior was proven. A full semantic consolidation review of the correction-named suites remains outstanding, which is why cleanup is marked FAIL.

The tasks root is organized under archive/, evidence/, and h3-reference-artifacts/, plus this single summary report. Historical reports and GPU/source evidence were retained. The exact current Git inventory and removed graph hashes are recorded in tasks/evidence/final-ui-media-closeout-git-manifest.json.

## UI review and browser evidence

Source and behavior-test coverage includes:

- Authentication, navigation, dashboard, project/product lists and details.
- Asset upload policy, role/scope/MIME behavior, retry and download recovery.
- Video create/list filters, workspace/storyboard, generation editor, accepted prompts, freshness and history.
- Generate All, Director modes and capability gating for Refine, Face Refine, Motion Context, continuity, and audio.
- Assembly audio scope, missing/stale clip gates, cancellation, final-version errors and downloads.
- Admin status/data loading and pagination, shared controls, keyboard labels, responsive wrapping, and SSE recovery.

Frontend behavior tests include page-two selection, upload retries, download failures/duplicate clicks, editor stale-response/freshness cases, aggregate actions, assembly cancellation, and SSE disconnect/reconnect/visibility/terminal cleanup. These are source/component tests, not browser verification.

Source-level responsive fixtures cover 1440, 1280, 1024, 768, and 480 px widths. They report the expected 248 px expanded rail / 64 px compact rail and constrained content widths. 360 px and actual browser measurements were not run. No screenshots were produced. The CUA browser kernel exited with the Windows sandbox helper error, and there was no frontend container to inspect. Therefore popup blocking, native picker, drag/drop, real focus behavior, network errors, and visual overflow remain unverified in a browser.

## Verification results

- Backend full suite, run from backend/: 1,014 passed, 27 skipped in 185.15s.
- Backend PostgreSQL lane: 24 skipped because POSTGRES_TEST_DATABASE_URL is unset. Two PostgreSQL migration checks were skipped in the full run for the same reason; one additional POSIX-only test skipped on Windows. No disposable PostgreSQL test URL was available.
- Post-cleanup Director workflow/aggregate tests: 83 passed in 8.41s.
- Frontend: npm test — 181 passed, 0 failed.
- Frontend: npm run typecheck — passed.
- Frontend: npm run lint — passed.
- Frontend: npm run build — passed.
- Backend: Ruff — all checks passed.
- Backend: compileall — passed.
- git diff --check — exit 0; Git emitted only line-ending normalization warnings.
- Alembic head: b5d9f1a3c7e2. Read-only DB alembic_version matched that head.
- Read-only API live health: HTTP 200. Readiness: HTTP 503, degraded; PostgreSQL and MinIO were healthy, ComfyUI was unavailable. API, PostgreSQL, MinIO, and workers were running.
- No GPU qualification was run. No ComfyUI generation was submitted.

## Independent rereview and remaining gates

A subagent rereview was requested after the final fixes, but the subagent tool returned a usage-limit error even after the account change. The local self-review and passing tests do not count as independent rereview. No independent reviewer verdict is claimed.

The functional closeout cannot be marked PASS until a real-browser run covers the requested viewports and user flows, the correction-suite cleanup is reviewed semantically, and an independent reviewer confirms the streaming, recovery, UI, and cleanup changes. PostgreSQL concurrency verification also remains environment-gated. GPU runtime qualification remains NOT_RUN.

The release remains blocked because required source/tests/migrations/workflows/docs are still untracked. The manifest captures all current paths; no staging was performed as required.

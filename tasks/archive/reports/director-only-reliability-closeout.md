# Director-only reliability closeout

Date: 2026-10-08 (Asia/Bangkok)\
Repository: `D:\project\ai-video-studio`\
Branch: `codex/production-safety-fixes`\
HEAD: `01b740d8be4d58d3bd15bc46dd9842afb48af8bb`\
Verdict: **DIRECTOR_ONLY_RELIABILITY_CLOSEOUT_FAIL**

## A. Repository

The requested work was performed in the existing dirty checkout. No reset, stash, clean, stage, commit, or push was run. At the latest inventory, 71 tracked paths were modified, 259 Git untracked entries represented 353 untracked files, and there were no staged paths or merge conflicts. The checkout's base HEAD is older than the source changes under review; source hashes were compared directly with the deployed API image.

## B. Legacy workflow inventory

The exact retired codes were `H3_T2V_STANDARD`, `H3_I2V_STANDARD`, `H3_FIRST_LAST_STANDARD`, `H3_R2V_1_IMAGE_REFERENCE`, and `H3_LAST_FRAME_STANDARD`. The Director registry retains 12 workflows: six single-scene and six aggregate. Legacy selection, creation, dispatch, retry/recovery, and collection paths are guarded or removed. New/recovered jobs validate the frozen execution against the current enabled Director record before external submission or collection.

## C. Historical DB dependency audit

The read-only audit recorded 39 SQL probes and 35 database-free behavior checks. Before cleanup, all five legacy rows had zero inbound foreign-key and JSON-value dependencies; the relevant history tables were empty. `scene_generations.workflow_id` and `director_runs.workflow_id` use restrictive foreign keys. The DB backup was written before cleanup to `backups/director-closeout-original-20261008.sql` (144,339 bytes; SHA-256 `702CF19E79249D081415982905F6F82848FCD6B1C467D0A4EEA8F28CC1BF41E4`).

## D. Removed workflows/files

The transactional retirement command rechecked dependencies on the original DB and deleted exactly the five unreferenced legacy registry rows. It preserved the 12 Director rows. The three obsolete tracked graph files removed from the source tree are the legacy T2V, I2V, and one-reference R2V API graphs. All 18 Director graphs remain, including the six `*_aggregate_2` graphs. The active registry pins upstream source commit `a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb`.

## E. Director-only runtime path

Workflow selection, capability reporting, enablement, creation, derivative generation, dispatch, and aggregate execution use Director graphs. The dispatcher validates registry identity and the frozen contract before submit/recovery; legacy/custom graphs cannot be staged or collected. Retired jobs with an existing external prompt are held for reconciliation with their prompt/client correlation retained. The aggregate collector now checks the same authority before changing status, contacting ComfyUI, or downloading artifacts.

## F. Generated output streaming

Comfy outputs stream in bounded chunks to unique staging files. The transfer checks limits, byte count, checksum, and response length before the media probe and persistence call. Canonical persistence hashes the actual file in bounded chunks, writes immutably, verifies stored bytes and size, maintains the durable claim/heartbeat, and publishes READY only after verification. Replays of READY verify the actual object. Claim loss and cancellation cleanup are fenced to the exact owned claim/key.

The independent streaming review found repeated-cancellation gaps in MinIO transfers and FFmpeg's final copy. Both were fixed with owned, shielded tasks drained before temporary-file cleanup. Regressions hold real files open, cancel twice, and assert the worker cannot return before the thread releases the file.

## G. Assembly output streaming

Final assembly passes the output path to canonical file persistence; it does not call `output.read_bytes()`. Scene and audio inputs continue to download to bounded files. A regression rejects `Path.read_bytes()` for the final output and verifies file-based persistence, digest, READY state, and temporary-directory cleanup.

## H. Asset queue

Asset validation remains a durable PostgreSQL queue. The worker claims jobs with database locking, streams and probes data, and promotes validated objects immutably. PostgreSQL concurrency tests passed in an isolated test database. User downloads remain presigned MinIO URLs.

## I. Generation queue

Generation admission remains PostgreSQL-backed. The standalone dispatcher excludes aggregate members; Director aggregate work continues through its dedicated dispatcher and frozen contract. No Redis or LISTEN/NOTIFY queue change was introduced. Recovery preserves the external prompt/client correlation and does not submit a duplicate after retirement validation fails.

## J. Scheduler fairness

The oldest-pending generation scheduler and row-lock behavior were exercised by the PostgreSQL lane. No scheduler redesign was made. The separate PostgreSQL suite passed 24 tests; the final full backend run also used a fresh isolated PostgreSQL database.

## K. Responsive/UI corrections

Upload fields and panels size to available content width. Upload-policy loading/error states fail closed, preserve retry, and share validation between file picker, drag/drop, and submit. The active fake 500 MiB fallback is removed. The exact face-detector literal and logout alert placement are covered by frontend checks. The videos list now wraps its action row and constrains long unbroken titles.

Fresh Edge browser validation after the UI fix measured 89 page states at 1440, 1280, 1024, 768, and 480 pixels. All 135 assertions passed with zero page exceptions, zero failed checks, and no blocked external requests. The test included long names, selected upload filenames, local table scrolling, dialogs, upload-policy failures/retry, and logout failure. Four console/network error entries were expected fixture responses (pre-login 401, standalone `/assets` 404, and injected policy/logout 503); they were not unhandled page exceptions.

## L. Application DB

The original database Alembic revision and sole head are both `b5d9f1a3c7e2`; the final runtime migration exited 0. The post-cleanup registry contains 12 rows, zero legacy rows, and 12 Director rows. The DB audit found no reason to migrate or reconstruct the schema. The application database was backed up before the explicitly authorized legacy-row deletion.

## M. Tests

- Full backend suite against isolated PostgreSQL: **1,027 passed, 1 skipped** in 198.77 seconds. The skip requires a POSIX host for a UID/mode boundary.
- Full backend Ruff and `compileall`: passed.
- Frontend tests after the final UI change: **160 passed**; typecheck, lint, and production build passed.
- Streaming/storage/claims/assembly independent affected-test run: **106 passed**. Retirement/recovery/aggregate scoped run after the final guard: **41 passed**.
- Real loopback HTTP media smoke: downloaded a 6,340,951-byte video in 97 chunks, verified its checksum and ffprobe metadata, persisted it to MinIO, and verified readback. Two-clip FFmpeg assembly produced a 4,222,331-byte valid output; immutable replay and conflicting bytes behaved correctly. Both exact smoke objects were deleted and verified absent. Smoke used in-memory SQLite; RSS was not measured.

Evidence is retained in `.artifacts/final-ui-media-closeout/director-closeout-*.log`, `.json`, and `.py` files, `tasks/archive/reports/director-closeout-*.md`, plus the focused frontend logs under `.artifacts/final-ui-media-closeout/`.

## N. Browser validation

The local isolated fixture and fresh headless Edge run passed the five-width/page matrix, including upload and videos list. Screenshots are in `.artifacts/final-ui-media-closeout/director-closeout-browser.png`; raw measurements and expected fixture responses are in `.artifacts/final-ui-media-closeout/director-closeout-browser.json`. No personal browser session or ChatGPT cookies were used.

## O. Runtime validation

The rebuilt API image and all four workers (dispatcher, assembler, asset-validator, reconciler) are running. The migration container exited 0. API liveness returned HTTP 200; PostgreSQL and MinIO were healthy. SHA-256 comparisons for dispatcher, Director dispatcher, worker persistence, assembler, MinIO, FFmpeg, Comfy adapter, and workflow registry all matched the deployed API image. Worker logs had no traceback in the inspected tail.

Readiness is **degraded** because ComfyUI is unavailable (`postgres=true`, `minio=true`, `comfyui=false`). No live GPU generation was run. This is recorded separately from the passing HTTP liveness and synthetic-media smoke.

## P. Untracked/release tracking

`tasks/archive/reports/director-closeout-release-inventory.md` and `.json` list the exact untracked paths and ignored cache/build directories. The final inventory reports 62 untracked production source/contract files, 4 migration files, 18 workflow graphs, 96 test files, and 176 reports/logs/probes; it distinguishes 262 collapsed untracked Git entries from 356 individual files. Git status also has 71 modified tracked paths, zero staged paths, and zero conflicts. Required production, migration, graph, and test files remain untracked. This is a release blocker under the requested inventory policy. No Git staging or commit was performed, as required.

## Q. GPU / feature gaps

GPU qualification is **NOT_RUN** because the ComfyUI service was unavailable. SelfLift, SemanticBridge, and Segmented LoRA are recorded as **FEATURE_GAP** pending separate implementation/qualification; they are outside this closeout verdict. No runtime qualification or advertised-production claim is made for unqualified graphs.

## R. Independent rereview

Independent reviewers found and documented the video-list overflow, two repeated-cancellation file-lifetime issues, the existing-prompt recovery bypass, and the aggregate-collection bypass. The cancellation issues were independently rereviewed and accepted after 106 affected tests passed. Recovery validation was independently confirmed fixed with 59 affected tests. The UI geometry fix passed a fresh parent-run Edge matrix after the initial independent browser finding.

The final aggregate-collection guard was added after the independent finding, and its regression plus aggregate suite passed (41 tests); the reviewer continuation failed because `gpt-6.1-sol` was unsupported for this ChatGPT account. The browser reviewer continuation also failed at the usage limit. Therefore a final independent rereview of the last aggregate guard and the UI rebuild is **NOT_VERIFIED**. The parent inspected the guard and the complete backend suite passed, but that does not replace the required independent sign-off.

## S. Verdict

**DIRECTOR_ONLY_RELIABILITY_CLOSEOUT_FAIL**

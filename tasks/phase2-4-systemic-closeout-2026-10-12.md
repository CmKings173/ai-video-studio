# Phase 2–4 Systemic Closeout — 2026-10-12

## Baseline

- Workspace: `D:\project\ai-video-studio`
- Branch: `codex/production-safety-fixes`
- HEAD: `50071aeb6c2ae9b76d8ac0e1e61904ffc94a04b9`
- Initial working tree: 80 tracked files modified plus the pre-existing untracked files recorded by `git status --short`; all retained.
- No commit, push, reset, clean, production database change, MinIO deletion, migration against a live database, or GPU generation is authorized.

## Issue ledger

| ID | Severity | Status | Owner | Evidence / next action |
|---|---|---|---|---|
| S1 | P1 | Fixed and verified | Lead | Import smoke regression was RED (ImportError in `final_version_deletion.py`) then GREEN. All four services now import the canonical `DELETION_HIDDEN_STATUSES`; semantics still come from the model constant. Python AST parse: 261 files, 0 syntax errors. Pytest collection: 2,014 collected, 0 collection errors. |
| S2 | P2 | Fixed and verified | Lead / Mencius | Updated frontend harness and preview contracts. Reproduced absent HIGH option with saved STANDARD canvas; quality choices now remain selectable and resolution stays explicitly qualified. Frontend 532/532, typecheck, lint, production build pass. Independent UI re-review found no further issue. |
| S3 | P2 | Fixed; full rerun pending | Lead / Euclid | Asset scope now rejects conflicting resource ownership; output reads require the canonical generation/final identity or an exact Director artifact link. Real DB regressions cover unscoped conflicting owner and sibling legacy output pointers; old rows are not rewritten. |
| S4 | P2 | Fixed and verified in SQLite fixtures | Lead / Euclid | Canonical output provenance, live references and active work block unsafe cleanup. Admin deletion now uses trusted role, records actor separately from resource owner, and operation visibility is scoped. All four resource kinds have Editor foreign 404-before-write and Admin/owner/outsider coverage. `test_phase4_deletion_safety.py`: 19 passed; independent read-only re-review found no actionable issue. PostgreSQL races remain unverified. |
| S5 | P1 security issue | User-approved correction; final gates pending | Lead / Confucius | User explicitly chose immediate revocation. Protected requests now validate current database role/status/authentication version; SSE revalidates each poll. API mutation and real DB SSE regressions pass; isolated SQLite migration/raw SQL/downgrade passes. Independent review found double increment when explicit revocation and role change coincide; corrected using persisted version floor and regression. PostgreSQL trigger execution remains unverified. |
| S6 | P2 | Scoped code/regressions verified; GPU unverified | Lead / Mencius | Director corrections 30/30; dynamic aggregate module 26/26; lifecycle replay and execution freshness fixtures updated to valid finalized batch persistence and real output ownership/links. Preparation candidates still cannot be persisted. Exact aggregate context remains required. Reviewer is checking fixture changes for weakened assertions. No GPU qualification claimed. |
| S7 | Verification gate | Full backend rerun in progress | Lead | Latest AST: 263 files, 0 errors; collection: 2,036 tests, 0 errors. Ruff passes; OpenAPI regenerated and exact schema contract passes; Compose config with `.env.example` passes; frontend build passes. Original full backend was 134 failed/1,832 passed/62 skipped; several shared fixture groups corrected; a new full run is underway. PostgreSQL/GPU explicitly unverified. |

## Rules and rulings

- Preserve the existing dirty working tree. No agent may edit outside its assigned disjoint paths; review agents are read-only.
- Deletion verification uses isolated fixtures only. Missing provenance fails closed; historical rows are not rewritten.
- Authentication findings that require changing token lifetime/revocation semantics are reported for a policy decision; no JWT architecture change is made implicitly.
- API startup/import is the first critical-path fix because test collection and all API routes depend on it.

## Progress

### Round 1 — reproduction

- Confirmed the API import failure through the actual `main` module and traceback ending at `final_version_deletion.py` importing the undefined `LOGICALLY_HIDDEN_STATUSES` from `deletion_state.py`.
- Current source defines and uses `DELETION_HIDDEN_STATUSES` imported from models.
- Added a focused application-import/router-registration regression; its pre-fix result is recorded after execution below.

### Round 1 — API startup correction and first integration gate

- Regression before fix: `pytest tests/unit/test_phase4_deletion_imports.py -q --tb=short` failed with the exact undefined import.
- Correction: the four deletion services now use `DELETION_HIDDEN_STATUSES`, re-exported from `deletion_state.py` from the model's canonical definition. Focused regression passed: **1 passed**.
- `compileall -q apps workers migrations tests` was attempted; Python could not replace pre-existing `__pycache__` files (PermissionError). No caches or ACLs were changed. Read-only `ast.parse` check passed for **261 Python files, 0 syntax errors**.
- `pytest --collect-only -q`: **2,014 tests collected, 0 collection errors**, exit 0.
- Independent Phase2 audit reported two P2 ownership/provenance inconsistencies for regression work; they remain open until reproduced and fixed.

### Round 2 - focused fixes and independent review

- Admin deletion reproduced RED for all four resource kinds: service signatures rejected `is_admin`. Services now resolve the resource with trusted Admin scope, keep dependency checks tied to `video.created_by`, and preserve `requested_by_id` versus `resource_owner_id` attribution.
- Added real HTTP foreign-resource preview/delete checks before operation insertion, plus Admin/owner/outsider operation reads. Latest exact command: `pytest tests/integration/test_phase4_deletion_safety.py -q --tb=short` -> **19 passed**, exit 0.
- Quality selection RED: the saved STANDARD 480x864 canvas removed the HIGH option. `qualifiedQualityProfiles` now offers independently qualified quality choices; the old canvas remains blocked until a qualified new canvas is selected explicitly. Focused workflow selection tests: **18 passed**.
- Frontend: `npm test` -> **532 passed, 0 failed**; `npm run typecheck`, `npm run lint`, `npm run build` -> exit 0.
- Euclid independently reviewed deletion ownership/attribution/operation visibility and reported no actionable issue after the expanded assertions. Mencius independently reviewed the quality fix and reported no new issue.

### Round 3 - shared backend fixture/contract correction

- First complete backend run: **134 failed, 1,832 passed, 62 skipped**. Failures were grouped; production safety guards were retained.
- HTTP Generate All tests now accept the actual preview hash before submission/replay. Synthetic workflow evidence uses the test video's actual aspect ratio/canvas; explicit derivative workflow overrides supply the required canvas fields.
- Focused green results: Director corrections **30 passed**; systemic adversarial **183 passed**; batch completion replay **7 passed**; business-context replay **4 passed**.
- Lifecycle fixture RED: aggregate preparation candidate persistence rejected before replay assertions. Corrected fixture order: prepare in memory, qualify exact run, finalize every member, bind every batch hash, persist members, persist run. Lifecycle cases now reach their real HTTP replay assertions.
- Freshness output fixtures now store `VIDEO_PARENT` owner provenance and `DIRECTOR_SEGMENT` links, matching actual worker behavior. Partial-prefix regression mutates a fully valid completed run binding and proves source freshness cannot certify incomplete execution-group selection.
- Dynamic aggregate RED also exposed nested sessions on SQLite StaticPool rolling back uncommitted outer fixture writes. Qualification is committed before worker preflight reads; member/run persistence is tested in its own transaction afterward. `pytest tests/integration/test_director_dynamic_aggregate.py -q --tb=short` -> **26 passed**, exit 0.
- Safe Ruff import fixes and formatting were applied only to files with reported errors. AST comparison confirmed no non-import logic change during automatic formatting; long error strings were split without changing values. Ruff now passes.
- `docs/openapi.yaml` regenerated from `app.openapi()` using the exporter serialization. Exact checked-in schema contract: **1 passed**, exit 0. Shell direct writes to inherited files were denied; normal patch application succeeded without ACL changes.
- Compose: `docker compose --env-file .env.example -f infra/compose.yaml config --quiet` -> exit 0. Docker reported denied access to its user config file; no containers or services were started.
- Latest pre-suite gates: **263 Python files parsed**, **2,036 tests collected**, no syntax/collection errors. Full backend rerun is in progress; no final passing claim yet.

### Round 4 - remaining source corrections and stronger regressions

- Baseline canvas resolution previously used the top-level baseline output for every verified canvas combination. Resolution now uses the selected verified combination output; exact optional-settings evidence remains mandatory.
- Capabilities previously parsed evidence canvas dictionaries as lists/tuples. They now use the same typed verified-settings resolver as creation, filtered by requested task/canvas. Valid large native canvases remain advertised without permitting unsupported optional settings.
- Generate All revision conflict is checked before the no-eligible-scenes guard. Qualification and Generate All focused gate: **34 passed**. Independent source review by Plato reported no additional issue.
- Turing strengthened historical replay tests: bound legacy fingerprint versus genuinely unbound cached key; quality-only mutation retains accepted hash; missing hash is a separate test. Contract edges **15 passed** without weakening assertions.
- Confucius reproduced bounded cleanup starvation on mixed shared/safe outputs, then prioritized non-live references in bounded SQL selection with locked rechecks retained. Owned cleanup/deletion/retention gate: **27 passed**. Independent rereview requested.
- Broad backend run before final JWT fixtures: **5 failed, 1,969 passed, 62 skipped** in **588.62 seconds**. Remaining five failures are in `test_phase2_systemic_output_conflict.py`; agent is investigating independently. This is not a final-source passing result.

### Round 5 - explicitly approved immediate JWT revocation

- RED: demoted/disabled/password-changed Admin retained HTTP 200 on admin APIs using old JWT. Three API regressions failed before source correction.
- Added monotonic `User.auth_version`, signed version claim, current account SQL projection on authenticated requests, and account checks on SSE opening and every snapshot poll. Logout still clears revoked cookies with their signed CSRF binding. No sessions table or ownership changes.
- New migration `e5a7c9b1d3f6` backfills version 1 and protects raw SQL account updates with SQLite/PostgreSQL triggers. Tested upgrade, raw role/status/password changes, monotonic floor, explicit preincrement, downgrade and preserved account data on isolated temporary SQLite only: **1 passed**. No application database migration performed.
- First combined auth/JWT/SSE gate after fixture updates: **74 passed**. Real API/database revocation plus real database SSE poll checks: **6 passed** before the explicit double-increment regression was added.
- Independent reviewer Confucius found explicit version increment plus role mutation could increment twice. Corrected ORM hook using persisted database version as floor; final rereview requested. Documentation updated to describe new policy and required fresh login for old token format.
- Read-only syntax gate: **258 files** across apps/migrations/tests; collection: **2,058 tests**, no errors. Workers must also be included in the final syntax gate. Final auth and broad gates pending.

### Round 6 - independent review and final verification

- Auth/JWT/SSE/ownership/OpenAPI combined gate: **101 passed**. Frontend authentication/context/proxy gate: **46 passed**. The unchanged frontend full gate remains **532 passed**, typecheck/lint/build passed.
- Confucius independently rereviewed the persisted-version fix and new migration/regressions: **8 passed**, no remaining actionable auth issue. Requests already authorized before an account change can finish; revocation rejects subsequent requests and terminates SSE on its next snapshot poll. No claim of in-flight transaction cancellation.
- Plato traced the five output-conflict failures to reused deterministic seeded output IDs with different setup bytes. Fresh generation parent fixture preserves immutable-output/no-backfill assertions. RED **5 failed, 17 passed** -> GREEN **22 passed**. Concrete pinned input socket types plus incompatible-link regressions: **11 passed**.
- Turing found remaining cleanup starvation behind a non-live READY output with invalid provenance. Confucius broadened SQL scheduling hints to safe READY outputs, locks candidates before bounded selection, retains all safety rechecks, and prevents completion while missing/locked/unresolved references remain. RED **18 failed** -> GREEN **57 passed, 1 PostgreSQL skipped**. Turing final independent rereview pending.
- Complete backend before the last cleanup change: **1,996 passed, 62 skipped**, exit 0, **474.76 seconds**. Final full run against the last cleanup source has started; its result is still pending.
- Final pre-suite gates including workers: **266 Python files parsed**, **2,089 tests collected**, no syntax/collection errors; full Ruff passed. No dependencies, ACLs, production data, migration state, commits or remote branches changed during this closeout.
- Turing final independent cleanup rereview found no new confirmed defect: **8 direct isolated checks passed**, including per-call bound and late-reference injection. All four resource variants and unresolved-row queries compile for PostgreSQL with `FOR UPDATE OF assets SKIP LOCKED`; compilation does not prove actual PostgreSQL locking. Dedicated PostgreSQL lock regression remains unexecuted.

### Final result

- Final source full backend: `.venv/Scripts/python.exe -m pytest -q --tb=short` -> **2,026 passed, 63 skipped, 0 failed**, **452.90 seconds**, exit 0.
- Final verdict **PARTIALLY_VERIFIED**: scoped corrections and local regression gates pass; dedicated PostgreSQL migration/locking/concurrency and GPU execution remain unverified. No known remaining confirmed P1/P2 in the reviewed correction slices; not a claim that the entire repository has no bugs.
- Vietnamese final report: `docs/phase2-4-systemic-closeout-2026-10-12.md`. Branch/HEAD unchanged; existing Git changes retained; no commit/push, production migrations or destructive data operations performed.

### Follow-up review requested by user

- Review-only follow-up found **two new P2 frontend deletion contract issues**: normal `RETENTION_PENDING` becomes a destructive error/completion helper result; logical deletion cache invalidation/navigation waits for physical `CLEANUP_COMPLETE` and can remain stale while pending/blocked.
- Both are reproduced using the actual TS helpers/status sets and verified against backend outcome/DTO/hidden-state source. No browser mount performed; no production source changed during review.
- Current review verdict **REQUEST_CHANGES**. Report: `docs/phase2-4-review-2026-10-12.md`. This supersedes the earlier “no known remaining confirmed P1/P2” statement for the expanded reviewed scope.
- Three additional reviewer agent calls failed capacity/stream; their new assignments are not counted as completed independent review. Main completed the documented scoped checks directly.

### Follow-up correction - 2026-10-13

- Closed the two frontend deletion-contract P2 findings from `docs/phase2-4-review-2026-10-12.md` without changing the backend deletion contract.
- Normal retention waits (`CLEANUP_PENDING` + `RETENTION_PENDING`) remain pending/warning state; cleanup error/completion is derived from canonical cleanup status rather than the presence of a cleanup reason code.
- Logical deletion now invalidates the supplied resource query keys and calls the page completion callback as soon as the backend reports a hidden deletion state (`WAITING_FOR_DEPENDENCIES`, `LOGICALLY_DELETED`, `CLEANUP_PENDING`, `CLEANUP_FAILED`, `CLEANUP_BLOCKED`, or `CLEANUP_COMPLETE`). Physical cleanup polling continues independently and the callback/invalidation is de-duplicated per operation ID.
- Focused regression: `node --test tests/deletion-control.test.cjs` -> **10 passed, 0 failed**.
- Full frontend verification: `npm test` -> **542 passed, 0 failed**; `npm run typecheck`, `npm run lint`, and `npm run build` -> exit 0.
- No commit/push, production database/storage operation, migration execution, or GPU generation was performed. PostgreSQL/GPU limitations from the prior closeout remain unchanged.

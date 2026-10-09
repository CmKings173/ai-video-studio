# Correction round — 2026-10-07

## Source state

- Workspace: `D:\project\ai-video-studio`.
- Connector: `Codex with ChatGPT · ai-video-studio`; live `workspace_info` and `git_status` succeeded before editing.
- Branch: `codex/production-safety-fixes`.
- HEAD: `01b740d8be4d58d3bd15bc46dd9842afb48af8bb`.
- Starting baseline: 0 staged, 48 unstaged tracked files, 142 untracked paths.
- Final verified state: 0 staged, 52 unstaged tracked files, 148 untracked paths; branch and HEAD unchanged.
- Existing dirty changes preserved; no commit, push, reset, stash, checkout or cleanup.

## Finding 1 — Product/Brand dependency invalidation

Previously, dependency PATCH/archive changed source semantics but did not update dependent Video lifecycle; completion checked Scene revision alone.

`generation_dependency_invalidation.py` owns atomic mutation/invalidation. It compares the same `dependency_semantics()` object used by generation fingerprints. Product brand reassignment is an association change and affects inherited-brand consumers only. Other Product semantics affect every directly linked video. Brand edits resolve both explicit Brand links and inherited Product Brand links. Same-value patches can increment resource revision without incrementing video revision or invalidating it.

Affected video source revisions increment. Videos with generation history or a promoted final become DIRTY; historical generations, selections, finals and final pointers remain intact. Empty drafts retain their lifecycle state while their revision changes.

### Lock protocol

- Product/Brand graph writers acquire one transaction advisory guard before resolving affected videos.
- They lock affected Videos in deterministic ID order before locking/mutating Product or Brand.
- Product brand reassignment uses that same guarded protocol; Video creation takes the guard before reading dependencies/inserting a consumer. VideoPatch cannot rebind dependencies.
- Worker completion locks Video, then Product, then effective Brand through the shared `lock_generation_dependencies()` helper, before Scene/generation processing. Assembly source promotion uses that same dependency helper.
- Workers never acquire the graph writer advisory guard; no dependency mutation holds Product/Brand while waiting for Video. This removes the reviewed Video/Product inversion.

Both workers use canonical source freshness before auto-selection. Aggregate completion evaluates all member sources before selecting any member. Successful stale renders remain COMPLETED with READY assets; cascade DIRTY remains authoritative. `refresh_video()` validates selected freshness, flushes terminal state before querying active jobs, and checks the promoted final manifest before retaining READY. An old job finishing after a new fresh final does not invalidate the new result.

`current_final_video_id` means latest promoted artifact retained for history. Semantic currency requires Video.status == READY. DTO/type comments document it; final-version UI uses the shared `isCurrentFinal()` gate. Cancellation/failure/retry restoration re-checks current source rather than trusting the retained pointer.

Changed files:

- `backend/apps/api/app/api/products.py`, `brands.py`, `videos.py`, `assembly.py`.
- `backend/apps/api/app/services/generation_dependency_invalidation.py`, `generation_freshness.py`.
- `backend/workers/dispatcher.py`, `director_dispatcher.py`, `common.py`, `assembler.py`.
- `backend/apps/api/app/schemas/api.py`, `frontend/lib/api/types.ts` (pointer meaning comments).
- `frontend/lib/utils/current-final.ts`, `frontend/app/videos/[videoId]/final-versions/page.tsx`.
- `backend/tests/integration/test_dependency_invalidation.py`, `test_assembly_dependency_freshness.py`, `test_director_dispatcher.py`.
- `backend/tests/postgres/test_dependency_invalidation_concurrency.py`, `frontend/tests/current-final.test.cjs`.

Regression coverage: Product/Brand edits after promoted final; retained selection/final history; pending assembly cancellation; edits during standalone and aggregate completion; semantic no-ops; Product archive; explicit/inherited Brand resolution; Product Brand reassignment with explicit override; late stale completion after a fresh final in QuickClip and LongVideo.

## Finding 2 — Explicit continuity subsets

Previously, an explicit prefix could pass predecessor checks even though its execution binding could never certify the full native chain. Generate All now calls `require_complete_continuity_selection()` built on canonical `continuity_chains()` before preparation/persistence. Explicit selections are never expanded silently. Automatic eligible selection still expands affected chains.

Incomplete chains return HTTP 422 `DIRECTOR_CONTINUITY_CHAIN_REQUIRED`, with `required_scene_ids` and `provided_scene_ids`. Revision maps must exactly cover the accepted explicit set. Tests flush/count generations, runs and members inside the rejected transaction before rollback; all remain zero. Complete chains, multiple chains and independent CUT selections remain accepted.

Changed files: `backend/apps/api/app/api/generations.py`, `backend/apps/api/app/services/continuity_groups.py`, `backend/tests/integration/test_director_corrections.py`, `test_generate_all.py` (updated typed error contract).

## Finding 3 — Aggregate parent derivatives

Previously, parent source identity could carry an old aggregate execution binding into a derivative which was not a member of that run. `_from_parent()` now rejects a parent with an execution-group binding or persisted DirectorRunMember association, including workflow overrides, before a new generation is persisted.

Policy: HTTP 422 `DIRECTOR_DERIVATIVE_REQUIRES_CHAIN`; regenerate through chain-aware Generate All. No aggregate derivative rerun implementation and no stripping of group metadata. Standalone Variation/Regenerate remain supported and inherit the parent's historical source identity, including stale identities. Tests check persisted counts before rollback and unchanged parent snapshots.

Changed files: `backend/apps/api/app/services/generation_service.py`, `backend/tests/integration/test_director_corrections.py`.

## Regression verification

- Targeted correction modules: PASS, 65 tests after the delayed-completion fix.
- Full final backend suite `pytest -q`: PASS, 569 passed, 19 skipped in 131.23s. Eighteen skips require PostgreSQL; one requires a POSIX host for Linux UID/mode testing.
- `ruff check .`: PASS, all checks passed.
- Scoped formatting: PASS.
- `python -m compileall -q apps workers tests`: PASS.
- `git diff --check`: PASS.
- `npm.cmd test`: PASS, 64 tests.
- `npm.cmd run typecheck`: PASS.
- `npm.cmd run lint`: PASS.
- `npm.cmd run build`: PASS, Next.js 16.3.5 production routes built successfully.

## PostgreSQL validation

NOT_RUN: `POSTGRES_TEST_DATABASE_URL` is unset. `pytest -m postgres -q` selected 18 tests; all skipped, including six new concurrency cases. Skipped tests are not PASS.

The six cases cover Product edit vs assembly promotion, Brand edit vs assembly promotion, and Product edit vs generation completion, each with edit-first and completion-first execution. Real API/worker transactions use barriers, PostgreSQL blocking diagnostics, randomized data and bounded timeouts. SQLite results do not prove row/advisory lock behavior.

## Independent review and remaining risks

Independent review of all three correction findings found no blocker: shared semantic invalidation and lock protocol, explicit full-chain preflight, and aggregate-parent derivative rejection. Its original validation passed 46 targeted backend tests and the frontend current-final gate test. The delayed-completion refinement also passed scoped review and 35 targeted tests. Reviewer made no edits.

Remaining validation gap: PostgreSQL concurrency cases require an actual disposable PostgreSQL test database. No GPU/Comfy runtime qualification is claimed.

## Final verdict

CORRECTION_COMPLETE

All three implementation corrections and available regression checks are complete. PostgreSQL lock behavior remains an explicitly unexecuted validation gap, not a passing concurrency result.

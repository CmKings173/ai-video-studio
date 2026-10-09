# Final core-correction round — 2026-10-07

## Source state

Workspace `ai-video-studio`, root `D:\project\ai-video-studio`.

Required connector `Codex with ChatGPT · ai-video-studio`: live workspace_info/git_status succeeded before editing. Branch `codex/production-safety-fixes`; HEAD `01b740d8be4d58d3bd15bc46dd9842afb48af8bb`. Starting dirty baseline: 0 staged, 52 unstaged tracked files, 148 untracked paths. No commit/push, branch change, reset, stash, cleanup or revert.

Final source verification: branch/HEAD unchanged; 0 staged, 52 unstaged tracked files, 151 untracked paths, including this report. Unrelated dirty work preserved.

## Fix 1 - Native continuity direct generation

Root cause: direct generation rejected only CONTINUOUS successors, allowing the leading CUT member to create an output without its required native execution-group binding.

Backend uses `requires_native_execution_group()` derived from canonical `continuity_chains()`. Every enabled member of a chain longer than one requires Generate All. ORIGINAL and standalone-parent VARIATION/REGENERATE call one service guard before persistence. Aggregate-parent derivatives retain the existing fail-closed error. The redundant CONTINUOUS-only API branch was removed; its existing Director Motion Context rejection remains. Full automatic/explicit Generate All semantics remain unchanged.

Frontend uses one enabled/ordered/adjacent-scene continuity helper for batch eligibility and direct capability gating. Generate Clip and direct mutation submission are blocked for the leading CUT and every successor. Independent CUT remains runnable. A separate settings-only action opens the generation editor for aggregate configuration, including reopening persisted Motion Context settings.

Files: `backend/apps/api/app/services/continuity_groups.py`, `generation_service.py`, `backend/apps/api/app/api/generations.py`; `frontend/lib/generation/eligible-scenes.ts`, `frontend/app/videos/[videoId]/page.tsx`, `frontend/components/generation/generation-editor.tsx`.

Tests cover all three members across ORIGINAL/VARIATION/REGENERATE, no new rows before rollback, isolated CUT success, existing full-chain Generate All, disabled/gapped continuity, UI actions and non-submission guards.

## Fix 2 - Dependency graph boundaries

`create_product()` acquires the existing transaction advisory graph guard before Brand validation and insertion. `create_video()` retains the graph guard and validates explicit Brand or inherited Product Brand before insertion. Existing explicit Product/Video Brand mismatch rejection (`PRODUCT_BRAND_CONFLICT`) is preserved.

Application-layer audit: Product create, Product PATCH brand reassignment, Product archive, Brand PATCH/archive and Video create use the graph-writer protocol. Brand creation adds a node without an existing edge; VideoPatch cannot mutate product_id/brand_id. No graph guard was added to read-only paths. Asset product links are outside the Product/Brand/Video generation dependency graph.

Graph writers: guard -> sorted affected Video locks where applicable -> dependency mutation. New Product creation validates Brand under that guard, then inserts the edge. Workers do not acquire the guard and retain Video -> Product -> effective Brand lock order. Existing canonical semantic invalidation/no-op behavior is preserved.

Files: `backend/apps/api/app/api/products.py`, `videos.py`, `backend/tests/integration/test_core_corrections.py`, `backend/tests/postgres/test_graph_creation_concurrency.py`.

PostgreSQL race coverage uses actual production advisory SQL and transactions, pauses after real lock acquisition, checks `pg_blocking_pids()`, and bounds execution. Both create-first and archive-first interleavings pass: create-first inserts before archive; archive-first rejects creation with BRAND_NOT_ACTIVE and persists no Product.

## Fix 3 - Director atomic auto-selection

Previously, independent per-member checks could preserve old-A but select new-B, creating mixed lineage. Completion now computes `auto_select_group` before changing any selection: all source identities current, all scenes enabled, all selections empty, all Scene revisions matching, all member operations ORIGINAL, and complete valid output coverage.

Only an eligible group selects all members. Every other group selects none while run/generations complete and READY output history remains intact. Each selected Scene revision increments once; Video revision increments once per selected group, zero when auto-selection is skipped. Optimistic revisions still change with selection; group size does not produce an unpredictable number of increments.

Files: `backend/workers/director_dispatcher.py`, `backend/tests/integration/test_core_corrections.py`. Coverage includes all-empty, partially occupied, all occupied, revision mismatch without semantic change, semantic edit, disabled member, and preserved Product/Brand invalidation tests.

## Frontend capability contract

`singleSceneCapability()` requires execution_scope == single_scene; direct submission never uses an aggregate-only workflow. `aggregateCapability()` independently resolves qualified aggregate settings. Motion Context controls and saving use aggregate evidence; enabled Motion Context blocks standalone preview/submission and guides Generate All. Separate settings access prevents a saved aggregate configuration from trapping the user outside the editor.

Refine/FaceRefine still depend on qualified flags/settings and fail closed. No graph/runtime qualification or hard-coded support was introduced.

Additional files: `frontend/lib/generation/capabilities.ts`, `frontend/tests/continuity-generation.test.cjs`, `generation-editor.test.cjs`, `generation-capabilities.test.cjs`.

## Verification

| Command | Status | Evidence |
| --- | --- | --- |
| Targeted backend core/Director/dependency modules | PASS | 90 tests |
| Targeted direct API/Generate All/derivative/dispatch after API cleanup | PASS | 68 tests |
| Full backend pytest -q with PostgreSQL enabled | PASS | 609 passed, 1 POSIX-only skipped in 139.42s on a pristine disposable database |
| pytest -m postgres -q | PASS | 20 passed, 590 deselected; no PostgreSQL skips |
| ruff check . | PASS | All checks passed |
| Scoped ruff format --check | PASS | 7 files formatted |
| python -m compileall -q apps workers tests | PASS | Exit 0 |
| git diff --check | PASS | Exit 0 |
| npm.cmd test | PASS | 81 passed, no failures/skips |
| npm.cmd run typecheck | PASS | Exit 0 |
| npm.cmd run lint | PASS | Exit 0 |
| npm.cmd run build | PASS | Next.js 16.3.5 production build/routes |
| docker build --no-cache --tag ai-video-studio-frontend:core-test-20261007 ./frontend | PASS | Frozen Yarn installation, Next build and runner image exported |

Docker local 29.6.2 was available with host permissions. PostgreSQL 16 ran in a newly created disposable container bound only to 127.0.0.1:15433, with no mounted repository/user data. Independent test invocations use pristine databases to avoid collisions from an existing fixture's fixed email. An initial full rerun against an already-used DB hit that fixture collision; the clean-database run passed 609 tests with one POSIX-only skip before the final redundant API cleanup. Final result is recorded above when complete.

`frontend/yarn.lock` is present. Dockerfile retains COPY package.json yarn.lock and yarn install --frozen-lockfile. Neither package manager nor locking was changed. Docker frontend PASS is an actual clean Docker build, separate from npm build.

## GPU

GPU/H3 runtime qualification: NOT_RUN - no GPU available in this phase.

## Remaining known work

- Refine/FaceRefine graph integration.
- Scheduler fairness.
- Director metrics.
- Render deployment.
- GPU qualification.
- Benchmark.
- Legacy cleanup.

## Verdict

CORE_CODE_READY_FOR_FEATURE_PHASE

Independent final review found no concrete blocker across all requested backend and frontend core scope, including the API cleanup and aggregate settings reentry. Independent checks passed 56 backend and 29 frontend tests plus reentry checks; the complete suites/builds above were separately verified. No feature-phase work was started.

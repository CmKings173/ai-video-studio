# AI Video Studio — Final P2 Corrections and Independent Release Review

## Whole-source review — 2026-10-08

Verdict: **REQUEST CHANGES**. Scope expanded from Dashboard to the current tracked and untracked backend, workers, Director provider, frontend, migrations, integrations and infrastructure. This section supersedes the earlier scoped PASS as a repository-wide verdict. Read-only source review; only this report was updated.

Reviewed checkout: `codex/production-safety-fixes`, HEAD `01b740d8be4d58d3bd15bc46dd9842afb48af8bb`. Four independent reviewers covered generation/Director; workers/media; frontend; and assets/assembly/domain APIs. Parent reviewed authentication/session/CSRF, trusted ingress, request limits, metrics, deployment and backup boundaries, and cross-checked the findings against live source. This is a risk-oriented review, not a proof that every line or runtime path is defect-free.

### Confirmed findings

1. **P1 — stale locked Scene permits lost edits.** `backend/apps/api/app/api/scenes.py:41` reads Scene before locking Video, then selects the same Scene `FOR UPDATE` without `populate_existing`. SQLAlchemy retains the earlier attributes. An interleaved editor commit from revision 1 to 2 was overwritten by a revision-1 patch, which stored revision 2 again. The same pre-read pattern exists in `services/generation_service.py:905`, risking stale generation inputs. Refresh under the child lock, or fetch only the parent identifier before locking.

2. **P1 — cancel can overwrite a completed generation.** `backend/apps/api/app/api/generations.py:755` locks a SceneGeneration already loaded in the identity map without refreshing. Reviewer reproduction interleaved `RUNNING -> COMPLETED` before the cancellation locks and persisted `CANCEL_REQUESTED/CANCELLING`. Refresh the locked generation before terminal-state checks.

3. **P1 — cancel can overwrite a READY final.** `backend/apps/api/app/api/assembly.py:119` has the same stale identity-map pattern. Parent independently reproduced worker `READY, revision=2` being overwritten by cancellation as `CANCEL_REQUESTED, revision=2` using a disposable SQLite file. This breaks the download readiness guard and cancellation recovery state. Refresh the locked final; inspect the matching assembler pre-read/lock paths at `backend/workers/assembler.py:131` and `:187` together. SQLite proves ORM staleness; PostgreSQL scheduling was not exercised.

4. **P1 — permanent oversized output retains the sole generation admission slot.** `backend/workers/dispatcher.py:824` treats accepted-job `COMFY_OUTPUT_TOO_LARGE` like an ambiguous transport failure, calls `_hold(COMFY_UNAVAILABLE)`, and repeats on recovery. DirectorDispatcher inherits this handler. Reviewer fake HTTP reproduction with a 9-byte output and 4-byte limit held twice and never finalized. Classify known permanent collection failures as terminal failures and release admission without resubmitting the external job.

5. **P1 — generated-output recovery uses the upload limit.** `backend/workers/reconciliation.py:255` rejects pending generated output against `max_upload_bytes`; `:277` also uses that limit when downloading. Normal generated-output publication and READY inspection use `max_generated_output_bytes`. After interrupted publication, a valid generated object between these limits can become FAILED/corrupt and cannot replay publication. Reviewer reproduction: upload limit 4, generated limit 12, object size 8 was rejected before storage inspection. Apply the category-appropriate limit throughout repair.

6. **P1 — proxy buffers oversized unauthenticated bodies before backend rejection.** `frontend/app/api/[...path]/route.ts:67` calls `request.arrayBuffer()` before contacting the bounded backend. `infra/ingress/default.conf.template:5` permits 502 MiB, despite the API's default 2 MiB body limit. Parent reproduction with a fake fetch observed all 3,145,728 bytes read and forwarded before a 413 response. Parallel large requests can exhaust the frontend process. Enforce actual-byte limits at the proxy before allocation/forwarding and align API ingress limits; uploads already use presigned MinIO URLs.

7. **P2 — arbitrary 404 URLs grow metric cardinality without bounds.** `backend/apps/api/app/main.py:57` uses raw request paths when no route matches. MetricsRegistry retains each unique label set indefinitely. Parent ASGI reproduction sent 25 distinct missing URLs: all returned 404 and added 25 counters plus 25 histograms. Use a constant unmatched-route label rather than client-controlled paths (and bound method labels where needed).

8. **P2 — generated storyboard exceeds its publish contract.** `backend/apps/api/app/services/storyboard_service.py:52` copies a brief allowing 20,000 characters into a description limited to 10,000; appended prompt text can also exceed its destination limit. Parent pure-helper reproduction accepted a 10,001-character VideoCreate brief and preview, but StoryboardPublish rejected all five descriptions with `string_too_long`. Produce bounded fields and validate preview scenes with the publish schema.

9. **P2 — disabled continuity predecessor blocks Generate All inconsistently.** `backend/apps/api/app/services/execution_groups.py:27` requires the physical predecessor even when disabled, while canonical enabled continuity chains treat the remaining scene as a new boundary. Parent reproduction: disabled CUT scene 0 + enabled CONTINUOUS scene 1 yielded a valid single-scene canonical chain/selection but `DIRECTOR_CONTINUITY_PREDECESSOR_REQUIRED` from the planner. Build execution groups from the canonical enabled-chain semantics.

10. **P2 — repeated cancellation releases image inspection before its file closes.** `backend/apps/api/app/integrations/media.py:246` initially shields Pillow's thread task, but the cancellation drain awaits it unshielded. A second cancellation exits the caller while the thread still owns the staging file, racing Windows temporary-directory cleanup. Reviewer held-thread reproduction showed caller done while file remained open. Reuse the existing shield-and-drain-through-repeated-cancellation pattern in `workers/common.py`.

11. **P2 — history reuse freezes hidden resolved settings.** `frontend/lib/generation/history-settings.ts:31` persists resolved width/height/CFG/frames, and editor changes preserve these hidden constraints. Reusing a 5-second output (124 frames), then changing the scene to 8 seconds (192 frames), leaves a request rejected by `generation_service.py:1003`; changing profile/ratio similarly conflicts at `:988`. Reuse semantic settings, preserve genuine custom-canvas inputs, and resolve derived settings for the current scene.

12. **P2 — primary resource lists stop at page one.** `frontend/app/videos/page.tsx:62`, `projects/page.tsx:36`, `products/page.tsx:74`, project detail `:64` and product detail `:67` render only the default first page without next-page controls. The backend default is 20, so older videos/resources/assets cannot be reached through these lists. Reviewer harness with total=21 rendered only 20 rows and no pagination control. Add server-driven paging and reset pages with filters.

13. **P2 — frontend page-size parameter does not match the API.** `frontend/lib/api/videos.ts:23` and related wrappers transmit `size`; `backend/apps/api/app/api/common.py:5` accepts `page_size`. The proxy preserves query parameters, so requests for 4/5/10/50 use backend default 20. This also makes Dashboard lists exceed their intended size. Reviewer captured `{params:{size:5}}` from the live wrapper. Translate to `page_size` at the API boundary and assert wire parameters in tests.

14. **P2 — revision-conflict recovery CTA is unreachable.** Project detail `frontend/app/projects/[projectId]/page.tsx:191` and product detail `:227` call `isRevisionConflict(serverError)` after handlers replaced the typed error with `getErrorMessage(err)`. That helper requires ApiClientError, so the reload action never appears and retries keep stale revisions. Reviewer verified typed error=true, converted string=false. Preserve the error or a separate conflict flag and refetch before retry.

15. **P2 — failed related lists masquerade as empty.** Project detail queries at `frontend/app/projects/[projectId]/page.tsx:62` discard video/assets errors and render empty messages at `:219`/`:270`; product assets at `frontend/app/products/[productId]/page.tsx:65` do likewise. Reviewer failed-query harness rendered “Chưa có video nào trong dự án”. Separate failed/loading/stale/success-empty states and provide query-specific retry, as Dashboard now does.

### Fresh verification and limits

- Backend full suite: **1,028 passed, 27 skipped** (202.65s). PostgreSQL lanes were not rerun in this review because POSTGRES_TEST_DATABASE_URL is unset; one POSIX-only case is skipped on Windows.
- Frontend full suite: **203 passed, 0 failed**.
- Backend Ruff, frontend typecheck and lint: passed.
- Reviewer targeted suites: generation/Director **343 passed**, worker/media **61 passed**, assets/assembly **67 passed**. These are overlapping subsets, not additional tests to sum with full-suite totals.
- Reproductions used pure helpers, fake HTTP/storage/query boundaries, ASGI without lifespan, or disposable SQLite. No production DB/MinIO/GPU operations and no source fixes were made.
- Browser/runtime UI and real ComfyUI/GPU execution: **NOT_RUN**. Previous scoped build verification remains historical evidence; a build was not rerun for this read-only review.
- Tests are green but do not cover the confirmed defects above. Repository-wide approval is withheld pending fixes and regressions, especially the six P1 items.
- Existing release tracking also remains blocked by untracked production source. No staging/commit/push/reset/stash/clean was performed.

## Dashboard UI state correction — 2026-10-08

Task: `dashboard-loading-stale-cache-final-fix`.

```text
DASHBOARD_UI_STATE: PASS
FRONTEND_TESTS: PASS
TYPECHECK: PASS
LINT: PASS
BUILD: PASS
UI_BROWSER: NOT_RUN
INDEPENDENT_REVIEW: PASS
```

Files changed in this task:

- `frontend/app/dashboard/page.tsx`
- `frontend/tests/dashboard-admin-query-errors.test.cjs`
- `tasks/final-p2-independent-release-review.md`

Root causes: the health header checked loading while its body independently treated missing data as failure; the cached-empty video/project branches rendered a stale notice alongside an assertion that the current list was empty.

Fix: a shared discriminated health state drives both header and body. Initial loading renders badge/detail skeletons in the existing responsive grid. Successful cached health remains visible during refresh, but a failed refresh suppresses cached health assertions and offers scoped Retry. A real successful degraded response still displays DEGRADED. Failed refreshes of cached-empty video/project lists now display an unverified-list notice and Retry without EmptyState. Populated stale lists remain visible with a warning; successful empty responses and retry recovery still render normal results. Metrics, Admin, upload contracts, pagination, editor and streaming code were not changed by this task.

Fresh verification:

- RED, before source fix: targeted suite **15 passed, 3 failed**. Failures reproduced initial health loading and both cached-empty lists.
- GREEN, final targeted suite: **19 passed, 0 failed**. Tests exercise rendered element trees, skeletons, badges, rows, initial failure, successful empty, stale populated/empty states, and retry recovery.
- Final `npm test`: **203 passed, 0 failed, 0 skipped**.
- `npm run typecheck`: exit 0.
- `npm run lint`: exit 0, including rerun after the last test addition.
- `npm run build`: exit 0, Next.js 16.3.5 production build.
- `git diff --check`: exit 0; line-ending warnings only.

Independent reviewer Zeno reread the final source and tests and reran the targeted suite: **19/19 passed**, no actionable issue. These are mocked query-state/component element-tree tests; they do not establish browser rendering or actual TanStack Query transition behavior.

Browser automation was attempted again using CUA; its trusted Node process exited unexpectedly during initialization. The requested 360/480/768/1024/1440px browser matrix was **NOT_RUN**; no browser-level PASS is claimed.

Repository identity remains `codex/production-safety-fixes`, HEAD `01b740d8be4d58d3bd15bc46dd9842afb48af8bb`, verified locally. Connector `workspace_info`/`git_status` actions are unavailable in this tool surface. Current Git inventory: **77 tracked changed/deleted paths, 285 untracked files, 0 staged paths**. This task adds no new files. Remaining release blockers are browser verification and untracked production source; no staging, commit, push, reset, stash, clean, database or MinIO operations were performed.

The sections below preserve earlier closeout evidence and its historical limitations; the fresh frontend counts and Dashboard verdicts above supersede earlier frontend results.

Captured 2026-10-08 from the local workspace. This is a final review addendum for the existing closeout report.

## Gate results

```text
SOURCE_REVIEW: PASS
UI_BEHAVIOR: PASS
UI_BROWSER: NOT_RUN
BACKEND_TESTS: PASS
FRONTEND_TESTS: PASS (full-suite result predates final test-only assertions; affected suites rerun)
POSTGRES_CONCURRENCY: PASS
DIRECTOR_ONLY: PASS
STREAMING: PASS
TEST_CLEANUP: PASS
RELEASE_TRACKING: BLOCKED
INDEPENDENT_REVIEW: PASS
GPU_RUNTIME: NOT_RUN
```

The P2 corrections are complete and the independent reviewer found no remaining actionable issue in the reviewed slice. The release is not fully ready: browser-based UI verification was unavailable, and source changes remain untracked in the working tree.

## Source and changes reviewed

The locally accessible checkout is on `codex/production-safety-fixes` at `01b740d8be4d58d3bd15bc46dd9842afb48af8bb`. The Codex `workspace_info` and `git_status` connector actions were not exposed in this session; this identity was read from the local Git checkout.

The dashboard and admin pages now distinguish query errors from legitimate empty or degraded results, provide scoped retries, and label cached data as stale. Admin storage mutations remain disabled until data is verified and while a refresh is in flight. The asset upload page reports initial and refetch errors instead of presenting a false empty inventory, and its role-to-MIME contract fails closed. The backend independently validates role/MIME compatibility before persisting an asset.

An independent rereview confirmed both follow-up P2 fixes: storage actions are disabled during fetch, and the upload asset inventory has scoped retry and stale-data behavior. No further actionable issue was found in that slice. Test-suite names were reviewed by domain and trigger; no duplicate behavior was identified, so no tests were removed or merged.

## Verification evidence

- Backend full suite: **1,028 passed, 27 skipped**. Skips include platform-dependent cases and PostgreSQL cases run separately below.
- PostgreSQL concurrency suite: **24 passed** on a disposable PostgreSQL container bound to localhost, using temporary test databases.
- Workflow migration checks: **5 passed** on that disposable database.
- Asset HTTP/lifecycle focused tests: **52 passed**.
- Backend Ruff and `compileall`: passed.
- Frontend full suite: **195 passed**; the two affected frontend suites were rerun after the final test-only assertions and passed **32/32**. `typecheck`, `lint`, and production build passed before those last test-only assertions. A final full frontend rerun could not start because the Windows sandbox command helper returned `helper_unknown_error: setup refresh had errors`.
- `git diff --check`: passed before the final review tooling failure.

## Limits and release tracking

`UI_BROWSER: NOT_RUN`: browser automation could not start. CUA reported the same Windows sandbox helper setup error, and no working browser or DevTools automation fallback was available. No screenshot or browser-level PASS is claimed.

`GPU_RUNTIME: NOT_RUN`: no GPU generation was launched. The production application database and MinIO were not accessed; no historical asset audit or production-data mutation was performed. PostgreSQL evidence above came only from a disposable local test container.

The last successfully captured Git inventory showed **77 tracked changed/deleted paths, 284 untracked files, 0 staged paths, and 0 conflicts**. No files were staged, committed, pushed, reset, or cleaned. The manifest could not be refreshed after that snapshot because new shell process creation failed with the sandbox helper error; treat its exact path lists as stale until regenerated. Source files remain untracked, so `RELEASE_TRACKING` is blocked.

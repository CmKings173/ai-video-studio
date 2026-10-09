# AI Video Studio — Group 1 Closeout

## Initial Group 1 scope and verdict

The initial focused review below was insufficient for closeout: full backend regression was red. The later **Group 1 — ORM Regression Correction** section records the source investigation and supersedes this preliminary verdict.

This closeout covers the seven Group 1 data-integrity and infrastructure findings in the current working tree on `codex/production-safety-fixes`, based on starting commit `01b740d8be4d58d3bd15bc46dd9842afb48af8bb`. The changes remain uncommitted. No commit, push, reset, stash, clean, or Group 2 work was performed.

| Review axis | Verdict | Evidence |
|---|---|---|
| Group 1 source findings | PASS | All seven scoped findings below have implementation and regression coverage. |
| Concurrency and persistence ordering | PASS | PostgreSQL race suite: 36 passed on a clean disposable database. |
| Independent review | PASS | Final reviewer found no remaining high-confidence issue across the seven findings. The last P2, oversized MinIO objects misclassified as transient, is fixed and covered. |
| Full backend regression | FAIL | 1,015 passed, 18 failed, 39 skipped. The remaining failures are listed below; the full suite cannot be called green. |

## Findings and corrections

1. **Child writes could race with parent edits and use stale ORM state.** Scene mutation and generation creation now read only the parent identity first, lock the Video before child rows, and refresh the Scene from the database. This serializes updates against parent edits and avoids trusting stale identity-map objects. Regressions cover stale Scene updates and generation snapshots.

2. **Generation cancellation could be overwritten by worker completion or retry.** Standalone and Director paths now refresh locked parent and child rows in a consistent order, preserve completed outputs, and honor cancellation before retry or terminal projection. PostgreSQL tests exercise cancellation before claim, concurrent cancellation, expired leases and Director aggregate cancellation.

3. **FinalVideo cancellation could race with final assembly publication.** Final-video cancellation and publication use consistent parent/child locking and terminal guards, preserving completed outputs while respecting cancellation. PostgreSQL tests cover final publication races.

4. **Oversized ComfyUI output could be retried as a transient error.** `COMFY_OUTPUT_TOO_LARGE` is now a deterministic terminal failure. The standalone worker regression processes another job afterward and confirms the oversized job is not submitted again; the Director regression verifies aggregate and member failure states and admission release.

5. **Recovery did not consistently apply the persisted asset-role limit, and oversized objects could remain pending as unavailable.** Recovery derives the byte cap from the persisted role on both pending and READY paths. MinIO raises `AssetObjectTooLargeError` when the actual stream exceeds the role cap, and reconciliation marks that asset failed/corrupt instead of treating it as a temporary storage outage. Tests include metadata under the limit with an actual object over the limit.

6. **Request-body limits could drift across the frontend proxy, API, and ingress.** A shared `REQUEST_BODY_MAX_BYTES` setting now defaults to 2 MiB and is wired through Next, FastAPI, and ingress configuration. The proxy enforces declared and streamed limits, handles missing or spoofed lengths, cancels the upstream request on overflow, and returns a 413 JSON response. Tests cover boundaries and concurrent streamed uploads.

7. **Metrics labels could grow without bound from arbitrary paths and methods.** HTTP request labels are normalized to bounded values, with unmatched inputs grouped under `__unmatched__`. A regression exercises hundreds of distinct paths and methods.

## Initial verification (before ORM correction)

- Focused Group 1 backend tests: **66 passed**.
- Clean PostgreSQL Group 1 suite: **36 passed**.
- Frontend suite: **211 passed**; typecheck, lint, and production build passed.
- Ruff, Python `compileall`, and Docker Compose configuration validation passed. Compose validation used `.env.example`.
- `git diff --check` passed before the final typed oversized-object change. A subsequent final diff check could not be run because the local command helper failed to start (`helper_unknown_error: setup refresh had errors`); the workspace connector also returned internal errors for status and directory reads.
- Full backend pytest, rerun after the final source change: **1,015 passed, 18 failed, 39 skipped** in 180.92 seconds.

The 18 failing tests were:

```text
tests/integration/test_assembly_dependency_freshness.py::test_dependency_edit_after_enqueue_preserves_artifact_without_promotion[product]
tests/integration/test_assembly_dependency_freshness.py::test_dependency_edit_after_enqueue_preserves_artifact_without_promotion[brand]
tests/integration/test_dependency_invalidation.py::test_dependency_edit_during_standalone_completion[product]
tests/integration/test_dependency_invalidation.py::test_dependency_edit_during_standalone_completion[brand]
tests/integration/test_dependency_invalidation.py::test_edit_after_promoted_final_retains_history[product]
tests/integration/test_dependency_invalidation.py::test_edit_after_promoted_final_retains_history[brand]
tests/integration/test_dependency_invalidation.py::test_product_archive_invalidates_dependents
tests/integration/test_dependency_invalidation.py::test_effective_brand_override_resolution[brand]
tests/integration/test_dependency_invalidation.py::test_effective_brand_override_resolution[product_brand]
tests/integration/test_dependency_invalidation.py::test_effective_brand_override_resolution[product]
tests/integration/test_director_corrections.py::test_standalone_derivative_preserves_historical_identity[True-VARIATION]
tests/integration/test_director_corrections.py::test_standalone_derivative_preserves_historical_identity[True-REGENERATE]
tests/integration/test_generation_contract_api.py::test_preview_constraints_once_and_stale_acceptance
tests/integration/test_generation_freshness.py::test_selection_endpoint_rejects_stale_without_clearing_history
tests/integration/test_generation_freshness.py::test_semantic_dependency_edit_invalidates_selection_and_batch_replay[product]
tests/integration/test_generation_freshness.py::test_semantic_dependency_edit_invalidates_selection_and_batch_replay[brand]
tests/integration/test_generation_freshness.py::test_derivative_after_edit_inherits_stale_parent_identity
tests/integration/test_generation_freshness.py::test_scene_api_responses_project_current_freshness
```

These initial failures were not baseline or environment failures. The investigation below reproduced and corrected the Group 1 ORM regressions; no assertions were weakened or tests removed.

## Group 1 — ORM Regression Correction

Task: `group1-orm-regression-and-full-suite-recovery`. Branch and HEAD reverified at the start: `codex/production-safety-fixes`, `01b740d8be4d58d3bd15bc46dd9842afb48af8bb`. Existing tracked and untracked work was preserved. Native execution outside the failing Windows sandbox restored source/test access. The old exposed connector tool names returned `Unknown tool`; repository identity and source were verified directly through Git and the filesystem.

### Root causes and RED evidence

`SessionFactory` deliberately uses `autoflush=False`. A locking ORM SELECT with `populate_existing=True` replaced pending values and reset their attribute histories without first writing them. Twelve failures originated in lost unflushed Video dependency links; six originated in lost Scene edits/revisions. This classification was made after collecting each traceback, rather than inferring one cause from the test filenames.

A real-service, isolated in-memory reproduction recorded: database `Video.product_id=None`; ORM had a newly assigned Product ID, `video in session.dirty=True`, `is_modified=True`, and history `added=[new_id], deleted=[None]`. Immediately after the locking refresh, ORM `product_id=None` and dirty membership was cleared. SQL contained SELECTs and a later status UPDATE, but no UPDATE carrying the Product link. The generation snapshot had `source_scope.product_id=None` and empty Product/Brand dependencies; explicit flush and a separate post-commit session still read `product_id=None`.

Original RED command covered the five affected integration files: **18 failed, 57 passed**. New dirty-input regressions independently produced **11 failed, 2 passed** before source correction. Additional repeated-PATCH/cancellation regressions produced **6 failed**. A PostgreSQL graph-guard test produced **2 failures (`DID NOT RAISE AppError`)** before adding the nonblocking guard.

In the matrix, **A** means dirty `Video.product_id` was overwritten by `GenerationService.create`'s Video refresh. Its caller chain was `seed/dependency_generation -> GenerationService.create -> SELECT Video FOR UPDATE`; the committed consumer link and original generation's dependency snapshot were missing. **B** means a same-transaction Scene prompt/revision was overwritten by the locked Scene refresh in generation creation or `_locked_scene_and_video`. Failed assertions roll back their fixture transaction; the observed wrong values below are identified as ORM/service results rather than invented post-commit evidence.

| Test (integration file and exact case) | Actual RED failure/state | Expected state/contract | Root, refresh boundary and correction |
|---|---|---|---|
| `test_dependency_invalidation.py::test_dependency_edit_during_standalone_completion[product]` | Completed generation auto-selected after Product edit | Completed artifact retained, selection not promoted, Video DIRTY | A; missing consumer link made Product edit ineffective. Preserve verified Video intent at create lock. |
| Same test `[brand]` | Completed generation auto-selected after Brand edit | Completed artifact retained without stale promotion | A; effective inherited Brand disappeared with Product link. Same source correction. |
| `test_dependency_invalidation.py::test_edit_after_promoted_final_retains_history[product]` | Video stayed READY instead of DIRTY | Historical final/pointer retained; current source marked DIRTY | A; consumer graph contained no Product link. |
| Same test `[brand]` | Video stayed READY instead of DIRTY | Historical final retained; inherited Brand change invalidates freshness | A; inherited Brand consumer missing. |
| `test_dependency_invalidation.py::test_product_archive_invalidates_dependents` | GENERATING instead of DIRTY | Product archive invalidates generated consumers | A; persisted Video was no longer a consumer. |
| `test_dependency_invalidation.py::test_effective_brand_override_resolution[brand]` | Inherited Video revision 1, expected 2 | Inherited and direct Brand consumers increment; explicit unrelated override remains distinct | A; inherited link absent; dependency-resolution assertions remain unchanged. |
| Same test `[product_brand]` | Inherited Video revision 1, expected 2 | Inherited Brand reassignment invalidates the correct consumers | A; lost Product link. |
| Same test `[product]` | Inherited Video revision 1, expected 2 | Product semantic edit invalidates its consumers | A; lost Product link. |
| `test_assembly_dependency_freshness.py::test_dependency_edit_after_enqueue_preserves_artifact_without_promotion[product]` | Video revision 1, expected 2 | Rendered artifact preserved; stale final not promoted | A; failure occurred at revision assertion after PATCH; assembly promotion assertions had not yet run. |
| Same test `[brand]` | Video revision 1, expected 2 | Effective Brand edit changes revision/freshness while retaining final history | A; inherited consumer missing. |
| `test_director_corrections.py::test_standalone_derivative_preserves_historical_identity[True-VARIATION]` | Freshness True after an unflushed current Scene edit | Frozen parent identity unchanged; derivative stale against edited current Scene | B; `create -> Scene refresh` erased current prompt; preserve validated Scene intent, do not rewrite frozen parent. |
| Same test `[True-REGENERATE]` | Freshness True instead of False | Frozen regenerate identity stays historical and stale | B; same refresh boundary. |
| `test_generation_contract_api.py::test_preview_constraints_once_and_stale_acceptance` | `DID NOT RAISE AppError` | Reject accepted preview after pending Scene revision increment with `PROMPT_PREVIEW_STALE` | B; `create -> Scene refresh` restored old revision. |
| `test_generation_freshness.py::test_selection_endpoint_rejects_stale_without_clearing_history` | `DID NOT RAISE AppError` | `SCENE_SELECTION_STALE`, historical selection retained | B; `select_generation -> _locked_scene_and_video` discarded pending prompt. |
| `test_generation_freshness.py::test_semantic_dependency_edit_invalidates_selection_and_batch_replay[product]` | Freshness stayed True after context edit | Fingerprint and batch identity must change | A; snapshot and current Video both lacked Product dependency. |
| Same test `[brand]` | Freshness stayed True after context edit | Effective Brand context participates in identity | A; inherited Brand absent after Product link loss. |
| `test_generation_freshness.py::test_derivative_after_edit_inherits_stale_parent_identity` | Derivative considered fresh | Parent snapshot retained but stale relative to current edited Scene | B; current prompt was reset at create lock. |
| `test_generation_freshness.py::test_scene_api_responses_project_current_freshness` | `REVISION_CONFLICT` during the next Scene API call | Consecutive Scene mutations retain accepted revisions and return correct freshness DTOs | B; `_locked_scene_and_video` reset the prior call's pending revision. |

### Source correction and transaction semantics

- Added `backend/apps/api/app/db/locking.py::lock_revisioned_row`. For clean instances it keeps the locked refresh. For dirty instances it locks a raw column projection without populating the identity map, checks the original revision and original value of every changed field, then refreshes and reapplies only the verified pending intent. Missing baselines, changed fields, revision conflicts and forbidden identity changes fail closed with HTTP 412. It performs no flush. A Scene's parent link cannot be overlaid under the wrong Video lock.
- Generation creation now locks **Video -> Product -> effective Brand -> Scene**. The parent-ID pre-read suppresses autoflush. Dependency context is refreshed safely before composing prompts and fingerprints. Direct and inherited Brand resolution remain distinct; derivatives still use the existing frozen-parent path.
- Unflushed dependency-binding edits take a nonblocking PostgreSQL graph advisory guard before service row locks. A graph editor that already resolved consumers causes a 412 without DML. This prevents missing a newly bound consumer; nonblocking acquisition also prevents waiting on the graph guard while a caller already owns Video.
- Scene API mutations, generation/Director cancellation and final-video cancellation use the same verified dirty-state boundary. The aggregate cancellation reread now retains the cancellation written earlier in the same transaction, including when `refresh_video` returns early for DIRTY Videos.
- Product/Brand invalidation first locks consumer Video IDs in canonical order, then safely refreshes each Video and the dependency. Repeated PATCH calls retain pending revisions and contexts. No-op semantic PATCH does not invalidate consumers. Existing worker terminal-state refreshes remain in their short, fresh units of work; their original stale-state protections were not removed.
- Independent review caught two further invalidation paths: a staged dependency context followed by a same-value PATCH could conceal the semantic change, and unflushed Video/Product bindings could hide consumers from database discovery. Invalidation now rejects pending binding rewrites before discovery and rejects unaccounted pending dependency content without DML. Successfully invalidated content is accounted only within the exact current transaction or savepoint, allowing repeated accepted PATCH/no-op calls. A rolled-back savepoint cannot authorize subsequent pending content. These guards retain staged values for caller rollback/retry; they do not flush arbitrary graph edits to make them visible.

No global autoflush change, unconditional pre-lock flush, expiration sweep, revision bypass, snapshot rewrite, queue redesign or assertion weakening was introduced. Persistence remains at existing intentional service boundaries; `persist=False` preflight retains pending edits with no INSERT/UPDATE/DELETE.

### New coverage and verification

The existing integration freshness file was extended with **19 cases**: Product/Brand bindings and contexts; Scene prompt/spec/revision; Video config/revision/status; multiple generation calls; stale preview; snapshot/fingerprint consistency from a separate session after commit; no-DML preflight with unrelated dirty User state; repeated Product/Brand PATCH and same/sibling aggregate cancel calls.

The existing PostgreSQL identity-map file was extended with **12 cases** using independent sessions and `autoflush=False`: dirty input persistence and exact snapshots; stale dirty Video/Scene conflicts, both with revision changes and with overlapping field changes without revision advancement; both Product/Brand create-versus-edit lock interleavings; actual lock order and bounded completion; busy graph guards rejecting unflushed bindings without DML even when the caller holds Video.

Independent-review follow-up added **14 more cases** to the same integration freshness file (33 new integration cases total): two unaccounted Product/Brand-context rejections; six pending Video/Product-binding rejections; two semantic-then-no-op sequences with exactly one Video invalidation; four savepoint-rollback reintroductions across outer/new-savepoint scopes. The completed freshness file passed **51 tests**. All **12 guard cases failed with `DID NOT RAISE`** when the pre-fix implementation was reconstructed in memory without modifying production files; all **four savepoint cases failed** when accounting used only the root transaction. Normal source passed both groups.

- Targeted affected files, including all original failures and new integration cases: **94 passed**.
- Full PostgreSQL lane initially passed **48 tests** on disposable database `codex_group1_orm_final_20261008`. After the review corrections, Docker was initially unavailable (48 fixture setup errors, no test bodies ran). Docker was restarted; the old disposable container was absent. A new isolated `ai-video-studio-group1-orm-pg-20261008` container/database `codex_group1_orm_resume_20261008` on loopback port 65521 passed **48 tests**. No application database was reset or MinIO objects deleted.
- Frontend: **211 passed**; typecheck, lint and production build passed. The build wrapper restored `next-env.d.ts`; no frontend source was edited by this task.
- Backend Ruff: passed. Python compileall: passed. `git diff --check`: passed (only line-ending warnings).
- Initial full backend before the two follow-up guards: **1052 passed, 51 skipped**. Final full run after the guards and 14 additional cases: **1066 passed, 51 skipped**, zero failures, in 242.89 seconds. Of the 51 skips, **48 PostgreSQL tests passed** in the separate real PostgreSQL lane and **two PostgreSQL migration tests passed** in a separate isolated-schema run (`-m postgres`, three SQLite cases deselected). The only unexecuted test is the POSIX UID/mode boundary requiring a Linux host.
- Independent initial review identified the two P2 invalidation paths above. Independent source rereview found no actionable high-confidence issue in the final binding/content guards, lock ordering, row-lock helper or transaction/savepoint isolation. Final rereview of all 14 follow-up regression cases found no actionable issue or weakened assertion. Coverage limits: staged semantic regressions exercise `context` rather than each other semantic field separately; real PostgreSQL locking is verified by the separate PostgreSQL suite. Reviewers did not independently rerun tests or mutate databases.

### Final verdict

```text
GROUP_1_ORIGINAL_P1_FIXES: PASS
SAME_TRANSACTION_DIRTY_STATE: PASS
POSTGRES_CONCURRENCY: PASS
DEPENDENCY_FRESHNESS: PASS
GENERATION_SNAPSHOT: PASS
ASSEMBLY_PRESERVATION: PASS
FULL_BACKEND_TESTS: PASS
FRONTEND_COMPATIBILITY: PASS
INDEPENDENT_REVIEW: PASS
GROUP_1_CLOSEOUT: PASS
```

No commit, push, reset, stash or clean. Group 2 was not started. All 18 original failures were reproduced, explained and corrected. Final verification has no unexplained failure or confirmed remaining scoped finding. The Linux-only UID/mode test remains a documented platform limit; all PostgreSQL-dependent skips were executed successfully against the disposable database.

## Group 1 — Final Proxy Slow-Client Correction

Scope: the confirmed P2 request-body lifecycle risk in `frontend/app/api/[...path]/route.ts`. Connector `workspace_info` and `git_status` succeeded and verified `ai-video-studio`, branch `codex/production-safety-fixes`, HEAD `01b740d`, dirty worktree with no staged changes. No backend source, authentication, CSRF, cookie forwarding, idempotency, workflow, database or MinIO changes were made by this correction.

### Root cause and RED reproduction

The streaming byte limiter counted actual bytes correctly, but its `readNext()` awaited `reader.read()` without a deadline. Both the early upstream response and fetch-failure paths then awaited `drain()`. A client could send the first valid chunk, never close the stream, and leave the proxy waiting indefinitely. In addition, declared/observed overflow cleanup awaited cancellation hooks, which can themselves return never-settling promises. The ingress response timeout did not bound these body reads.

Before changing source, two runtime reproductions used native `ReadableStream` bodies with one valid chunk and no EOF. Mock fetch either returned HTTP 422 immediately or threw `TypeError`. `node --test --test-name-pattern='slow-client reproduction' frontend/tests/auth-proxy.test.cjs` exited 1: **two failed, zero passed**, both `proxy did not settle within 250ms`. Test cleanup closed each source in `finally` and the runner exited in 1087ms; no test hung indefinitely. The exact same tests passed after the correction.

### Source changes and timeout contract

- `PROXY_REQUEST_BODY_TIMEOUT_MS` sets an absolute input-body deadline, default **60000ms**, valid integer range **1..3600000ms**. Missing, empty, noninteger, zero, negative, nonfinite or excessive values fall back to 60000ms. `.env.example` documents it and Compose supplies it only to the frontend; the shared byte limit is unchanged.
- The deadline begins when the proxy takes ownership of the incoming body, before fetch. It is never renewed by incoming chunks, so steady slow-trickle traffic is bounded. It stops immediately at body EOF, even if the upstream fetch has not returned headers yet. It does not limit a completed request's backend execution or response stream.
- Serialized body reads have one active interruption callback. Deadline or disconnect interrupts the read, starts reader cancellation without waiting for its hook, releases the source reader lock and aborts upstream. There is no per-chunk race subscription retained until EOF and no whole-body buffering. `drain()` and upstream stream cancellation use the same deadline.
- Cancellation hooks on incoming and discarded upstream bodies have rejection handlers and are not awaited. A late response from fetch after terminal failure is also cancelled. Timer cleanup occurs at EOF/failure; the request abort listener is removed in `finally`.
- The first observed terminal body failure wins. Actual observed overflow, or oversized declared Content-Length rejected before fetch, yields **413 `REQUEST_BODY_TOO_LARGE`**. Deadline yields **408 `REQUEST_BODY_TIMEOUT`**. A request-signal disconnect yields **499 `REQUEST_CLIENT_DISCONNECTED`**; an unclassified body read error without that signal yields **400 `REQUEST_BODY_READ_FAILED`**. Network failure with a completed valid body remains **503 `BACKEND_UNAVAILABLE`**, never 413. With an incomplete body, its existing deadline bounds the remaining byte check and can produce 408. Error envelopes include code/message/trace ID/details and no-store caching.
- A valid early upstream response is retained until input EOF or a terminal body failure; overflow/timeout discards its body. Valid upstream status, headers, multiple cookies, authentication/forwarded identity and streaming response are preserved. Direct presigned MinIO uploads do not traverse this route or its deadline.

### Ingress and deployment assessment

NGINX was not modified. Its default `client_body_timeout` is **60 seconds between successive reads**, whereas this proxy deadline bounds the **whole body**. They are complementary; the ingress idle timer alone does not stop slow-trickle traffic. With a larger proxy deadline, ingress can still terminate a 60-second input stall first. The existing `proxy_read_timeout 3600s` governs gaps in upstream response reads and remains available for long responses after input EOF. The proxy-specific setting is documented separately from both ingress timers. [NGINX client body timeout](https://nginx.org/en/docs/http/ngx_http_core_module.html#client_body_timeout), [NGINX response read timeout](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_read_timeout).

### Regression matrix and verification

The existing proxy test file retained all **14 original cases** and added **29 runtime cases** using the actual compiled TypeScript route with native Request/Response/ReadableStream objects and controlled fetch boundaries.

| Required scenario | Evidence |
|---|---|
| Small valid body | Existing success/configured-limit cases pass. |
| Declared oversized body | 413 before fetch, including a never-settling cancellation hook. |
| Chunked oversized body | 413 from actual bytes; source cancelled and upstream aborted. |
| Spoofed small Content-Length | Actual-byte limit still yields 413. |
| Early response with completed valid input | Upstream status/headers retained; deadline cleared at EOF. |
| Early 422 with open body | Bounded 408; reader released and upstream response cancelled. |
| Backend unavailable with open body | Bounded 408; completed-body network failure separately stays 503. |
| Continuous small chunks | Overall deadline fires despite continued input. |
| Client disconnect | Already-aborted and in-drain signals yield 499 with cleanup. |
| Deadline/overflow/disconnect race | Both event orders enforce the first terminal outcome without a second response. |
| Concurrent slow clients | Sixteen open bodies finish with bounded cleanup; timers/listeners removed and source locks released. |
| Valid response streaming | Input EOF ends the deadline before delayed fetch/stream completion; headers/cookies and chunks remain intact. |

Additional coverage checks fallback/range configuration, rejected cancellation hooks without unhandled rejections, a fetch mock ignoring abort with late-response disposal, generic body read failure, and Compose default wiring.

- Main-agent targeted proxy run: **43 passed**, zero failures/cancellations/skips. Independent reviewer also ran and verified **43 passed** against final source.
- Full frontend suite after the final Compose assertion: **240 passed**, zero failures/cancellations/skips.
- `npm run typecheck`: passed. `npm run lint`: passed.
- Production build: passed through `node tests/build-preserving-next-env.cjs`, which invokes the same installed Next.js production build while restoring the original user-owned `next-env.d.ts`.
- Compose validation: `docker compose --env-file .env.example -f infra/compose.yaml config --quiet` passed. NGINX template remained unchanged, so no changed NGINX configuration required validation.
- Related backend configuration contracts: **5 passed** in `backend/tests/contract/test_nfr.py`. The full backend was not rerun because backend source and backend environment were unchanged; the preceding correction's 1066-pass full backend and 50 PostgreSQL-dependent tests remain its recorded verification.
- `git diff --check`: passed.

### Independent review, limits and final gates

Independent read-only review found no high-confidence P1/P2 in final source. It verified bounded read interruption, first-failure precedence, nonawaited cancellation, late-response disposal, timer/listener cleanup, EOF behavior, header/cookie/identity preservation and timeout bounds; it independently ran the 43 focused tests. No review edits or database mutations occurred.

Remaining verification limits: controlled fetch/native-stream tests on Node 24.18.0, no live Next.js/NGINX network interleaving test or heap profiling. JavaScript timers are subject to event-loop scheduling; the configured value is a deadline, not a hard realtime guarantee. Arbitrary external cancellation implementations cannot be forced to settle, but their promises do not block proxy completion and their rejections are handled. No confirmed remaining scoped source issue was identified.

```text
PROXY_SLOW_CLIENT: PASS
PROXY_BODY_LIMIT: PASS
PROXY_STREAMING: PASS
PROXY_CLEANUP: PASS
FRONTEND_TESTS: PASS
TYPECHECK: PASS
LINT: PASS
BUILD: PASS
INDEPENDENT_REVIEW: PASS
GROUP_1_CLOSEOUT: PASS
```

No stage, commit, push, reset, stash or clean. No production DB, MinIO or workflow changes. Stop after this correction; Group 2/3 not started.

## Group 2 — Business Logic & Director Correctness

Task: `group2-business-director-root-cause-correction`. Verified workspace `ai-video-studio`, branch `codex/production-safety-fixes`, HEAD `01b740d8be4d58d3bd15bc46dd9842afb48af8bb`. This is the existing dirty working tree; Group 1 and unrelated changes were preserved. Group 2 corrects all four business findings and the publish mutation ordering discovered during independent review.

### Root causes, RED evidence and corrections

| Finding | Root cause and correction | RED before source edits | Final regression evidence |
|---|---|---|---|
| G2-01 Storyboard schema | Deterministic text could exceed canonical SceneSpec/SceneCreate bounds; planner validation omitted publish schema and broad fallback hid programmer errors. Derive text bounds from Pydantic, budget the prompt suffix, validate the generated scenes through StoryboardPublish, and restrict fallback to contract/provider failures. | Storyboard suite: **30 failed, 4 passed**. | Actual HTTP preview→publish for 30/60 seconds with long brief/product context, invalid LLM fallback, canonical count/order/duration, unchanged valid text and propagation of programmer exceptions. Final storyboard plus API/contract neighbors: **100 passed**. |
| G2-02 Continuity | Execution planning looked up numeric predecessors independently of canonical enabled chains. Reuse continuity_chains and complete-selection validation, respecting disabled/gap boundaries and retaining aggregate scope for genuine chains and motion-context singleton requests. | Unit matrix: **7 failed, 8 passed**; actual Generate All disabled-predecessor reproduction: **2 failed, 2 passed**. | Disabled CUT/CONTINUOUS predecessor and middle boundaries, leading CONTINUOUS, gaps, all disabled, explicit disabled selection, partial/full chains, qualified/unqualified aggregate and motion singleton. Actual generation/run/member snapshots match enabled IDs; rejection asserts zero rows after explicit flush inside the caught transaction before rollback. |
| G2-03 Media cancellation | The initial shield was followed by an unshielded wait, so a second cancellation abandoned inspection while the thread still owned the file. Lazily reuse the existing workers.common owned-task drain helper, preserving cancellation and error observation. | **2 failed, 5 passed**, including native Windows PermissionError during premature staging cleanup. | **7 lifecycle tests passed**, **70 focused and neighboring tests passed**. Normal/error completion, single/repeated cancellation, finishing race, closed handle before cleanup and no leftover inspection task. Native Windows verification: **PASS**. |
| G2-04 History Reuse | Resolved width/height/frames/CFG/FPS were copied into new request overrides. Reuse projects user intent only; current qualification resolves execution values. Preserve valid FIXED seeds, random policy, ordered asset roles and cloned feature/audio policies. | **4 failed, 32 passed**. | Actual History Reuse button/page mutation replaces stale scene config; editor/request harness exercises current qualification, capabilities and explicit custom settings. Fixed/random backend bridge cases compile actual TypeScript reuse/request code and call real GenerationService: historical 5s/124 frames remains immutable, current 8s resolves 192 frames, explicit stale 124 is still rejected. |

Source changes are limited to `storyboard_service.py`, the storyboard publish preflight in `api/videos.py`, `execution_groups.py`, `integrations/media.py`, and `frontend/lib/generation/history-settings.ts`, with focused regression files. No schema relaxation, provider workflow changes, modified Variation/Regenerate frozen-parent semantics or changes to Group 1 cancellation/locking helpers were needed.

### Independent review correction: publish preflight

Independent reviewer James found a P2 in the existing publish path: replacement deleted/flushed existing scenes before validating total duration, and late order validation occurred after adding earlier scenes. Four real-ORM regressions (new/replacement × late invalid order/invalid duration) produced **3 failed, 1 passed** before the correction. The late-order new request wrote four partial scenes; replacement either removed the original two or replaced them with four partial scenes.

The final publish path validates the entire duration and order before any Scene deletion or insertion. Existing revision/history/exact-replacement guards keep their precedence. Regression assertions inspect pending/deleted scene state, explicitly flush, then query unchanged scene IDs/prompts/revisions and video status/revision in the same caught transaction **before rollback**. All four pass; the final 100-test neighboring run includes them. Idempotency claim transaction behavior remains unchanged.

### Full verification

Commands used the existing backend `.venv/Scripts/python.exe` from `backend` and installed Node/npm from `frontend`.

- Final full backend, after the publish correction: `python -m pytest -q --tb=short` — **1137 passed, 51 skipped in 192.37s**.
- Full frontend: `npm test` — **254 passed**, zero failures/cancellations/skips. This includes the 43 proxy tests protecting Group 1 slow-client, limits and cleanup.
- PostgreSQL: fresh disposable database `codex_group2_business_20261008` on the existing loopback-only test PostgreSQL 16 container. `pytest tests/postgres tests/integration/test_workflow_execution_scope_migration.py tests/integration/test_workflow_version_migration.py -m postgres -q --tb=short` — rerun after final source correction, **50 passed, 3 deselected**. No application database reset or production connection.
- `python -m ruff check .`: passed. `python -m compileall -q apps workers tests migrations main.py`: passed.
- `npm run typecheck` and `npm run lint`: passed.
- Production build: `node tests/build-preserving-next-env.cjs` passed, invoking installed Next.js build and preserving the original user-owned next-env.d.ts.
- `git diff --check`: passed after the report update. Branch and HEAD unchanged; no staged changes.

The full backend's 51 skips comprise 50 PostgreSQL-dependent tests separately exercised in the real PostgreSQL lane and one POSIX-specific backup/restore ownership/mode case unavailable on Windows. That POSIX skip is distinct from the native Windows media cleanup tests, which ran and passed.

### Independent rereview and limits

Feynman independently reviewed canonical continuity/qualification/persistence ordering and media ownership, ran **22 focused unit tests**, checked both import orders, and found no actionable P1/P2. Its attachment access was unavailable; main supplied the task requirements, and the reviewer inspected integration assertions without executing those DB tests. Main executed the broad and PostgreSQL lanes.

James independently reviewed Storyboard/History Reuse and editor integration, ran focused storyboard/history tests, identified the publish P2 above, and rereviewed the final correction and its four regression cases. Its independent session-double checks confirmed zero scene mutations for all four rejection combinations; main verified the real ORM cases. No remaining actionable P1/P2 was found in the reviewed scope. Implementer reports were not treated as independent review.

Limits: frontend interaction is exercised through the repository's compiled React/editor harness and actual request builders, not a live browser session. Planner tests use controlled HTTP LLM responses; no live LLM/GPU/provider generation was run. Most business integration tests use disposable SQLite; PostgreSQL concurrency claims rely only on the separate real PostgreSQL lane. Owned-task draining necessarily waits for a running synchronous inspection to finish; this change does not forcibly terminate a thread. No production DB, MinIO, environment secrets or workflow configuration was changed.

```text
G2_STORYBOARD_SCHEMA: PASS
G2_CONTINUITY_POLICY: PASS
G2_MEDIA_CANCELLATION: PASS
G2_HISTORY_REUSE: PASS
WINDOWS_MEDIA_CLEANUP: PASS

GROUP_1_REGRESSION: PASS
BACKEND_FULL_SUITE: PASS
FRONTEND_FULL_SUITE: PASS
POSTGRES_INTEGRATION: PASS
TYPECHECK: PASS
LINT: PASS
BUILD: PASS
INDEPENDENT_REVIEW: PASS

GROUP_2_CLOSEOUT: PASS
```

No stage, commit, push, reset, stash or clean. Stop after Group 2; Group 3 was not started.

## Group 3 — Frontend & API Contract Closeout

Task: `group3-frontend-api-contract-closeout`. Workspace and Git state were checked against the connected workspace and local repository. Branch remains `codex/production-safety-fixes`, HEAD `01b740d8be4d58d3bd15bc46dd9842afb48af8bb`; existing Group 1/2 and unrelated working-tree changes were preserved. Backend production source was not changed for Group 3.

### Root causes, source changes and RED → GREEN evidence

| Finding | Original defect and correction | RED before production changes | GREEN evidence |
|---|---|---|---|
| G3-01 Pagination | Primary pages and related sections always fetched page 1. Add server-driven Previous/Next using verified `page`, `page_size`, `total`, scoped query keys and synchronous filter/search page resets. Independent section pagination and keyed parent bodies prevent stale cross-parent state. | Primary behavioral suite: **39 failed, 0 passed**. Original detail reproduction includes the two failing related-item-21 cases. | Primary suite **42 passed**; detail matrix covers counts 0, 1, 19, 20, 21, 40, 41 and 100+, last-page boundaries, page-N errors/back/retry, filter resets, independent sections and parent changes. |
| G3-02 Wire contract | Eleven list wrappers passed friendly `size` unchanged; backend expects `page_size`. Normalize at the wrapper boundary, retain compatible caller types and filters, enforce integer sizes 1–100 without mutating inputs. | Corrected actual-URL/NextRequest harness: **22 failed, 12 passed**, demonstrating explicit sizes sent under the wrong key. An initial proxy harness used Request without nextUrl; that harness failure is excluded from this RED count. | **35 passed**: all eleven wrappers at size 5, size 50 and omitted/default size, proxy query forwarding, minimum/maximum and invalid sizes. Actual FastAPI contract suite **93 passed**. |
| G3-03 Revision recovery | Stringifying errors lost typed conflict identity. Preserve errors until rendering and recognize the exact `REVISION_CONFLICT` code rather than treating every 409/412 as a conflict. Shared recovery checks dirty state, refetch success and duplicate calls; keep the edit revision captured with its draft and require an explicit subsequent mutation. | Original detail reproduction **6 failed, 0 passed** includes two unreachable typed-Reload cases. Separate non-conflict-412 regression **2 failed, 0 passed** before narrowing recognition. | Final detail suite **99 passed**, covering typed errors, edit/archive, failed reload retention, confirmation decline/accept, duplicate suppression, background refetch, locked recovery and current-revision explicit retry. |
| G3-04 Related errors | Missing data after a failed request was interpreted as an empty collection. Render explicit loading, verified empty, populated, initial error, stale cached data/error and page-N error states, with section-local retry. | The original six-case detail RED includes two initial-related-error cases rendered as empty. | Final detail matrix checks actual messages and controls, including cached-empty refresh errors, unaffected healthy sections and both failed sections with independent retries. |

Production files: `frontend/app/{videos,projects,products}/page.tsx`, `frontend/app/projects/[projectId]/page.tsx`, `frontend/app/products/[productId]/page.tsx`; `frontend/components/list-pagination.tsx`, `frontend/components/related-resource-state.tsx`, `frontend/lib/hooks/use-revision-recovery.ts`; `frontend/lib/api/pagination.ts`, `errors.ts`, and the seven wrapper files `videos.ts`, `projects.ts`, `products.ts`, `assets.ts`, `brands.ts`, `generations.ts`, `admin.ts`. No proxy, backend production, workflow or infrastructure change was needed.

Exact regression name templates and representative tests:

- `${resource}: ${total} rows reachable through bounded real pagination callbacks`
- `${resource}: page two failure offers scoped retry and previous without false empty`
- `${resource}: ${value} resets page two and isolates results/query key`
- `${name} transmits canonical pagination ${JSON.stringify(pagination)}`
- `Next proxy preserves canonical pagination and unrelated query parameters`
- `pagination sizes respect backend bounds without mutating filters`
- `${kind}: typed conflict renders Reload inside edit dialog`
- `${kind}: related item 21 is reachable with scoped pagination`
- `${kind}: initial related error is not empty`
- `${kind}: non-conflict HTTP 412 has no Reload action`
- `${kind}: failed confirmed reload preserves draft conflict and cached parent`
- `${kind}: background refetch never advances the dirty form revision`
- `${kind}: archive conflict reload does not repeat archive and manual retry uses revision 4`
- `${kind} ${section}: parent change resets pages draft errors and cached rows`
- Backend: `test_all_wrapper_endpoints_pagination_wire_contract`, `test_filtered_and_related_pages_preserve_scope_and_totals`, `test_revision_conflict_preserves_row_then_current_revision_retries_once`, `test_parent_switch_returns_only_new_scope`, `test_missing_parent_is_typed_error_not_successful_empty`.

These names are generated for videos/projects/products or project/product sections in `primary-list-pagination.test.cjs`, `pagination-wire-contract.test.cjs`, `detail-contract-recovery.test.cjs` and `backend/tests/integration/test_group3_api_contract.py`.

### Independent review and browser-driven corrections

Ramanujan independently reviewed wrappers/shared pagination/primary pages, ran **77 focused tests**, checked real Query cache behavior and six additional pagination boundaries. Its detail review found a P2: closing the conflict dialog bypassed dirty-draft confirmation because protection depended on dialog visibility. Two `outside Reload protects retained dirty draft after closing conflict dialog` regressions were **RED, 2 failed**, then passed after recovery used actual dirty state irrespective of dialog visibility. Its rereview ran **99 tests** (then-current 97 detail plus two responsive tests) and found no remaining scoped P1/P2.

Real browser testing exposed two additional defects. Long select-option names caused horizontal overflow in the videos filter grid; all six widths reproduced this before constraining those two selects locally. Later, successful edit acknowledgment left the saved form dirty, causing archive-conflict Reload to ask about an already-saved draft. Two `successful edit clears saved dirty state and prior error before archive recovery` cases were **RED, 2 failed**, then passed after both edit-success callbacks reset canonical API-acknowledged fields and revision. The four dirty-recovery followup regressions passed together.

Anscombe independently rereviewed that final acknowledgment fix, dirty-draft protection, failed reload preservation, retry guards and product-context preservation, read-only. **111 focused tests passed** (99 recovery plus 12 neighbors). No high-confidence P1/P2 remained in that bounded rereview. Implementer reports and agents stopped by usage limits were not counted as independent approval.

### Final verification

- `npm test` from `frontend`: **430 passed, 0 failed/cancelled/skipped**, duration **13068.6811ms**, after the final acknowledgment fix. Existing proxy, history, workspace, selectors and product-edit tests remain included.
- `npm run typecheck` and `npm run lint`: exit **0**, after the final source fix.
- `node tests/build-preserving-next-env.cjs`: exit **0**, final production build after the acknowledgment fix; protects the original user-owned next-env.d.ts.
- Backend `python -m pytest tests/integration/test_group3_api_contract.py -q --tb=short`: **93 passed in 16.63s**. Real FastAPI HTTP routes with isolated SQLite/auth fixtures test all eleven endpoint contracts, ordering, pagination bounds/defaults, individual filters/scopes, parent changes, typed revision conflicts and explicit successful retries.
- Group 1/2 targeted protection: `python -m pytest tests/unit/test_storyboard_service.py tests/unit/test_execution_groups.py tests/unit/test_media_cancellation.py tests/integration/test_generate_all.py tests/integration/test_generation_contract_api.py tests/integration/test_generation_races.py tests/integration/test_asset_claims.py tests/integration/test_dependency_invalidation.py tests/integration/test_output_file_persistence.py -q --tb=short`: **146 passed in 12.49s**. Full frontend includes all **43 proxy tests**.
- Backend `python -m ruff check .` and `python -m compileall -q apps workers tests migrations main.py`: passed.
- `node tests/group3-browser.cjs http://127.0.0.1:18043`: **72/72 cases passed, 2286/2286 checks passed, exit 0**, on the final production build. Six widths: 360, 480, 768, 1024, 1280, 1440. There were **0 unexpected console errors, 0 warnings, 0 runtime/page errors and 0 blocked/unmatched requests**; 132 controlled HTTP console errors correspond to deliberately injected failure scenarios. The runner uses isolated API fixtures and records actual page navigation, requests, loading/error states, scoped retry, dirty recovery, edit/archive conflicts, parent navigation, overflow and browser diagnostics in `tasks/evidence/group3-browser-matrix.json`; representative screenshot `tasks/evidence/group3-viewports.png`.
- Final tracked `git diff --check` and no-index whitespace checks for new Group 3 source/test/report files: passed after removing one trailing blank line in the browser runner. That whitespace-only correction did not alter the tested browser behavior. Branch/HEAD remain unchanged and nothing is staged.

Verification limits: browser API responses are controlled fixtures; they do not prove live authentication/CSRF, DB or provider execution. Backend contract tests invoke actual FastAPI routes but use isolated SQLite and injected authentication, not PostgreSQL concurrency. Backend production source and concurrency behavior did not change, so the full backend and PostgreSQL lanes were **NOT_RUN** for Group 3; Group 2 results above are historical evidence only. Pagination fetches requested pages rather than eagerly downloading the dataset; inactive Query cache uses its existing five-minute GC, not a fixed entry-count limit. This closeout does not declare whole-system production readiness.

```text
G3_PRIMARY_PAGINATION: PASS
G3_API_PAGE_SIZE_CONTRACT: PASS
G3_REVISION_CONFLICT_RECOVERY: PASS
G3_RELATED_QUERY_ERROR_STATES: PASS

GROUP_1_REGRESSION: PASS
GROUP_2_REGRESSION: PASS
FRONTEND_FULL_TESTS: PASS
TYPECHECK: PASS
LINT: PASS
BUILD: PASS
BACKEND_CONTRACT_TESTS: PASS
BACKEND_FULL_TESTS: NOT_RUN
POSTGRESQL_TESTS: NOT_RUN
UI_BROWSER: PASS
INDEPENDENT_REVIEW: PASS
GROUP_3_CLOSEOUT: PASS
```

No stage, commit, push, reset, stash, clean or deployment. No production DB/MinIO or environment secrets changed. Stop after Group 3.

### Followup review — pending edit acknowledgment race and UI/UX audit

The subsequent code-quality review found a further P2 on both Project and Product detail pages: while PATCH was pending, the fields remained editable. An earlier save acknowledgment reset the form and closed the dialog, discarding edits entered after submission. Real Chromium reproduction held the PATCH response, submitted `Submitted A`, entered `New unsaved B`, then released the response; reopening showed only `Submitted A` on both pages. This supersedes the earlier no-findings verdict for mutation timing.

Regression `${kind}: pending save locks fields and prevents close or reopen from replacing draft` reproduced **2 failed, 0 passed** before production edits. Both pages now disable the fieldset during React Hook Form submission, guard dialog close/cancel and opening handlers, and disable the external edit action. Recovery locking remains intact. A failed save unlocks the unchanged draft; a successful save still resets to canonical acknowledged fields/revision. The shared Dialog component and backend production code were not changed.

The two new tests are GREEN. Full `npm test`: **432 passed, 0 failed/cancelled/skipped**, duration **15207.2513ms**. Typecheck, final lint and production build passed. The browser runner now also holds actual PATCH responses for both pages at every width and checks disabled inputs/cancel, Escape/close-icon retention, successful acknowledgment, failed-save unlocking and draft preservation.

Final followup browser: **84/84 cases, 2646/2646 checks passed**, exit 0, at all six widths. **0 unexpected console errors, warnings, runtime/page errors or blocked traffic**. The 144 controlled HTTP errors are deliberately injected failure cases. Evidence replaces the prior browser matrix at `tasks/evidence/group3-browser-matrix.json`.

Curie independently rereviewed pending-save guards, callback state, failure retention and recovery behavior read-only, ran **101/101 focused tests** and checked browser-runner syntax. No high-confidence P1/P2 remained in the pending-save fix. Its tests mock RHF state and it did not execute the browser runner; main performed the real delayed-PATCH browser matrix above.

UI/UX audit by Nietzsche, using the ui-ux review branch: **four outstanding P2 findings**, below. These remain review findings, not changes included in the authorized pending-save correction. Functional test gates above remain PASS; they are not a blanket accessibility approval. Followup counts supersede the earlier 430-test/72-browser-case counts. UI_UX_REVIEW: REQUEST_CHANGES.

| # | Classification | Location and observed defect | Corrective design | Evidence |
|---|---|---|---|---|
| 1 | Broken keyboard interaction / P2 | Shared `components/ui/dialog.tsx:40`, Project/Product conflict dialog: Tab from Cancel escapes to body while Save is disabled. | Exclude disabled/hidden targets; wrap focus among enabled controls, including reverse Tab. | Real Chromium at 360px with fixture 412. |
| 2 | Missing accessible label / P2 | `app/videos/page.tsx:169`, format/status filters have no associated label, aria-label or aria-labelledby. | Add explicit accessible names consistent with the existing filter controls. | Actual DOM at 360/480px. |
| 3 | Missing validation association / P2 | Shared `components/ui/input.tsx:29`, `select.tsx:38`, `textarea.tsx:29`: create-video errors are rendered but fields have no aria-invalid or error aria-describedby. | Stable error IDs, field associations and invalid state; preserve existing caller-provided descriptions. | Submit empty `/videos/new`; measured title/project/brief fields at 360/480px. |
| 4 | Small touch target / P2 | Retry controls in `app/videos/page.tsx:133` and `app/videos/new/page.tsx:208` are too small to tap reliably. | Use the existing Button or increase actual clickable padding while preserving action handlers. | Selector retry 35.34×16px, video-list retry 19.5px high at 360/480px. |

No horizontal overflow was measured in the audited mobile states. Initial `/videos` header overlap was not reproduced: topbar bottom/title top were 105px/147.80px at 360px and 61px/103.80px at 480px; hit-testing reached H1. The earlier screenshot shows a scrolled capture below a sticky header, not evidence of a broken initial layout. No purely aesthetic preference was promoted to a defect.

Audit coverage: actual fixture browser checks at 360/480px for videos, new-video, dashboard, admin error tabs, upload and auth-shell loading/error; Project/Product conflict keyboard behavior at 360px. Main's six-width 84-case matrix separately covers primary/related lists, recovery and pending saves. Video detail, assembly, final versions and login were not exercised by the additional UI audit. Fixtures do not verify live production data, auth or providers. No new visual redesign, backend or infrastructure change was made.

The ui-ux skill's review branch requires choosing review rows before new UI changes. The four rows are reviewable proposals awaiting user selection; no additional authorization is needed for the already-completed pending-save fix.

### Approved UI/UX corrections — all four audit rows

The user approved all four rows. The earlier UI_UX_REVIEW: REQUEST_CHANGES records the pre-fix audit; the following section supersedes those outstanding findings.

| Audit row | Implemented correction | RED and regression evidence |
|---|---|---|
| 1: Dialog focus | `components/ui/dialog.tsx` computes current enabled, visible, non-inert controls for initial focus and Tab boundaries, excludes negative tabindex/disabled fieldset descendants, wraps in both directions and falls back to the dialog container. A callback ref keeps Escape current without tearing down focus restoration on every callback change. | `dialog-focus.test.cjs`: **6 failed, 0 passed** before source changes, then **6/6 passed**. Covers disabled Save, hidden/inert/negative-tabindex controls, outside/no-target focus and callback update. Actual conflict-dialog Tab/Shift+Tab added to both Project/Product cases at all six widths. |
| 2: Filter labels | `app/videos/page.tsx` gives format and status selects explicit, distinct accessible names. Existing filtering and page-reset handlers remain intact. | Video-filter/retry original suite: **10 failed, 1 passed** before source changes; **11/11 passed** after correction. Browser checks actual native select accessible names. |
| 3: Validation association | Shared Input/Select/Textarea assign stable IDs with React.useId when callers omit IDs, associate labels/error paragraphs, expose aria-invalid on errors and append error IDs without discarding caller help descriptions. Explicit no-error ARIA values, refs, props and handlers are retained. | `field-accessibility.test.cjs`: **19 failed, 18 passed** before source changes; **37/37 passed** after. Real React SSR covers all three controls and unique IDs; actual create-video browser submission checks title/project/brief errors. |
| 4: Retry touch targets | Five relevant Retry controls in video list/create use the existing shared Button, query-scoped disabled state and unchanged retry callbacks. They remain type=button, so create-form retries cannot submit the form. | Portable page/shared-Button/Tailwind tests cover emitted HTML, sizing, callbacks, fetching isolation and recovery. Six-width browser measures actual targets: at least 36px high and 24px wide. |

New default-suite tests do not depend on an Admin-specific browser installation. An initial test implementation used the bundled browser path; main required a portable SSR/Tailwind/handler replacement before final gates. Actual native browser verification remains in the standalone fixture runner.

Final `npm test`: **486 passed, 0 failed/cancelled/skipped**, duration **18278.0062ms**. Final typecheck, lint and production build passed. Backend production source was unchanged; no new backend/DB lane was required for these accessibility corrections.

Popper independently reviewed the four production corrections and scoped regressions read-only: **54/54 tests passed**, no high-confidence P1/P2 in the reviewed corrections. Its snapshot preceded the portable replacement of the video-filter test; main inspected the equivalent assertions and ran the final complete suite afterward. Actual browser verification is owned by main, not attributed to that reviewer.

Final Chromium matrix: **96/96 cases and 2910/2910 checks passed**, exit 0, at 360/480/768/1024/1280/1440px. No unexpected console errors, warnings, page/runtime errors or blocked requests. The 234 controlled HTTP console errors are intentional fixture failures. Both conflict dialogs keep Tab and Shift+Tab inside enabled controls at every width; actual list/create Retry targets measure at least 36px high and 24px wide; native filter names and rendered create-video validation associations pass at all widths. No page-wide horizontal overflow was measured in these tested states.

Browser evidence: `tasks/evidence/group3-browser-matrix.json`. Post-fix images: `tasks/evidence/ui-ux-dialog-360.png`, `tasks/evidence/ui-ux-videos-360.png`, `tasks/evidence/ui-ux-create-360.png`. Main visually inspected the conflict dialog and create form. Pre-fix numerical measurements and reproductions are recorded in the audit above; no before-image comparison was fabricated.

```text
UI_DIALOG_KEYBOARD: PASS
UI_FILTER_ACCESSIBLE_NAMES: PASS
UI_VALIDATION_ASSOCIATIONS: PASS
UI_RETRY_TOUCH_TARGETS: PASS
FRONTEND_FULL_TESTS: PASS (486)
TYPECHECK: PASS
LINT: PASS
BUILD: PASS
UI_BROWSER: PASS (96 cases / 2910 checks)
INDEPENDENT_REVIEW: PASS (scoped production corrections)
APPROVED_UI_CORRECTIONS: PASS
```

Final whitespace checks passed for tracked changes and new test/report files. Branch `codex/production-safety-fixes`, HEAD `01b740d8be4d58d3bd15bc46dd9842afb48af8bb`, no staged changes. Main's loopback test server was stopped after verification. No commit/push/deploy or production DB change.

This is a correction of four measured issues, not a full redesign or a claim that every route/state of the app has been accessibility-audited. Browser traffic uses isolated fixtures, not production auth/DB/provider execution. The prior audit's untested routes remain untested by this supplemental run.

### Screenshot-driven UI simplification and navigation help

The user explicitly preferred removing the unclear auxiliary search fields. Removed loaded-project/product searches from video filters and creation, and the loaded-brand search from the product-list filter. The actual selectors, current selections outside page one, Load More and scoped Retry remain. The separate brand selector search in product creation remains a separate use. Visible labels and responsive aligned filter grids replace the uneven toolbar; the video search wrapper has an explicit height so its absolute icon stays inside the input. The mobile create-form Cancel label no longer splits across lines.

Sidebar destinations now show their name and purpose on hover and keyboard focus. A body portal avoids sidebar clipping; pointer transfer into the tooltip keeps it open. Escape works with hover-only tooltips and keyboard focus. Forced dismissal clears hover/timers, preventing stale portal hover from keeping a later tooltip visible after blur. Click, resize, scroll and unmount cleanup are covered. Assembly is labelled “Ghép & xuất video”; the page explains joining selected scene clips into MP4 with optional music and output size/FPS. Creation explains that AI clip sizing is workflow/scene configuration, while final MP4 sizing is selected during joining/export. No new resolution presets or backend capability were invented.

Franklin implemented selector simplification and its regressions. Before source changes, three selector regressions failed and one passed because auxiliary fields were present; the sizing-help regression separately failed (one failed, four passed). Seven selector tests now pass, including page-two reachability, retained selected IDs, URL project, null product and errors/retries. There was no claimed icon-layout RED unit reproduction; the user's screenshot and actual browser measurements supply layout evidence.

Kepler independently reviewed the tooltip and found hover-only Escape, tooltip pointer transfer and stale hover after forced dismissal issues. Each was corrected; the extended helper regression reproduced the stale-hover failure before the fix. Final read-only rereview found no remaining actionable scoped tooltip issue and ran the tooltip test successfully. Main owns actual DOM verification.

Full frontend suite: **490 tests passed**, exit 0 (`node --test --test-reporter=dot tests/*.test.cjs`). An earlier run alongside the production build had 489 pass/one failure in the preexisting 40ms slow-client timer test: too few interval chunks arrived. The separate full run passed without changing that test or proxy source. Final Cancel-class-only adjustment then passed the eight selector/tooltip tests; lint and production build/typecheck passed again. This is not a backend test claim.

Broad fixture Chromium matrix after toolbar/selector/label changes: **96 cases / 2994 checks passed**, no unexpected console/page errors, warnings or blocked traffic; intentional controlled HTTP errors remain fixture failures. That broad run preceded the final tooltip-dismissal refinements. The final targeted DOM runner verifies all six links at 360/480/768/1024/1280/1440px: hover-only Escape, transfer into the description, focus/blur after portal dismissal, keyboard Escape and actual tooltip X/Y/width/height bounds. It also checks removed inputs, the mobile Cancel label and create-form horizontal overflow. Evidence: `tasks/evidence/sidebar-tooltip-browser.json`; broad evidence: `tasks/evidence/group3-browser-matrix.json`.

Post-change screenshots: `tasks/evidence/ui-simplified-videos-360.png`, `tasks/evidence/ui-simplified-create-360.png`, `tasks/evidence/ui-simplified-products-1280.png`. These use long-name fixture data and isolated API responses. They do not prove live auth, production DB, provider execution or every possible UI state. Backend production code was unchanged by this UI followup. No commit, push or deployment.

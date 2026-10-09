# Independent Phase0 Fixround2 re-review

2026-10-05; workspace `D:/project/ai-video-studio`.

**Result: remaining round-1 P2 CLOSED. Spec PASS; quality PASS within this scope.**
No actionable finding established in the changed selection/batch callbacks, pending-action hook, new retirement tests, or existing batch test support.

Scope: Fixround2 top section of `tasks/archive/reports/production-phase0-report.md` and the single P2 in `tasks/archive/reports/production-phase0-rereview-1.md`. No implementation edits, subagents, commits, research, broad tests, backend reruns, or build; only this report is written. Unrelated Phase0/later requirements are excluded.

## Closure proof

- `frontend/app/videos/[videoId]/page.tsx:255-257`: actual manual-selection `onSuccess` retires the pending action before detail invalidation. Executed select/deselect/reselect callbacks rotate the key and prepare from fresh detail despite the stale rendered DTO. Selection failure preserves the action.
- `page.tsx:324-328`: retirement requires the real `ApiClientError` class, HTTP 409/412, and `IDEMPOTENCY_KEY_REUSED`/`REVISION_CONFLICT`. Independently executed all four status/code combinations: the rejected replay uses the original body/key; the following attempt refetches and sends a new key with fresh eligibility and revision preconditions. Lookalike errors and unrelated 409/412 codes preserve the request.
- `page.tsx:310-316` and `frontend/lib/hooks/use-generate-all-action.ts:21-32`: batch preparation awaits refetch and uses its returned DTO. A deferred-refetch probe sends nothing until resolution, then uses fresh selected IDs and video/scene revisions rather than the old render.
- `page.tsx:312-314` and `use-generate-all-action.ts:22-24,35-37`: stale data plus an error, absent data, and a rejected refetch promise each send no batch on two consecutive attempts. Successful recovery still refetches and rotates the retired key; the refresh requirement survives failure.
- `use-generate-all-action.ts:26-32`: transport/503/500 failures preserve the original payload, revision preconditions, and key through partial and full automatic completion. Independently checked 503/500 conflict-code responses too; status gating prevents unintended retirement. Existing editor-change rotation tests continue to pass.

Quality: retirement/reset state is localized; the page classifies definitive errors explicitly; the new tests execute production TypeScript callbacks and hooks using the real error class rather than copying their logic. No separate actionable quality issue found. These are callback/hook checks with query/API boundaries faked, not live browser/backend claims.

## Independent narrow verification

Commands ran from `frontend`; existing `tests/load-typescript.cjs` harness reused without edits.

| Check | Result |
| --- | --- |
| Command-local TEMP/TMP=`frontend/.tmp`; `yarn node --test tests/generate-all-retirement.test.cjs tests/generate-all.test.cjs` | **18 passed, 0 failed/skipped**, exit 0; Node 1750.0599ms, Yarn 1.88s |
| In-memory Node stdin callback probes via `yarn node` | **16 bounded cases PASS**, exit 0; Yarn 1.89s: four definitive gates, eight ambiguity gates through partial/full completion, three failed-refetch recovery cases, one selection/deferred-refetch ordering case |
| Root: `git diff --check -- 'frontend/app/videos/[videoId]/page.tsx'` | Exit 0; existing LF/CRLF notice only |

Supplied evidence retained separately, not rerun or relabelled: implementer RED **6 fail/3 pass**, GREEN new+old **18 pass**, full frontend **27 pass**, ESLint/typecheck pass, `next-env.d.ts` preserved. Controller post-round-2 production Next build **PASS in 8.71s**, preserving wrapper. Round 2 is frontend-only; round-1 real backend batch **17 pass** and controller full backend **250 pass/11 skip** stand; no new backend qualification claim.

## Scoped source stability

SHA-256 captured before checks and compared after checks: **all nine scoped production/test/support files unchanged**. Each value below is both before and after; the report is excluded.

| File | SHA-256 before = after |
| --- | --- |
| `frontend/app/videos/[videoId]/page.tsx` | `073E4DD1F827525EE138DCBE5D7E04447E5C9E7582695ABC0ED45BFF6626EA02` |
| `frontend/lib/hooks/use-generate-all-action.ts` | `81F6B53DD724F4DD138176DA42AB2E8D6994FD9FCCA16009145C99815F9BAA89` |
| `frontend/tests/generate-all-retirement.test.cjs` | `10064FC8D1521AE764E354DB626DE6BB5E16E49DB9EF544A54535080A9A900E7` |
| `frontend/tests/generate-all.test.cjs` | `D73DBFFA21E798309B0AEAC1D50866ADC36F716565611B6F270BF6C048A5AA96` |
| `frontend/tests/load-typescript.cjs` | `08639FD8D6F2504F5169F67B561D686363F32D3E62E6B6B827695E2CE0CBA59E` |
| `frontend/lib/api/generations.ts` | `6F4AFB943A1BB47B3DD8EBD0F705115C583FC15F12B3D9516CCE9C9F236C3CAC` |
| `frontend/lib/api/client.ts` | `6F2F19F89E0903BF8FB586D1E75B6360CF62BE5EC15D958F7630F03BC15BED8C` |
| `frontend/lib/api/errors.ts` | `3DBC67351379F022B23844818FE9CFCEF2E4601C92400F86AB14C989CFCCA3BB` |
| `frontend/lib/hooks/use-idempotent-action.ts` | `63ABEB6830CE06DBF02CEF8190146E84672C6DD666DBC8EB7C88C0AEEA438FCB` |

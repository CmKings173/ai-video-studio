# Audio scope and final-version error corrections

Scope: UTF-8 correction attachment `fe7a7858-609d-47bd-b3d2-e354882c6a4f/Pasted text.txt`, sections 16 and 19 only.

Workspace: `D:/project/ai-video-studio`, branch `codex/production-safety-fixes`, connector HEAD `01b740d`. The mandatory ai-video-studio connector workspace_info/git_status calls confirmed a dirty shared workspace, no staged changes, and no conflicts. Existing dirty work was preserved. No commits, push, reset, stash, database changes, or GPU execution.

## Section 16: background audio ownership

The picker previously queried the global first 50 assets and filtered audio locally. It now waits for the video's project scope and requests `kind=AUDIO`, `status=READY`, `project_id=video.project_id`, and `product_id=video.product_id` when linked. The query key contains the same scope and filters.

Assets have mutually exclusive project/product ownership (`single_business_scope`), and the existing assets API treats these two filters as a union. The assembly service now enforces the equivalent rule under the existing Asset row lock: the audio belongs to the video's project OR its non-null linked product. Unrelated-project, unrelated-product, unowned, and product-owned audio on an unlinked video raise `BACKGROUND_AUDIO_SCOPE_INVALID` (422) before a FinalVideo is created or Video enters ASSEMBLING. Existing readiness, kind, checksum, deletion checks and immutable manifest behavior remain in place.

Changes:

- `backend/apps/api/app/services/assembly_service.py`: ownership validation shared by API and direct service callers. No assembly API changes were needed because its creation path calls this service.
- `frontend/app/videos/[videoId]/assembly/page.tsx`: scoped READY AUDIO query, disabled until project identity is available.
- `backend/tests/integration/test_assembly_audio_scope.py`: real SQLite service/API entry-point cases, allowed project/project-only/product audio, rejected unrelated/unowned/unlinked audio, frozen checksum and zero-final-persistence assertions.
- `frontend/tests/assembly-audio-final-errors.test.cjs`: picker query scope/key, loading guard, and final-version error cases.

## Section 19: final-version errors

Cancel and download failures now appear in the existing app Alert component through page state. Browser alert calls were removed. Cancellation clears the old error before a new mutation and on success; downloading clears it before a retry. Download loading state and successful link opening are preserved.

Change: `frontend/app/videos/[videoId]/final-versions/page.tsx`.

## TDD and verification

- RED backend: 8 failed / 4 passed. Both API and direct service accepted all four disallowed ownership cases before the fix.
- RED frontend: all 5 new tests failed, reproducing global audio querying, lack of a loading guard, and browser alerts.
- GREEN backend (expanded with project-only success): **22 passed**. Command: `.venv/Scripts/python.exe -m pytest -p no:cacheprovider tests/integration/test_assembly_audio_scope.py tests/integration/test_assembly_manifest.py tests/integration/test_assembly_dependency_freshness.py -q` with `PYTHONDONTWRITEBYTECODE=1`.
- GREEN frontend: **9 passed**. Command: `node --test tests/assembly-audio-final-errors.test.cjs tests/assembly-settings.test.cjs tests/current-final.test.cjs`.
- PASS: Ruff on the modified service and new backend test.
- PASS: `npm run typecheck`.
- PASS: ESLint on both modified pages and the new frontend test.
- PASS: scoped `git diff --check` (existing CRLF conversion notices only).

Limits: backend focused tests use isolated SQLite; no PostgreSQL lane, live browser verification, full suite, build, or Docker build was run by this slice. Parent owns global validation and application-database work. Frontend behavior was verified with the existing compiled-component test harness.

GPU/H3 runtime qualification: NOT_RUN - no GPU available.

Disposition: sections 16 and 19 complete with no remaining concrete blocker in this slice. This is not a verdict on the parent's overall correction round.

## Section 11 follow-up: assembly stale-selection wording

The assembly scene row now distinguishes a historical selected clip whose freshness is false (`Clip cần tạo lại`) from an absent selection (`Thiếu clip`). Fresh selections retain `Ready`. The readiness warning no longer describes stale selections as never selected. Historical selection IDs are not modified; existing submit gating still requires all enabled scenes to be fresh.

Only the assembly page and its frontend tests changed in this follow-up; no backend changes. Added cases verify fresh/stale/missing row labels and submit gating, preservation of the historical selection, and exclusion of disabled stale scenes from gating.

- RED: stale row regression failed; the other 8 cases passed.
- GREEN: **13 passed** across `assembly-audio-final-errors.test.cjs`, `assembly-settings.test.cjs`, and `current-final.test.cjs`.
- PASS: typecheck and scoped ESLint.
- PASS: scoped diff check.

Page changes refrozen for the parent's frontend full-suite/build/Docker validation. Live browser verification remains NOT_RUN by this slice.

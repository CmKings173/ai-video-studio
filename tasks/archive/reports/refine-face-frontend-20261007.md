# Refine / Face Refine frontend sidecar — 2026-10-07

## Source and scope

- Workspace/root: `D:\project\ai-video-studio`.
- Branch: `codex/production-safety-fixes`.
- HEAD: `01b740d8be4d58d3bd15bc46dd9842afb48af8bb`.
- Existing dirty working tree preserved; no commit, push, or subagents.
- Read requirements sections 13, 14, 16, 17, frontend AGENTS.md, and the installed Next.js use-client documentation.
- Frontend changes only, plus this requested report. Backend, importer, workflows, Docker, and lockfiles remain outside this sidecar.

## History P2

History details now receive the current scene and complete loaded storyboard. They use the existing `directGenerationRequiresAggregate` helper to disable snapshot Regenerate and Variation when current continuity or saved Motion Context requires aggregate execution. This includes a CUT head with an old standalone parent. The actions explain that Generate All is required, and both button callbacks and page mutation functions guard submission.

Independent CUT scenes retain their qualified single-scene derivative actions. Reuse configuration, ready-output downloads, and historical prompt display remain available.

## Refine / Face Refine fail-closed controls

Controls retain scope-specific capability selection: single-scene for direct execution, aggregate for aggregate settings. The new `qualifiedDirectorFeature` helper additionally requires the Director provider, its qualified support flag, and an enabled feature setting in qualified Director settings. Support flags or upstream source capability alone cannot enable the controls.

Existing complete settings comparison remains the save/execution gate. Tests demonstrate that exact combined aggregate Refine/FaceRefine settings can be saved while standalone preview/submission remains blocked. Changed Refine passes or FaceRefine confidence fail the gate. Separate individually qualified examples cannot qualify a combined request. All capability examples used by these tests are synthetic fixtures, not production runtime evidence.

## Changed paths

- `frontend/app/videos/[videoId]/page.tsx`
- `frontend/components/generation/history-details.tsx`
- `frontend/components/generation/generation-editor.tsx`
- `frontend/lib/generation/capabilities.ts`
- `frontend/tests/history-aggregate.test.cjs`
- `frontend/tests/generation-editor.test.cjs`
- `tasks/archive/reports/refine-face-frontend-20261007.md`

## Verification

TDD: before implementation, the new history chain test and missing-settings feature-control test both failed on the reported behavior (11 passing, 2 failing). After the correction and expanded coverage:

- PASS: `node --test tests/history-aggregate.test.cjs tests/generation-editor.test.cjs tests/generation-actions.test.cjs tests/generation-capabilities.test.cjs tests/continuity-generation.test.cjs tests/history-settings.test.cjs` — 43 tests, 0 failed, 0 skipped.
- PASS: `npm run typecheck`.
- PASS: `npm run lint`.
- PASS: scoped `git diff --check`.
- NOT_RUN: full frontend test suite, build, Docker build, backend tests — focused frontend sidecar scope; parent owns integration validation.

GPU/H3 Refine runtime qualification: NOT_RUN - no GPU available.

Frontend sidecar complete. This report makes no claim about backend/importer completion or GPU execution.

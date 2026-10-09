# Independent generation frontend checkpoint review

Date: 2026-10-06. Baseline: `01b740d`; current source in `D:/project/ai-video-studio` is authoritative. Applied `code-review-and-quality`.

**Spec: REQUEST CHANGES. Quality: REQUEST CHANGES.** Three actionable P2 findings below. No P0/P1 finding established. Correctness gaps prevent approval; the component/helper decomposition itself improves the existing page structure.

## Findings

### 1. [P2] Reject unsafe fixed seeds before saving scene configuration

Primary location: [generation-editor.tsx:67](D:/project/ai-video-studio/frontend/components/generation/generation-editor.tsx:67). Supporting locations: [generation-editor.tsx:93](D:/project/ai-video-studio/frontend/components/generation/generation-editor.tsx:93), [generation-editor.tsx:106](D:/project/ai-video-studio/frontend/components/generation/generation-editor.tsx:106).

**Trigger:** Select FIXED, type/paste `9007199254740993`, then save the scene configuration. The number input converts this to `9007199254740992` before storage. Its `max` attribute does not prevent invoking the save button: this is not a submitted form and the callback performs no numeric validation.

**Impact:** Preview/generation correctly rejects unsafe integers, but saving bypasses that check and persists a rounded seed. Generate All subsequently inherits that FIXED seed from stored configuration, bypassing the editor's safe-integer guard. The user can therefore generate with a different seed from the one entered. Keeping unqualified configurations saveable does not require accepting numerically invalid seeds.

**Bounded proof:** Loaded the actual editor through installed `tests/load-typescript.cjs`, with the same state-hook technique as the existing editor tests and a capturing `patchScene` boundary. Called its seed policy/input callbacks and actual save callback. Asserted save was enabled, one PATCH occurred, its seed was unsafe and rounded, and `batchSummary` retained that seed. A separate read-only Python `-B` invocation of the actual `SceneGenerationConfig` and `GenerationService.effective_request` confirmed the backend accepts and inherits it when global settings are omitted. Both processes exited 0:

```text
PROOF seed: typed=9007199254740993; save enabled; PATCH FIXED seed=9007199254740992; batch summary retains rounded seed.
PROOF backend: SceneGenerationConfig accepts rounded unsafe seed; effective_request with no global overrides inherits FIXED/9007199254740992.
```

**Required remedy:** Validate FIXED seed independently of capability availability before PATCH, enforcing a nonnegative safe integer. Preserve the raw input until validation if needed to prevent silently accepting precision loss. Add a save-boundary regression, rather than only testing `generationInputProblems`.

### 2. [P2] Hydrate saved input assets independently of the first picker page

Primary location: [generation-editor.tsx:38](D:/project/ai-video-studio/frontend/components/generation/generation-editor.tsx:38). Supporting locations: [generation-editor.tsx:40](D:/project/ai-video-studio/frontend/components/generation/generation-editor.tsx:40), [capabilities.ts:35](D:/project/ai-video-studio/frontend/lib/generation/capabilities.ts:35).

**Trigger:** Open a saved scene configuration, or explicitly reuse a parent's settings, whose READY frame/reference asset is older than the newest 100 READY project/product assets. The editor requests only the first page, ignores `total`, and uses those items as the entire validation inventory.

**Impact:** The saved asset is shown as an ID or absent picker option, incorrectly reported as not READY/wrong kind, and preview/generation is disabled even though the backend accepts the asset. Generated outputs also populate the READY inventory, so an unchanged saved configuration can stop working as more outputs accumulate. The finding is the new saved-input validation failure, not a reopening of general Phase0 picker pagination.

**Bounded proof:** Actual editor with a synthetic qualified first-frame capability and a Page boundary containing 100 newer READY videos, `total=101`, and a saved READY image on page 2. Invoked the query callback and verified one `size:100` request with no page override; preview was disabled and the input ID appeared in the error. The actual validator returned no problems for the identical settings when the READY image was added to the inventory. Process exited 0:

```text
PROOF persisted input: total=101/page_size=100; saved READY old-frame absent on page 1; preview disabled and asset reported not READY; same input validates when asset is hydrated.
```

Backend evidence: [assets.py:65](D:/project/ai-video-studio/backend/apps/api/app/api/assets.py:65) sorts newest first and applies pagination. [generation_service.py:95](D:/project/ai-video-studio/backend/apps/api/app/services/generation_service.py:95) resolves requested assets directly by ID.

**Required remedy:** Resolve every selected/saved asset by ID and merge those results into the picker inventory before validation. Distinguish unresolved/loading IDs from absent or invalid assets. Offer bounded pagination/search for selecting older assets.

### 3. [P2] Show frozen FPS and resolved duration alongside measured output

Primary location: [history-details.tsx:30](D:/project/ai-video-studio/frontend/components/generation/history-details.tsx:30).

**Trigger:** Inspect a completed generation with a declared 24 FPS profile and duration alignment, especially when observed output differs. The history component displays expected/measured canvas separately, but only measured FPS and video duration. It never renders the frozen FPS, requested duration, or `resolved_duration_seconds`.

**Impact:** The required declared-versus-observed FPS/duration comparison is missing. An editor cannot distinguish an aligned generation length from the scene's requested length, or assess output against the frozen contract. This is observable with ordinary v2 snapshots, not a pending delivery/enhancement integration.

**Bounded proof:** Rendered the actual history component through the installed harness with a synthetic v2 snapshot: requested duration 5s, frozen profile 24 FPS, 124 frames (valid `17k+5` alignment), resolved duration `124/24`, measured 23 FPS/4.9s. Traversed its actual `dd` nodes and asserted measured values existed while frozen FPS and resolved duration did not. Process exited 0:

```text
PROOF history (valid 17k+5 frame count): requested=5s; frozen=24 FPS/124 frames/5.166666666666667s; visible measured=23 FPS/4.9s; frozen FPS and resolved duration absent.
```

Backend evidence: [generation_service.py:825](D:/project/ai-video-studio/backend/apps/api/app/services/generation_service.py:825) stores `runtime_profile`; [generation_service.py:850](D:/project/ai-video-studio/backend/apps/api/app/services/generation_service.py:850) stores frames, resolved duration and requested duration.

**Required remedy:** Render explicitly labeled frozen FPS/resolved duration and requested scene duration alongside observed FPS/video duration. Keep truthful unknown values for historical snapshots.

## Review coverage and limits

Read the review brief first, controller report, frontend brief, exact backend generation contract report, packaged diff, `frontend/AGENTS.md`, and installed Next `use-client` documentation. Reviewed the new semantic tests before implementation, then editor, ordered-reference controls, acceptance/context helpers, history/reuse helpers, batch summary, API/type boundaries and page mutations. Read the existing batch replay/retirement tests and hooks to distinguish Phase0 behavior.

- **Spec:** Exact accepted text is copied without recomposition; settings/order/scene/revision context is frozen and checked. Late responses retain their originating context. Workflow IDs are pinned. Parent-only Variation/Regenerate and explicit revision-aware Reuse are wired correctly. Batch payloads inherit stored scene settings and retain the existing semantic replay mechanism. The three findings concern seed persistence, saved asset resolution and history observability.
- **Quality:** Correctness requires the changes above. Readability/architecture benefit from extracting the old generation dialog into focused components and pure helpers. No additional actionable security or performance regression was established within this slice. The paginated asset boundary should not be treated as a complete input model.
- **Verification:** Two bounded Node stdin processes using the installed TypeScript harness and one read-only Python `-B` schema/inheritance proof, all exit 0. Boundary fixtures are synthetic and establish callback/rendering behavior only. No new test files, source edits, subagents, server/browser/operator changes, full suites, lint, typecheck or builds were performed.
- **Author evidence:** Controller reports 53 tests, typecheck/lint/build success and real unqualified-browser configuration persistence. Those broad checks were reviewed as reported evidence, not independently rerun. They do not cover the demonstrated boundary cases. No qualified browser/GPU execution is claimed here.
- **Scope:** Delivery selectors and real enhancement capability integration remain explicitly deferred and are not findings. Project/product scope was checked: the asset API uses OR semantics, so no scope-filter bug is reported. Phase0 findings were not reopened.

Only `tasks/archive/reports/production-frontend-controller-review.md` was written by this review. Controller owns fix/delivery sequencing and subsequent verification.

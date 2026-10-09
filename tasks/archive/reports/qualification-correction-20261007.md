# Qualification correction handoff — 2026-10-07

Scoped qualification and capability changes are ready for the parent's full backend/PostgreSQL checks. No GPU inference, weight installation, filesystem weight measurement, or production qualification was performed. All execution evidence in the new tests is synthetic and stays in tests.

## Behavior and contract

- A common executable weight inventory covers UNET, CLIP, VAE, LoRA, UpscaleModelLoader, Director Refine latent upscaler and Face Refine detector literal filenames. Missing/dynamic filename bindings fail closed. Graph inventory and measured profile/evidence filename-digest maps must match exactly. Settings overrides also require the selected face detector's own measured digest.
- Single settings and aggregate cases bind all nine provenance fields: workflow_hash, slot_map_hash, model_hash, lora_hashes, comfyui_commit, custom_node_versions, profile_hash, weight_hashes, dependency_versions. Same filename with changed bytes invalidates previous optional-settings evidence.
- Single cases explicitly require task and base_canvas={width,height}, matching an executed baseline combination. settings_hash is director_settings_identity(settings, base_canvas=..., task=...), hashing all three values. Old settings-only hashes and missing input context intentionally fail closed.
- verified_settings, qualified_features and qualified_audio_modes accept optional keyword filters base_canvas and task. The capability endpoint supplies each row's resolved canvas and Director task for flags, settings presets and audio modes. Defaults preserve existing callers' profile-wide inspection behavior.
- DirectorPlanBuilder passes actual intent canvas/task to require_settings. require_frozen repeats exact single-scene matching; aggregate member snapshots defer their optional settings gate to the existing aggregate case qualification seam.
- Canonical canvas math is shared by both case qualification paths. Disabled/refine modes preserve base geometry; upscale and latent_upscale use megapixels*1024^2, source aspect, nearest-32 rounding and pinned minimum-canvas correction; zero/<0.1 MP use source 1.0 fallback. FL2V skip preserves base geometry, passes do not compound resize, Face Refine paste-back preserves the resolved output canvas.
- Source parity uses pinned local upstream a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb: nodes/director_refine.py, director/refine_pack.py, director/refine_sampling.py and lib/image_prep.py. Pure source functions were checked without importing GPU/runtime packages.

## Derivative insertion order — fixed after expanded authorization

The original derivative path added and flushed SceneGeneration before the plan-builder qualification call. The strengthened regression failed because catching the error inside an open transaction left an inserted derivative. The user expanded authorization to generation_service.py. Derivatives now receive an explicit new_id before constructing their output prefix; plan-builder qualification and snapshot construction complete before session.add/flush and before GenerationAsset writes. The ORIGINAL preparation path remains unchanged.

The regression catches rejected ORIGINAL, VARIATION and REGENERATE requests in the same transaction without a savepoint or rollback. It explicitly flushes, verifies no derivative rows and an unchanged parent snapshot, commits that transaction, then verifies the database again. This now passes without relying on exception rollback.

Follow-up verification command from backend:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/integration/test_director_qualification_corrections.py tests/integration/test_generation_preparation.py tests/integration/test_generation_contract_edges.py tests/integration/test_generation_contract_api.py tests/integration/test_generation_freshness.py tests/integration/test_director_corrections.py tests/integration/test_director_dynamic_aggregate.py tests/unit/test_director_qualification_corrections.py -q
```

Result: **134 passed in 26.95s**. Existing successful Director derivative API regressions and original creation paths pass. Ruff check and format --check pass for generation_service.py and the modified integration test. Follow-up touched exactly these two Python files and this report.

### Final P1/P2 review corrections

The same-transaction regression additionally asserts GenerationAsset count is zero after each caught failure and after commit, with zero derivative generations and the parent's snapshot unchanged. No nested transaction or rollback masks ordering.

test_director_output_canvas.py now requires the local audited checkout to exist, asserts HEAD equals a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb and git status --porcelain --untracked-files=all is empty. It loads lib/image_prep.py and director/refine_pack.py from immutable git show COMMIT:path objects, checks that all required pure functions exist, and executes only those pure function ASTs. Missing/dirty/wrong-HEAD source fails rather than skips. No mutable worktree file supplies the math oracle.

Final focused checks from backend:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_director_output_canvas.py tests/integration/test_director_qualification_corrections.py -q
.\.venv\Scripts\python.exe -m ruff check apps/api/app/services/generation_service.py tests/integration/test_director_qualification_corrections.py tests/unit/test_director_output_canvas.py
.\.venv\Scripts\python.exe -m ruff format --check apps/api/app/services/generation_service.py tests/integration/test_director_qualification_corrections.py tests/unit/test_director_output_canvas.py
```

Results: **29 passed in 4.17s; All checks passed; 3 files already formatted**. The preceding scoped command from the follow-up verification above, with tests/unit/test_director_output_canvas.py appended, passed **161 tests in 21.90s** before adding the explicit clean/HEAD assertions; the final 29-test check includes those assertions. No broad suite or application DB operations were run.

Final follow-up files changed: generation_service.py, tests/integration/test_director_qualification_corrections.py, tests/unit/test_director_output_canvas.py and this report.

### Nonempty asset association regression — reviewer P2 follow-up

The capability/creation regression retains an asset-free completed t2v parent, then registers a synthetic r2v workflow and a project-bound READY reference IMAGE with dimensions and a SHA-256 checksum. Rejected ORIGINAL, VARIATION and REGENERATE requests explicitly bind that asset. Their resolved asset list is therefore nonempty, while the expected persisted GenerationAsset count remains zero. After catching each qualification AppError in the same open transaction, the test flushes and asserts zero associations, zero derivative generations, unchanged generation IDs and unchanged parent snapshot/status/phase/workflow/generation number. The transaction commits normally and the test verifies those invariants again.

Sensitivity check: a process-only pytest plugin temporarily wrapped DirectorPlanBuilder.build to add the bound GenerationAsset before qualification for derivative intents (inherited_from present). The regression failed at its explicit post-rejection flush with an INSERT INTO generation_assets foreign-key error. This diagnostic confirms premature association additions are observed before rollback; it made no production file edits and the monkeypatch expired with that Python process. The normal implementation then passes.

Final focused commands from backend:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/integration/test_director_qualification_corrections.py -q
.\.venv\Scripts\python.exe -m ruff check tests/integration/test_director_qualification_corrections.py
.\.venv\Scripts\python.exe -m ruff format --check tests/integration/test_director_qualification_corrections.py
```

Results: **2 passed in 1.34s; All checks passed; 1 file already formatted**. Only tests/integration/test_director_qualification_corrections.py and this report changed for this follow-up. No production changes or broad tests. Dalton was not present among addressable task listings, and no agent ID was supplied, so targeted rereview must be forwarded through the parent.

The derivative regression supplies explicit width/height and timeline=[] so unrelated inheritance conversion does not prevent reaching the qualification seam. That follow-up exposed a separate standalone Director derivative bug: frozen internal timeline rows contain `asset_indices`, which the strict public `DirectorTimelineSegment` model rejects during `GenerationRequest.model_validate`. `_inherit_director_timeline` now projects frozen rows to the unchanged public fields while retaining the validated internal timeline for `_build_director_intent`; an explicit request timeline bypasses the inherited internal rows.

Coverage now includes a service integration regression for both VARIATION and REGENERATE with inherited, replaced, empty, and null timelines, plus a provider staging test that binds one of two staged reference images by ordinal and asserts only the selected image reaches the segment. The aggregate-member chain guard remains exercised by the existing correction tests.

Focused verification after this fix: **46 passed in 9.82s** across `test_director_timeline_inheritance.py`, `test_director_execution_contract.py`, `test_director_corrections.py`, and `test_director_qualification_corrections.py`. Ruff check passed and all three changed Python files were already formatted. The provider staging fixture is synthetic and does not represent GPU or production qualification.

## Verification

From D:/project/ai-video-studio/backend:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_director_qualification_corrections.py tests/unit/test_director_output_canvas.py tests/unit/test_executable_weights.py tests/unit/test_refine_face_qualification.py tests/unit/test_director_execution_contract.py tests/unit/test_director_dynamic_aggregate.py tests/unit/test_workflow_qualification.py tests/integration/test_director_qualification_corrections.py tests/integration/test_director_dynamic_aggregate.py tests/integration/test_workflow_qualification.py tests/integration/test_generation_contract_api.py -q
```

Result: **172 passed in 9.93s**. This includes real SQLite service creation, frozen retraction, capability endpoint filtering, task/canvas helper filtering, equal-output MP collision, old settings hash rejection, detector override rejection, same-filename digest changes, full single/aggregate provenance and pure pinned-source geometry parity.

Ruff check and ruff format --check on exactly the fourteen Python files below: **All checks passed; 14 files already formatted**. Initial default-sandbox formatter writes were denied; authorized scoped escalated formatting succeeded. No unrelated files were formatted.

## Exact files touched in this qualification slice

- D:/project/ai-video-studio/backend/apps/api/app/services/workflow_contracts.py
- D:/project/ai-video-studio/backend/apps/api/app/services/generation_service.py
- D:/project/ai-video-studio/backend/apps/api/app/services/director_run_service.py
- D:/project/ai-video-studio/backend/apps/api/app/services/executable_weights.py
- D:/project/ai-video-studio/backend/apps/api/app/services/qualification_binding.py
- D:/project/ai-video-studio/backend/apps/api/app/providers/minimax_h3_director/qualification.py
- D:/project/ai-video-studio/backend/apps/api/app/providers/minimax_h3_director/output_canvas.py
- D:/project/ai-video-studio/backend/apps/api/app/providers/minimax_h3_director/plan_builder.py
- D:/project/ai-video-studio/backend/apps/api/app/api/generations.py
- D:/project/ai-video-studio/backend/tests/unit/test_director_qualification_corrections.py
- D:/project/ai-video-studio/backend/tests/unit/test_director_output_canvas.py
- D:/project/ai-video-studio/backend/tests/unit/test_executable_weights.py
- D:/project/ai-video-studio/backend/tests/unit/test_refine_face_qualification.py
- D:/project/ai-video-studio/backend/tests/integration/test_director_qualification_corrections.py
- D:/project/ai-video-studio/backend/tests/integration/test_director_dynamic_aggregate.py
- D:/project/ai-video-studio/tasks/archive/reports/qualification-correction-20261007.md

New files: executable_weights.py, qualification_binding.py, output_canvas.py, three unit correction/inventory/canvas test files, integration/test_director_qualification_corrections.py and this report. Existing dynamic aggregate fixture changes only align synthetic weight inventory with the newly required auxiliary bindings; existing Refine/Face fixture changes provide complete synthetic provenance and signed input context.

No db/models.py or schemas/api.py edits. No commits, pushes, resets, stashes, checkouts or cleanup.

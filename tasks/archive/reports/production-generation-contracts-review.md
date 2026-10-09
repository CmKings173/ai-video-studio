# Independent backend generation-contracts review

Reviewed 2026-10-06 in `D:/project/ai-video-studio`, branch `codex/production-safety-fixes`, HEAD `01b740d8be4d58d3bd15bc46dd9842afb48af8bb`. Read the review brief, implementation requirements/report, H3 qualification research, original qualification review, relevant master-prompt sections A-J, diff package and authoritative current backend source. Applied `code-review-and-quality`.

**Spec verdict: REQUEST CHANGES.** The feasible contracts are substantially implemented, but reference-audio consumption, single-generation upgrade replay and fail-closed resolver handling have local gaps.

**Quality verdict: REQUEST CHANGES.** Four required P2 findings below. No P0/P1 finding established. Both original qualification P2 findings are **closed**. No style-only findings or unrelated Phase 0 issues are included.

## Required findings

### [P2] Require the acoustic VAE path before admitting reference audio

Location: [workflow_contracts.py:233](D:/project/ai-video-studio/backend/apps/api/app/services/workflow_contracts.py:233), with the native contract checks at [workflow_contracts.py:338](D:/project/ai-video-studio/backend/apps/api/app/services/workflow_contracts.py:338).

**Trigger:** a native Ref2VA graph has a `LoadAudio.audio` symbolic slot connected to `ref_audios.ref_audio_0`, but the conditioning node has no `audio_vae` input. The loader/slot checks pass, and the shared gate does not require the missing acoustic path. Having an audio VAE elsewhere for output decoding does not supply it to reference conditioning.

**Impact:** the workflow can be enabled/advertised and accept an audio asset whose actual sound is silently ignored. The pinned native implementation adds only `{"type": "audio"}` to tokenizer presentation; it encodes the supplied waveform and adds acoustic reference blocks only when `audio_vae` is supplied. Video/audio output metadata does not establish that the reference sound was consumed. This violates the requirement to reject inputs that will be silently ignored; it is a feasible local gate omission, independent of unavailable GPU qualification.

**Bounded proof:** using the existing `native_contract_fixture()` entirely in memory, changed its conditioning class/mode to native Ref2VA and added test-only image/audio loader bindings using the pinned interfaces. Updated synthetic graph/slot/profile/dependency hashes and combination mode. Both `require_contract(record, approved)` and `require_asset_slot(approved, "REFERENCE_AUDIO_1")` returned successfully with no conditioning `audio_vae`; a final check with both record and ApprovedWorkflow modes set to `r2v` also passed. This fixture was never saved, submitted, or represented as an official full-reference export. Source confirmation: [upstream-nodes_minimax_h3.py:352](D:/project/ai-video-studio/tasks/h3-reference-artifacts/upstream-nodes_minimax_h3.py:352), particularly the conditional at line 356 and tokenization/encoding at lines 360-361.

**Required correction:** reject native audio-reference slots unless their conditioning node has the verified, hash-bound audio-VAE path needed to consume the waveform. Cover missing/disconnected acoustic conditioning at the shared gate and its callers; keep unsupported graphs disabled.

### [P2] Preserve unexpired single-generation idempotency replay across the fingerprint change

Location: [generations.py:160](D:/project/ai-video-studio/backend/apps/api/app/api/generations.py:160).

**Trigger:** after upgrading, repeat the identical single-generation POST body and idempotency key for an unexpired action accepted by the previous implementation. `_create` now hashes `model_dump(exclude_unset=True)`; the previous implementation hashed the full defaults, including `steps: 8`. The batch endpoint has an exact historical-hash compatibility path, but `_create` does not.

**Impact:** a client recovering an ambiguous accepted request gets `409 IDEMPOTENCY_KEY_REUSED` instead of the original generation response. The change breaks durable replay during the configured retention window. Starting a replacement action with a new key can create additional work, even though the original request was accepted.

**Bounded proof:** isolated in-memory SQLite, actual `create_generation` and `claim` functions. Created the control response, then set its saved request hash to `payload_hash` of the exact historical full-default `{}` request shape verified against HEAD: remove the three new quality/ratio/seed-policy fields and retain historical `steps=8`. Repeating `create_generation` with the same empty request, user, scene and key returned `409 IDEMPOTENCY_KEY_REUSED`. No scene/editor mutation was involved.

**Required correction:** recognize the exact saved historical single-action fingerprint for replay, including the shared variation path where applicable. Keep explicit-field hashing for new actions and reject materially changed requests. Do not restore historical defaults to new execution.

### [P2] Malformed nested resolver declarations escape the qualification status contract

Location: [workflow_contracts.py:431](D:/project/ai-video-studio/backend/apps/api/app/services/workflow_contracts.py:431); the analogous unchecked duration lookup is at line 448.

**Trigger:** an otherwise complete native profile declares `resolution.kind="resolution_selector"` but omits `node_id`. Nested resolver fields are arbitrary dictionaries. The native connectivity branch indexes `node_id` before `resolve_canvas` can return the intended `WORKFLOW_NOT_QUALIFIED` error. Profile-hash agreement does not establish structural validity.

**Impact:** admin enable raises an unexpected exception, an enabled historical/imported row breaks capability discovery for all workflows, and an auto-approved manifest aborts seeding rather than being inserted disabled. The gate fails closed for execution, but violates its availability/error contract.

**Bounded proof:** complete native synthetic fixture passed positive admin-enable and auto-approved seed controls. Deleted only `resolution.node_id` and recomputed the profile hash. Actual `approve_workflow`, `generation_capabilities`, and `seed_workflows` each raised `KeyError('node_id')`; the malformed seed entry was absent after the aborted transaction. SQLite was in memory; seed JSON was supplied through an in-memory Path adapter calling the unchanged loader functions.

**Required correction:** validate resolver structures before using their fields, preferably with typed resolver variants, and return the existing unqualified error/status. Cover malformed native resolver declarations through enable, capability discovery and disabled seeding. A broad exception mask is unnecessary.

### [P2] Validate variation cross-field invariants at the request boundary

Location: [generations.py:220](D:/project/ai-video-studio/backend/apps/api/app/api/generations.py:220), supported by [api.py:395](D:/project/ai-video-studio/backend/apps/api/app/schemas/api.py:395).

**Trigger:** `VariationRequest` accepts an `execution_prompt` without paired source revisions, or accepts only one source revision. The handler subsequently converts that valid DTO to `GenerationRequest`, whose stronger invariants raise an uncaught Pydantic `ValidationError`.

**Impact:** ordinary invalid client input returns HTTP 500 instead of the advertised request-validation 422. The accepted-prompt contract is absent from the variation request schema, and clients cannot handle these errors consistently with original generation requests.

**Bounded proof:** in-process HTTPX ASGI requests against the actual generations router with dependencies replaced by isolated stubs. Both bodies below produced HTTP 500 before database access:

```json
{"parent_generation_id":"<valid UUID>","execution_prompt":"accepted"}
{"parent_generation_id":"<valid UUID>","source_scene_revision":1}
```

The application handles `RequestValidationError` as 422, but the internal model conversion raises plain `ValidationError`, which reaches its generic 500 handler.

**Required correction:** share accepted-prompt/revision invariants with `VariationRequest` at boundary validation, while retaining parent-aware seed inheritance and explicit-field semantics. Add HTTP coverage for both invalid shapes and a valid exact-prompt variation.

## Original qualification findings

- **Boolean FPS/duration: CLOSED.** Strict numeric measurement fields reject booleans while permitting valid integer/float measurements. Existing helper/caller tests passed. Supplemental calls using a complete native synthetic profile, with positive enable/seed controls, separately proved `fps=True` and `duration_seconds=True` yield admin `409 WORKFLOW_NOT_QUALIFIED` and an inserted disabled seed row.
- **Malformed declared custom-node map: CLOSED.** Explicit declaration validation returns unqualified for `None`, lists and malformed maps. Supplemental complete-contract cases for `None` and `[]` likewise proved admin 409 and disabled seeding without `AttributeError`.

The checked-in caller regressions use profiles that lack the newer complete contract, so their negative assertions alone are not sufficient to demonstrate rejection for the intended reason. The positive-control supplemental checks above remove that ambiguity. Synthetic evidence proves local validation behavior only.

## Spec and quality coverage

Inspected shared qualification at approval, seed, capability discovery, creation and fresh dispatch; graph/slot/profile hashes; native sampler/seed/prompt/output connectivity; pinned selector arithmetic and frame expression; persisted last-frame mode and disabled derivation provenance; reference ordering, counts, scope, READY/checksum/stream metadata checks; explicit scene config/global override handling; exact accepted prompts and revision guards; frozen parent/v1 behavior; separate observed output metadata; named constraints and migration/backfill/index changes.

The implementation preserves the sole durable admission slot, active/uncertain submission ownership and `max_pending_prompts=1`. Existing batch completion replay checks pass, including the truthful snapshot `scene_revision` alias and empty migrated config compatibility. Technical retry remains the same generation with another attempt. No new production bypass flag was found.

Pinned resolution uses binary megapixels and nearest multiple-of-32 rounding with a post-rounding native area cap. Duration uses `round(seconds*24)` and upward `17k+5` alignment. The customized one-image R2V artifact remains unqualified; no fictional full Ref2VA export or measured profile was introduced. Frozen input snapshots and separately collected output observations remain distinct. The code follows the repository's service/API separation overall; the required quality defects concern validation boundaries and durable compatibility, not formatting or preference.

## Independent verification and limits

From `backend`, existing `.venv/Scripts/python.exe`, `-B`, pytest cache disabled:

```text
-m pytest -q -p no:cacheprovider tests/unit/test_workflow_qualification.py tests/integration/test_workflow_qualification.py tests/unit/test_generation_contracts.py
59 passed in 2.08s

-m pytest -q -p no:cacheprovider tests/integration/test_generation_contract_api.py::test_scene_config_batch_and_exact_prompt tests/integration/test_generation_contract_api.py::test_variation_inherits_frozen_parent_through_actual_api tests/integration/test_generation_contract_api.py::test_regenerate_seed_policy_and_v1_parent_replay tests/integration/test_generation_contract_api.py::test_preview_constraints_once_and_stale_acceptance tests/integration/test_generation_dispatch_contract.py tests/integration/test_generation_contract_migration.py tests/integration/test_batch_completion_replay.py
16 passed in 9.00s
```

**75 focused pytest cases passed**, plus the isolated proofs described above. The migration check used a disposable test database; no operator data was migrated. Reviewed the author's 338-pass/11-skip, Ruff/compile/OpenAPI and migration claims; did not rerun the broad suite or those build/export checks. Tests using fabricated execution assertions are not runtime qualification, and dispatcher metadata tests do not establish decoded media fidelity.

**External NOT_RUN:** real H3/GPU execution, hardware capacity/benchmark, live worker-namespace schemas, real full Ref2VA exports and output fidelity, PostgreSQL migration/locking execution. Disabled/unavailable behavior at these boundaries is expected and is not itself a finding. The four findings above are independently reproducible local behavior, not requests to invent that missing evidence.

Implementation remained read-only. No subagents, commits, live runtime calls, frontend/delivery/probe/benchmark review, source fixes or unrelated slice changes. The only persistent review write is this report.

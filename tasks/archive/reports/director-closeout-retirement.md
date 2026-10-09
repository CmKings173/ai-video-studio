# Director-only retirement implementation evidence

Date: 2026-10-08. Shared working tree: `D:/project/ai-video-studio`.
Branch `codex/production-safety-fixes`; HEAD `01b740d` verified live.
No commit, push, reset, stash, clean, checkout, child agents, or original-DB mutation.
Only frontend AGENTS was found; backend and docs have no local AGENTS.
The complete attached spec was read. This report covers sections 1�6 and 28�31.

## Runtime selection and interface changes

- `workflow_router.require_director_execution(record)` raises `WORKFLOW_RETIRED` (409)
  for non-Director graphs or `profile.retired`. It does not modify records.
- `GenerationService._workflow` filters automatic candidates to Director, rejects explicit
  legacy IDs, and retains graph/slot integrity, scope and qualification checks. No fallback.
- Parent derivatives reject historical non-Director parents before rebuilding any snapshot,
  even with explicit workflow overrides. Prepared persistence revalidates Director frozen
  contracts before writing. Legacy slot planning and alternate executor branches were removed.
- `require_frozen(snapshot, record)` applies the family guard before qualification; historical
  DTO reads do not invoke this execution gate. `require_frozen_director_run` also applies it.
- Admin enablement rejects non-Director records; capability selection omits historical rows.
- `GenerationIntent.provider_task` is bounded to t2v/i2v/fl2v/r2v/v2v/rv2v.
- AUTO and persisted i2v_last/i2v_first_last names retain their semantics and map to FL2V.
- Standalone PostgreSQL dispatch remains for Director single-scene execution; DirectorRun
  remains the aggregate path. No queue redesign, provider repin or qualification enablement.

### Parent dispatch integration checkpoint

The parent owns workers/dispatcher.py and all collection/persistence worker changes.
`Dispatcher._validate_frozen_snapshot` delegates to guarded `require_frozen`. Initial submit
through `_prepare_workflow` therefore rejects non-Director snapshots. During independent
inspection, `_submit_or_recover` returned an existing prompt ID before `_prepare_workflow`;
the parent was explicitly notified to guard at the start of `process`, before recovery.
Resume protection must be verified against the parent's final worker edits, not inferred
from the initial-submit guard. No worker files were edited by this retirement slice.

## Reference audit and artifacts

The five removed active definitions are H3_T2V_STANDARD, H3_I2V_STANDARD,
H3_FIRST_LAST_STANDARD, H3_R2V_1_IMAGE_REFERENCE and H3_LAST_FRAME_STANDARD.
Full original definitions are recorded in `tasks/evidence/director-retirement-registry-audit.json`.
The active registry now contains exactly 12 Director entries (six tasks x two scopes).

Removed from runtime directory and preserved byte-for-byte as historical test artifacts:

- backend/workflows/h3/video_minimax_h3_t2v.api.json
- backend/workflows/h3/video_minimax_h3_i2v.api.json
- backend/workflows/h3/video_minimax_h3_r2v_1ref.api.json

Their destination is `backend/tests/fixtures/historical_h3/`, with a historical registry.
Legacy probe/static contracts read that test archive rather than the active registry.
Historical reports, reference source checkouts and PoC evidence were not rewritten.
All 18 `director*.json` files remain, including all six `_aggregate_2` variants.
Registry/provider builders/dynamic aggregate tests were traced before retirement.
Active loader legacy derivation and external source fallback were removed.
Upstream pin remains a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb.
Runtime qualification/advertisement remain false; GPU_RUNTIME_QUALIFICATION: NOT_RUN.
SelfLift/SemanticBridge/Segmented LoRA: FEATURE_GAP, unchanged outside this slice.

## Actual application DB audit and cleanup command

Initial connection timed out while Docker was unavailable. The parent's restored-runtime
read-only audit is in `tasks/archive/reports/director-closeout-db-audit.md/.log/.py`. It verified original
host localhost, port 5432, database studio, current=head b5d9f1a3c7e2. Five disabled legacy
rows have zero history/JSON dependencies; 12 Director rows are disabled and preserved.
True FKs from SceneGeneration and DirectorRun use ON DELETE RESTRICT. Attempts and
final-video history depend indirectly; JSON surfaces were also audited.

A subsequent live dry-run of this slice's command succeeded with `applied: false`,
exactly five `delete_unreferenced` actions and empty FK/JSON dependency maps.
No original-DB write occurred in this slice. Parent owns pg_dump backup and apply.

From `D:/project/ai-video-studio/backend`:

```powershell
.\.venv\Scripts\python.exe -m apps.api.management.retire_legacy_h3
# Only the parent applies, after its verified original database backup:
.\.venv\Scripts\python.exe -m apps.api.management.retire_legacy_h3 --apply
```

The command selects only the five explicit legacy codes, locks them FOR UPDATE and
rechecks live reflected FK dependencies and every non-registry JSON column. PostgreSQL
apply requires READ COMMITTED so dependency queries see writers committed while waiting.
SHARE locks on JSON-bearing tables protect concurrent JSON-only writes; row locks conflict
with FK insert KEY SHARE locks. Locks and mutations share the caller's transaction.
Unreferenced rows are deleted; referenced rows retain their original graph, profile,
version, hashes, evidence and identity with enabled=false. Director and unrelated rows
are untouched. A Director graph under a legacy code aborts rather than being removed.
Lock timeout 15s and statement timeout 60s bound apply; errors roll back the transaction.
Repeat apply deletes nothing after cleanup, or leaves historical disabled tombstones intact.
No schema migration is needed. Final apply/DB results belong to the parent report.

## Fresh verification

- Retirement/router/registry tests after conditional command: **20 passed in 2.06s**.
- Expanded service/retirement suite including qualification fixture repairs: **192 passed in 43.64s**.
  Exact output: `.artifacts/final-ui-media-closeout/director-closeout-retirement-tests.log`.
- Full backend run before the last qualification fixture conversion:
  **9 failed, 981 passed, 26 skipped in 192.91s**.
  Exact output: `.artifacts/final-ui-media-closeout/director-closeout-retirement-backend-full.log`.
  Six failures used a legacy active qualification fixture; converted to Director without
  loosening qualification checks, fresh rerun **6 passed in 2.06s**.
  Under the subsequent explicit request to fix all nine failures, the race adapter was
  updated with synthetic object_info, the declared exporter node 7, portrait canvas and
  complete probe metadata matching frozen evidence. Fresh affected-module rerun:
  **12 passed in 4.06s**, `.artifacts/final-ui-media-closeout/director-closeout-retirement-repairs.log`.
  Production guards and existing recovery/retry assertions were not weakened.
- PostgreSQL retirement concurrent-writer regression was added. Local command returned
  **1 skipped**, POSTGRES_TEST_DATABASE_URL unset. Parent reported **24 passed** in its
  isolated PostgreSQL lane; verify inclusion of the newly added retirement test separately.
- Scoped Ruff: All checks passed. Scoped compileall: exit 0. git diff --check: exit 0.
- No duplicate full pytest was started. Parent runs final full validation with PG environment.

Tests preserve meaningful prompts/seeds/reference immutability, scope uniqueness,
Director task aliases, disabled/unqualified gating, no fallback, idempotency and history.
New tests verify dry-run no mutation, exact-code deletion, retained FK history, JSON-only
history and non-Director frozen parent rejection. Shared seed uses a real pinned Director
single-scene template with synthetic test-only evidence; workflow_data remains historical.
`director_workflow_data` returns graph, slots, profile, approved and accepts mode, quality,
steps, execution_scope. The generation race seed function alone was converted via service
creation. Parent MemoryStore/path-API edits were preserved. The subsequent explicit
request to fix all nine failures authorized additional adapter/probe fixture changes.

## Changed paths owned by this slice

Production:
- backend/apps/api/app/api/admin.py
- backend/apps/api/app/api/generations.py
- backend/apps/api/app/services/generation_service.py
- backend/apps/api/app/services/generation_intent.py
- backend/apps/api/app/services/director_run_service.py
- backend/apps/api/app/services/workflow_contracts.py
- backend/apps/api/app/services/workflow_loader.py
- backend/apps/api/app/services/workflow_router.py
- backend/apps/api/management/retire_legacy_h3.py (new)
- backend/workflows/h3/registry.json
- Three retired runtime graphs listed above (moved to historical test fixtures).

Tests:
- backend/tests/fixtures/historical_h3/registry.json (new)
- backend/tests/fixtures/historical_h3/video_minimax_h3_t2v.api.json (archived)
- backend/tests/fixtures/historical_h3/video_minimax_h3_i2v.api.json (archived)
- backend/tests/fixtures/historical_h3/video_minimax_h3_r2v_1ref.api.json (archived)
- backend/tests/historical_workflow_fixtures.py (new)
- backend/tests/integration/test_director_only_retirement.py (new)
- backend/tests/postgres/test_workflow_retirement_concurrency.py (new)
- backend/tests/integration/test_generation_preparation.py
- backend/tests/integration/test_generation_races.py (Director seed, synthetic adapter/probe contract)
- backend/tests/integration/test_generation_contract_api.py
- backend/tests/integration/test_generation_contract_edges.py
- backend/tests/integration/test_h3_probe.py
- backend/tests/integration/test_workflow_execution_scope.py
- backend/tests/integration/test_director_corrections.py
- backend/tests/integration/test_generate_all.py
- backend/tests/integration/test_workflow_qualification.py
- backend/tests/unit/test_generation_contracts.py

Docs/evidence:
- docs/architecture/h3-director-target-architecture.md
- docs/architecture/h3-director-gap-analysis.md
- docs/architecture/h3-director-migration-plan.md
- docs/decisions/012-minimax-h3-director-execution-engine.md
- tasks/archive/reports/director-closeout-retirement.md
- tasks/evidence/director-retirement-registry-audit.json
- .artifacts/final-ui-media-closeout/director-closeout-retirement-tests.log
- .artifacts/final-ui-media-closeout/director-closeout-retirement-backend-full.log
- .artifacts/final-ui-media-closeout/director-closeout-retirement-repairs.log

workflow_registry.py was audited and deliberately left unchanged: generic immutable
manifest/registry utilities remain useful for historical reads and isolated contract tests.

## Independent rereview and remaining parent validation

Family enforcement was rereviewed across automatic/explicit selection, admin enablement,
capabilities, frozen derivatives, prepared persistence and aggregate validation. No legacy
runtime template or fallback remains selectable. The existing-prompt resume shortcut
was identified and relayed to the parent; final dispatcher verification is required.
Graph archival retains historical coverage. Command selection preserves Director records
and uses transactional history rechecks. Qualification remains fail closed.

This slice does not claim the overall reliability-closeout PASS: parent must finish worker
resume protection, original DB apply, final full/PostgreSQL validation and its other
streaming/frontend/runtime scope. No frontend/Comfy/worker implementation was edited here.

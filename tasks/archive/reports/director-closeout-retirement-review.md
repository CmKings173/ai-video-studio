# Independent Director retirement review

Review status after independent rereview: **REQUEST_CHANGES — recovery P2 closed; one remaining P2 in aggregate collection**. Scoped review only; this is not the full reliability closeout verdict.

Reviewed 2026-10-08 against the live dirty tree at `D:\project\ai-video-studio`, branch `codex/production-safety-fixes`, HEAD `01b740d`. HEAD was used only to identify the baseline; production work includes untracked source. Read the supplied `Pasted text.txt` specification and `code-review-and-quality/SKILL.md`. No children, messages, commits, source/test edits, or original application DB writes. This report is the only authored file.

## Independent rereview of parent fix (2026-10-08)

The original recovered-submission finding below is **CLOSED** in the live source. `Dispatcher._require_execution_snapshot` at lines 457-469 loads the enabled registry record, explicitly requires Director graph identity, and invokes the worker-specific frozen validator. `_submit_or_recover` calls it before persisted-prompt and client-ID recovery branches. Invalid recovered submissions hold before ComfyUI I/O. `_hold` retains job/attempt correlation and the active status/admission while setting RECONCILING and lease backoff. The standalone legacy staging and collector branches were removed; standalone `_collect` revalidates before changing status or collecting.

Fresh affected-suite command, from `backend`, with `PYTHONDONTWRITEBYTECODE=1`, `POSTGRES_TEST_DATABASE_URL=''`, and cache disabled:

```text
.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider tests/integration/test_retired_recovery.py tests/integration/test_generation_dispatch_contract.py tests/integration/test_director_dispatcher.py tests/integration/test_generation_races.py tests/integration/test_director_only_retirement.py tests/integration/test_director_dynamic_aggregate.py --tb=short
59 passed in 13.44s
exit 0
```

The eight new recovery cases use real in-memory SQLite registry records and cover legacy/custom graphs, both worker classes, and both persisted-prompt/client-ID recovery. They assert no adapter access and unchanged correlation. Their `_hold` is stubbed, so durable hold/admission preservation is additionally established by source inspection, not those eight cases alone.

### [P2] Aggregate collector bypasses the new collection validation checkpoint

Current location: **`backend/workers/director_dispatcher.py:240-243`**. Base guard: `backend/workers/dispatcher.py:581-582`. Dynamic collection dispatch: base `process` calls `self._collect` after awaiting ComfyUI history.

`DirectorDispatcher._collect` overrides the guarded base method and immediately calls `_update(... status="COLLECTING")`; it never calls `_require_execution_snapshot`. If an aggregate workflow is disabled, retired, loses qualification, or changes its graph while the external run is executing, recovery validation at process entry does not catch that later change. The collector uses frozen provenance and can persist segment outputs and complete members despite revoked current execution authority. Standalone collection now correctly checks that authority at this same boundary.

Independent isolated SQLite probe used the real shared seed, changed its registry graph to `ArbitraryCustomH3`, and invoked each worker's `_collect` with the same `{"workflow_id": row_id}` snapshot. `_update` was replaced with a recorder returning False, preventing any writes or external I/O. Results:

```text
Dispatcher guard= WORKFLOW_RETIRED updates= []
DirectorDispatcher guard=NOT_CALLED updates= [{'status': 'COLLECTING', 'phase': 'COLLECTING'}]
```

This proves the aggregate override bypasses registry validation before status transition; subsequent publish behavior is established from the collector source, not a complete media publish in this probe. Required fix: invoke `_require_execution_snapshot(run.input_snapshot)` at the start of the aggregate override, before `_update` or artifact work, and add aggregate collection regressions for retirement/disablement or qualification revocation between submission and completed-history collection. Ensure the failure path preserves external correlation according to the existing recovery policy. No source/test fix was made by this reviewer.

### Updated original DB evidence

The parent reports completed safe apply and 12 Director rows remaining. Read `.artifacts/final-ui-media-closeout/director-closeout-db-retirement.log`: `applied=true`, five exact legacy identities, each with empty FK/JSON dependency reports and action `delete_unreferenced`. Read the after-audit console evidence as well. Backup existence/metadata is supplied by the parent; this reviewer did not independently restore/verify the backup. These are parent operation/audit evidence, not a new application DB query by this reviewer. Original-row retirement is no longer listed as pending, and no original DB write was performed here.

## Original finding — CLOSED by parent fix

### [P2] Recovered submissions bypass the frozen Director execution guard

Location: `backend/workers/dispatcher.py:530-552`, especially the existing-prompt return at **531-532** and the recovery branch at **538-547**. Related active legacy collector: **586-592**. `DirectorDispatcher` inherits `_submit_or_recover`; its `process` at `backend/workers/director_dispatcher.py:197-203` delegates to the same base process.

`_prepare_workflow` loads the registry record and validates the frozen contract only for a CREATED attempt submitting a new graph. An active generation with an existing `comfy_prompt_id` returns before that validation. A non-CREATED attempt recovered by `find_by_client_id` also skips validation. The base process then polls/collects, and the standalone collector explicitly retains the branch for snapshots without `director_execution`. Thus an in-flight historical/custom non-Director generation can resume, collect media, and publish COMPLETED output despite the Director-only execution boundary. Disabled or retired registry state does not close this recovery path. Completed historical reads do not need this execution path.

Database-free reproduction run with backend `.venv` Python; no storage/Comfy calls or source edits:

```python
import asyncio
from types import SimpleNamespace
from workers.dispatcher import Dispatcher
from workers.director_dispatcher import DirectorDispatcher

async def main():
    for cls in (Dispatcher, DirectorDispatcher):
        obj = object.__new__(cls)
        calls = []
        async def reject(snapshot):
            calls.append("validation")
            raise RuntimeError("WORKFLOW_RETIRED")
        obj._prepare_workflow = reject
        result = await obj._submit_or_recover(
            SimpleNamespace(
                comfy_prompt_id="historical-prompt",
                input_snapshot={"workflow": {
                    "1": {"class_type": "Legacy", "inputs": {}}
                }},
            ), None,
        )
        print(cls.__name__, result, calls)
asyncio.run(main())
```

Observed:

```text
Dispatcher historical-prompt []
DirectorDispatcher historical-prompt []
```

This reproduces the validation bypass, not a full end-to-end media publish; the subsequent polling and non-Director collection are confirmed by source inspection. No existing test in the scoped run exercised a retired recovered submission.

Original required remedy: separate registry/frozen-contract validation from reference staging and apply the execution gate before recovery as well as submission. Preserve historical records and provide a deliberate terminal/hold/cancellation path for already submitted retired jobs without resubmitting or losing external correlation. This recovery remedy and its regression coverage are independently verified above.

## Checks and scope results

- **Active registry:** fresh assertions confirmed exactly 12 entries, six single-scene and six aggregate, all `H3_DIRECTOR_*`. All retain upstream commit `a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb`; none declare `poc_verified`, `runtime_qualified`, or `advertised` true.
- **Exact legacy set:** cleanup targets only `H3_T2V_STANDARD`, `H3_I2V_STANDARD`, `H3_FIRST_LAST_STANDARD`, `H3_R2V_1_IMAGE_REFERENCE`, and `H3_LAST_FRAME_STANDARD`. Tests verify unrelated/custom rows are retained and repeated apply is idempotent.
- **Files:** the three tracked obsolete legacy graph files are deleted in the dirty tree. All 18 Director graphs remain, including six `_aggregate_2` files. Tests cover count-independent dynamic aggregate contracts and historical execution signatures. No Director artifact was deleted by this review.
- **Routing/creation:** inspected automatic selection, explicit IDs, frame aliases, capability filtering, enable approval, parent derivatives, prepared persistence, and Generate All composition. The canonical gate uses actual Director class identity, not merely a code prefix. Fresh direct probes rejected `HistoricalH3`, `UNRELATED_CUSTOM`, and `MiniMaxH3ImageToVideo` with `WORKFLOW_RETIRED`. `i2v_last` and `i2v_first_last` map to Director `fl2v`.
- **Fail closed:** new selection/approval and frozen validation reject non-Director records; unqualified Director does not fall back to legacy. Fresh retirement tests verify failure before creating generation rows.
- **Frozen contracts:** standalone and aggregate validators enforce graph/settings/intent identity; aggregate validator also checks member coverage, timeline, totals, and aggregate evidence. Recovery entry validation is fixed. Aggregate collection still bypasses its new checkpoint, as recorded in the rereview above.
- **Retirement preservation:** FK-dependent and JSON-value-dependent legacy rows are disabled in place without rewriting IDs, graph, version, hashes, or generation snapshots. Unreferenced exact-code rows are deleted. Director graphs accidentally stored under legacy codes cause an exception, rather than deletion.
- **Transactions/locks:** inspected PostgreSQL schema reflection, inbound workflow-ID FK counts, non-registry JSON scans, SHARE table locks before ordered FOR UPDATE workflow locks, READ COMMITTED enforcement, 15-second lock timeout, and 60-second statement timeout. The caller owns the transaction; the CLI wraps apply in one transaction. FK locks prevent concurrent references from silently disappearing; JSON table locks cover JSON-only writers. Inspected the real-lock-wait concurrency test, but did not run it here.
- **JSON audit boundary:** the command excludes `workflow_registry` JSON and recursively examines JSON values, not object keys. No inspected current history writer demonstrated a dependency in those omitted locations, so this is recorded as a coverage limitation rather than a second confirmed production defect. Do not describe this implementation as checking literally every JSON location without that qualification.

Preserved `_aggregate_2` byte hashes captured read-only:

| Task | SHA-256 |
| --- | --- |
| fl2v | `22b599645dd1e9f968f1346386d1ad4672540e635488d269ed7ec32343dd24e1` |
| i2v | `06fd11af9d001993a6803ec114f7282c38116bb2bc3498d55dcea3f0ec25edeb` |
| r2v | `f3487bb3f0dad52cd7d435fe2eec5d8af5f3f1efc49b79799db3f5833dae5f34` |
| rv2v | `c4c5873902119afee97d3f88ebbac3f4d14e4a45c38d8f87da1e1f4e22021191` |
| t2v | `9bd80d8e0353542d616cdf72d9ea73a24fea77a972b1011f5d5cf14a0fff4a75` |
| v2v | `57738b463602c4bd48035d99061d3064c44333e90b445f646ebc61f1fde97c71` |

These prove current artifact presence/content, not comparison to a separately fetched upstream checkout. No repin was performed.

## Fresh validation

Commands ran from `backend` with `PYTHONDONTWRITEBYTECODE=1`, `POSTGRES_TEST_DATABASE_URL=''`, `.venv/Scripts/python.exe -B`, and pytest cache disabled. Integration `session_factory` uses in-memory SQLite with FK checks. Tests may create only temporary isolated artifacts; the original DB was not connected by these runs.

1. `pytest -q -p no:cacheprovider tests/integration/test_director_only_retirement.py tests/unit/test_director_graph_identity.py tests/unit/test_director_dynamic_aggregate.py tests/integration/test_director_dynamic_aggregate.py --tb=short`
   - **57 passed in 8.96s**, exit 0.
2. `pytest -q -p no:cacheprovider tests/unit/test_workflow_router.py tests/unit/test_director_execution_contract.py tests/unit/test_director_native_manifest.py tests/integration/test_generation_dispatch_contract.py tests/integration/test_director_dispatcher.py tests/integration/test_generate_all.py tests/integration/test_batch_completion_replay.py tests/integration/test_workflow_execution_scope.py --tb=short`
   - **66 passed in 11.19s**, exit 0.
3. Database-free recovery repro and registry/custom-gate assertions: exit 0, outputs recorded above.

No full-suite pass is claimed. Implementer fixture work was ongoing. PostgreSQL retirement concurrency is **NOT_RUN by this reviewer**; an existing parent log reports 24 PostgreSQL tests passed, but is not fresh evidence from this review. GPU qualification is **NOT_RUN**.

## Original database and closeout status

Original backup existence and five unreferenced legacy rows were supplied in the handoff. The existing read-only DB audit report independently documents 17 registry rows (12 Director plus five disabled legacy), zero legacy history dependencies, and current Alembic head. Those are other-agent evidence, not a fresh database query by this reviewer. No retirement apply, migration, or DB mutation was performed here.

The initial DB audit described above predates the parent's apply; updated operation evidence is recorded in the rereview section. Original-row retirement is reported completed by the parent. This reviewer did not perform that write. Scoped source approval remains blocked on the aggregate-collection P2; the original recovered-submission P2 is closed. Full closeout PASS cannot be inferred from these targeted results.

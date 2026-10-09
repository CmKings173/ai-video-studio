# Independent partial qualification-gate review

Reviewed 2026-10-05 against `tasks/archive/reports/production-qualification-gate-report.md` and master prompt J at `docs/ai-video-studio-production-generation-plan.md:2329-2357`.

Scope: current working-tree `workflow_qualification.py`, admin enable, workflow seed, and the two qualification test files. Tracked changes were compared with HEAD; the new helper/tests were read directly. Supporting schemas/registry and test fixtures were read only to establish caller behavior. No implementation edits, subagents, commits, or review of unrelated Phase 0 changes.

**Result: two P2 fixes are necessary for this partial gate.** No P0/P1 defect was established in these new enable/insert checks. This does not qualify production generation or complete prompt J.

## Necessary current-gate fixes

### [P2] Boolean measurements pass the executed-output gate

Location: [workflow_qualification.py:20](D:/project/ai-video-studio/backend/apps/api/app/services/workflow_qualification.py:20), also line 21. Consumers: [admin.py:261](D:/project/ai-video-studio/backend/apps/api/app/api/admin.py:261) and [workflow_loader.py:75](D:/project/ai-video-studio/backend/apps/api/app/services/workflow_loader.py:75).

`fps` and `duration_seconds` use coercing float fields. JSON `true` becomes `1.0` and passes positivity/finite checks, despite representing no numeric measurement. This contradicts the report's invalid-measured-metadata rejection and J's requirement for output evidence. This is schema validation, independent of the deferred profile/ratio contract.

Reproducer from `backend`, using the existing synthetic unit fixture:

```python
import runpy
from apps.api.app.services.workflow_qualification import qualification_status
fixture = runpy.run_path("tests/unit/test_workflow_qualification.py")["evidence_profile"]
p = fixture()
p["execution_evidence"]["output"].update(fps=True, duration_seconds=True)
assert qualification_status(p, "a" * 64, "b" * 64).qualified  # currently passes
```

Also reproduced with graph/slot hashes computed from the integration manifest: `approve_workflow(..., enabled=True)` returns `enabled=True`; a fresh `auto_approve=True` seed inserts `enabled=True`. These calls used isolated SQLite and synthetic evidence, not a GPU execution.

Fix: require numeric measurement types that reject booleans while retaining valid integer/float measurements and finite positive values. Add `True` cases to [test_workflow_qualification.py:60](D:/project/ai-video-studio/backend/tests/unit/test_workflow_qualification.py:60), plus caller regression coverage proving enable is rejected and seed stays disabled.

### [P2] Malformed declared custom-node map crashes instead of returning unqualified

Location: [workflow_qualification.py:96](D:/project/ai-video-studio/backend/apps/api/app/services/workflow_qualification.py:96), especially line 99. Same admin/seed consumers as above.

Only `execution_evidence` is structurally validated. The separate profile declaration is used with `.items()` outside that validation block. An otherwise complete profile with `custom_node_versions: null` or `[]` raises `AttributeError`. The admin profile schema permits arbitrary JSON fields, so this is reachable without modifying source. Admin approval propagates an unexpected error instead of the intended `WORKFLOW_NOT_QUALIFIED` 409. Seeding an auto-approved manifest with this declaration aborts the seed transaction rather than inserting the entry disabled.

Reproducer:

```python
p = fixture()
p["custom_node_versions"] = None  # [] also reproduces
qualification_status(p, "a" * 64, "b" * 64)
# AttributeError: 'NoneType' object has no attribute 'items'
```

The exception was independently reproduced through both actual admin approval and seed functions. This fails closed for enable, but breaks the helper's status/error contract and seed availability; it is not an unauthorized enable bypass.

Fix: validate the declaration as a string-to-string mapping, or explicitly reject malformed values with an unqualified status. Preserve absent/empty native-only declarations. Add helper, admin-409, and disabled-seed regressions. The current tests do not cover malformed declarations.

## Alignment and requirements for the next contract task

The partial implementation correctly requires literal `poc_verified is True`, complete structured execution evidence, matching graph/slot hashes, timezone-bearing timestamps, finite positive media values, and video/audio flags. Admin checks qualification before disabling another workflow. Fresh seed inserts cannot auto-approve a boolean-only/static-preflight profile. Model/LoRA declarations, when present, are compared with evidence. No production escape flag was introduced. No separate documented-standard breach was established.

The following are outstanding requirements, **not additional current-slice defects**:

- **Generation preparation and legacy enabled records:** seed skips existing code/version rows at [workflow_loader.py:62](D:/project/ai-video-studio/backend/apps/api/app/services/workflow_loader.py:62). Re-seeding an existing enabled, unqualified row therefore leaves it enabled. This was pre-existing behavior; the next contract must guard generation dispatch and capability selection independently of `enabled`, including native graphs and old frozen snapshots. The report explicitly defers the generation guard; the contract brief explicitly requires legacy rows not to bypass it.
- **Profile/mode/ratio/settings and runtime dependency binding:** the helper receives only two graph hashes and cannot establish which settings were executed or whether dependencies cover the graph. An empty custom-node evidence map is accepted without proving native-only provenance. A top-level `comfyui_commit` different from evidence is currently ignored (reproduced with the fixture and `p["comfyui_commit"] = "0" * 40`). Define and bind the authoritative runtime/profile contract before using this status to advertise qualified combinations. Do not treat mere field presence as proof of complete graph dependency coverage.
- **Benchmark/release evidence:** J also says to record benchmark evidence and prefers the fuller lifecycle. There is no benchmark evidence schema/check here; successful helper status is only `POC_EXECUTED`. Track this explicitly in the next contract/release work; do not claim `BENCHMARKED`, full J completion, or target-GPU qualification from this patch.
- **Trusted evidence production:** this helper validates administrator-supplied assertions and hashes; it does not fetch Comfy history or verify a stored output's checksum/streams. Specify the trusted capture/import process for genuine execution artifacts. Synthetic test records establish validation behavior only.

## Verification and limits

Ran from `backend` with the existing `.venv`, bytecode writes disabled, and pytest cache disabled:

```text
.venv\Scripts\python.exe -B -m pytest -p no:cacheprovider tests/unit/test_workflow_qualification.py tests/integration/test_workflow_qualification.py --basetemp=<temporary directory>
```

Observed **22 passed, 1 setup error**: all 21 unit cases and the admin integration case passed. The seed case's `tmp_path` directory creation was blocked by filesystem sandbox permissions, both in system temp and a workspace scratch path; this is a verification limitation, not a test assertion failure. No escalation was requested.

Separately invoked the unchanged seed integration test using an in-memory Path adapter for its JSON file I/O and the actual loader/SQLite functions: **PASS**. Then reproduced both findings through the actual admin and seed functions with the same adapter. This supplemental result is not a claim that the normal 23-test pytest run passed. No full-suite, PostgreSQL concurrency, external runtime, or live GPU checks were run. Only this review file was written.

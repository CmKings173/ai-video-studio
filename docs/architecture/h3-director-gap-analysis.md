# H3 Director gap analysis

Classification:

- **KEEP**: retain responsibility and behavior.
- **EXTEND**: preserve module and add Director-aware behavior.
- **REPLACE**: replace an implementation seam while preserving surrounding contracts.
- **MIGRATE**: additive schema/data/API compatibility work.
- **REMOVE**: delete only after replacement and qualification.

## Component map

| Component | Classification | Required change |
|---|---|---|
| `generation_service.py` | EXTEND | keep asset resolution/frozen semantics; add GenerationIntent -> DirectorExecutionSpec and new modes |
| `workflow_router.py` | EXTEND | keep business routing; separate business mode from Director task/provider |
| `workflow_contracts.py` | EXTEND | retain frozen/qualified checks; add Director group/timeline/aggregate contracts |
| `workflow_loader.py` | EXTEND / REPLACE internals | keep qualification boundary; build Director graph from typed contract rather than invented node IDs |
| `dispatcher.py` | EXTEND | keep leases/retries/reconciliation; add DirectorAdapter, materialization and manifest collection |
| `h3_validator.py` | EXTEND | move product limits toward source + release qualification where appropriate |
| `comfy_adapter.py` | KEEP + EXTEND | retain transport/reconciliation; support Director artifact manifest and aggregate runs |
| `registry.json` | MIGRATE | store release/provider identity and qualified builder metadata; retain legacy DB identities for historical reads only; active manifests are Director-only |
| frontend generation editor | EXTEND | consume backend capabilities; add V2V/RV2V and qualified Director options |
| delivery presets / FFmpeg | KEEP | remain final delivery/assembly boundary; do not substitute for Director Refine |
| PostgreSQL generation history | KEEP + MIGRATE | preserve records; add versioned Director run/member state |
| MinIO asset lifecycle | KEEP | add deterministic worker materialization contract |
| SSE/status | KEEP + EXTEND | expose aggregate/member progress without breaking scene history |
| legacy graph-specific hardcoded slots | REPLACE after migration | typed DirectorWorkflowBuilder + `/object_info` contract |
| obsolete legacy-only validation paths | REMOVE last | only after equivalence tests and rollback window |

## Required migrations

### Generation mode enum

**CURRENT_AI_VIDEO_STUDIO_SOURCE:** API schema, DB constraints and frontend `GenerationMode` currently center on `t2v/i2v/i2v_last/i2v_first_last/r2v`.

Add `v2v` and `rv2v`, preserve persisted legacy values, and normalize legacy FL2V aliases only in planning.

### Snapshot shape

Current `input_snapshot` is the right durability seam. Extend it with a versioned Director contract covering timeline, references, continuity, Refine, FaceRefine, provider identity and collector policy.

### Workflow qualification

Current qualification already verifies hashes and measured AV output. Extend release identity so evidence is invalidated when Director commit, ComfyUI, model/dependency hashes, graph contract, Motion Context pipeline or enhancement config changes.

### Dispatcher boundary

Native Motion Context spans ordered segments. Add aggregate durability instead of simulating continuity with independent scene jobs.

### Asset references

Extend roles to explicit last-frame/source-video and stable ordinals/checksums/materialized filenames. R2V mixed-reference ordering becomes part of the semantic hash.

### Output collection

Director can emit list/batch outputs and per-segment MP4 exports. Collector must consume a manifest and probe each artifact.

## Preserve production-hardening work

The refactor must preserve:

- auth and authorization;
- asset READY/DELETING lifecycle and retention;
- MinIO upload/delete/finalize;
- PostgreSQL transaction boundaries;
- immutable semantic snapshots and parent generation relationships;
- worker claim/lease/retry/reconciliation;
- output media validation;
- SSE/status recovery;
- final delivery and assembly.

## Architecture risks

1. Aggregate durability for native Motion Context.
2. Provider/source drift between the pin and installed runtime.
3. Accepted prompt versus effective provider conditioning.
4. Segment exports and released tensors breaking single-file assumptions.
5. Unknown runtime envelope despite 8192/16 MP UI bounds.
6. 32 kHz internal/model audio handling versus 44.1 kHz stereo export normalization.
7. Task selection not loading/switching compatible UNET weights by itself.
8. FaceRefine ultralytics/model dependency qualification.

## Exit criteria for implementation start

- typed execution contract and aggregate-run boundary are explicit;
- capability/source/qualification distinction is explicit;
- source-vs-spec discrepancies are recorded;
- migration order has rollback boundaries;
- no implementation phase depends on invented Comfy node IDs;
- runtime-only claims remain unqualified until executed.

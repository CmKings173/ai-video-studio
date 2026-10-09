# Director closeout database and read-only runtime-path audit

Scope: sidecar audit only. No application source edits, database writes, backups, migrations, runtime starts/restarts, Git mutations, child agents, or test database/schema creation. Only the three `tasks/director-closeout-db-audit.*` paths are owned by this audit. The full attachment specification was read; this narrower handoff governs actions.

## Outcome

**LIVE APPLICATION DB AUDIT VERIFIED READ-ONLY.** The original DB recovered after the parent restored Docker Desktop. Current=head=`b5d9f1a3c7e2`. There are five disabled legacy registry rows and 12 disabled Director rows. Each legacy row has zero historical dependencies, and the JSON identity scan found no legacy references. Recommendation: CASE A, remove the five legacy rows in a separately authorized cleanup after a transactional dependency recheck; no history tombstones are required by the current data. This audit performed no deletion and does not claim full closeout PASS.

Fresh database-free behavior probes: **35 PASS**. These directly call current application functions with fake sessions/storage; they neither create a database nor exercise PostgreSQL concurrency or live MinIO.

## Repository and instructions

Live starting branch: `codex/production-safety-fixes`; HEAD: `01b740d`. The shared working tree was already extensively dirty and untracked. Its working files, including current untracked code, supplied the audit evidence. No commit/push/reset/stash/clean/checkout occurred, and no unrelated files were removed. Only `frontend/AGENTS.md` was found in the repository; it was read. No frontend code was edited. Source fingerprints and test-function inventories are captured in the JSON log, since other work can continue in this shared tree.

## Original application database and Alembic

| Configuration source | Host | Port | Database |
| --- | --- | --- | --- |
| `backend/.env` DATABASE_URL used by Settings when started from backend | localhost | 5432 | studio |
| Compose API/workers DATABASE_URL override | postgres | 5432 | studio |
| Compose database published loopback mapping | 127.0.0.1 | 5432 | studio |
| Settings default | localhost | 5432 | studio |

Root `.env` has no DATABASE_URL; backend `.env` does. Secrets were read privately and never printed or saved. Docker inspection confirms the running original `ai-video-studio-api-1` uses `postgres:5432/studio`; `ai-video-studio-postgres-1` publishes 127.0.0.1:5432 and mounts named volume `ai-video-studio_postgres_data`. No alternative/test database was substituted. Initial connection refused while Docker's engine pipe was absent; after the parent's restoration, the audit retried successfully. This sidecar did not start/restart any runtime service.

Fresh offline command, using `backend/.venv/Scripts/python.exe -B -m alembic heads` from backend:

```text
b5d9f1a3c7e2 (head)
alembic heads native exit=0
```

The audit uses Alembic `ScriptDirectory.get_heads()` for repository heads and `MigrationContext.get_current_heads()` for the live revision on the same protected application connection. Both return `b5d9f1a3c7e2`. An explicit executed `SELECT version_num FROM alembic_version` independently returned that revision. No upgrade is needed for the inspected migration graph.

The live SQL identity query returned database `studio`, schema `public`, server port 5432, `transaction_read_only=on`, `transaction_isolation=repeatable read`. The connection sets `default_transaction_read_only=on` at connection creation and limits connection/statement time. Exception text is suppressed to avoid credential leaks. No upgrade, bootstrap, create_all, or write statement is used. Final audit native exit: **0**.

## Historical dependency evidence and safe recommendation

The following is confirmed by **ORM/source and live SQL schema/FK evidence**:

- `scene_generations.workflow_id` and `director_runs.workflow_id` are non-null FK references to `workflow_registry.id`, both `ON DELETE RESTRICT`.
- GenerationAttempt has no workflow identity column. Its `generation_id` references SceneGeneration with RESTRICT; count attempts through that parent. DirectorRunAttempt similarly references DirectorRun with RESTRICT.
- Workflow registry stores `id`, `code`, `version` (VARCHAR(128)), `workflow_hash`, `slot_map_hash`, graph/slot payloads, profile and enablement. There is no dedicated retired column; current execution guards also recognize `profile.retired`.
- SceneGeneration's JSON input_snapshot stores workflow ID/code/version/hash, slot-map hash, graph/base graph and runtime profile. DirectorRun has JSON input_snapshot and output_manifest; its service stores workflow identity in the frozen input. A snapshot does **not** remove a relational FK dependency.
- FinalVideo has JSON manifest and manifest_hash, not a workflow FK. Current assembly manifests record scene/generation/asset identities and content checksums rather than embedding workflow identity. `final_video_scenes.generation_id` references SceneGeneration with RESTRICT. Final-version dependency counts must include that indirect path and scan existing JSON manifests, whose historical shapes cannot be inferred from today's writer.

Live total row counts:

| Table | Rows |
| --- | ---: |
| scene_generations | 0 |
| generation_attempts | 0 |
| director_runs | 0 |
| director_run_attempts | 0 |
| final_videos | 0 |
| final_video_scenes | 0 |

Exact legacy registry rows below all have `enabled=false`, `approved_at=NULL`. Each has **0** SceneGenerations, GenerationAttempts, DirectorRuns, DirectorRunAttempts and dependent final versions. All legacy code/ID/hash matches in the JSON identity scan were **0**.

| Code | Registry row ID | Version |
| --- | --- | --- |
| H3_T2V_STANDARD | 7b3051b8-2574-4280-af90-61fc5ed70e91 | 2026-09-10-reference |
| H3_I2V_STANDARD | 03eb4d56-e19c-4997-bdb1-3e4485e02162 | 2026-09-10-reference |
| H3_FIRST_LAST_STANDARD | 769ced38-3107-47cc-bc77-33b0cd4f374f | 2026-09-10-reference |
| H3_R2V_1_IMAGE_REFERENCE | f6c114e8-2778-4a9f-b456-dd6f23e25712 | 2026-09-10-reference |
| H3_LAST_FRAME_STANDARD | 8c449953-ec6b-42d5-859c-bfaf65925d43 | 2026-10-05-derived-unexecuted |

**Exact recommendation: CASE A.** These five specific rows can be removed by a narrowly targeted, separately authorized cleanup command/migration after locking/rechecking their dependencies in the cleanup transaction. Keep all 12 Director rows, all unrelated rows, and all historical reports. Retention tombstones are unnecessary for this observed dataset. No row was removed by this audit. The present disabled legacy rows also remain blocked by source execution guards, so retaining them until that cleanup has no inspected new-generation execution path.

Live schema defines two direct inbound workflow_registry FKs, both RESTRICT (SceneGeneration and DirectorRun); no others were returned. Their empty referencing tables permit targeted removal now, but dependency checks must still be repeated at write time. If future data appears before cleanup, retain each referenced original row intact with execution disabled. A snapshot does not remove an FK dependency. Do not repoint history to Director IDs, alter graph/slots/profile/version/hash fields, or strip payloads without auditing historical readers.

Current source registry: **12 Director entries**, six single-scene and six aggregate. Live registry has **17 rows: 12 Director + 5 legacy**, all disabled. `workflow_loader.load_manifests` applies `require_director_execution`; GenerationService rejects explicit non-Director/retired IDs and filters automatic selection; generation capabilities omit non-Director/retired records. This audit did not enable or qualify Director workflows.

## SQL evidence and reproducible queries

**39 recorded read-only SELECT statements executed successfully**, plus Alembic's live revision API query. Exact statements, bound parameters and returned rows appear in the log's `sql` list; the three originally prepared `sql_plan` queries now also have EXECUTED_READ_ONLY status. The registry identity query records IDs, versions, workflow/slot hashes and disabled state without graph/profile secrets.

The recursive scan covered all non-registry JSON columns, including snapshots/manifests, scene generation_config/spec, video config, assets/media metadata, idempotency responses and other context/output columns. All legacy identity matches were zero. Arbitrary prompts and media references are excluded from saved scan results.

Representative SQL for the executed checks is shown below; the log preserves the exact executed forms/parameters:

```sql
SELECT version_num FROM alembic_version;

SELECT table_name, column_name, data_type, character_maximum_length
FROM information_schema.columns
WHERE table_schema = current_schema()
  AND (column_name LIKE '%workflow%'
       OR column_name LIKE '%snapshot%'
       OR column_name LIKE '%manifest%')
ORDER BY table_name, ordinal_position;

SELECT con.conname, src.relname AS source_table,
       dst.relname AS target_table, pg_get_constraintdef(con.oid) AS definition
FROM pg_constraint con
JOIN pg_class src ON src.oid = con.conrelid
JOIN pg_class dst ON dst.oid = con.confrelid
JOIN pg_namespace ns ON ns.oid = src.relnamespace
WHERE con.contype = 'f' AND ns.nspname = current_schema()
  AND dst.relname = 'workflow_registry';

SELECT w.id, w.code, w.version, w.workflow_hash, w.enabled,
       (SELECT count(*) FROM scene_generations g
        WHERE g.workflow_id = w.id) AS generations,
       (SELECT count(*) FROM generation_attempts a
        JOIN scene_generations g ON g.id = a.generation_id
        WHERE g.workflow_id = w.id) AS attempts,
       (SELECT count(*) FROM director_runs d
        WHERE d.workflow_id = w.id) AS director_runs,
       (SELECT count(*) FROM director_run_attempts a
        JOIN director_runs d ON d.id = a.director_run_id
        WHERE d.workflow_id = w.id) AS director_attempts,
       (SELECT count(DISTINCT fvs.final_video_id) FROM final_video_scenes fvs
        JOIN scene_generations g ON g.id = fvs.generation_id
        WHERE g.workflow_id = w.id) AS dependent_final_versions
FROM workflow_registry w
WHERE w.code IN ('H3_T2V_STANDARD', 'H3_I2V_STANDARD',
                 'H3_FIRST_LAST_STANDARD', 'H3_R2V_1_IMAGE_REFERENCE',
                 'H3_LAST_FRAME_STANDARD')
ORDER BY w.code, w.version;
```

The script inventories workflow identity and snapshot/manifest columns plus all non-registry JSON columns, and recursively extracts identity fields and exact legacy code/row-ID/hash matches. The live registry version column is checked alongside ORM metadata. All 18 scanned non-registry column surfaces returned zero identity/legacy matches in this dataset.

## User downloads

Concrete inspected behavior:

- Both asset and final-download endpoints depend on `require_editor`. Authentication requires a valid unexpired session and an active account; ADMIN/EDITOR are permitted. Workspace resources are intentionally shared among editors per `docs/authz-matrix.md`; created_by is provenance, not ownership authorization.
- Assets must exist, be READY and have deleted_at unset; otherwise no URL is signed. Final versions must additionally exist, be READY and have output_asset_id; their backing asset receives the same READY/deletion checks.
- `AssetStore.presign_download` calls the public S3 client's `generate_presigned_url('get_object', ...)`, with the persisted validated key and default **900-second expiry**. Content disposition is `attachment` with a percent-encoded UTF-8 filename; unsafe object-key paths are rejected.
- Frontend download handlers request the API URL then use `window.open(download.url, '_blank')`. The inspected routes return URL metadata; they neither queue a job nor proxy the media body through FastAPI. No Redis step is involved in these download routes.

Database-free probes cover roles, missing/expired sessions, disabled accounts, ready/not-ready/deleted/missing assets and finals, one signing call for valid inputs, no signing call for invalid inputs, public-client GET parameters, filename encoding and unsafe keys. The signature SDK was replaced with a fake client, so actual object access, live endpoint reachability and cryptographic URL validity are **NOT_RUN**. Issued URLs use the configured expiry; the API session is checked when issuing a URL, not on each direct storage GET.

No confirmed download authorization/READY/deletion/disposition/expiry-path defect was found within this scope.

## Queue fairness and current tests

`workers/common.py::oldest_pending_generation` locks each CREATED queue head with `FOR UPDATE SKIP LOCKED`, excludes DirectorRunMember projections from standalone candidates, and compares `(created_at, id, kind)` globally. Both claim paths use PostgreSQL transaction advisory lock `971031`, check active work in the opposite queue, prioritize recovery, and re-evaluate oldest pending work during admission. GenerationScheduler routes active work first and does not fall back to a younger queue after a failed/stale route. No new queue framework was introduced.

The five fake-result helper probes cover either job type being older, equal timestamps, absent/locked Director head, empty queues, and PostgreSQL SQL compilation with SKIP LOCKED/member exclusion. They do **not** prove real lock contention, concurrent replicas, recovery durability or starvation freedom.

Current tests were inspected and inventoried, **not run**:

- `tests/integration/test_generation_scheduler.py`: stale-route revalidation, prior-video ordering, continuous backlog in either direction, older standalone priority, active lease/recovery in either direction, stable ties, member exclusion and no younger fallback.
- `tests/postgres/test_scheduler_fairness.py`: locked Director head yields to standalone; replicas admit one pending job across both types.
- `tests/postgres/test_asset_validation_concurrency.py`: locked validation row/single claim and concurrent enqueue checksum binding.
- Asset validation worker tests: success/replay, failure/backoff, lease fencing, heartbeat failure, crash reclamation, immutable recovery, deletion race, reconciler recheck, rollback and SKIP LOCKED compilation.

Why NOT_RUN: integration `session_factory` calls create_all on SQLite; PostgreSQL fixtures run `alembic upgrade head`, CREATE SCHEMA/create_all and DROP SCHEMA. Running these would violate this handoff's no-test-DB/schema/migration restriction. No previous test logs are presented as fresh results.

AssetValidationWorker admission uses VALIDATING, undeleted rows, QUEUED/RUNNING validation phases, due retries and available operation claims; its query orders by updated_at/id and uses SKIP LOCKED. This is separate from GPU generation's age fairness. No confirmed fairness regression was found by source inspection; real PostgreSQL fairness remains **NOT_RUN**.

## Compose migration gate

Fresh YAML probes passed for `api`, `dispatcher`, `assembler`, `asset-validator`, and `reconciler`: each depends on migrate with `condition: service_completed_successfully`. The migration service command is `alembic upgrade head`, restart `no`, and inherits healthy PostgreSQL/MinIO dependencies. Runtime services also require healthy MinIO. The original API and PostgreSQL containers are running and current=head was queried. This confirms the gate configuration and application DB revision; migrate-service completion status, all service health checks and actual worker progress were not exercised by this sidecar. No restart/start was attempted.

## Fresh validation and changed paths

Audit invocation:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
& .\backend\.venv\Scripts\python.exe -B .artifacts/final-ui-media-closeout/director-closeout-db-audit.py
```

Actual output summary:

```text
database_status: VERIFIED_READ_ONLY
alembic_current: [b5d9f1a3c7e2]
alembic_heads: [b5d9f1a3c7e2]
behavior_status: PASS
checks_passed: 35
sql_queries: 39
audit native exit: 0
```

Only these paths were created/edited by this audit:

- `.artifacts/final-ui-media-closeout/director-closeout-db-audit.py`
- `.artifacts/final-ui-media-closeout/director-closeout-db-audit.log`
- `tasks/archive/reports/director-closeout-db-audit.md`

The read-only original application DB audit is complete. Removing the five unreferenced legacy rows remains separate work for the parent; this sidecar performed no DB mutation. PostgreSQL concurrency suites and live storage downloads remain NOT_RUN under this handoff. This scoped report does not provide a full closeout verdict.

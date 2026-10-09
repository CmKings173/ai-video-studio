# Qualification after local implementation

The live A–S evidence ledger is `tasks/evidence/production-acceptance-matrix.md`. Local fixtures and CPU exports do not qualify H3 generation or a production deployment. On Oct6 the Docker engine pipe was absent; the observed host GPU was QuadroT1000/4096MiB. No target run, production migration or restore drill was performed. Local review gates are still open.

## Local verification

Run only after the implementation/review fix slices finish. From `D:/project/ai-video-studio/backend`:

```powershell
$env:PYTHONPYCACHEPREFIX='D:\project\ai-video-studio\workspace\test-pycache'
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
.\.venv\Scripts\python.exe -m ruff check . --no-cache
.\.venv\Scripts\python.exe -m compileall -q apps workers migrations
.\.venv\Scripts\python.exe -m alembic heads
.\.venv\Scripts\python.exe -m apps.api.scripts.export_openapi
```

From the frontend, run frozen installation, tests, typecheck, lint and the preserving production build wrapper. The wrapper preserves the user's existing `next-env.d.ts` bytes. A missing disposable `POSTGRES_TEST_DATABASE_URL` is an explicit skip, not PostgreSQL migration or locking proof. Use only a dedicated test database: the PostgreSQL test lane creates/changes its test schema.

## Target runtime and profile approval

Follow `docs/h3-poc/runtime-tooling.md` for exact probe/matrix commands, media roles, provenance and interrupted-submission reconciliation. Export genuine API graphs from the installed target and verify their current `/object_info`; UI/subgraph exports or guessed node IDs are insufficient. Pin the model, encoder, VAE, LoRA and custom-node versions/digests required by each candidate graph.

Qualify each offered mode/profile/ratio/input-count variant independently. Run First, Last, Both and each named reference workload with its actual required categories. Check the downloaded output's checksum, decoded AV, dimensions, FPS, video/audio spans and frame count. Record cold/first-observed, family-switch and excluded warmup separately, followed by at least three measured warm repeats. Unknown timing/VRAM components stay unknown. Preserve concurrency1 until actual scheduler/hardware evidence supports a change.

An uncertain or interrupted submit is unresolved even if the queue later becomes empty. Reconcile its client/prompt correlation against queue and history before any new submission or resume. A VERIFIED probe is media evidence; it is not automatic workflow enablement. Promotion still needs hash-bound evidence accepted by the admin/shared contract gate and creative review. Keep unexecuted/unsupported cases disabled, including full references without an actual matching native export. No hosted H3 2K capability is implied for the open-source runtime.

## Disposable restore drill

Use an already isolated UAT Compose stack and a reviewed environment file for that stack. Its database/bucket/ports/volumes must be distinct from operator data; verify Compose `env_file` references as well as command-line interpolation. This document does not supply or copy operator credentials. Set `$taskUatEnv` to the actual UAT env path and validate without printing interpolated secrets:

```powershell
Set-Location D:\project\ai-video-studio
$taskUatEnv='D:\project\ai-video-studio\workspace\qualification\uat.env'
docker compose --env-file $taskUatEnv -f .\infra\compose.yaml config --quiet
.\infra\scripts\backup.ps1 `
  -Destination D:\project\ai-video-studio\workspace\qualification\backups `
  -EnvironmentFile $taskUatEnv
```

Use the actual backup path printed by the successful script, including its timestamp/UUID; do not guess it. The script checks DB dump, MinIO object report and SHA256 manifest. Generate new disposable restore names for each attempt. Example after assigning `$taskBackupPath` to that actual path:

```powershell
$taskRestoreId=[Guid]::NewGuid().ToString('N')
$taskRestoreDb='studio_restore_'+$taskRestoreId
$taskRestoreBucket='ai-video-restore-'+$taskRestoreId
$taskRestoreStarted=Get-Date
.\infra\scripts\restore.ps1 -Backup $taskBackupPath `
  -Database $taskRestoreDb -Bucket $taskRestoreBucket `
  -EnvironmentFile $taskUatEnv -Force
$taskRestoreElapsed=((Get-Date)-$taskRestoreStarted).TotalSeconds
```

Existing/live database names and source buckets are rejected by the script; an existing target database is an error. A partial restore remains failed until both database and object report checks pass. Record measured backup age/RPO and restore/RTO, counts/bytes/checksums, then point a separate UAT API at the restored database and bucket. Run reconciliation there and download original generations, enhanced selections and final versions. Compare immutable hashes and lineage with the pre-backup records. Restore-script success alone does not prove API downloads or reconciliation. Preserve failed drill artifacts for diagnosis.

## Deployed UAT evidence

Use five distinct editor sessions through the configured trusted ingress. Verify one editor's failed-login throttle and logout do not revoke other sessions; spoofed browser forwarding headers must not become trusted identities. Exercise optimistic edit conflicts, a lost generation/batch response, parent Regenerate/Variation after source edits, per-scene mixed settings, ordered references and exact accepted prompt. Verify unknown/unqualified capabilities remain unavailable.

Inject worker restart/cancel and submission response loss only into this isolated UAT stack. Reconcile before retry; demonstrate no duplicate active generation/assembly and no mutable historical snapshots. Exercise scoped background audio, CUT/CROSSFADE, keep/mute/background delivery, all seven preset canvases and both pad/crop policies, MP4 H264/yuv420p/AAC48k/+faststart and actual download. Real enhancement needs its independently configured provider; an unavailable-provider response is not enhanced-output proof.

Store sanitized transcripts, reports and actual media hashes in a new run directory. Do not overwrite historical results or attach cookies/passwords to reports. Update `docs/uat.md` and the A–S ledger only for checks actually performed. READY_FOR_TARGET_GPU_POC, READY_FOR_UAT and PRODUCTION_READY are separate verdicts; choose the strongest verdict supported by current evidence, never by intended commands.

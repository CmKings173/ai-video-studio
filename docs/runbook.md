# Operations runbook

## Health and first response

Use `GET /api/v1/health/live` for process liveness and
`GET /api/v1/health/ready` for PostgreSQL, MinIO and ComfyUI readiness. Scrape
`/api/v1/metrics` with the dedicated bearer token. `python -m workers.health
--json` gives the same checks plus queue and stale-lease counts.

If generations stop progressing, inspect `studio_generation_*_count` and
`studio_generation_stale_lease_count`. A worker restart is safe: leases expire,
the dispatcher reconciles uncertain Comfy submissions by client correlation,
and technical retries create a new `generation_attempts` row.

If an asset is missing or corrupt, stop cleanup, run the admin storage
reconciliation endpoint, inspect `corrupt_objects`/`missing_objects`, and restore
the matching MinIO object from backup before retrying the generation. Do not
manually change a `READY` checksum.

If assembly fails, the final version remains tied to its immutable manifest. A
transient FFmpeg error is retried up to `MAX_ASSEMBLY_ATTEMPTS`; an invalid
manifest or exhausted retry is terminal and leaves the video dirty for an
operator-selected retry.

## Backup and restore

Run the backup script daily from the compose project directory:

```powershell
.\infra\scripts\backup.ps1 -Destination D:\backups\ai-video-studio
```

Keep the generated PostgreSQL dump, MinIO snapshot (`manifest.json`) and
`manifest.sha256` together. The MinIO utility streams objects and verifies each
object checksum during restore; it never replaces a non-empty target without
the explicit `--replace` flag.
Set `BACKUP_STATUS_FILE` to the generated `backup-status.json` path through a
read-only host mount if the Prometheus backup-age alert is required.
For a restore drill, use a dedicated database/bucket and an explicit `-Force`
flag; never restore over production in place:

```powershell
.\infra\scripts\restore.ps1 -Backup D:\backups\ai-video-studio\<timestamp> `
  -Database studio_restore -Force
```

Record start/end timestamps, row counts, object counts, checksum mismatches and
measured RTO in the UAT/restore evidence log.

# Database and storage operations

PostgreSQL is the source of truth for identity, project state, workflow versions,
generation attempts, immutable assembly manifests and asset metadata. MinIO stores
the bytes referenced by `assets.object_key`; a `READY` row is valid only when the
object exists, has the recorded size and (when available) the recorded SHA-256
metadata.

## Recovery contract

Browser uploads land at `staging/assets/<asset-id>/upload`. Completion validates
the staged object, conditionally promotes it to the immutable `assets/...` key,
then marks the row `READY`. The reconciler can finish either half of that
transaction after a crash. Staging objects older than
`ORPHAN_OBJECT_RETENTION_HOURS` are eligible for cleanup only after reconciliation.

| Item | Default target | Evidence owner |
| --- | ---: | --- |
| RPO | 24 hours (`BACKUP_RPO_HOURS`) | Operator backup report |
| RTO | 4 hours (`BACKUP_RTO_HOURS`) | Restore drill report |
| PostgreSQL backup | daily custom-format dump | `infra/scripts/backup.ps1` |
| MinIO backup | daily data copy plus checksum manifest | `infra/scripts/backup.ps1` |
| Failed/temp asset retention | 24 hours | `RETENTION_FAILED_HOURS` |
| Pending/temp asset retention | 24 hours by default | `PENDING_ASSET_RETENTION_HOURS` / `ORPHAN_OBJECT_RETENTION_HOURS` |
| Deleted asset record retention | 7 days by default | `DELETED_ASSET_RETENTION_HOURS` and cleanup audit |

RPO/RTO are targets until a restore drill records measured values. Never delete
the source database or MinIO bucket as part of a routine backup. Restore into an
isolated PostgreSQL database and bucket first, run migrations, reconcile assets,
and compare the checksum manifest before cut-over.

## Required indexes and invariants

Alembic is authoritative. `alembic upgrade head` creates FK indexes, one enabled
workflow per mode, one active assembly per video, immutable final manifest
triggers (PostgreSQL), and the relational final background-audio reference.

## Scene specification mapping

V1 deliberately uses the spec's proposed JSON shape instead of a separate
`storyboards` table: `Scene.prompt`, `negative_prompt`, `duration_seconds`,
`enabled`, `scene_order` and `selected_generation_id` are queryable columns;
the creative fields (`title`, `purpose`, `description`, `subject`, `action`,
`environment`, `camera`, `lighting`, `style`, `continuity`, `dialogue`,
`soundscape`) are validated and stored in `Scene.spec`. Generation and assembly
snapshots copy this JSON, so later editor changes cannot rewrite history.
Application mutations lock `video -> scene -> generation/final` in that order.

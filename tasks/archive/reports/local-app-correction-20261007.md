# Local application correction round — 2026-10-07

Spec: user attachment `fe7a7858-609d-47bd-b3d2-e354882c6a4f/Pasted text.txt`.

## Source and constraints

Workspace ai-video-studio; branch codex/production-safety-fixes;
HEAD 01b740d8be4d58d3bd15bc46dd9842afb48af8bb.
Initial state dirty, staged 0 / unstaged 52 / untracked 161.
No reset, stash, commit, push, database recreation, data truncation or GPU qualification.
Ruling: preserve and modify the existing dirty workspace as explicitly requested;
do not create a separate checkout which omits the pending implementation.

## Work slices

| Slice | Owner | Scope / shared seams | Status |
|---|---|---|---|
| DB version and operational validation | parent | ORM/API version128, new Alembic migration, actual local DB | verified; final review pending |
| Auxiliary weights, provenance, output canvas | Banach | backend qualification helpers; preserves public settings contract | implementing |
| Editor, continuity, selection, generation UX | Parfit | workspace/editor/generation helpers; uses unchanged DTO | implementing |
| Audio ownership and final-version errors | Boyle | assembly backend + assembly/final pages; disjoint from editor | implementing |
| DB independent review | Planck | read-only migration/test review | pending |

Ruling: PostgreSQL narrowing downgrade refuses versions longer than64 rather
than truncating identifiers or altering the unique constraint.

## Actual application database

Effective configuration loaded in the same order as backend/main.py: backend/.env,
then root/.env, retaining shell overrides. Host localhost, port5432, database studio.
The pre-existing python processes were running main.py but no API port was listening.
Original PostgreSQL revision a4c8e0f2b6d1, version VARCHAR(64).
Backup: backups/database/studio-before-version-migration-20261007T083727Z.dump;
87681 bytes, SHA256 4218db802eba043943e9651e9bcb9638403569802c9710f2a8bf3766358e5174.
Container PostgreSQL system identity was verified against the configured app
connection before dumping. Custom archive pg_restore --list validated including
workflow_registry. Backup retained and excluded from Git.

Alembic current / heads / upgrade head / current: PASS.
After: revision b5d9f1a3c7e2; information_schema confirms version VARCHAR(128),
quality_profile and execution_scope both present. PostgreSQL system identity unchanged.
Real main.py / uvicorn startup: Application startup complete; /api/health/live=200.
Actual DB contains all six aggregate Director versions of length68, all disabled.

## Verification so far

- Before fix, new schema/ORM regressions:2 failed,1 passed.
- Targeted schema/seeding/migration tests with isolated PostgreSQL lane:5 passed.
- Migration scoped ruff: PASS.
- Whole round backend/frontend/containers and independent review: pending.

GPU/H3 runtime qualification: NOT_RUN — no target GPU available.

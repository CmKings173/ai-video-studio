# Working in AI Video Studio

## Code map
- `backend/apps/api/app/`: API routes, authorization, models, schemas and services.
- `backend/workers/`: generation, Director, assembly and retention workers.
- `backend/tests/`: unit, integration, contract and isolated PostgreSQL tests.
- `frontend/`: UI and API client; also follow `frontend/AGENTS.md`.
- `infra/compose.yaml`: local service configuration.
- `docs/architecture/`: workflow and runtime qualification contracts.

## Search and changes
- Prefer `rg` for text and `rg --files` for filename discovery.
- Preserve existing modified and untracked files. Never reset or clean another task's work.
- Give parallel agents disjoint write scopes. The lead owns shared files; reviewers are read-only.
- Do not commit or push without an explicit request.

## Data protection
- Use isolated test databases and synthetic storage. Never delete live application or MinIO data,
  run migrations on the application database, or start GPU generation without explicit authorization.
- Missing ownership, output provenance or runtime evidence must fail closed.
- Keep actor attribution separate from resource ownership; do not rewrite historical ownership
  merely to make tests pass.
- PostgreSQL tests require a dedicated test database. SQLite results do not verify row-lock races.

## Verification and handoff
- In `backend`, use `.venv/Scripts/python.exe -m pytest` and `.venv/Scripts/ruff.exe check apps workers tests`.
- Check Python syntax and pytest collection before broad test execution.
- In `frontend`, run relevant tests, typecheck, lint and build for UI changes.
- Validate configuration with `docker compose --env-file .env.example -f infra/compose.yaml config --quiet`.
- Record reproduction, commands, results, reviewers and outstanding limits in `tasks/`.
  Current cross-phase ledger: `tasks/phase2-4-systemic-closeout-2026-10-12.md`.
- Relevant policy: `docs/authentication.md`, `docs/authz-matrix.md`,
  `docs/production-generation-qualification.md` and `docs/architecture/`.

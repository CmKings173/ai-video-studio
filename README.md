# AI Video Studio

Monorepo for the on-premise AI advertising video studio.

## Folder ownership

```text
backend/   FastAPI API, workers, Alembic migrations, H3 workflows, backend tests
frontend/  Next.js web app, UI components, browser-facing API client code
infra/     Docker Compose and deployment wiring
docs/      product, architecture, and implementation notes
tasks/     local task notes and review artifacts
```

Backend code must stay under `backend/`. Frontend code must stay under `frontend/`.
The two apps run as separate servers and communicate over HTTP/SSE.

## Backend

```bash
cd backend
uv run uvicorn apps.api.app.main:app --reload --host 0.0.0.0 --port 8000
```

Useful checks:

```bash
cd backend
uv run ruff check .
uv run python -m compileall -q apps workers migrations tests
uv run pytest -q
```

## Frontend

```bash
cd frontend
yarn install
yarn dev
```

The browser-facing frontend calls relative `/api/v1/...` paths. Next.js proxies those requests server-side to `API_BACKEND_URL`, which defaults to `http://localhost:8000` for local development and is set to `http://api:8000` in Compose:

```text
API_BACKEND_URL=http://localhost:8000
```

Useful checks:

```bash
cd frontend
yarn typecheck
yarn lint
yarn build
```

## Docker Compose

Compose lives in `infra/` so runtime wiring is separate from app source:

```bash
cp .env.example .env
docker compose --env-file .env -f infra/compose.yaml up --build
```

Default local ports:

```text
frontend  http://localhost:3000
backend   http://localhost:8000
minio     http://localhost:9000
```

For a local-only PostgreSQL container without a password, use:

```bash
docker compose -f infra/compose.local-postgres.yaml up -d
```

Local PostgreSQL connection:

```text
host      localhost
port      5432
user      studio
password  <blank>
database  studio
```

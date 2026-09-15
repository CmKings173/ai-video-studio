# BE/FE Next.js Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split `ai-video-studio` into a backend FastAPI server and a frontend Next.js server with explicit folder ownership.

**Architecture:** Keep one monorepo. Move existing Python backend implementation into `backend/`; create a new `frontend/` Next.js app; keep Compose orchestration in `infra/` and configure it to build/run both servers.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, uv, PostgreSQL, MinIO, Next.js, React, TypeScript, CSS modules/global CSS, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-09-14-be-fe-nextjs-split-design.md`

## Global Constraints

- Backend and frontend must run as separate servers.
- Backend files live under `backend/`; frontend files live under `frontend/`.
- Do not reuse deleted/nested reference frontend source.
- Do not alter historical migrations.
- Keep backend imports runnable from the backend package root.
- Keep Docker Compose as orchestration, not as application source.

---

### Task 1: Move backend into `backend/`

**Files:**
- Move: `apps/`, `workers/`, `migrations/`, `tests/`, `workflows/`, `pyproject.toml`, `uv.lock`, `alembic.ini`, `Dockerfile` into `backend/`
- Modify: `backend/.dockerignore`
- Modify: root `.gitignore`

**Interfaces:**
- Produces: `backend/` as the Python working directory.
- Preserves: `uvicorn apps.api.app.main:app`, `python -m workers.dispatcher`, and Alembic `script_location = migrations`.

- [ ] Verify repo is clean before moving.
- [ ] Create `backend/`.
- [ ] Move backend-owned files into `backend/`.
- [ ] Move root `.dockerignore` into `backend/.dockerignore` and update ignored paths for the backend build context.
- [ ] Keep root `.gitignore` at root and add frontend ignores.
- [ ] Run backend static checks from `backend/`.

### Task 2: Move Compose into `infra/`

**Files:**
- Move: `compose.yaml` to `infra/compose.yaml`
- Modify: `infra/compose.yaml`
- Modify: root `.env.example`

**Interfaces:**
- Produces: Compose services `api`, `dispatcher`, `assembler`, `reconciler`, `frontend`, `postgres`, `minio`, `migrate`.
- Backend build context: `../backend`.
- Frontend build context: `../frontend`.

- [ ] Move `compose.yaml` into `infra/`.
- [ ] Change backend build context to `../backend`.
- [ ] Change backend `env_file` to `../.env`.
- [ ] Add a `frontend` service on port `3000`.
- [ ] Set `NEXT_PUBLIC_API_BASE_URL` for frontend.
- [ ] Update `.env.example` with frontend URL and base API URL.
- [ ] Run Compose config validation if Docker Compose is available.

### Task 3: Scaffold Next.js frontend

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/next.config.ts`
- Create: `frontend/tsconfig.json`
- Create: `frontend/Dockerfile`
- Create: `frontend/.dockerignore`
- Create: `frontend/app/*`
- Create: `frontend/components/*`
- Create: `frontend/lib/*`

**Interfaces:**
- Produces: Next.js app on port `3000`.
- Consumes: `NEXT_PUBLIC_API_BASE_URL`.

- [ ] Create minimal Next.js TypeScript config.
- [ ] Create app router pages for the simplified Stitch screen map.
- [ ] Create shared shell/navigation components.
- [ ] Create local mock data and an API config seam.
- [ ] Add responsive, accessible dark UI styling.
- [ ] Run frontend lint/build if dependencies can be installed.

### Task 4: Root documentation

**Files:**
- Modify: `README.md`

**Interfaces:**
- Produces: developer entrypoint explaining backend and frontend commands.

- [ ] Document folder ownership.
- [ ] Document backend local commands from `backend/`.
- [ ] Document frontend local commands from `frontend/`.
- [ ] Document Docker Compose command from `infra/`.
- [ ] Document environment file expectations.

### Task 5: Final verification

**Files:**
- No production file changes unless verification reveals a path issue.

**Interfaces:**
- Produces: honest status for backend, frontend, and Docker checks.

- [ ] Run `git diff --check`.
- [ ] Run backend `uv run ruff check .`.
- [ ] Run backend `uv run python -m compileall -q apps workers migrations tests`.
- [ ] Run backend `uv run pytest -q`.
- [ ] Run frontend `npm install` or `npm ci` if possible.
- [ ] Run frontend `npm run lint` and `npm run build` if dependencies are present.
- [ ] Run `docker compose -f infra/compose.yaml config --quiet` if Docker Compose is available.

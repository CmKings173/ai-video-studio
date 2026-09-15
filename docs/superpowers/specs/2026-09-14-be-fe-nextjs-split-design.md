# BE/FE Next.js Split Design

## Goal

Split the current backend-only `ai-video-studio` repository into a clear monorepo with a Python/FastAPI backend and a separate Next.js frontend, each running as its own server.

## Architecture

The repository remains one Git project. Runtime code is separated by ownership:

- `backend/` owns FastAPI, workers, database migrations, H3/Comfy workflows, tests, and Python packaging.
- `frontend/` owns the Next.js web application and calls the backend over HTTP/SSE.
- `infra/` owns Compose/nginx orchestration that wires the two servers together.

Root files are limited to repository documentation, ignore rules, and shared environment examples.

## Server boundaries

- Backend server: FastAPI on port `8000`, exposing REST, SSE, auth, asset lifecycle, workers, PostgreSQL and MinIO integration.
- Frontend server: Next.js on port `3000`, rendering UI and using `NEXT_PUBLIC_API_BASE_URL` to reach backend APIs.

## Initial frontend scope

The first frontend scaffold should follow the simplified/basic Stitch screen map:

- Dashboard
- Projects and Project Detail
- Products and Product Detail
- Videos
- Create Video
- Video Workspace
- Asset Upload
- Assembly and Version History
- Admin

The first pass may use local mock data to establish navigation, layout, and visual hierarchy. Backend API integration should be isolated in `frontend/lib/api.ts` so real calls can replace mock data without changing page structure.

## Non-goals

- Do not reuse deleted/nested reference frontend source.
- Do not mix frontend files into backend folders.
- Do not move backend business logic into frontend.
- Do not change database migrations as part of the split unless a backend path break requires a new migration, which is not expected.

## Verification

Backend verification runs from `backend/` with `uv run ruff check .`, `uv run python -m compileall -q apps workers migrations tests`, and `uv run pytest -q`.

Frontend verification runs from `frontend/` with `npm run lint` and `npm run build` once dependencies are installed.

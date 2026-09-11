# AI Video Studio backend checklist

## Implemented

- [x] H3/ComfyUI offline preflight, symbolic slots, workflow/model hashes and report tooling
- [x] FastAPI foundation, environment configuration and Docker Compose stack
- [x] PostgreSQL schema, Alembic migrations, concurrency indexes and immutable-manifest triggers
- [x] OpenAPI v1, structured errors, idempotency keys and optimistic concurrency
- [x] Cookie/CSRF authentication, editor/admin authorization and last-admin serialization
- [x] MinIO staged upload, checksum validation, immutable promotion and reconciliation
- [x] Prompt engine, storyboard planner and SSRF-safe operator LLM configuration
- [x] Quick Clip generation preparation, H3 validation and ComfyUI adapter
- [x] Durable generation attempts, lease dispatch, progress, cancellation, retry and crash recovery
- [x] SSE snapshots with schema-versioned events and status-aware deduplication
- [x] Storyboard editing/history protection and immutable final-video assembly
- [x] Metrics endpoint, worker health report, benchmark command, backup/restore scripts and UAT/runbook

## External release gates

- [ ] Run H3/ComfyUI execution PoC on target GPU for every enabled mode and record model/custom-node hashes
- [ ] Run multi-editor/concurrent-worker UAT against PostgreSQL and MinIO
- [ ] Run isolated backup restore drill and record measured RPO/RTO
- [ ] Run benchmark matrix and record p50/p95 latency, failure rate and peak VRAM
- [ ] Freeze production workflow enablement only after executed PASS evidence is checked in

Smoke tests were intentionally not run. Unit, integration, contract, migration and static checks remain part of CI.

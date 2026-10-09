# Phase 0: verified defect closure

Read this brief first. Implement the five defects in the current checkout directly, with regression evidence, without commit or push. This is one bounded production-correctness slice before H3 features.

## Constraints

- Workspace `D:\project\ai-video-studio`, current feature branch, HEAD `01b740d`.
- Preserve pre-existing `frontend/next-env.d.ts`, `docs/ai-video-studio-production-generation-plan.md` and `tasks/archive/reports/review-2026-10-05.md`.
- NEVER run `git clean -fd` or `git reset --hard`. No commit, push, merge or new thread.
- Keep backend/frontend split. Use Yarn. Read frontend/AGENTS.md and relevant installed Next documentation before frontend code.
- Do not spawn other agents. Do not change H3 workflows/profiles, general generation snapshot shape or assembly features in this slice.
- Local tests may use existing `backend/.venv/Scripts/python.exe` per README. Docker is unavailable; do not claim live restore/production login verification.
- Other work is read-only research. Own the files needed for the five fixes and their meaningful tests. Do not alter the production plan or execution ledger.

## Required behavior

1. Product edit preserves all unrelated context keys on rename and tone update; empty tone removes only tone. Keep revision precondition. Area: frontend/app/products/[productId]/page.tsx.
2. Backup: default Compose path resolved from script location; every run volume option before api; fail on every nonzero native exit; verify dump, MinIO report, manifest before writing success. Never expose secrets or run a production backup. Area: infra/scripts/backup.ps1.
3. Restore: same path resolution, mount helper before api, safe disposable DB/bucket defaults, fail on nonzero native exit, do not announce success on partial restore. Area: infra/scripts/restore.ps1. Add regression checks using a disposable temporary directory and stub Docker command so execution and argument order are exercised without services.
4. Generate All: explicit semantic batch fingerprint includes video revision, ordered eligible scenes with scene revision/config state; unchanged ambiguous retry retains key, changed semantic input rotates key. Backend accepts/validates expected revisions and includes semantic inputs in idempotency payload; maintain safe existing API compatibility. Areas: frontend/app/videos/[videoId]/page.tsx, frontend/lib/api/generations.ts, frontend/lib/api/types.ts, backend/apps/api/app/api/generations.py, backend/apps/api/app/schemas/api.py. Add lost-response/changed-scene/added-disabled-scene regression coverage. Future per-scene generation_config will be added later; fingerprint must naturally include it when present.
5. Trusted client identity: address shared frontend IP throttling without trusting arbitrary browser headers. Prefer configured trusted ingress and backend internal proxy trust, safe fallback by default; include production wiring/config and spoof rejection tests. Areas: frontend/app/api/[...path]/route.ts, backend/apps/api/app/api/auth.py, backend/apps/api/app/core/config.py/login_throttle.py as needed, infra/compose.yaml and .env.example as needed. Use tightly scoped new helper(s) and explicit proxy trust configuration, not insecure generic forwarded-header passthrough.

Existing yarn.lock and tsbuildinfo ignore exist. Ensure Docker uses frozen lockfile and fails if missing. Do not rewrite or regenerate lock unnecessarily.

## Process and report

Write tests first where runnable; demonstrate original failure, then green. Use actual production functions with minimal boundary fakes rather than brittle source string assertions. No new dependency unless necessary. If frontend lacks runner, use Node's built-in test runner and installed TypeScript transpilation in a small reusable harness and keep tests in frontend/tests; add a Yarn test script if appropriate.

Run focused tests and appropriate backend lint/type/build checks. Avoid running a full frontend build while other checks write generated files; preserve next-env bytes around any build.

Write detailed report to `tasks/archive/reports/production-phase0-report.md`: changed paths, before/after behavior, commands and exact outcomes, red/green evidence, caveats, and questions needing controller decisions. Return a short status and changed path list.

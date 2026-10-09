# Production reliability implementation — 2026-10-07

Spec: user attachment f559c7f5-eeb3-412a-877b-567d17784c73/Pasted text.txt.
Branch codex/production-safety-fixes; starting HEAD 01b740d. Preserve existing dirty/untracked source; no commits or Git cleanup.

1. Durable asynchronous asset validation with existing Asset metadata and operation claims; HTTP 202; streaming storage/path inspection; explicit retry and recovery.
2. Independent upload/generated-output limits, bounded buffering, assembly size guard.
3. Database-coordinated fairness preserving serial Comfy admission.
4. Responsive left rail, forms, tables, tabs and accessibility.
5. Actual validated drop upload, validation polling and exact audio capability scope.
6. Bounded face detector contract.
7. Targeted tests, full backend/frontend validation, real DB/API/worker startup, independent review.

Ruling: reuse VALIDATING plus durable metadata validation envelope to distinguish queued/running, and existing OUTPUT_WRITE claim columns. No migration unless implementation proves existing semantics insufficient.
Ruling: conditional streaming PUT from validated local file preserves immutable destination; ordinary S3 CopyObject cannot safely guarantee destination non-overwrite, so do not use unsafe server-side copy.

Implementers: Archimedes asset orchestration; parent storage/limits/runtime; Cicero scheduler; Ohm responsive layout; James upload/audio; Curie detector schema.
All implementation tasks complete. Three independent scoped reviews found and then rechecked fixes; no remaining scoped P1/P2. Final backend 963 passed/1 Windows-only skip; separate PostgreSQL23 passed; frontend152 passed/typecheck/lint/build; Docker no-cache final frontend build passed; final API healthy and validation worker running. Detailed report: tasks/archive/reports/production-reliability-report-20261007.md. No commit/push.

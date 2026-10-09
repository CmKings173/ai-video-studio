# Independent review: runtime orchestration slice

Read `production-runtime-controller-report.md`, approved master prompt I/J/S in
`docs/ai-video-studio-production-generation-plan.md`, and the provided diff package.
Review spec compliance AND code quality of seven scoped files: H3Probe, benchmark_h3,
ComfyAdapter.object_info method, probe/benchmark boundary tests, offline H3Probe mode
test, five-editor login acceptance test. No implementation edits/subagents/commits.
Write only `tasks/archive/reports/production-runtime-controller-review.md`; return concise verdicts
and actionable findings with absolute file/line locations, concrete triggers and
proof. Use `code-review-and-quality` skill. Do not rerun broad tests/builds; supplied
27PASS/Ruff evidence is recent. Narrow reproductions if needed to assess actual risk.

Global constraints: no fabricated executed PASS; no increase beyond one submitted
benchmark job; uncertain submissions must be reconciled before any resubmission;
>=3 measured warmed repeats after excluded warmup; exact ratio/canvas/settings must
describe graph and actual output. Missing model/runtime/GPU evidence remains unknown.
Preserve dirty user files and monorepo split. These tools never enable workflows.

Existing media helper's two open review findings are a separate assigned fix slice:
`tasks/archive/reports/production-probe-media-review.md`; it is not changed here. The orchestration
currently requires decoded AV count evidence that the helper does not yet supply,
as explicitly documented, so actual target output cannot be labeled VERIFIED yet.
Generation core/model/schema/qualification changes are owned by an active backend
worker and outside this diff. Assess interactions only where a scoped change depends
on them. External Docker/GPU and deployed ingress are NOT_RUN.

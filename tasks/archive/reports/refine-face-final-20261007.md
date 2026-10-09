# Source state

Workspace: ai-video-studio. Root: D:/project/ai-video-studio.
Branch: codex/production-safety-fixes.
HEAD: 01b740d8be4d58d3bd15bc46dd9842afb48af8bb.
Initial dirty state: 0 staged / 52 unstaged / 151 untracked paths. Final connector/git counts: 0 staged / 52 unstaged / 161 untracked paths (directories grouped, same counting method as the initial snapshot). Dirty remains true.
Live source was checked through Codex with ChatGPT · ai-video-studio workspace_info, then git_status, before edits. Existing dirty work preserved. No commit, push, reset, stash, checkout, clean or revert.

# Upstream pinned contract

Pinned source: AIMixer/ComfyUI_MiniMaxH3_Director at a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb; checkout verified clean.
MiniMaxH3DirectorRefine output 0 (MMX_DIR_REFINE) -> Director.refine.
MiniMaxH3DirectorFaceRefine output 0 (MMX_DIR_FACE_REFINE) -> Director.face_refine.
Exact field mappings, required/optional sockets, enum/default/seed/resolution behavior and unsupported assumptions are documented in docs/architecture/h3-refine-face-contract.md and tasks/archive/reports/refine-face-graph-20261007.md.

All requested app fields are explicitly mapped or rejected. No enabled input exists upstream: disabled features disconnect Director sockets. Refine forces follow-Director aspect and ignores custom target dimensions; alternate aspect and nonzero enabled width/height are rejected. Megapixels remains supported. Separate arbitrary refine seeds/samplers/external upscale-model controls are not advertised; graph-fixed settings and frozen job seed remain deterministic. Face select is largest_face/centre_most; source face_ellipse exists but is outside the current app contract.

# Refine implementation

Six task templates across single_scene and aggregate (12 current entries) now contain one Refine config node and a typed MODEL/BasicScheduler/SIGMAS path. No feature-combination registry expansion. Importer uses an explicit allowlist, validates pinned example defaults/links, stages all artifacts before publishing files, and replaces owned entries deterministically. Builder locates classes semantically, validates topology and object_info types/enums, rejects ambiguous/missing paths and unknown fields, and binds the frozen seed. API schema, explicit frontend types and OpenAPI artifact agree. Static and negative tests cover all task/scope graphs and configurations.

# Face Refine implementation

The same graphs contain one Face config node wired to Director.face_refine, with exact source BDGROUP presentation defaults and euler/simple defaults. Typed detector/crop/canvas/selection/sampling/mask/colour/blend settings patch independently of Refine. Unknown selection/fields, invalid object_info, wrong links, missing nodes and duplicates fail closed. Frontend retains scope-specific controls and exact-settings matching.

# Qualification behavior

source_supported / graph_wired / statically_valid: true for the bounded templates.
runtime_qualified / advertised: false. All regenerated entries retain auto_approve=false, poc_verified=false and no execution_evidence. No installed model or detector availability is inferred from a filename.

Canonical typed settings participate in frozen intent/execution hashes and aggregate qualification identity. Separate Refine and Face evidence does not qualify their combination. Meaningful settings changes require exact matching evidence. Profile validation requires actual semantic feature paths before advertising. Frontend flags alone cannot enable controls without qualified Director settings.

# Motion Context P2

Removed the public API's late post-create semantic check. GenerationService now checks the effective original/derivative request before persistence: enabled Motion Context without aggregate qualification deferral raises DIRECTOR_AGGREGATE_REQUIRED. Tests prove zero newly persisted jobs, inherited scene settings, derivative overrides, and normal routing/qualification under deferral. Existing Generate All aggregate tests pass.

# History P2

History Regenerate/Variation now use the canonical current-chain rule, including the leading CUT with an old standalone parent. A review-discovered frozen-parent Motion Context case was reproduced and fixed through the shared history helper. Buttons and page mutation callbacks both guard direct submission. Reuse configuration, ready downloads and historical data remain available; independent CUT behavior is preserved.

# Verification

| Command / lane | Result |
| --- | --- |
| Targeted graph/importer/registry/qualification/Motion/scope/OpenAPI pytest | PASS: 122 tests |
| Full pytest -q with POSTGRES_TEST_DATABASE_URL on fresh refine_final database | PASS: 705 passed, 1 skipped, 129.05s |
| pytest -m postgres -q on separate fresh refine_lane_final database | PASS: 20 passed, 686 deselected, 8.87s |
| python -m ruff check . | PASS |
| python -m compileall -q apps workers tests | PASS |
| git diff --check (repository's existing line-ending configuration) | PASS |
| npm test | PASS: 89 tests, no skips |
| npm run typecheck | PASS |
| npm run lint | PASS |
| npm run build | PASS |
| docker build --no-cache --tag ai-video-studio-frontend:refine-test-20261007 ./frontend | PASS: frozen-lockfile dependencies, production frontend build |
| Docker rebuild after additive frontend type declarations | PASS: final image b8d69aa6aceeac67707817c637736faca1399609b8e6b2f994f770a179748e29 |

The single backend skip is the existing Linux UID/mode boundary test on Windows. The full suite includes all PostgreSQL tests; these were also run independently on a pristine database. An initial full run exposed stale OpenAPI and two outdated importer fixtures; all three were corrected before the passing full run. A diagnostic git diff check with autocrlf forcibly disabled reported CRLF lines as whitespace; the requested command with the repository configuration passes. No unrelated files were reformatted to address that diagnostic artifact.

Independent code review found no remaining concrete blockers in frontend/service/schema checks or pinned graph/importer/builder/registry validation. Detailed sidecar reports: tasks/archive/reports/refine-face-frontend-20261007.md and tasks/archive/reports/refine-face-graph-20261007.md. CPU tests use clearly synthetic evidence only in test fixtures; production evidence remains absent.

Disposable PostgreSQL container ai-video-studio-refine-test-20261007 was stopped after its exact container ID was verified. It used --rm, no mounted volumes, and loopback port 15433. No production database was used. The Docker test image remains local; no deployment or push occurred.

# GPU

GPU/H3 Refine runtime qualification: NOT_RUN - no GPU available.

# Remaining work

- Scheduler fairness.
- Director metrics.
- Render deployment.
- Real GPU qualification.
- Benchmark.
- Legacy cleanup.

# Verdict

REFINE_FACE_CODE_COMPLETE_AWAITING_GPU

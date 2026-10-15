# gx10-source-docker — iteration 1

Plan: user-supplied C2C PLAN, 2026-10-15. Source preparation only.

Baseline: `D:\project\ai-video-studio`, branch `codex/production-safety-fixes`,
HEAD `2e865e4b7c7519d26c9733b17c810f1ed90da7c2`.
Pre-existing changes: `frontend/next-env.d.ts`, untracked
`backend/.pytest-e-approved-regression-114e8b/`, `c2c-reconnected-proof.png`.

## Scope and rulings

- No SSH, container execution/build/pull, migrations, GPU execution, model downloads,
  commit, push, reset, or clean. Compose `config` is offline model validation only.
- Ruling: image builds remain unrun because the explicit ban on container operations
  takes precedence over "image builds where available". ARM64 runtime is not verified locally.
- Ruling: use the existing feature checkout to preserve the user's current source and
  disjoint agent writes; do not move/reset existing changes or create hidden commits.
- Keep existing H3 qualification manifests/gates unchanged; installation is not qualification.
- Ruling: scope the source-built/non-root MinIO image to the GX10 UAT overlay.
  Changing the base deployment's MinIO UID could break existing volume permissions;
  the base Compose recipe remains unchanged. GX10 receives the fixed pin/probe recipe.

| Task | Owned files/interfaces | Check |
| --- | --- | --- |
| UAT configuration | lead: infra Compose/env + deployment docs + config tests | ARM64, isolated project/volumes, private API/DB, browser MinIO |
| Node package | Popper: backend/comfy_nodes + dedicated unit tests/docs | exact upstream pin, verified hash, required nodes/dependencies, fail closed |
| Backend build | Fermat: Dockerfile/.dockerignore + dedicated contract test | uv lock installation; no architecture-specific executable |
| Integration review | lead + fresh read-only reviewer + ChatGPT | compose config, syntax/collection, backend/frontend gates, scoped diff |

Tasks share deployment interfaces but have disjoint write scopes. Node package deploys
to existing ComfyUI independently of API image; backend build leaves GPU runtime intact.

## Verified facts

- Existing Dockerfile ignored `uv.lock` (`uv pip install --system .`).
- Existing MinIO used `latest`. Official latest security release is source-only
  `RELEASE.2025-10-15T17-29-55Z`. It resolves to commit
  `9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a`; downloaded 24,232,282-byte
  source archive SHA256 is
  `45521908307306e925c98d629e1c17d78c8b72b6ee242b1bfb1409f7d8ee5841`.
  Build this source with `go.mod/go.sum`, explicit curl runtime and non-root UID.
- Registry manifest requests from this environment returned HTTP 401; no digest was
  invented and no image was pulled. Runtime healthcheck/ARM64 image execution remains unverified.

## Verification

- Python AST syntax: 255 files passed before broad test execution.
- Initial backend unit/contract collection: 898 tests, no collection failures.
- Initial backend unit/contract suite: 897 passed, 1 skipped, 42.28 seconds.
- After reviewer P2 correction, final AST syntax and collection passed (1011 tests).
  Final backend unit/contract suite: **1010 passed, 1 skipped**, 42.11 seconds.
  The skip is the Linux UID/mode-only backup test. SQLite/synthetic tests do not
  prove PostgreSQL lock races.
- Compose/Docker/JWT scoped integration of source contracts: 86 passed, 5.49 seconds.
- Node package + existing native manifest tests: initially 22 passed; after the
  P2 schema fix, **133 passed, no skips** (130 package + 3 existing native-manifest).
  Official 105 upstream file hashes verified against downloaded exact ZIP.
- Frontend `npm test`: 542 passed, 0 failed/skipped, 28.72 seconds.
- Frontend typecheck, ESLint and production build: exit 0.
  Build-generated next-env references were restored byte-for-byte to pre-build content.
- Backend Ruff (`apps workers tests`): passed after one long new test line was wrapped.
- Base and GX10 merged Compose `config --quiet`: exit 0, Compose 5.3.1.
- `git diff --check`: passed (Git autocrlf warnings only).
- Reviewer Fermat: final infra approved, no P1/P2. Independently resolved ARM64,
  UAT-scoped volumes, loopback ports, inherited MinIO command/probe and non-root
  image scoped to fresh UAT storage; base Compose matches HEAD.
- Reviewer Dalton: found P2 malformed required input containers accepted; fixed
  with structure/descriptor/known-socket/type checks and realistic positive fixtures.
  Final re-review approved, no remaining P1/P2: original malformed cases rejected,
  deletion of every one of 34 known sockets rejected, pinned valid inputs accepted;
  independently ran 133 tests, all passed. Bridge bytes match manifest.
- ChatGPT iteration 1: reviewed current source/new files and backend execution output;
  requested explicit `/out` creation in the MinIO build stage and a regression.
  Added `mkdir -p /out` before `go build` and a source contract for output-parent order.
  No native build failure was reproduced (container operations remain prohibited).
- Iteration 2: changed only MinIO Dockerfile, its Compose contract assertion, and
  this ledger. Syntax/collection passed, focused Docker/Compose/JWT **86 passed**
  (5.03 seconds), full Ruff passed, base/GX10 Compose config and diff checks passed.
  No backend/frontend runtime source changed; the previous full suite results stand.
- Frontend test/typecheck/lint/build command outputs were separately recorded in C2C
  iteration 1 after the initial backend output; available via execution_output.
- ChatGPT iteration 2 re-review: **DONE — SOURCE_READY_FOR_GX10_UAT**.
  Reviewed current source and recorded output; accepted the MinIO directory fix
  and regression, confirmed qualification/ownership/ARM64 isolation preserved,
  and reported no remaining P1/P2 source findings within the task scope.
  Review chat: https://chatgpt.com/g/g-p-6aa3bcd0393c819199b78548eca299d2-ai-video-studio/c/6ad02cd1-a7d0-83ec-8b45-f13fe7743110
- Final source inspection: 17 task files; no unexpected edits under backend apps/workers
  or frontend app/components; credential-signature scan had no matches. Base Compose
  matches HEAD, full HEAD remains `2e865e4b7c7519d26c9733b17c810f1ed90da7c2`.

## Limits

No ARM64 image builds/runtime validation, GX10 operations, database migrations,
GPU generation, model downloads, or live storage changes. All remain operator gates
in `docs/gx10-deployment.md`; no production-readiness claim is made from source tests.
The pre-existing working tree changes are retained. No commit or push.

## Source review inventory

New files are untracked and must be read directly; `git diff` does not include them.

- `backend/Dockerfile`, `backend/.dockerignore`, `frontend/.dockerignore`
- `backend/comfy_nodes/gx10_package.py`, `gx10_manifest.json`, `.gitattributes`
- `backend/comfy_nodes/studio_director_bridge/__init__.py`
- `backend/tests/contract/test_gx10_compose.py`, `test_gx10_docker_build.py`
- `backend/tests/unit/test_gx10_node_package.py`
- `infra/compose.gx10.yaml`, `infra/gx10.env.example`
- `infra/minio/Dockerfile`, `infra/minio/.dockerignore`
- `docs/gx10-deployment.md`, `docs/gx10-node-packaging.md`
- This ledger.

Unrelated pre-existing files listed in baseline are outside this task review.

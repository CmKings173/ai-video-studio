# Phase0 implementation and verification report

Date: 2026-10-05. Workspace: `D:\project\ai-video-studio`; resumed HEAD `01b740d`.

## Fix round 2: retire stale frontend batches after manual/editor changes

Read `tasks/archive/reports/production-phase0-rereview-1.md` completely. This round addresses only
its remaining P2 frontend pending-action finding. Implementation and bounded checks
are complete and ready for independent re-review; no re-review pass is claimed.
The round-1 backend completion replay, Linux helper ownership strategy, and explicit
Compose environment fixes are preserved.

The actual manual-selection mutation's `onSuccess` now retires the pending batch.
Generate All also retires it after an actual `ApiClientError` with HTTP 409/412 and
code `IDEMPOTENCY_KEY_REUSED` or `REVISION_CONFLICT`. Retirement clears the old
payload/key and requires a successful video-detail refetch before preparing another
batch. The page uses the returned fresh DTO, rather than its pre-refetch render's
video object, for current eligibility and revision preconditions. A failed refetch,
including a query result containing both stale data and an error, sends no batch
and retains the refetch requirement for the next attempt.

Transport errors and server failures remain ambiguous: the hook retains the exact
original payload, key, and revision preconditions through partial/full automatic
completion. Even a 500 response carrying a conflict code does not retire the action.
Externally made revision-only saves, manual selections, and edits restored to their
original field values may first receive the backend's definitive rejection; the
following attempt refetches and prepares a new payload/key instead of looping on
the rejected action. This supersedes round 1's incomplete frontend handling of
manual selection and revision-only transitions.

### Round-2 changed paths (only these four)

- `frontend/app/videos/[videoId]/page.tsx`: actual selection-success and batch-error
  callbacks retire the action; batch preparation awaits fresh detail when required.
- `frontend/lib/hooks/use-generate-all-action.ts`: retirement/refetch state and
  asynchronous preparation; ambiguous requests remain pinned.
- New `frontend/tests/generate-all-retirement.test.cjs`: executes the actual
  TypeScript page mutation callbacks and production hook with query/API boundaries
  faked, using the real `ApiClientError` class. Covers acknowledged select,
  deselect/reselect, external manual selection, revision-only and restored-value
  revisions, both definitive conflict codes, 412, failed refresh, and transport/503/
  500 ambiguity across partial/full completion. These are callback/hook execution
  tests, not live-browser or live-backend selection claims.
- `tasks/archive/reports/production-phase0-report.md`: this evidence and scope update.

No backend, API schema, OpenAPI artifact, shared test harness, controller qualification,
delivery preset/geometry, media probe, dependency installation, or deployment edits
were made in round 2. Existing user changes were preserved; no subagents, commits,
pushes, resets, cleans, owner/ACL, or global environment changes were performed.

### Exact round-2 verification

Frontend commands below ran from `D:\project\ai-video-studio\frontend`. Test commands
used only command-local `$env:TEMP='D:\project\ai-video-studio\frontend\.tmp';
$env:TMP=$env:TEMP` to avoid the known system-temp restriction.

| Command | Observed result |
| --- | --- |
| Before implementation: `yarn node --test tests/generate-all-retirement.test.cjs` | **Known RED: 6 failed, 3 passed**, exit 1, Node duration 1671.3782ms. Manual selection retained the old key; definitive conflicts never refetched; failed-detail handling kept sending the stale batch. The three ambiguous automatic-completion cases already passed. |
| After implementation: `yarn node --test tests/generate-all-retirement.test.cjs tests/generate-all.test.cjs` | **18 passed, 0 failed**, exit 0, Yarn 1.84s; all nine new retirement tests and all nine existing batch-identity tests. |
| `yarn test` | **27 passed, 0 failed, 0 skipped**, exit 0, Yarn 3.52s (Node 3329.8667ms); includes Product and auth-proxy regressions. |
| `yarn node node_modules/eslint/bin/eslint.js app lib components tests eslint.config.mjs next.config.ts` | **Exit 0**, Yarn 26.44s. Explicit installed ESLint entry point avoids the known Yarn shim/PATH issue and excludes scratch directories. |
| `yarn node node_modules/typescript/bin/tsc --noEmit` | **Exit 0**, Yarn 4.79s; explicit installed TypeScript entry point. |
| Repository root: `git diff --check -- 'frontend/app/videos/[videoId]/page.tsx'` | **Exit 0**; only the existing LF/CRLF notice. |
| Repository root: `Get-FileHash frontend/next-env.d.ts` | SHA-256 before/after **`0F70629890B72A0A82E91972CC032C04B658B26C265373CB711CF576BFBF8FCC`**, unchanged. |

Controller-supplied post-round-1 evidence, **not rerun or relabelled as round-2 checks**:
full backend **250 passed, 11 skipped in 91.35s**; Next production build **passed in
38.33s**, with user `next-env.d.ts` preserved. Round 2 changes no backend contracts,
so a full backend rerun/OpenAPI export was unnecessary. A fresh production build was
not run in this narrow frontend round; current lint, typecheck, and regression tests
are the supported round-2 evidence. Native live Docker backup/restore and deployment
remain **NOT_RUN**; the Windows host still does not establish Linux container writes.

## Fix round 1: three independent-review findings

The complete `tasks/archive/reports/production-phase0-review.md` was read before this round. All three
findings have implementation fixes and targeted regression coverage. The working tree
is ready for controller independent re-review; this is not a claim that re-review has
already passed. The earlier report/evidence below is retained as historical context;
this section describes the current behavior where it supersedes the initial implementation.

### P1: dispatcher completion and ambiguous batch identity

The frontend now pins a pending batch's original payload, revision preconditions,
ordered scene IDs, fingerprint, and key. Its editor-state comparison considers the
video's editor fields/config and all ordered scenes' enabled/config/prompt state,
excluding output selection and the revision increments that completion can cause.
Partial or full automatic completion therefore does not replace an ambiguous request
with a new batch containing only unfinished scenes. A true editor-input change rotates
the action. Successful acknowledgement resets it for the next action.

Backend stores a private `_batch` editor-state baseline with the idempotency response.
This is internal metadata, excluded by the existing public response model; no API
schema or general generation snapshot shape changed. Before replay, it checks the
current editor state against that baseline. It discounts only the exact automatic
selection transition: previously unselected scene, one scene revision increment,
selected COMPLETED ORIGINAL generation whose recorded input scene revision matches
the baseline, and the corresponding video increment. All editor fields, ordering,
enabled flags, scene set, and remaining revision changes must still match. The saved
response is returned through the original request hash, so request-body/key reuse is
still checked. A manual/revision-only or substantive editor change is rejected.

New HTTP tests use actual login, CSRF, request commit/rollback, generation endpoint,
READY output assets, and `Dispatcher._finish`. Explicit and legacy implicit batches
replay the identical saved response after both partial and full completion, with
exactly two total generations for the two-scene batch. Prompt/disable/add/config and
revision-only edits after completion reject the saved action. Frontend tests execute
the actual page callback and new hook through completion-driven selection/revision
updates and a subsequent editor change.

Round-1 paths: `backend/apps/api/app/api/generations.py`, new
`backend/apps/api/app/services/batch_identity.py`, new
`backend/tests/integration/test_batch_completion_replay.py`,
`frontend/app/videos/[videoId]/page.tsx`, `frontend/lib/api/generations.ts`, new
`frontend/lib/hooks/use-generate-all-action.ts`, `frontend/tests/generate-all.test.cjs`.
The dispatcher and idempotency service were not modified.

### P1: helper output ownership on Linux

Only the disposable MinIO helper `compose run` now overrides the image user. On POSIX,
`Get-HelperRunOptions` resolves the invoking UID:GID with checked native `id` commands
and runs the helper as that owner, so its files/directories are writable without
world-write permissions or changing the production image's studio UID 10001. On
Windows Docker Desktop the scoped helper uses `0:0`. PostgreSQL operations and all
long-running application services retain their existing users. Script-source and
restore-snapshot mounts remain read-only. No production chmod, chown, ACL, owner,
privileged-container, or global sandbox changes were made.

The native boundary fake checks the selected user before the service and, on POSIX,
requires it to match the host writer. A POSIX-only regression executes the production
wrapper, checks actual output ownership/mode, checks why image UID 10001 lacks access
when different from the output owner, and checks the chosen owner can write. That
Linux-specific check is **NOT_RUN on this Windows host** (explicit skip); a live
container permission probe was also **NOT_RUN**. Windows option/execution checks passed.

### P2: Compose interpolation independently of CWD

Both wrappers expose `-EnvironmentFile`, defaulting to repository `.env` resolved
from `$PSScriptRoot`. `Get-ComposeArguments` resolves and validates absolute Compose
and environment paths, and every Compose invocation uses that same explicit
`--env-file` and `-f` prefix. Missing files fail before any Docker operation.

The daemon-free regression copies the real production Compose YAML into a disposable
directory, writes synthetic required settings to its root `.env`, removes those
settings from the child environment, uses an isolated empty Docker configuration,
and invokes from a separate `caller` directory. Real `docker compose ... config
--quiet` without the explicit env file fails as the negative control. The actual
production `Get-ComposeArguments` function supplies the explicit file and the same
real Compose config check succeeds. No production credentials are loaded or printed,
and neither probe contacts the daemon. The installed CLI reported Compose v5.3.1.

Script paths for both findings: `infra/scripts/backup.ps1`, `infra/scripts/restore.ps1`,
`infra/scripts/backup_common.ps1`, `backend/tests/unit/test_backup_restore_scripts.py`.
No Compose service/image dependency change was needed in this round.

### Round-1 RED and GREEN evidence

Directly observed before these fixes:

- Backend new HTTP completion tests: **3 failed, 4 passed in 4.31s**. Explicit and
  implicit retries returned 409 after real dispatcher completion; disabling a scene
  returned selection validation rather than the saved-action conflict.
- Frontend suite with the new completion cases: **4 failed, 16 passed**, demonstrating
  replacement of the pinned payload/key. Two test cases had accidentally duplicated
  registrations in that snapshot; the duplicate registrations were removed before
  final verification (18 distinct current tests).
- New script option/interpolation selection:
  `.\.venv\Scripts\python.exe -m pytest tests/unit/test_backup_restore_scripts.py -k 'pins_environment or interpolation or linux_helper' -q`
  returned **3 failed, 1 skipped, 19 deselected in 11.03s**. Every-call env assertions
  failed for both wrappers and the production interpolation helper did not exist yet.

Current completed verification:

| Working directory / exact command | Result |
| --- | --- |
| backend: `.\.venv\Scripts\python.exe -m pytest tests/integration/test_batch_completion_replay.py tests/integration/test_generate_all.py -q` | **17 passed in 4.99s** |
| backend: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_backup_restore_scripts.py -q` | **22 passed, 1 skipped in 68.83s**; includes the real daemon-free Compose test; skip is the POSIX UID/mode boundary |
| backend: `.\.venv\Scripts\python.exe -m ruff check .` | **All checks passed** |
| backend: `.\.venv\Scripts\python.exe -m pytest tests/contract/test_openapi.py -q` | **5 passed in 1.92s**; schemas unchanged, so no export required in this round |
| frontend: `$env:TEMP = 'D:\project\ai-video-studio\frontend\.tmp'; $env:TMP = $env:TEMP; yarn test` | **18 passed, 0 failed**, Yarn duration **1.97s** |
| frontend: same command-local temporary environment, `yarn node node_modules/eslint/bin/eslint.js app lib components tests eslint.config.mjs next.config.ts` | **Exit 0**, **6.31s** |
| frontend: same command-local temporary environment, `yarn node node_modules/typescript/bin/tsc --noEmit` | **Exit 0**, **2.77s** |
| repository: `git diff --check` | **Exit 0**; only LF/CRLF notices |

An initial test invocation from the repository root rather than `backend` hit the
Windows scratch-directory ACL and produced **16 setup errors, 1 passed**. This was
an environment error, not behavioral GREEN/RED evidence; the correctly located backend
command above then passed. No ACL change or cleanup was attempted.

The controller reports the pre-fix complete backend suite at **192 passed, 10 skipped**,
the pre-fix frontend at **16 passed**, and frozen install/lint/typecheck/build passing.
The controller's new separate delivery preset/geometry and media helpers add **28 + 20**
passing cases; they are outside Phase0 and were neither edited nor claimed here.
Per controller instruction, this round ran targeted regressions/lint/typecheck,
not another full backend suite, frozen install, or frontend build. The earlier build
result below is historical; a post-round build is **NOT_RUN**. Native live Docker,
live backup/restore, and production login remain **NOT_RUN**.

`frontend/next-env.d.ts` still has the exact preservation hash recorded below. All
existing/controller/user work was retained. No subagents, commits, pushes, resets,
Git clean, or delivery-SAR work was performed. No schema was changed in this round.

## Outcome and ownership

The five requested fixes are implemented in the current checkout. Existing Product,
Generate All, API/schema, Dockerfile, package script, and regression edits were inspected
and retained. Backup, restore, and trusted client identity were completed. The subsequent
requested NFR test isolation, OpenAPI artifact refresh, and entrypoint import spacing
fixes are also complete. Independent review remains controller-owned.

No subagents, commits, pushes, resets, Git clean, production backup, or live restore
were performed. No H3 workflow/profile, generation snapshot shape, or assembly feature
was implemented by this work.

`frontend/next-env.d.ts` is byte-identical to its state at resumption, including after
the production build. SHA256 before and after:
`0F70629890B72A0A82E91972CC032C04B658B26C265373CB711CF576BFBF8FCC`.
Its pre-existing Git diff remains the user's. The production plan and execution ledger
were not edited. Qualification files, their tests, and concurrent controller changes
to `admin.py`/`workflow_loader.py` are outside Phase0 ownership and were not edited here.

## Changed paths within this slice

Paths are repository-relative. Existing partial edits are included because they form
part of the completed slice; unrelated files visible in Git status are excluded.

| Area | Paths |
| --- | --- |
| Product context; retained partial fix | `frontend/app/products/[productId]/page.tsx` |
| Semantic batch; retained partial implementation | `frontend/app/videos/[videoId]/page.tsx`, `frontend/lib/api/generations.ts`, `frontend/lib/api/types.ts`, `backend/apps/api/app/api/generations.py`, `backend/apps/api/app/schemas/api.py` |
| Native scripts and artifact checks | `infra/scripts/backup.ps1`, `infra/scripts/restore.ps1`, `infra/scripts/backup_common.ps1` |
| Trusted identity | `frontend/app/api/[...path]/route.ts`, `frontend/lib/server/client-identity.ts`, `backend/apps/api/app/api/auth.py`, `backend/apps/api/app/core/client_identity.py`, `backend/apps/api/app/core/config.py` |
| Production trust wiring | `.env.example`, `infra/compose.yaml`, `infra/ingress/default.conf.template` |
| Backend regression evidence | `backend/tests/unit/test_backup_restore_scripts.py`, `backend/tests/integration/test_generate_all.py`, `backend/tests/integration/test_trusted_client_identity.py` |
| Frontend evidence and reusable harness | `frontend/tests/product-edit.test.cjs`, `frontend/tests/generate-all.test.cjs`, `frontend/tests/auth-proxy.test.cjs`, `frontend/tests/load-typescript.cjs`, `frontend/tests/build-preserving-next-env.cjs` |
| Test/build support | `frontend/eslint.config.mjs`, `frontend/package.json`, `frontend/Dockerfile` |
| Requested baseline closure | `backend/tests/contract/test_nfr.py`, `backend/main.py`, `backend/apps/api/scripts/export_openapi.py`, `docs/openapi.yaml` |
| Report | `tasks/archive/reports/production-phase0-report.md` |

`frontend/yarn.lock` was not regenerated or edited. Docker now copies the exact lockfile
and installs with `yarn install --frozen-lockfile`; a missing lockfile fails COPY.

## Before and after

1. Product edits previously replaced context with only tone (or an empty object).
   They now clone existing context, update/remove only tone, and keep the revision
   precondition. Regression tests execute the actual page mutation and check cached
   context is not mutated.
2. Backup previously placed a helper volume after the Compose service name and could
   write success despite a failed native command or absent artifacts. The default
   Compose path is resolved from the script location and made absolute; all mounts
   precede `api`. Every Docker exit code is checked. A nonempty dump, valid MinIO
   report/object manifest, object sizes/hashes, and a complete SHA256 manifest are
   checked before success status is written. Backup directories use unique suffixes.
3. Restore previously used a cwd-relative Compose file, an unavailable container
   helper path, a fixed target database, and unchecked native exits. It now mounts
   the helper before `api`, validates the entire backup before Docker, creates a
   unique disposable database/bucket by default, rejects known live targets, fails
   if createdb fails, uses `pg_restore --exit-on-error`, and requires a consistent
   MinIO restore report before success. Snapshot mounts are read-only; reports go
   into a separate temporary directory. It does not implicitly replace bucket data.
4. Generate All now fingerprints video revision/config and ordered eligible scenes,
   including each scene's revision, prompt, negative prompt, duration, spec, and
   future `generation_config` when present. An unchanged lost-response retry keeps
   its key; semantic changes rotate it. Execution progress and object key ordering
   do not rotate it. Requests pin scene IDs and paired positive expected revisions.
   Backend locks the video/scenes, includes semantic state in the idempotency hash,
   validates revision preconditions, replays unchanged requests without duplicates,
   and rejects reused keys after semantic changes. Older clients may omit both
   revision fields; pairing and positivity are validated when supplied.
5. Login previously throttled clients behind Next using the shared frontend address.
   The configured nginx ingress overwrites the client address from its socket peer
   and supplies a server-only ingress credential. Next validates that credential
   and a single literal IP, then sends a separate internal proxy credential to the
   API. Backend validates the internal credential and normalizes the IP, including
   IPv4-mapped IPv6. Generic Forwarded/X-Forwarded-For headers and arbitrary browser
   identity headers do not establish trust. Missing/wrong credentials or malformed
   addresses use socket-address throttling. Compose disables Uvicorn proxy headers
   so generic headers cannot alter that fallback.

NFR contracts now disable dotenv loading and clear declared Settings environment
variables with a test-local monkeypatch. Runtime configuration loading is unchanged.
The OpenAPI exporter now resolves the repository root correctly (previously it wrote
under `backend/docs`), and the checked-in artifact matches the current application.
It consequently includes concurrent application contracts already present in this checkout.

## Directly observed RED evidence

Only runs observed in this resumed session are claimed here. No original RED was
available for the retained Product or Generate All implementation; those partial
edits were not reverted to manufacture one.

- Initial backend command from `backend`:
  `.\.venv\Scripts\python.exe -m pytest tests/unit/test_backup_restore_scripts.py tests/integration/test_generate_all.py -q`
  gave **12 failed, 9 passed in 43.82s**. Failures demonstrated false success after
  dump/copy/MinIO failure, absent dump/report, unsafe fixed restore defaults, unchecked
  restore exits, missing restore report, and corrupt object manifest.
- Before trusted identity implementation,
  `.\.venv\Scripts\python.exe -m pytest tests/integration/test_trusted_client_identity.py -q`
  gave **2 failed, 6 passed in 2.45s**: a second authenticated proxy client was still
  throttled with the first client, and a short proxy token configuration was accepted.
- Frontend `yarn test` after adding proxy tests but before implementation gave
  **1 failed, 14 passed**: authenticated ingress identity did not reach the backend.
- Full backend run before baseline closure, excluding the unit qualification file,
  gave **2 failed, 167 passed, 10 skipped in 75.31s**. The failures were the reported
  NFR dotenv/default-credentials pollution and checked-in OpenAPI drift. This run
  also collected concurrent controller integration tests; it is not a pure Phase0 count.
- After NFR isolation but before successful artifact refresh, the two contract files
  gave **1 failed, 9 passed in 1.90s**; only artifact drift remained.

## Completed GREEN evidence

Commands below are the exact completed verification commands, not proposed commands.
Backend uses the existing `.venv`; frontend uses Yarn and installed packages.

| Working directory / command | Observed result |
| --- | --- |
| backend: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_backup_restore_scripts.py tests/integration/test_generate_all.py tests/integration/test_trusted_client_identity.py tests/integration/test_authz.py -q` | **37 passed in 47.10s**, before the later extra script/config tests |
| backend: `.\.venv\Scripts\python.exe -m pytest -q --ignore=tests/unit/test_workflow_qualification.py --ignore=tests/integration/test_workflow_qualification.py -k 'not test_production_rejects_default_database_credentials and not test_checked_in_openapi_artifact_matches_application_schema'` | **167 passed, 10 skipped, 2 deselected in 69.66s**; baseline tests were still pending closure at that time |
| backend: `.\.venv\Scripts\python.exe -m pytest tests/contract/test_nfr.py tests/contract/test_openapi.py -q` | After isolation/artifact refresh: **10 passed in 1.64s**, no deselection |
| backend: `.\.venv\Scripts\python.exe -m ruff check .` | **All checks passed**, including requested `main.py` spacing fix |
| backend: `.\.venv\Scripts\python.exe -m apps.api.scripts.export_openapi` | **Exit 0**, after exact-scope escalation for the artifact write; following equality test passed |
| frontend: per-command `$env:TEMP = 'D:\project\ai-video-studio\frontend\.tmp'; $env:TMP = $env:TEMP; yarn test` | Current suite after removing a duplicated test: **16 passed, 0 failed**, 1.50s Yarn duration |
| frontend: same temporary environment, `yarn node node_modules/eslint/bin/eslint.js .` | **Exit 0** on completed earlier full lint runs |
| frontend: same temporary environment, `yarn node node_modules/eslint/bin/eslint.js app lib components tests eslint.config.mjs next.config.ts` | **Exit 0**, current scoped lint after the duplicate-test removal, 5.32s |
| frontend: same temporary environment, `yarn node node_modules/typescript/bin/tsc --noEmit` | **Exit 0** on completed checks |
| frontend: same temporary environment, `yarn node tests/build-preserving-next-env.cjs` | **Exit 0**, Next 16.3.5 production build; compiled, typechecked, generated 11 pages; Yarn duration **17.62s** |
| repository: `git diff --check` | **Exit 0**; only Git LF/CRLF notices |
| Seven scoped backend Python files compiled with Python `compile(..., 'exec')` in memory | **All seven passed** without bytecode writes |

Controller-supplied verification, separately attributed:

- Focused native scripts / Generate All / trusted identity / two qualification files:
  **60 passed in 60.33s**. Phase0 does not claim ownership of the qualification tests.
- Controller `yarn test` with per-command TEMP/TMP pointing to `workspace/test-temp`:
  **17 passed, 0 failed in 3.75s**. That snapshot included a duplicated execution-progress
  test subsequently removed; the current suite has 16 distinct tests and passes.
- Controller `node_modules/.bin/tsc.cmd --noEmit`: **exit 0**. The shim exists;
  Yarn's default PATH lookup is the local issue, not a missing TypeScript dependency.
- Controller compileall over `apps workers migrations tests`, with task-specific
  `PYTHONPYCACHEPREFIX=workspace/test-pycache`: **exit 0**. Default-sandbox pycache
  errors were ACL failures, not syntax errors; no ACL repair was made.

A complete undeselected backend suite was not rerun after the two baseline fixes;
their exact contract tests now pass, and the prior broad run covers the remaining tests.
No combined new full-suite count is inferred from these separate runs.

## Harness, environment, and limits

The script harness copies production PowerShell scripts into a disposable pytest
directory and runs them from a different cwd. A native Docker shim checks the actual
argument order, absolute Compose path, read-only snapshot, helper mounts, failure
stopping, artifact validation, and success reporting without services. It uses
`sys.executable`, `docker.cmd` on Windows, and an executable POSIX `docker` shell
launcher with shell-quoted paths elsewhere. Only absence of PowerShell skips this
module. Windows execution passed; the POSIX branch was implemented but not executed
on this Windows host. No Linux runtime result is claimed.

The frontend harness transpiles production TypeScript with the installed compiler,
executes actual page mutations/proxy handlers, and fakes only boundary hooks/network.
Tests use Node's built-in runner, with CommonJS imports allowed only in `tests/**/*.cjs`.
No application package dependency was added.

Default Yarn temp access under AppData failed with EPERM; per-command workspace
TEMP/TMP resolved it without global environment changes. Normal `yarn lint` and
`yarn typecheck` could not resolve their command shims here; the recorded `yarn node`
commands use the actual installed binaries. Python compileall hit existing Windows
bytecode-directory ACL denial; the in-memory syntax check succeeded. No ACL, owner,
or sandbox rules were changed. An accidentally generated `backend/docs/openapi.yaml`
from the original exporter was removed by exact file path before the corrected export.

Docker is unavailable: no real container build, Compose runtime, production login,
production backup, PostgreSQL restore, or MinIO restore was verified. The ten PostgreSQL
lane skips require `POSTGRES_TEST_DATABASE_URL`. Browser visual verification was not
performed; regression execution, lint, typecheck, and production build are recorded.

## Controller / deployment follow-up

- Independent Phase0 review is pending controller delivery; preserve any findings for
  follow-up. No implementation-blocking user question remains for this slice.
- Deployment must set different random server-only ingress and internal proxy
  credentials (the example recommends 64 hexadecimal characters), route clients
  through ingress, keep frontend/API private, and provide TLS at the public edge.
  Empty credentials deliberately retain socket-address throttling.
- If a TLS/load-balancing proxy precedes nginx, configure exact trusted `real_ip`
  peers there. The included configuration uses its immediate socket peer by default;
  it does not infer a browser address from arbitrary forwarding headers.
- Perform the disposable live restore/reconciliation and production ingress login
  checks when Docker/services become available. Partial restores leave disposable
  resources for diagnosis and never print overall success.

Relevant primary references checked for the runtime wiring:
[nginx image template substitution implementation](https://github.com/nginx/docker-nginx/blob/master/entrypoint/20-envsubst-on-templates.sh).
Installed Next documentation for route handlers, headers, and server environment
variables was read under `frontend/node_modules/next/dist/docs` before frontend changes.

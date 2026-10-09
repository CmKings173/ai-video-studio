# Independent Phase0 spec and code quality review

Result: **3 actionable findings (2 P1, 1 P2); Phase0 closure is not yet a pass.**

Reviewed the working-tree diff against HEAD `01b740d8be4d58d3bd15bc46dd9842afb48af8bb`, the Phase0 brief, new helpers, ingress and regression tests. Snapshot began at **2026-10-05T13:55:49.763244+00:00**. Inspected Phase0 files remained unchanged when checked before report creation. No implementation edits, subagents, commits, builds, production backups or live restores were performed.

## Actionable findings

### 1. [P1] Automatic output selection breaks ambiguous batch retries and can duplicate unfinished work

Locations: `backend/apps/api/app/api/generations.py:235–256`, particularly revisions in the current-state idempotency hash; `frontend/lib/api/generations.ts:16–30`; `frontend/app/videos/[videoId]/page.tsx:307–310`.

The frontend fingerprint and backend claim hash use live eligibility and editor revision counters. Those counters change without an editor action: `Dispatcher._finish` automatically selects successful ORIGINAL output and increments scene and video revisions (`backend/workers/dispatcher.py:282–291`). A committed batch with a lost response therefore cannot reliably replay after completion. This violates the Phase0 requirement to retain/replay an unchanged ambiguous action.

Independently reproduced through the actual HTTP endpoint, login, CSRF, request transactions, existing in-memory fixture and `test_generation_preparation.seed`:

1. POST an explicit batch with matching video/scene revision preconditions. First response **202**; immediate same-body/same-key retry **202**, identical response.
2. Give its generation a READY output asset and finish through actual `Dispatcher._finish(generation_id, "COMPLETED", asset_id=...)`. No prompt/config/editor changes. Scene and video revisions become 2 and the output is selected.
3. Repeat the original body/key: **409 `IDEMPOTENCY_KEY_REUSED`**, rather than the saved response. Generation count remains 1.
4. In a two-scene batch, finish only the first scene and GET refreshed video detail. The actual transpiled page mutation, exercised with existing `pageHarness`, rotates its key and selects only the unfinished second scene. Posting that resulting request/new key returns **202**, creates **generation_no 2** for scene two and leaves **2 generations** for it, although its first generation is still CREATED and no editor input changed. `useVideoEvents` refreshes video detail on completion, so this is a normal UI path.

Preserve a pending batch's action identity/payload/key across completion-driven refreshes while continuing to rotate/reject real semantic edits. Backend replay must likewise survive automatic completion. Add HTTP replay coverage after real dispatcher completion and a frontend partial-completion/lost-response regression. Existing tests change progress/active-generation fields but miss actual selection/revision updates.

### 2. [P1] Linux output mounts are not writable by the production container user

Locations: `infra/scripts/backup.ps1:18–26`; `infra/scripts/restore.ps1:35–46`. Supporting configuration: `backend/Dockerfile:21–24` sets container user `studio` (UID 10001); Compose does not override it for the helper runs.

The wrappers create host output directories with `New-Item` and bind mount them into `api` without assigning write permission to UID 10001 or selecting a compatible helper user. On a Linux host invoked by UID 1000 or root with the usual umask 022, the directories are owned by the caller and mode 0755. Container UID 10001 cannot write. Backup fails creating `/backup/minio/objects` or its report. Restore can complete PostgreSQL and object uploads, then fail writing `/reports/restore-report.json`. The wrapper correctly rejects partial success, but the requested restore still fails at the report boundary.

Disposable reproduction with the API image already built: create a temporary directory using restore's `New-Item -ItemType Directory`, then run `docker compose --env-file <repo>/.env -f <repo>/infra/compose.yaml run --rm --no-deps -T -v "${probe}:/reports" api python -c "from pathlib import Path; Path('/reports/probe').write_text('ok')"`. This performs only a temporary-file write, with no database/bucket operation. Under the stated ownership/mode it fails with `PermissionError`. Backup's new directories have the same mismatch.

Provide a narrowly scoped output ownership/user strategy for the helper container, keeping snapshots read-only. Add a Linux boundary check for the image UID's access. The Docker stub runs as the host test user and cannot detect this mismatch.

Evidence: source-proven permission mismatch under the stated Linux conditions, **not a live container reproduction**. Docker/live recovery was unavailable; WSL enumeration returned access denied. No live restore failure is claimed.

### 3. [P2] Default script calls still depend on CWD for Compose environment interpolation

Locations: `infra/scripts/backup.ps1:20–26`; `infra/scripts/restore.ps1:38–46` (every Compose call omits `--env-file`). `infra/compose.yaml:13–24` requires interpolated settings; service `env_file: ../.env` does not supply Compose interpolation variables.

The absolute Compose path is corrected, but a checkout's root `.env` is not loaded when either script is invoked from another directory without exporting all settings. Compose fails before executing the operation, so the script-location path fix still lacks a usable default outside the repository root.

Independently reproduced with the installed Compose CLI, without contacting the daemon or reading real credentials: copied the production YAML into disposable `<temp>/infra/compose.yaml`, wrote synthetic required values to `<temp>/.env`, cleared those variables in the child environment, and invoked from `<temp>/caller`.

- `docker compose -f <temp>/infra/compose.yaml config --quiet`: **exit 1**, required `BOOTSTRAP_ADMIN_EMAIL` missing during interpolation (the first reported required variable can vary).
- Same command with `--env-file <temp>/.env`: **exit 0**.

Resolve the environment file independently of CWD, or expose a consistent explicit environment-file parameter and pass it to every Compose call. Add an outside-CWD regression using real Compose interpolation and synthetic values. The current shim checks only an absolute existing `-f` file and substitutes `services: {}`, which cannot catch this failure.

## Coverage and verification

Accepted the controller's fresh **60 focused backend + 17 frontend passing tests** as supplied evidence; these were not independently rerun or relabelled. Additional execution was limited to the necessary diagnostics above: two actual HTTP scenarios, the actual transpiled page callback, and two daemon-free Compose configuration probes. No broad suite/lint/build rerun.

- Product context copying, tone-only update/removal and retained revision precondition satisfy the bug fix. Three production-callback tests preserve nested/unknown keys and cached context.
- Backup/restore implement script-relative Compose paths, pre-service helper mounts, native exit checking, full checksum coverage, object size/hash checks, disposable targets, live-target refusal, read-only snapshots and report validation. Findings 2 and 3 remain. No live recovery verified.
- Batch paired positive preconditions and immediate replay work. Actual HTTP probes for unpaired revisions and zero scene revision returned **422 `REQUEST_VALIDATION_FAILED`**. Parent-before-scene locks align with scene edits and dispatcher; same-video batch requests serialize on the parent. No additional concrete locking defect found. PostgreSQL concurrency was not run; SQLite does not validate FOR UPDATE. Finding 1 remains.
- Trusted identity: ingress overwrites browser identity/credential headers from its socket peer; frontend allowlist/helper require the ingress credential and mint the separate internal credential; backend verifies it, validates/canonicalizes one IP and falls back to socket identity by default. Compose disables Uvicorn generic proxy headers. Frontend and actual HTTP login tests cover spoofed/unconfigured/malformed identity. No actionable defect found in the configured chain. Additional TLS/real-IP hops require the documented deployment configuration; no live production login verified.
- Linux test source has a quoted POSIX Docker launcher and explicitly skips when PowerShell is absent. This is not an independently verified Linux CI pass. Tests miss container UID permissions and actual Compose interpolation.
- Frontend Dockerfile requires `yarn.lock` and runs `yarn install --frozen-lockfile`; missing lockfile fails image build.

## Deferred requirements and excluded work

Later H3 qualification/workflow/profile requirements, future per-scene generation-config storage/UI/API, general generation snapshot expansion, assembly features, GPU benchmarks and launch qualification are separate phases, not Phase0 findings. Future `generation_config` fingerprint inclusion was inspected; implementing the future field is deferred. Separate `workflow_qualification.py`, admin/seed changes and tests were excluded. Worker report/NFR/lint finishing work was not modified or treated as a defect.

## Referenced source versions

Full HEAD SHA above pins repository history. SHA-256 hashes below pin inspected uncommitted/untracked Phase0 files and supporting files. Phase0 snapshot files were checked unchanged before report creation; supporting unchanged sources were hashed when inspected.

| File | SHA-256 |
| --- | --- |
| `tasks/archive/reports/production-phase0-brief.md` | `9569272bc9f8c34c9e933bdf2885f171ed44ab400b3b34d2f108682879c842f2` |
| `.env.example` | `c515c1398e06101e6fcc63b57feb6f38a694cf2338640e595e06d51b83c2fa2e` |
| `infra/compose.yaml` | `79fd1ec45a5d6149fb3af01898a0ea291ce9bde0065f72bda13048f572ecbf5f` |
| `infra/ingress/default.conf.template` | `d25a2c28e8288586892b78bb3fb7641d1c2825d8df4bde8ff9bcef66397bdf90` |
| `infra/scripts/backup.ps1` | `8f7628a3d5be940ff43a9aa745332479862689a624fa56d6736189e85cd4a479` |
| `infra/scripts/restore.ps1` | `c0acf5e94e429430db374b97431dcd3c25317523a4cba59e950c39b4d095f3aa` |
| `infra/scripts/backup_common.ps1` | `a328886640f0945f4f5a034a2badf57f83382aaed5577f32961ff4a935572049` |
| `infra/scripts/minio_snapshot.py` | `4be47702d43071e3e0d4c17677465ba0c755d7361c0e6467b40aba12506c3db3` |
| `backend/apps/api/app/api/generations.py` | `b63ff8c9fd48204b6795b9f491650b2dd5d6ded07d30c65081b411af7518201d` |
| `backend/apps/api/app/api/auth.py` | `83b8822bd82dc735191b7be1dc764adf4c1c161641cb8b506f27c8e485411e48` |
| `backend/apps/api/app/core/config.py` | `35dd22ae6974c73253e03a43bae257ae7edb5d162c7c7b434ee25f1687b88558` |
| `backend/apps/api/app/core/client_identity.py` | `e32fb25726161d6dde9f89f21541b9a4f0c2c32706b42f5f8eebf2c60ffe07d6` |
| `backend/apps/api/app/schemas/api.py` | `e7f9eeb213683bd81e6eff29466aba54fed50bfa8bb451f0153986d014057a03` |
| `frontend/app/products/[productId]/page.tsx` | `8c82f7a47a7a09fe8059a98e0398cabf4335baf8cc347150152f98c1c9ca0a67` |
| `frontend/app/videos/[videoId]/page.tsx` | `e54e56289ca8006703fbe9ee5e26d4163a87ea01e51b3756295d6af462de0285` |
| `frontend/app/api/[...path]/route.ts` | `89de9c52eaee6df449d05f364a07bd675360bc4c2279e71a58102578f19531cd` |
| `frontend/lib/server/client-identity.ts` | `1fe11ff7650a63fd359be34f8c43f351fab2b7a8bb6eead7672bbae691ad8ec7` |
| `frontend/lib/api/generations.ts` | `fa2467a9a4bb530467767ca47c8fed17cd913279a9186b8b858a12df8ab6667a` |
| `frontend/lib/api/types.ts` | `74fae7df9a783fbffcf505999faa42a5375bcd585d43beef063b4b6d58799312` |
| `frontend/Dockerfile` | `d7e207cea8d3f8f43563c8c86b51df40fcef6f10effa47547148a2318e26b81a` |
| `frontend/package.json` | `9039f3977f47dc44aafbeec05398551b7adda7b9eaf87be0ee937a25c534d7a6` |
| `backend/tests/integration/test_generate_all.py` | `9f2a8c2b7d2f0954c0e39b667c493180f8db8f0a5cff18fbca86013254dde4b2` |
| `backend/tests/integration/test_trusted_client_identity.py` | `0ab0fb97597bd094b8d696fb1c225df84a6d56e38754bd904509d4fccc7df535` |
| `backend/tests/unit/test_backup_restore_scripts.py` | `074a62c24dbcbed07084cd568197813f5396ed72e50f38329dc5c4b7313a3392` |
| `frontend/tests/auth-proxy.test.cjs` | `a47aa79a08d61404ed90c4a0aa424c485691a99e57d23a726114463574eb8217` |
| `frontend/tests/build-preserving-next-env.cjs` | `f788c42292546b4f2333c75c33ad01dfd7ea4c8fb00d09bd38ca0ab446382bc1` |
| `frontend/tests/generate-all.test.cjs` | `27b4f4ef3f04109ef6970a469816e1ba3f79478a17a95ade78587d017ca8b4e3` |
| `frontend/tests/load-typescript.cjs` | `08639fd8d6f2504f5169f67b561d686363f32d3e62e6b6b827695e2ce0cba59e` |
| `frontend/tests/product-edit.test.cjs` | `c5725dcc341b91663bca8ce0b63093fee1e112efd1e2e545a6afeac0ba7c334a` |
| `backend/Dockerfile` | `be134f0b7914e812446ec9b93f60ed0c091e698f84b7643ee276264907da5d52` |
| `backend/workers/dispatcher.py` | `684ca864b1f598d054f3f33f182a0b4c652fe9f24bd774e37b63b61605949da3` |
| `frontend/lib/hooks/use-video-events.ts` | `10fe200e332e23bff9cdc0a32a2909fed83da98b51110ce3cfa4b364248db968` |
| `frontend/lib/hooks/use-idempotent-action.ts` | `63abeb6830ce06dbf02cef8190146e84672c6dd666dbc8eb7c88c0aeea438fcb` |
| `backend/apps/api/app/services/idempotency.py` | `c6f753b8ddd8c3efda66e9717888c9c04bcbfc7d88d8dba1bcc92da286efdc6a` |
| `backend/apps/api/app/services/generation_service.py` | `2c8a1e8d8ca766b29552ddf820fcc2305774250ce39bc81ce7f63cf89dac15d0` |
| `backend/apps/api/app/main.py` | `057467cf893d04ccc653c942c4a03674ee319b058e4c8ea3a1ae5771c098f727` |
| `backend/apps/api/app/db/session.py` | `c9d9961ec05b74a2179b58d49c08cf75114d6cce09f0e23ec26cb1dff8036f7b` |
| `frontend/AGENTS.md` | `63f2c50380ed6303237cce215ce27af1d620d094c215e28d1b1538a3c070e3bb` |

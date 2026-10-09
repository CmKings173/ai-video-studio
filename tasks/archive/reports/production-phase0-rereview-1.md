# Independent Phase0 FIXROUND1 re-review: spec and quality

Result: **one actionable P2 finding remains; all-three closure is not yet a pass.** The original automatic-completion replay/unfinished-job duplication defect is fixed on the exercised paths. The Linux helper mount user strategy and explicit Compose environment handling address the other two original findings. A frontend regression still conflates manual selection/editor revisions with automatic completion.

Scope: original three findings in `tasks/archive/reports/production-phase0-review.md` and FIXROUND1 in `tasks/archive/reports/production-phase0-report.md`, against working-tree sources at HEAD `01b740d8be4d58d3bd15bc46dd9842afb48af8bb`. Read-only implementation review, no subagents, commits, implementation edits, full-suite rerun or build. Only this requested report is written. Temporary repros used disposable fixtures/in-memory code, login/CSRF and request transactions; no production services or credentials were used.

## Actionable finding

### [P2] Manual selection and editor revision changes leave the pending frontend batch permanently stale

Primary locations: `frontend/lib/hooks/use-generate-all-action.ts:15-21`, `frontend/lib/api/generations.ts:40-52`; error handling at `frontend/app/videos/[videoId]/page.tsx:312-316`. Supporting rejection: `backend/apps/api/app/services/batch_identity.py:37-49`.

The new pending hook replaces an action only when `candidate.editorInputs` changes. Those inputs omit **all** scene/video revisions and selected-generation IDs, rather than discounting only automatic completion. Consequently a manual output selection, revision-only save, or edit followed by restoration of the original field values preserves the old payload, old revision preconditions and old key. Backend correctly rejects these real editor transitions. The page resets only on success; its error callback only displays an error. Repeated clicks therefore keep returning `409 IDEMPOTENCY_KEY_REUSED` until a separately tracked field changes or the page remounts.

This is a regression in the original Phase0 requirement that real semantic/editor changes rotate the action, and contradicts FIXROUND1's claim that a true editor-input change rotates it. It does not reopen the automatic-completion duplicate-job defect.

Independently reproduced with actual HTTP endpoints and production dispatcher:

1. Seed two enabled unselected scenes with real request fixture. Login and POST the original explicit batch: **202**, video revision 1, both scene revisions 1.
2. Finish the first ORIGINAL through `Dispatcher._finish` with a READY asset. GET video detail: video revision 2, first scene revision 2, original automatically selected. Same-body/key retry: **202**, byte-equivalent parsed response to the original.
3. POST a variation through the real API using `parent_generation_id`, complete it through `Dispatcher._finish`, then POST `/scenes/{id}/select-generation` with the actual `If-Match` and READY variation output: **200**. GET video detail: video revision 3, first scene revision 3, selected output is now the variation. Prompt/spec/config/enabled/order values are unchanged.
4. Original-body/key retry: **409 IDEMPOTENCY_KEY_REUSED**. Repeat: **409**. Generation count is exactly **3** (two initial batch jobs plus the deliberately requested variation); rejected retries create none.
5. Feed those exact before/automatic/manual HTTP DTOs to the actual transpiled page callback and hook using the existing boundary harness. Automatic refresh correctly retains the original request. Manual selection **also retains the exact original key and payload**, including video revision 1 and both scene IDs. Invoking the production `onError` callback and clicking again still retains them.
6. A further revision-only change also retains the stale payload/key. Production `onSuccess` does reset the action, as expected.
7. Separately render the page's actual JSX with query/mutation boundaries faked, find and invoke its Generate All button `onClick`: it remains available after partial/full completion, sends the pinned request in both cases, and also sends the stale pinned request after manual selection. This is component/callback execution, not a live-browser claim.

Requested correction: retain the pending payload/key for proven automatic completion, but retire/rotate it for actual manual selection or editor revision changes. A definitive backend rejection can be used to retire a stale action without rotating on an ambiguous transport failure. Add coverage using a real manual selection plus refreshed DTOs, and a revision-only editor operation, alongside the existing completion replay cases. Do not simply reintroduce live-eligibility rotation, which would restore the original duplicate-job defect.

## Disposition of the original findings

| Original finding | Re-review disposition | Evidence / limit |
| --- | --- | --- |
| P1 automatic output selection breaks replay and duplicates unfinished jobs | **Automatic-completion paths closed; P2 frontend editor-transition regression above remains** | Explicit and implicit HTTP replay after real partial/full dispatcher completion returns the original saved response and exactly two generations. Actual page hook/button keeps the original payload/key through automatic partial/full refresh. |
| P1 Linux UID 10001 cannot write caller-owned 0755 output mounts | **Implementation finding closed; native Linux/container execution NOT_RUN** | Both MinIO helper runs use checked caller UID:GID on POSIX and scoped `--user 0:0` on Windows, before the `api` service. Output directories are made by that caller; production image/service user remains unchanged. Snapshot and helper-source mounts remain read-only. |
| P2 absolute Compose `-f` alone still depends on CWD for interpolation | **Closed** | Both wrappers default `EnvironmentFile` from script location and reuse absolute validated `--env-file`/`-f` arguments for every Compose call. Independent daemon-free real Compose negative/positive check from separate CWD passes. |

Backend `batch_identity.py` matches the dispatcher's automatic-selection conditions: previously null selection, enabled scene, exactly one scene revision increment, a selected COMPLETED ORIGINAL with matching recorded input scene revision, and a matching video increment. It compares the complete stored/current editor baseline after those discounts. The real manual VARIATION selection above and the existing revision-only/prompt/disable/add/config cases are rejected. No additional concrete backend replay or lock-order defect was established in this scope.

The private `_batch` baseline survives in the stored response while the public DTO omits it. Replay uses the original request hash and saved inputs; it returns the stored generation statuses rather than synthesizing a response from current progress. The tested response equality after actual completion confirms that behavior. Paired/positive revision preconditions and original legacy/explicit batch compatibility continue to pass.

The Linux-specific test remains a **host Docker shim plus POSIX mode/ownership check**, not a Docker container permission test. It was explicitly skipped on Windows. Source inspection establishes why matching the caller's UID:GID fixes the stated ordinary Linux ownership/mode mismatch; it does not establish a live Linux deployment or Docker permission pass. No world-write/chown/ACL changes are required by the fix, and none were performed here.

## Independent narrow verification

| Working directory / command | Observed result |
| --- | --- |
| backend: `$env:PYTHONDONTWRITEBYTECODE = '1'; .\.venv\Scripts\python.exe -m pytest tests/integration/test_batch_completion_replay.py tests/integration/test_generate_all.py -q -p no:cacheprovider` | **17 passed in 5.27s** |
| backend: same bytecode setting; `.\.venv\Scripts\python.exe -m pytest tests/unit/test_backup_restore_scripts.py -k 'every_compose_call or real_compose_interpolation or linux_helper' -q -p no:cacheprovider` | **3 passed, 1 skipped, 19 deselected in 8.44s**; skip explicitly requires POSIX host. Both wrappers' every-call environment/helper-user tests and real Compose interpolation ran. |
| frontend: command-local TEMP/TMP set to existing `frontend/.tmp`; `yarn node --test tests/generate-all.test.cjs` | **9 passed**, Yarn duration **1.90s** |
| In-memory backend HTTP manual-selection diagnostic via stdin Python and existing fixtures | **Exit 0**; 202 initial, 202 identical automatic replay, 200 manual selection, repeated 409; count 3 as described above |
| In-memory Node production page/hook replay with actual HTTP DTOs | **Exit 0**; asserts automatic pinning, unintended manual/revision-only pinning, no reset on error, and reset on success |
| In-memory Node actual JSX Generate All button diagnostic | **Exit 0**; partial/full automatic clicks preserve request/key and remain available; manual selection click wrongly preserves them |
| `git diff --check -- backend/apps/api/app/api/generations.py frontend/lib/api/generations.ts 'frontend/app/videos/[videoId]/page.tsx' infra/scripts/backup.ps1 infra/scripts/restore.ps1` | **Exit 0**; only LF/CRLF notices |

Diagnostic correction: the first manual-selection probe used an incorrect variation field `source_generation_id` and stopped at **422 REQUEST_VALIDATION_FAILED**. The corrected reproduction above uses the actual `parent_generation_id` contract and completes successfully; the initial attempt is not counted as defect evidence.

Supplied evidence, **not independently rerun or relabelled**:

- Implementer: 17 batch/backend passing; 22 script passing plus one Linux-only skip; 18 frontend passing; lint/typecheck, Ruff and OpenAPI checks passing.
- Controller's fresh full backend run: **250 passed, 11 skipped in 91.35s**. Ten skips require the PostgreSQL test database; one is the Linux UID boundary on Windows.
- Controller's post-round Next production build: **passed in 38.33s** with wrapper-preserved `next-env.d.ts` hash unchanged.

No additional broad suite/build was run. PostgreSQL lock/concurrency execution and live Linux helper writes, Docker backup/restore and deployment remain **NOT_RUN** by this reviewer.

## Quality and scope

No separate actionable quality/standards issue found in the reviewed fixes. Helpers keep the mounted-user override confined to the disposable MinIO runs; native failures stop before success; explicit environment resolution is centralized; saved replay precedes current eligibility validation. The frontend distinction between automatic and manual/editor transitions is the single concrete issue above.

Excluded: separate delivery presets/geometry, media probe helpers, controller qualification work, `.artifacts/final-ui-media-closeout/local-browser-fixture.py`, future generation contracts/config storage, snapshot expansion, assembly, ingress/product issues already outside these three findings, and unrelated controller changes. No later-generation contract requirements were introduced.

## Stable source hashes before and after

**39 tracked review inputs/support files had identical SHA-256 hashes before inspection/execution and after the review**, verified at **2026-10-05 14:36:15 UTC**, before writing this report. Additional supporting files were hashed when brought into scope and included in the final comparison. The controller also stated there were no Phase0 mutations during this review. The same hash in the table is both the before and after value; the report itself is the only requested new artifact.

| File | Before SHA-256 | After |
| --- | --- | --- |
| `tasks/archive/reports/production-phase0-review.md` | `63fa44f26d93b9f6c9de8d9d124312839256b3d0ff5d7bc79f83e64449c44134` | same |
| `tasks/archive/reports/production-phase0-report.md` | `eb2f947b1eb4d3cc296ca05d47d9ad8c689d575b99f081429685e340e1a06ef2` | same |
| `tasks/archive/reports/production-phase0-brief.md` | `9569272bc9f8c34c9e933bdf2885f171ed44ab400b3b34d2f108682879c842f2` | same |
| `backend/apps/api/app/api/generations.py` | `e2b6672a99c9e822963b0951ce57fc8748403e2220409ba8c035e6aa6835a7c7` | same |
| `backend/apps/api/app/services/batch_identity.py` | `1b17d76440fb5367cfc7283684983fefbab2642b429f756fa5d9ce2326792572` | same |
| `backend/apps/api/app/schemas/api.py` | `e7f9eeb213683bd81e6eff29466aba54fed50bfa8bb451f0153986d014057a03` | same |
| `backend/tests/integration/test_generate_all.py` | `9f2a8c2b7d2f0954c0e39b667c493180f8db8f0a5cff18fbca86013254dde4b2` | same |
| `backend/tests/integration/test_batch_completion_replay.py` | `c1b4d1b128877a15289b7ecfa22bf6f051d04bc60b1cc991501e8cfad781fc87` | same |
| `backend/tests/unit/test_backup_restore_scripts.py` | `6ed4858ddb0980168b899be9043779f10b46a34d77cda8c0986714a6efa11493` | same |
| `backend/tests/integration/test_generation_preparation.py` | `57c4da271350ebb14503f0b56e53b8e680e698d5aa21f01cc84c506cbc7980c9` | same |
| `backend/tests/conftest.py` | `bc6df13a01e3271cfb0c3e488adf6040dd02b0d71998879f86a40414a7dcd2d3` | same |
| `backend/workers/dispatcher.py` | `684ca864b1f598d054f3f33f182a0b4c652fe9f24bd774e37b63b61605949da3` | same |
| `backend/Dockerfile` | `be134f0b7914e812446ec9b93f60ed0c091e698f84b7643ee276264907da5d52` | same |
| `backend/apps/api/app/api/scenes.py` | `dbfe04ba62bfaeeb9c06d15f1b5620eb3a7d080849ffb7eda735f76333496091` | same |
| `backend/apps/api/app/api/videos.py` | `2f3086b65576d8f0144f82e109c5ef4034da500a7e068784e4de1013797b6e5a` | same |
| `backend/apps/api/app/services/idempotency.py` | `c6f753b8ddd8c3efda66e9717888c9c04bcbfc7d88d8dba1bcc92da286efdc6a` | same |
| `frontend/app/videos/[videoId]/page.tsx` | `821a335bb454a18842c91f752dcc7b73141da88aa46c5817431bbfae2ddf9e49` | same |
| `frontend/lib/hooks/use-generate-all-action.ts` | `8f96da0c3b138e59d3495eb9260813c6ca7aea9adf305ea8279dd8e75c1f520c` | same |
| `frontend/lib/api/generations.ts` | `6f4afb943a1bb47b3dd8ebd0f705115c583fc15f12b3d9516cce9c9f236c3cac` | same |
| `frontend/lib/api/types.ts` | `74fae7df9a783fbffcf505999faa42a5375bcd585d43beef063b4b6d58799312` | same |
| `frontend/AGENTS.md` | `63f2c50380ed6303237cce215ce27af1d620d094c215e28d1b1538a3c070e3bb` | same |
| `frontend/package.json` | `9039f3977f47dc44aafbeec05398551b7adda7b9eaf87be0ee937a25c534d7a6` | same |
| `infra/compose.yaml` | `79fd1ec45a5d6149fb3af01898a0ea291ce9bde0065f72bda13048f572ecbf5f` | same |
| `infra/scripts/backup.ps1` | `f466a90ea37d7bca16e2f86c961609686f85f4288e163aa8f9e52a5e824a7866` | same |
| `infra/scripts/restore.ps1` | `93e00c8810d4be6e3cda02383d7747f43359516a725c5c4b66ea0fa5eb2c5ee5` | same |
| `infra/scripts/backup_common.ps1` | `33b5e6123f4e0478a4054100c5865d5f2c153f02f493cecdef684fe6411d94ce` | same |
| `infra/scripts/minio_snapshot.py` | `4be47702d43071e3e0d4c17677465ba0c755d7361c0e6467b40aba12506c3db3` | same |
| `.env.example` | `c515c1398e06101e6fcc63b57feb6f38a694cf2338640e595e06d51b83c2fa2e` | same |
| `frontend/tests/product-edit.test.cjs` | `c5725dcc341b91663bca8ce0b63093fee1e112efd1e2e545a6afeac0ba7c334a` | same |
| `frontend/tests/generate-all.test.cjs` | `d73dbffa21e798309b0aeac1d50866adc36f716565611b6f270bf6c048a5aa96` | same |
| `frontend/tests/auth-proxy.test.cjs` | `a47aa79a08d61404ed90c4a0aa424c485691a99e57d23a726114463574eb8217` | same |
| `frontend/tests/load-typescript.cjs` | `08639fd8d6f2504f5169f67b561d686363f32d3e62e6b6b827695e2ce0cba59e` | same |
| `frontend/tests/build-preserving-next-env.cjs` | `f788c42292546b4f2333c75c33ad01dfd7ea4c8fb00d09bd38ca0ab446382bc1` | same |
| `frontend/lib/hooks/use-idempotent-action.ts` | `63abeb6830ce06dbf02cef8190146e84672c6dd666dbc8eb7c88c0aeea438fcb` | same |
| `backend/apps/api/app/db/models.py` | `ead9d9525a0174a3bb232663678ffa4ec3184dc1e14fb5b41357f6942dd2924f` | same |
| `backend/apps/api/app/api/deps.py` | `39b355f706d2cf1b0b87ee74d765cbcb1b51958c4c8bb81c6633d4cd1da0fcbd` | same |
| `backend/apps/api/app/services/generation_service.py` | `2c8a1e8d8ca766b29552ddf820fcc2305774250ce39bc81ce7f63cf89dac15d0` | same |
| `backend/tests/integration/test_authz.py` | `fc67bfafdcaf713809db6eb1a8f7a598bd90e17c22e3216aef4eb51b97ebdf06` | same |
| `frontend/components/ui/button.tsx` | `d19c3d3d59078ba5df581e8e84eea76830296d1ddc88d62518c128631364331e` | same |

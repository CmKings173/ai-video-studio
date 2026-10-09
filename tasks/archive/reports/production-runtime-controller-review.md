# Independent runtime controller review

Reviewed 2026-10-06 in `D:/project/ai-video-studio`, branch `codex/production-safety-fixes`, HEAD `01b740d8be4d58d3bd15bc46dd9842afb48af8bb`. Basis: the controller brief, controller report, supplied `production-runtime-controller-review.diff`, and production-generation plan I/J/S (lines 2305–2355 and 2516–2559), with phases 1/5 and the benchmark case requirements at lines 1467–1522.

**Spec verdict: REQUEST CHANGES.** The orchestration records distinguish static validation, submission, execution, media verification, and release qualification, but three submission/reconciliation paths violate the uncertainty constraint. Candidate step evidence and reference case labels can also misdescribe what ran.

**Quality verdict: REQUEST CHANGES.** Required correctness fixes are listed below. Existing tests cover useful happy paths and selected failures, but miss timeout during submission, unresolved later cells on resume, CLI continuation, missing step bindings, and incomplete reference categories. No separate merge-blocking readability, security, or performance finding was established in this slice.

Scope was exactly seven implementation/test files: `backend/apps/api/scripts/h3_probe.py`, `backend/apps/api/management/benchmark_h3.py`, only the added `ComfyAdapter.object_info()` method in `backend/apps/api/app/integrations/comfy_adapter.py`, `backend/tests/unit/test_h3_probe_evidence.py`, `backend/tests/unit/test_h3_benchmark.py`, `backend/tests/integration/test_h3_probe.py`, and `backend/tests/integration/test_five_editor_login.py`. Existing adapter submission semantics were read only to trace the scoped probe's timeout interaction. Backend core, qualification contracts, and the media helper were not independently reviewed here. No implementation edits, subagents, commits, broad test reruns, or GPU/runtime requests were made.

## Required findings

### 1. [P1] Preserve uncertain submission when the probe's outer deadline cancels submit

Location: [h3_probe.py:387](D:/project/ai-video-studio/backend/apps/api/scripts/h3_probe.py:387), with error classification at line 446 and deadline cancellation at line 264.

**Trigger:** Comfy accepts the POST, but the probe's remaining deadline expires before `submit()` returns its prompt ID. `asyncio.wait_for` cancels the operation; the existing adapter translates transport errors, but this cancellation is not an HTTP transport error. The probe consequently returns `stage=STATIC_VALIDATED`, `error_code=PROBE_TIMEOUT`, and no prompt ID, despite retaining a client correlation. The benchmark only halts for `SUBMITTED` or `COMFY_SUBMISSION_UNCERTAIN`, so it overwrites its protective pending journal and proceeds. Resume also treats this row as retryable. An idle queue later does not reconcile a prompt that may already have completed.

**Executed bounded proof:** used the scoped probe test's `entry()`/`Adapter`, replacing only `submit` with a fake that records admission and then sleeps one second; ran the real probe with `timeout_seconds=0.2`. Output:

```text
SUBMIT_TIMEOUT {"submit_calls": 1, "stage": "STATIC_VALIDATED", "error": "PROBE_TIMEOUT", "prompt_id": null, "client_id": true}
TIMEOUT_MATRIX {"probe_calls": [5, 10, 10, 10, 10], "halt_reason": null, "first_stage": "STATIC_VALIDATED", "first_error": "PROBE_TIMEOUT"}
```

The second check fed that actual returned result as the first outcome into the real `run_matrix` and used synthetic successful outcomes thereafter. It proves continuation policy, not server admission or GPU concurrency.

**Required remedy:** track entry into the submit boundary separately from receipt of the prompt ID. A timeout after submission starts must preserve an unresolved stage and correlation, and be classified as uncertain unless definite rejection is established. Keep definite pre-submit failures distinct. Add a boundary regression proving both matrix continuation and resume remain blocked after this timeout.

### 2. [P1] Scan the entire saved journal before submitting anything on resume

Location: [benchmark_h3.py:187](D:/project/ai-video-studio/backend/apps/api/management/benchmark_h3.py:187), especially the per-cell check at lines 199–206.

**Trigger:** an earlier cell has a terminal failure and a later cell has an unresolved submission. Resume reaches and retries the earlier cell before checking the later cell. The unresolved-row guard is local to the current cell; even its completed-cell check precedes reconciliation. This contradicts the report's promise that uncertain/interrupted rows halt the entire resume before new submissions.

**Executed bounded proof:** two duration cells, 5 and 10 seconds. An injected probe first returned `EXECUTED` failure for 5 and `SUBMITTED/COMFY_SUBMISSION_UNCERTAIN` for 10. Resuming the same saved plan with synthetic success for 5 made five new probe calls before detecting the unresolved 10-second row:

```text
GLOBAL_RESUME {"initial_calls": [5, 10], "calls_before_reconcile": [5, 5, 5, 5, 5], "halt_reason": "Reconcile uncertain prompt before resume"}
```

**Required remedy:** validate the complete journal for unresolved submissions immediately after loading/verifying the plan, before completed-cell skipping or any probe call. Keep the row correlation available for reconciliation. Add a two-cell regression with an earlier incomplete terminal cell and a later unresolved row, requiring zero resume calls.

### 3. [P1] Stop the probe CLI after an unresolved submission

Location: [h3_probe.py:526](D:/project/ai-video-studio/backend/apps/api/scripts/h3_probe.py:526).

**Trigger:** `--all --execute`, or a mode selection containing several manifests, receives `SubmissionUncertain` or a post-submit timeout/history failure. The list comprehension unconditionally invokes the next manifest. Unlike the benchmark, this entry point has no unresolved-result guard; returning a final exit code of 1 does not prevent the subsequent submission.

**Executed bounded proof:** ran real `_main()` with two supplied synthetic manifests, patched settings/adapter/report boundaries, and a probe method returning `STATIC_VALIDATED/COMFY_SUBMISSION_UNCERTAIN` with correlation. Both manifests were called:

```text
PROBE_CLI_UNCERTAIN {"calls": ["SYNTHETIC_TEST_ONLY", "SECOND_FIXTURE"], "exit_status": 1}
```

**Required remedy:** replace unconditional collection with a loop that persists the partial result and stops on any unresolved submission. Use a shared explicit unresolved-result predicate for the CLI and matrix, including finding 1's corrected timeout state. Add a CLI regression requiring only the first manifest to be called and its correlation to appear in the partial report.

### 4. [P2] Reject step declarations/overrides that are not bound to the executed graph

Location: [h3_probe.py:337](D:/project/ai-video-studio/backend/apps/api/scripts/h3_probe.py:337), filtering at lines 345–347, and candidate evidence at lines 307–309.

**Trigger:** a candidate graph fixes its sampler at 20 steps but omits a symbolic `STEPS` binding. `steps=25`, or a mismatched profile declaration, changes recorded `candidate_steps` and the profile hash while the patch silently discards the setting. Successful output dimension/decode checks cannot prove the actual sampler used that step count.

**Executed bounded proof:** removed only `STEPS` from the existing synthetic probe fixture's slot map, leaving its graph's `steps=20`; ran real `H3Probe` with the fixture adapter, `steps=25`, and the existing synthetic decoded-output measurement boundary:

```text
STEPS_WITHOUT_BINDING {"stage": "VERIFIED", "accepted": true, "reported_steps": 25, "graph_steps": 20}
```

**Required remedy:** require a verified binding for mutable step settings, or prove that a fixed graph setting equals the declared candidate count. Reject unsupported overrides before submit; derive candidate evidence from the graph that will run. Extend the existing step-hash test to cover a missing binding and a fixed/declaration mismatch. This fix belongs at the scoped orchestration boundary; no backend-core implementation change is prescribed here.

### 5. [P2] Require each reference benchmark case's named media categories

Location: [benchmark_h3.py:311](D:/project/ai-video-studio/backend/apps/api/management/benchmark_h3.py:311), especially lines 313–315.

**Trigger:** the operator supplies images but no audio or video. `_cases` still builds `ref_audio_image` and `ref_mixed` with just the images. The common `r2v` validator legitimately accepts image-only references, so these rows can become verified under false case labels. Their timings do not measure audio-plus-image or mixed input overhead.

**Executed bounded proof:** ran real `run_matrix`/`H3Probe` for those two generated cases, one ratio/duration/profile, excluded warmups and three repeats, using the scoped fixture extended with one image slot. Input inspection/upload/output measurements were synthetic; the media path was an existing source file, so no media was created. Both case rows contained only `REFERENCE_IMAGE_1`:

```text
CATEGORY_MATRIX {"submit_calls": 9, "complete": true, "warm_verified_count": 6, "cases": [{"case": "ref_audio_image", "stage": "VERIFIED", "reference_slots": ["REFERENCE_IMAGE_1"]}, {"case": "ref_mixed", "stage": "VERIFIED", "reference_slots": ["REFERENCE_IMAGE_1"]}]}
```

**Required remedy:** make required categories part of the case contract and check them before submission. Audio-plus-image needs both categories; mixed needs the specified image/video/audio mix. Record missing category cases as `NOT_RUN` with an actionable reason, and do not substitute another workload. Add category-absence regressions requiring zero probe calls for those cells.

## Bounded verification and positive evidence

The three reproduction commands were PowerShell here-strings piped to `./.venv/Scripts/python.exe -B -` from `D:/project/ai-video-studio/backend`; all exited 0 and produced the outputs above. They loaded only the two scoped test fixture modules with `runpy.run_path`. Report writes were mocked in memory, resume reads were supplied from that memory, and CLI report output was mocked. No test suite was rerun, no temporary report/media files were created, and no HTTP/GPU request was sent. Synthetic `VERIFIED` results above demonstrate orchestration behavior only; they are not executed target PASS evidence.

The controller report supplies **27 passed in 4.81s**, Ruff check PASS, and seven-file format PASS. Those results are author-supplied, not independently rerun here. Package contents matched the current full-file hunks for both orchestration modules and all three new tests; the adapter addition and offline mode-test hunk were inspected directly.

Positive findings: actual dimensions remain absent until output measurement; the decoded video/audio count gate rejects declaration-only evidence; uncertain adapter exceptions retain correlation; benchmark journal writes precede probe invocation; warmup is excluded and at least three warm repeats are required; missing profiles remain `NOT_RUN`; one adapter/client is reused by the benchmark CLI; absent trustworthy queue/execution timing stays unknown; and media verification never sets `qualified` or enables workflows. The five-client ASGI tests cover session isolation and shared-identity throttling, with deployed browser/ingress UAT explicitly separate. The new read-only `object_info()` method fits the existing adapter boundary.

Architecture follow-up for the required fixes: centralize the unresolved-submission state predicate so the CLI, continuation, and resume cannot disagree. Keep candidate-setting bindings and benchmark media category contracts explicit. The reported idle-queue race remains a deployment-isolation limitation, not evidence of safe concurrent runtime clients.

The seven scoped files retained these SHA-256 hashes from the initial snapshot through the reproduction checks:

```text
h3_probe.py                  dfa85f691256901100cf55e590434d12c9432a0187c9742777f7a6e6aed24e4b
benchmark_h3.py              9626c9670e97e63dd4fbd4b08adb4379f4b1d3728c62c0ced4d9ad8a7d6d94a9
comfy_adapter.py             7e5b10083f97d89c971d3418cb8d4ca33a99f6f0d422dcd40889f20e0487dfc5
test_h3_benchmark.py         4642a30b4da831f3cd188951086fb5842774774c25b9e5cdb7f1e14c9826402c
test_h3_probe_evidence.py    b81618f58bef2bc26e266d4bb220852ec77fa93677795997be608e22b4b7ed94
test_h3_probe.py             e6c1f9497188e585ba75cccdf92c307c30ea26fc7db88ab228eb6257e4a640c0
test_five_editor_login.py    8a02231e9a8e458466464e6bf0e0fcfbe6dcbd1e0de4ec0ab2eecef62cbd33a4
```

## Separate pending decoder slice and external gates

The two findings in [production-probe-media-review.md](D:/project/ai-video-studio/tasks/archive/reports/production-probe-media-review.md) remain separately assigned and pending: reject undecodable payloads, and prevent container/audio duration from masking missing video. Neither was changed or re-reviewed here. The controller's decoded-count requirement currently prevents actual helper output from reaching `VERIFIED`; this is a truthful dependency block, not a new decoder finding or evidence that those fixes passed.

Actual target schemas/exports, model/node/runtime hashes, GPU runs, timings, residency, creative/profile qualification, Docker validation, and deployed ingress/UAT remain **unknown or NOT_RUN**. This scoped review does not establish completion of phases 1/5 or production readiness. After the five controller findings and separate helper findings are fixed, bounded regressions and actual downloaded-media integration evidence are still required before target qualification.

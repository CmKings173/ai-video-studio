# Runtime tooling fix slice - 2026-10-06

**All five controller findings and both media findings addressed with scoped
regressions and actual portable FFmpeg CPU verification. External real GPU,
operator ComfyUI and Docker remain NOT_RUN. No release qualification or automatic
promotion is claimed.** Independent re-review is separate from these author-run
checks. The sequential core fix worker owns subsequent full-backend verification.

## Authority and scope

Read `tasks/archive/reports/production-runtime-brief.md` first; its Oct 6 handoff was authoritative.
Extended the existing controller implementation and consumed the final
`tasks/archive/reports/production-generation-contracts-report.md` and shared read-only contracts.
Read both independent runtime/media reviews and the controller checkpoint. The
controller's historical 27-test result was not treated as review approval.

Implementation writes are exactly:

- `backend/apps/api/scripts/h3_probe.py`
- `backend/apps/api/management/benchmark_h3.py`
- `backend/apps/api/scripts/probe_media.py`
- `backend/tests/unit/test_h3_probe_evidence.py`
- `backend/tests/unit/test_h3_benchmark.py`
- `backend/tests/unit/test_probe_media.py`
- `backend/tests/integration/test_probe_media.py`
- `docs/h3-poc/runtime-tooling.md`
- `docs/h3-poc/runtime-cpu-verification-20261006.json`
- This report.

No generation core, adapter, migration, frontend or delivery source edits; no
commits, pushes, reset, clean, subagents, installs, model downloads or target-runtime
execution. Existing dirty controller/core/frontend/delivery work was preserved.
CPU tests use the existing backend `.venv` and existing portable tool binaries.
Generated media is temporary; historical target/static results remain unchanged.

## Exact controller finding closures

| Finding | Final behavior | Regression proof |
| --- | --- | --- |
| 1 P1: outer submit deadline loses uncertain admission | Mark entry into submit inside the bounded operation as SUBMISSION_PENDING, independently of receiving the prompt ID. Any ambiguous failure there becomes COMFY_SUBMISSION_UNCERTAIN, retaining client ID, submitted graph hash and original cause. Definite COMFY_REJECTED returns STATIC_VALIDATED; a pre-submit timeout never claims admission. | `test_submit_deadline_preserves_unresolved_correlation`: real probe with fake adapter recording admission then sleeping, 50ms deadline, one submit, no prompt ID/execution, original PROBE_TIMEOUT cause. `test_pre_submit_deadline_does_not_claim_uncertain_admission`: zero submits. `test_actual_probe_submit_timeout_blocks_continuation_and_resume`: real H3Probe inside real matrix, two duration cells, one total submit across initial run and resume. |
| 2 P1: resume submits before detecting a later unresolved cell | Validate plan identity, then scan the entire saved journal with the shared predicate before constructing a probe, skipping a completed cell or retrying any failed cell. Correlated rows remain unchanged; summary remains incomplete. | `test_resume_scans_later_unresolved_cell_before_retrying_earlier_failure`: first cell terminal EXECUTED failure, later cell unresolved; zero resume probe calls and unchanged rows. Existing interrupted-journal test still passes, including a report without a completed summary. |
| 3 P1: probe CLI continues after unresolved submission | Sequential loop; correlation is persisted before awaiting each run, and partial JSON is atomically persisted after each result. Stop on the shared unresolved predicate. Interrupted calls leave the pending correlation. Existing output evidence cannot be overwritten by another invocation. | `test_cli_stops_and_persists_partial_correlation` tests ambiguous legacy result, accepted-prompt/history error and cancellation. Two manifests supplied, only the first called, one correlated row saved; accepted history error retains prompt ID. |
| 4 P2: candidate step count need not match executed graph | Require STEPS binding on collected output ancestry, or an explicit fixed `profile.setting_bindings.steps` on that ancestry with an equal graph literal. Reject missing/dead bindings and mismatched fixed declarations before submission. Derive recorded candidate count from patched graph and record binding/mutability. | `test_unbound_steps_are_rejected_before_submission` tests profile declaration and 25-step override with missing slot; zero submits. `test_step_binding_must_reach_collected_output` rejects a disconnected node. `test_explicit_fixed_steps_must_match_graph` accepts explicit fixed 20 and rejects declared 25. Existing step override/hash regression remains passing. |
| 5 P2: named reference workloads omit their media categories | Required categories are explicit and plan-hash-bound, including frame cases. Audio/image requires both categories; mixed requires image/video/audio. Missing inputs yield NOT_RUN / BENCHMARK_MEDIA_CATEGORY_MISSING with actionable category names and no probe call. | `test_named_reference_case_cannot_substitute_missing_category`: five category-absence permutations across audio/image and mixed; all zero probe calls and incomplete summaries. |

`unresolved_submission` is the single shared guard for live ProbeResult and saved
dict rows in the CLI, matrix continuation and global resume. Seven predicate cases
cover pending/submitted, legacy uncertain, definite rejection, pre-submit timeout,
terminal media failure and VERIFIED results. No automatic retry/reconciliation was
added and no existing adapter reconciliation semantics were changed.

## Exact media finding closures

| Finding | Final behavior | Actual CPU proof |
| --- | --- | --- |
| P1: FFprobe declarations accept unreadable mdat | After declaration validation, strictly decode both selected streams through FFmpeg rawvideo/PCM framehash. Require positive real decoded video/audio output and compare decoded count/span. Confirmed decoder input errors raise ProbeOutputError; unavailable/failed tooling remains MediaInspectionError. | `test_zeroed_mdat_is_rejected_despite_valid_declarations` preserves moov/headers and zeroes mdat. FFprobe still reports 24 video frames and both streams; helper rejects actual decode. `test_corrupt_audio_is_rejected_even_when_video_decodes` corrupts only audio packet payloads, proves real video-only decode succeeds, then rejects AV verification. |
| P2: longer audio/container masks short video with no nb_frames | Keep absent declared `frames` missing. Require decoded video count equal expected count and decoded video span equal frames/FPS within 1ms. Check audio span/start alignment and container padding independently within one frame plus 1ms. Timestamp passthrough prevents filling gaps with synthesized CFR frames. | `test_short_video_long_audio_cannot_hide_missing_decoded_frames`: actual 12-frame/0.5s MKV plus 1s PCM; container 1s and declared frames missing; rejected for decoded video mismatch against 24 frames. `test_full_mkv_keeps_missing_declared_count_and_distinct_decoded_count`: valid MKV accepted with frames=null and decoded count=24. Real timestamp-gap test remains rejected even when a test boundary substitutes matching container duration, proving decoded span is independent. Long-audio case is separately rejected. |

No missing declared video/audio span or frame count is fabricated. The helper
adds `decoded_video_frames`, `decoded_audio_frames`,
`decoded_video_duration_seconds`, `decoded_audio_duration_seconds` and both
`decoded_*_start_seconds`. Audio count denotes decoded PCM output frames/packets,
not audio samples. The final inspection method is
`ffprobe_declarations+ffmpeg_full_av_decode`; exact downloaded-byte SHA256 and size
remain bound to the measured output. First video/audio streams are selected
explicitly with `-map 0:v:0 -map 0:a:0`.

Decoder limits: 512MiB input, 30s decoder deadline, 2MiB stdout, 64KiB stderr;
strict `-xerror -err_detect explode`, CPU `-hwaccel none`, preserved timestamps,
passthrough video timing and single-thread video decoding/encoding. Probe deadline
may cancel decoding earlier. Timeout/output overflow/invalid tool evidence or
startup errors are inspection failures; none can reach VERIFIED. Actual subprocess
regressions cover timeout, cancellation, output overflow and nonzero configuration
failure, proving child processes are reaped. A regression exposed Windows
backpressure during cleanup: fixed by terminating and draining remaining pipes
rather than waiting on a blocked stdout pipe. No orphan decoder was accepted as
successful output.

FFmpeg defaults to the sibling of configured FFprobe; helper also offers optional
explicit `ffmpeg_binary` and `decode_timeout_seconds`, retaining all existing
required call arguments. No core media inspector behavior was changed.

## CPU evidence and integration boundary

Observed at **2026-10-06T02:06:06.031225+00:00**. Exact evidence is saved in
`docs/h3-poc/runtime-cpu-verification-20261006.json`, including all downloaded-byte
hashes, sizes, declarations, decoded counts and spans. Binaries were already installed:

| Binary | Version / SHA256 |
| --- | --- |
| `workspace/tools/ffmpeg-9.0.2/ffmpeg-9.0.2-essentials_build/bin/ffmpeg.exe` | 9.0.2 essentials build; `3256173f3f8bffd7df12227c68adf68025edb1832273a9530688a7bb1ed8edec` |
| `workspace/tools/ffmpeg-9.0.2/ffmpeg-9.0.2-essentials_build/bin/ffprobe.exe` | 9.0.2 essentials build; `f0d36ecbbdd3bcfac3efa078c96c7271c2e68b3810595552ac3b7f17e9a65c52` |

| Actual synthetic media | Decoded video / PCM audio count | Decoded video / audio span | Outcome |
| --- | --- | --- | --- |
| 256x256 H.264/AAC MP4, 24FPS | 24 / 47 | 1s / 1s | Accepted; 12,241 bytes, exact hash in JSON |
| 256x256 H.264/PCM MKV, missing declared count | 24 / 47 | 1s / 1s | Accepted; declared frames and stream durations remain null |
| Graph-length MP4, 124 frames / 24FPS | 124 / 243 | 5.1666666667s / 5.1666666667s | Accepted; 52,788 bytes, SHA256 `7ecaca1b5bdf23c7364dc7f5decde3712551af24a9a05c49f679111b26a57431` |
| Zeroed MP4 mdat, intact declarations | No accepted decoded evidence | Unreadable payload | Rejected; 12,241 bytes, SHA256 `4ee8b782a94db50ab521fe8eaf75a0c6c1ba1743d7e72a4e8a85ef9a6c1ecaed`, identical corruption hash to independent review |
| 12-frame video with 1s audio/container | Expected 24 frames not met | Short video cannot borrow audio span | Rejected |

`test_actual_adapter_download_decode_and_graph_correlation` exercises the real
ComfyAdapter, H3Probe and repaired media helper together. Only HTTP/runtime is a
protocol boundary fake via httpx.MockTransport; actual generated bytes traverse
the adapter's download path and real portable FFprobe/FFmpeg. Both success and
corruption cases assert exact submitted graph/base graph/profile hashes, client
correlation including adapter extra_data, and prompt ID. Valid CPU output reaches
VERIFIED with 124 decoded video frames and positive audio count; corrupted output
stays EXECUTED/unaccepted with no actual canvas or output metadata. GPU memory
remains null and qualified remains false. This proves downloaded-media tooling,
not execution of an H3 model or a genuine target runtime.

## Final generation interfaces and runtime docs

Reuse shared `resolve_canvas`, `profile_hash` and output-ancestry graph traversal.
The existing native H3Validator still aligns frames to 17k+5 at 24FPS: requested
5/10/15s resolve to 124/243/362 frames, matching the final generation contract's
arithmetic. Reference request durations now use `video_duration_seconds` and
`audio_duration_seconds` exclusively on actual inspection. Six regressions prove
missing/short stream durations cannot use a longer container and valid 2.5s spans
remain valid. Static placeholders remain explicitly uninspected.

`docs/h3-poc/runtime-tooling.md` now describes decoded/declaration field semantics,
bounds and errors, graph step bindings, required reference categories, global
resume blocking, partial CLI journals and correlation reconciliation through the
existing adapter. Exact Windows and Compose operator examples, media slots and
provenance/hash prerequisites are included. They were not executed against an
operator runtime. Historical static CLI and NOT_RUN matrix evidence stays dated
and separate; no historical result was overwritten as a new executed PASS.

Existing benchmark semantics are retained: one long-lived adapter/client,
concurrency one, excluded first/cold/family-switch/warmup observations, at least
three warm repeats, no model unload, only verified warm timing percentiles,
sampled VRAM maximum with sample count, unknown queue/execution timing null and
separate collection/wall time. Candidate quality/step settings do not become
production defaults and decoded media never triggers automatic enablement.

## Verification

From `D:/project/ai-video-studio/backend`, existing `.venv/Scripts/python.exe`:

```powershell
.\.venv\Scripts\python.exe -B -m pytest tests/unit/test_probe_media.py tests/integration/test_probe_media.py tests/unit/test_h3_probe_evidence.py tests/unit/test_h3_benchmark.py tests/integration/test_h3_probe.py -q -p no:cacheprovider
.\.venv\Scripts\python.exe -B -m ruff check --no-cache apps/api/scripts/h3_probe.py apps/api/scripts/probe_media.py apps/api/management/benchmark_h3.py tests/unit/test_h3_probe_evidence.py tests/unit/test_h3_benchmark.py tests/unit/test_probe_media.py tests/integration/test_probe_media.py
.\.venv\Scripts\python.exe -B -m ruff format --check --no-cache apps/api/scripts/h3_probe.py apps/api/scripts/probe_media.py apps/api/management/benchmark_h3.py tests/unit/test_h3_probe_evidence.py tests/unit/test_h3_benchmark.py tests/unit/test_probe_media.py tests/integration/test_probe_media.py
.\.venv\Scripts\python.exe -B -m pytest tests/unit/test_probe_media.py -k decoder_bounds_and_cancellation -q -p no:cacheprovider
```

- Initial reproductions: **27 failed, 27 passed in 4.60s**, including the exact
  zeroed-mdat and short-video/long-audio real CPU failures and all five controller
  findings. No implementation changes preceded that reproduction run.
- After primary fixes: **75 passed in 4.07s**.
- Final scoped suite after additional decode/process proofs: **99 passed in 6.01s**,
  exit 0, no skips: 33 helper unit, 34 probe unit, 19 benchmark unit, 11 actual CPU
  integration, 2 existing offline probe integration.
- Final scoped Ruff check: **PASS**, exit 0; format check **7 files already
  formatted**, exit 0. Direct formatter writes hit the filesystem sandbox;
  formatting was computed through Ruff stdin and applied through the file patch
  tool, then verified with the read-only check.
- After replacing the cancellation test's polling with an Event: **4 passed,
  29 deselected in 0.40s**, exit 0. No unchanged broad suite was rerun.
- `git diff --check`: **PASS**, exit 0. Existing Git LF/CRLF notices are conversion
  warnings, not whitespace errors. Read-only scoped AST parsing and whitespace
  checks include all seven Python files, docs, JSON and this report: **PASS**.
  Saved CPU JSON consistency checks confirm exact-byte hash binding, expected
  decoded video counts, positive audio counts, both reproduced media rejections
  and external GPU NOT_RUN: **PASS**. Scoped status contains exactly the ten
  implementation/test/document paths listed above.

Final source/evidence SHA256:

```text
h3_probe.py       0b1e296d71e640d80d26fe53d9755989efd3984df751be4ab0bd400b7ce07af2
benchmark_h3.py   11c174951b0c90a018711e526e396b25c48a0cda0ac882f2e063a7756b1c1a15
probe_media.py    49e96e71b119053be8a468059e47f99361fc83a876b9fcd2e89f9a170a7cebc5
CPU evidence JSON 75c9d18640679adacae1ea732bccddf7a4a7b4b2b8fc997bd67d68571b2f90e2
```

Full-suite verification was deliberately left to the sequential generation-core
fix worker per the controller's latest instruction. Four bounded P2 core findings
are owned by that independent slice and are not claimed fixed here.

## External gates

Real H3 GPU generation, operator ComfyUI namespace/export/object_info, installed
model/node/runtime provenance, GPU fit/residency/VRAM, real candidate benchmarks,
creative acceptance, Docker/deployment and deployed UAT remain **NOT_RUN**.
Production release remains NOT_APPROVED. No new request was sent to an operator
runtime, and no CPU/synthetic HTTP assertion is labeled external GPU evidence.
The scoped implementation worker is finished; subsequent core fixes/full-suite
verification can proceed sequentially.

# Independent backend streaming and claims review

Initial review 2026-10-08 approximately 09:19; independent rereview approximately 09:24 Asia/Bangkok. Current verdict: **STREAMING_CLAIMS_SCOPE_PASS** — both original P2 findings independently verified resolved. No remaining confirmed P1/P2 in this reviewed scope. This is not a project-wide closeout verdict. Original failure evidence below is retained as history; the rereview at the end supersedes it.

## Scope and method

Read the supplied `Pasted text.txt` sections 7–14, 22–26 and 40. Reviewed actual working-tree files, not HEAD. Compared the parent snapshots in `.superpowers/sdd/director-only-reliability-plan/`: `before-output-persistence.txt`, `before-dispatcher.py`, `before-director_dispatcher.py`, and `before-assembler.py`, using Python unified diffs. No children, source edits, Git mutations, or application/external DB writes. Only this report is owned by this reviewer. Authorized tests used the repository Windows venv and their in-memory SQLite fixtures. GPU qualification, retirement implementation, frontend, migrations and deployment are outside this verdict.

## Initial confirmed findings — both resolved in rereview

### P2 — A second cancellation releases the caller before the storage thread exits

Source: `backend/apps/api/app/integrations/minio.py:276`, in `_finish_thread` (266–279). Callers include `download_to_path:323`, `put_file_immutable:379` and `checksum_object:221`.

The initial shield protects the thread task, but the first cancellation handler drains it with an unshielded `await task`. A second `cancel()` propagates into that asyncio task and lets the caller return while the underlying synchronous thread continues. `except Exception` does not catch CancelledError. Worker staging cleanup can consequently run while a GET is writing its file or a PUT is reading it. On Windows, open handles can prevent cleanup; on platforms permitting unlink, a download thread can also continue/recreate a path after its owner has exited. For output writes, claim release/compensation can run before a still-active PUT has resolved. This violates specification section 9's requirement not to unlink while a transfer thread still reads the file.

Confirmed with the actual `_finish_thread`, an in-process gated synchronous operation, and two cancellations. Observed:

```text
after_first_cancel: task_done= False thread_finished= False
after_second_cancel: caller_returned=True thread_finished= False
```

Reproduce from `backend` with `.venv/Scripts/python.exe` using the following script on stdin; no DB required:

```python
import asyncio, threading
from apps.api.app.integrations.minio import AssetStore

async def main():
    started, release, finished = (threading.Event() for _ in range(3))
    def transfer():
        started.set()
        release.wait(3)
        finished.set()
    task = asyncio.create_task(AssetStore._finish_thread(transfer))
    while not started.is_set():
        await asyncio.sleep(.001)
    task.cancel()
    await asyncio.sleep(.02)
    print('first', task.done(), finished.is_set())
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        print('second: returned; thread finished?', finished.is_set())
    finally:
        release.set()
    while not finished.is_set():
        await asyncio.sleep(.001)

asyncio.run(main())
```

Recommended correction: repeatedly shield the drain until the operation finishes, preserve cancellation, and observe the operation's exception. `workers/common.py:202–219` already demonstrates the corresponding owned-task pattern. Add repeated-cancellation regression coverage that holds an actual transfer file open and proves the caller cannot exit first. This helper was already present; this is a current acceptance gap exposed by the new production file lifetime, not a claim that the parent introduced its implementation.

### P2 — Default assembly copy remains cancellation-unsafe after encoder cancellation fix

Source: `backend/apps/api/app/integrations/ffmpeg.py:295` in the default KEEP_SCENE_AUDIO/no-background branch. Its enclosing TemporaryDirectory begins at line 217; worker staging ownership is `backend/workers/assembler.py:243`.

`await asyncio.to_thread(shutil.copyfile, assembled, output)` does not drain the copying thread when cancelled. The enclosing temporary-directory cleanup immediately attempts to delete the still-open source. Reproduction on this Windows host returned **PermissionError while the copy thread was still active**, replacing cancellation with a processing error. The outer worker can then treat cancellation as a retryable assembly failure; cleanup is also no longer reliably ordered. The encoder kill/reap logic at FFmpeg `_run:39–61` protects subprocesses, but does not protect this copy branch. Relevant acceptance: section 9 and section 40 cancellation/temp-leak review.

Confirmed by executing actual `FFmpeg.assemble` with valid 256×256 delivery dimensions, fake normalization/concat writing tiny files, and a gated file-copy thread that holds the source open. Observed:

```text
caller_exception= PermissionError thread_finished= False
```

Reproduction script, also from `backend` on stdin:

```python
import asyncio, threading
from pathlib import Path
from tempfile import TemporaryDirectory
from apps.api.app.integrations.ffmpeg import FFmpeg
import apps.api.app.integrations.ffmpeg as module

async def main():
    started, release, finished = (threading.Event() for _ in range(3))
    original = module.shutil.copyfile
    def held_copy(src, dst):
        try:
            with open(src, 'rb') as reader:
                started.set()
                release.wait(3)
                with open(dst, 'wb') as target:
                    target.write(reader.read())
        finally:
            finished.set()
    ffmpeg = FFmpeg()
    async def normalize(src, dst, *args):
        dst.write_bytes(b'clip')
        return {'duration_seconds': 1}
    async def cut(clips, out):
        out.write_bytes(b'assembled')
    ffmpeg._normalize, ffmpeg._concat_cut = normalize, cut
    module.shutil.copyfile = held_copy
    try:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            task = asyncio.create_task(ffmpeg.assemble(
                [root/'in.mp4'], root/'out.mp4',
                {'width': 256, 'height': 256, 'fps': 24}))
            while not started.is_set():
                if task.done():
                    task.result()
                await asyncio.sleep(.001)
            task.cancel()
            try:
                await task
            except BaseException as exc:
                print(type(exc).__name__, 'thread finished?', finished.is_set())
            finally:
                release.set()
            while not finished.is_set():
                await asyncio.sleep(.001)
    finally:
        module.shutil.copyfile = original

asyncio.run(main())
```

Recommended correction: own and shield/drain the copy task before leaving the temporary context, including repeated cancellation. Add a behavior regression for this branch. No source correction was made by this read-only reviewer. No supplied pre-change FFmpeg snapshot establishes when this copy behavior originated.

## Verified implementation evidence

- **Comfy transfer:** `comfy_adapter.py:281–367` uses exclusive caller-selected file creation and 1 MiB chunks, SHA-256 of actual received bytes, configured ceiling plus tighter caller limit, strict Content-Length parsing/early rejection, runtime byte count, excess/short length detection, identity encoding, HTTP error rejection, redirects disabled even for injected clients, HTTP timeout and overall asyncio deadline. Failure/cancellation removes only a newly created destination. Remote names/subfolders are validated and never become the local destination. Without a declared length, clean-EOF truncation cannot be distinguished at this layer; subsequent video probing remains required.
- **Production workers:** standalone collection `dispatcher.py:600–632`, Director collection `dispatcher.py:646–701`, and aggregate collection `director_dispatcher.py:268–325` download into fixed local paths, probe video, retain Director artifact contract validation, and call `save_output_file` with download checksum/size expectations. Parent snapshot comparisons show replacement of full-buffer download/save calls and preservation of frozen output expectations. No worker output call to `adapter.download` or final `read_bytes` remains in the inspected sources.
- **Assembly:** `assembler.py:251–284` keeps production store file downloads for clips and background audio; injected-store fallback remains. `assembler.py:289–311` passes final.mp4 to file persistence after output-size checking. The added assembly behavior test guards final.mp4 against read_bytes and checks actual file-upload use, digest, READY and normal temp cleanup; it ran successfully. Its fake encoder does not exercise the live FFmpeg copy lifetime above.
- **Canonical persistence:** `common.py:364–405` hashes actual file bytes in bounded chunks, rejects early/runtime oversize and unexpected download identity, and delegates to `_save_output`. Video metadata checks preserve the prior contract (missing kind was previously allowed with has_video true; path validation normalizes VIDEO). Production callers provide probe results; no weaker Comfy metadata shortcut was introduced.
- **Claims and immutable publication:** deterministic identity/key validation remains at `common.py:435–497`; pre-storage ownership check at 578–586; heartbeat covers upload, read-after-write and READY replay. Actual stored checksum and size must match before publication. The final row lock rechecks exact claim and PENDING_UPLOAD status at 607–628. File PUT uses destination `IfNoneMatch='*'` at `minio.py:365–373`; conditional collisions compare metadata identity, then canonical persistence verifies actual stored bytes. The common helper now fences cleanup after failed/cancelled PUT attempts and DB publication failures. Loss to another owner retains replacement ownership/bytes; deletion-owned compensation and durable deletion retries remain. The repeated-cancellation adapter finding prevents a blanket cancellation-safety verdict.
- **Temporary files:** unique per-job children, normal context cleanup and an initial free-disk guard are present at `common.py:148–160`. Received files and persisted output sizes are bounded. No claim is made that the post-encoding size check bounds total assembly scratch space during encoding, or that abrupt process termination runs context cleanup. Search found no worker-staging crash sweeper in backend source.
- **Accepted collateral architecture:** asset completion remains durable enqueue; validator still claims with skip_locked, streams files, probes and uses immutable file promotion. Generation admission still calls shared oldest_pending_generation and excludes Director members from standalone selection. Parent worker diffs do not change admission. User asset/final downloads still return presigned MinIO URLs (`api/assets.py:177`, `api/assembly.py:106`). No queue redesign was introduced in the inspected paths.

## Fresh test evidence

Both commands ran in `D:/project/ai-video-studio/backend`, with `PYTHONDONTWRITEBYTECODE=1`, using `.venv/Scripts/python.exe`. `-p no:cacheprovider` disabled pytest cache writes. Exact outputs are reported separately, without presenting their sum as a suite result.

```text
python -m pytest -q -p no:cacheprovider tests/unit/test_comfy_streaming.py tests/unit/test_ffmpeg_cancellation.py tests/integration/test_output_file_persistence.py tests/integration/test_asset_claims.py tests/integration/test_assembly_manifest.py
55 passed in 4.07s
exit 0

python -m pytest -q -p no:cacheprovider tests/unit/test_comfy_adapter.py tests/unit/test_storage_streaming_limits.py tests/unit/test_minio_storage.py tests/integration/test_asset_validation_worker.py tests/integration/test_asset_validation_http.py
49 passed in 5.41s
exit 0
```

File-persistence coverage includes before/during/after-storage claim loss, immutable collision, actual-byte READY replay rejection, missing-object repair, deletion compensation, cancellation claim release and publication DB failure. Existing claim tests include heartbeat DB failure and compensation failures. The successful suites do not cover either confirmed thread-lifetime reproduction above. Parent-reported 87/21 results are not used as independent evidence.

PostgreSQL scheduler-fairness execution: **NOT_RUN** in this read-only review; those fixtures create/write isolated PostgreSQL schemas, and this assignment prohibits DB edits. Fairness is source-reviewed only; SQLite tests do not establish replica/row-lock behavior. Live MinIO/HTTP large-media smoke, application DB, deployment/runtime and full backend validation: **NOT_RUN by this reviewer**. No retirement-fixture mismatch was treated as a finding.

Finding-source SHA-256 at final inspection:

```text
minio.py  C734D4C1F364C89CC49E271085443C50B74217AFC4747DDE8005C16A95905BC2
ffmpeg.py 707FBF19BF0D78992A7F8D1BDC0A520FF49F90D7D9FD9C9A1D51EE27CE4940A5
```

## Initial independent verdict — superseded

**STREAMING_CLAIMS_SCOPE_FAIL.** Full-buffer production output removal and the canonical claim/verification flow are supported by source and targeted behavior evidence. Two reproduced P2 cancellation/lifetime gaps prevent this reviewer from accepting the requested cancellation-safe storage/temp-file guarantee. Fix and independently rerun these reproductions plus the affected targeted tests before changing this scoped verdict.


## Independent rereview — 2026-10-08 approximately 09:24 Asia/Bangkok

Current verdict: **STREAMING_CLAIMS_SCOPE_PASS**. Both original P2 findings are resolved in the inspected live working tree. This reviewer changed only this report.

### Fix inspection and finding closure

1. **MinIO repeated cancellation: RESOLVED.** `backend/apps/api/app/integrations/minio.py:275–284` now repeatedly shields/drains the owned transfer task, survives further cancellation, observes a completed task's exception and re-raises the caller's cancellation. The optional stopped event still signals the upload reader. The worker cannot leave this adapter call while the thread remains active solely because a second cancellation arrived.
2. **FFmpeg final copy: RESOLVED.** `backend/apps/api/app/integrations/ffmpeg.py:295–309` now owns and shields the copy task and drains it before the temporary context can clean up. Repeated cancellation is preserved; a copy exception during cancellation is observed without replacing cancellation. The normal path still propagates copy failure. This closes the distinct final-copy gap; the encoder kill/reap behavior remains tested.

Read the new actual-open-file regressions in `test_storage_cancellation.py` and `test_ffmpeg_cancellation.py`. They hold an actual source handle across two cancellations, assert that the task stays pending until release, and assert cancellation plus thread completion. The FFmpeg test also proves that its temporary source exists during the held copy and is removed after the copy finishes. These are behavior tests, not source assertions.

Read `.artifacts/final-ui-media-closeout/director-closeout-cancellation-red.log`: it contains the expected two failures and one pass against the earlier implementation (MinIO returned early; FFmpeg returned PermissionError). This is implementer-captured historical evidence, not an independently repeated RED run; no source rollback was performed.

### Exact original reproductions rerun independently

Extracted the two Python code blocks above directly from this report with a regular expression and executed each unchanged, from backend with the repository venv and bytecode writes disabled. Both gates have a three-second bound, allowing unchanged scripts to complete with the fixed drain semantics. Command exit 0. Observed:

```text
Original reproduction 1
first False False
second: returned; thread finished? True
Original reproduction 2
CancelledError thread finished? True
```

The first reproduction no longer returns before the transfer finishes. The second preserves CancelledError and no longer produces the previously observed PermissionError. The independent new regression run below additionally asserts that each task stays pending while the file handle is explicitly held.

### Independent affected-test run

Ran from `D:/project/ai-video-studio/backend`, with `PYTHONDONTWRITEBYTECODE=1`, using `.venv/Scripts/python.exe` and pytest cache disabled:

```text
python -m pytest -q -p no:cacheprovider tests/unit/test_comfy_streaming.py tests/unit/test_ffmpeg_cancellation.py tests/unit/test_storage_cancellation.py tests/integration/test_output_file_persistence.py tests/integration/test_asset_claims.py tests/integration/test_assembly_manifest.py tests/unit/test_comfy_adapter.py tests/unit/test_storage_streaming_limits.py tests/unit/test_minio_storage.py tests/integration/test_asset_validation_worker.py tests/integration/test_asset_validation_http.py
106 passed in 12.09s
exit 0
```

This is one fresh combined invocation, not a sum of previous results. It independently covers the affected cancellation fixes and surrounding streaming, immutable storage, persistence, claims, assembly behavior and accepted asset queue regressions. No external/application DB was modified; integration fixtures used in-memory SQLite.

Live SHA-256 fingerprints captured after these runs:

```text
minio.py                    AC32CB7831FF3700D752190CF2E64208B5BBE55CFA92C77733FF4622599A2542
ffmpeg.py                   03E2A73DB555BDF3F420F633C4D8E9A25A9F687DC20C1D18B6802771AC1614DF
test_storage_cancellation.py 62373A2A85288C429841234C8A40A1DBFBD84AF21F786EB6FFC777E1BE3A5A23
test_ffmpeg_cancellation.py  307529FA2E72A2E03D666D37AB0A792AFF5F9003CBA48CBE57DF95308AAF7C27
```

### Scope of acceptance

**STREAMING_CLAIMS_SCOPE_PASS**, with no remaining confirmed P1/P2 from this independent streaming/claims review. The original production-path and claim-semantic review evidence remains applicable, supplemented by the fresh affected-test invocation and both independently rerun reproductions. This is acceptance of the reviewed source and targeted regressions, not live MinIO/large-media deployment verification or a full project closeout. PostgreSQL fairness concurrency, live runtime/application DB, real-encoder large-media smoke and full backend validation remain NOT_RUN by this reviewer, as stated above. GPU qualification and queue redesign remain excluded.

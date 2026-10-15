# GX10 Director dependencies — iteration 1

Status: source recipe prepared; GX10 runtime compatibility remains unverified.

## Repository state

- Root: `D:\project\ai-video-studio`
- Branch: `codex/production-safety-fixes`
- HEAD before changes: `06ea18cd6bb3ba878295f5e9c4441c772e47c529`
- No staged changes at start; branch matched `origin/codex/production-safety-fixes`.
- Preserved unrelated worktree items: `frontend/next-env.d.ts`,
  `backend/.pytest-e-approved-regression-114e8b/`, and `c2c-reconnected-proof.png`.
- No commit, push, reset, or clean operation.

## Findings and decisions

- Audited the exact Director pin `a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb`.
  Its source ZIP SHA-256 is
  `c3d1193ec6d41b19b53e7f05493fde85829cbea71077ace2b1d768e6c595c05a`; all 105
  source files were checked against the existing manifest. The manifest and bridge
  hashes are unchanged in this task.
- `director/audio_export.py` resamples with `torchaudio`; on failure the pinned code
  logs a warning and returns the original audio. Motion Context fails when a required
  sample-rate conversion has no resampler. No safe missing-resampler fallback exists.
- PyPI has no exact `torchaudio==2.14.1` wheel. The selected candidate is the official
  CPython 3.10 ARM64 `torchaudio==2.11.0` wheel. TorchAudio's stable-ABI table states
  that 2.11 supports PyTorch 2.11 and later. This makes it a candidate for Torch
  `2.14.1+cu130`, not proof on the GB10. The recipe imports it and exercises a CPU
  32 kHz to 44.1 kHz resample before declaring the image ready.
- PyPI wheel metadata resolved to 30 exact CPython 3.10/aarch64 artifacts, totaling
  159,177,563 bytes. `pip download --require-hashes --only-binary --no-deps` fetched
  all 30 and verified their hashes. A local metadata audit found no unsatisfied Linux
  transitive requirements after applying the documented OpenCV provider policy.
- Ultralytics metadata requires `opencv-python`; the pinned Director requires
  `opencv-python-headless`. The image installs only headless to avoid overlapping
  native `cv2` files. The runtime verifier fails if a desktop distribution is also
  present, checks that the selected headless version satisfies Ultralytics' version
  range, and imports `cv2`. Upstream metadata does not recognize the alias, so
  `pip check` reports that package-name mismatch; use the recipe's explicit provider
  check and import smoke test for this known case.
- Jammy FFmpeg is pinned to `7:4.4.2-0ubuntu0.22.04.1` from an Ubuntu archive
  snapshot; the same package supplies `ffprobe`. Target glibc 2.35 is above the
  manylinux 2.28 wheel floor.

## Changes

- `backend/comfy_nodes/gx10_package.py`: require TorchAudio 2.11.0 and fail closed
  unless ABI version checks and a CPU resample smoke test pass before copying nodes.
- `backend/tests/unit/test_gx10_node_package.py`: cover stable-ABI and resample
  success/failure behavior.
- `backend/tests/contract/test_gx10_director_dependencies.py`: lock, image, target,
  base-dependency, and OpenCV provider contracts.
- `infra/gx10-director/requirements.lock`: 30 exact versions, one SHA-256 per wheel;
  Torch, torchvision, and desktop OpenCV excluded.
- `infra/gx10-director/fetch-wheels.sh`: hash-verified CPython 3.10 ARM64-only wheel
  download, accepting manylinux 2.28 and compatible manylinux2014 tags.
- `infra/gx10-director/Dockerfile`: derived from the existing image, checks and
  preserves Torch/torchvision/CUDA, installs offline locked wheels and pinned FFmpeg,
  then runs a CPU-only resampler/native-dependency smoke test.
- `infra/gx10-director/build-image.sh`: requires the expected base image ID, verifies
  ARM64, preserves the configured user, and accepts an explicit ComfyUI Python path.
- `infra/gx10-director/verify_runtime.py`: verifies exact wheel versions, existing
  ComfyUI dependencies/imports, the OpenCV provider policy, FFmpeg/ffprobe, and
  NumPy-to-TorchAudio resampling on CPU without querying a GPU.
- `docs/gx10-node-packaging.md`: documents the dependency choice, recipe, and target
  gates. Wheel binaries are generated locally and are not committed.

## Verification

- Python syntax compilation: passed for installer, changed tests, and runtime
  verifier.
- Pytest collection: 144 tests collected across the dependency contract, Director
  package, and native-manifest suites; no collection errors.
- Focused pytest: **144 passed** (`test_gx10_director_dependencies.py`,
  `test_gx10_node_package.py`, and `test_director_native_manifest.py`).
- Ruff on changed Python files: passed.
- `bash -n` for both shell scripts: passed.
- `git diff --check`: passed; Git emitted only existing LF-to-CRLF working-copy
  warnings for documentation and `frontend/next-env.d.ts`.
- The first OpenCV contract run exposed an incorrectly modeled test fixture; it was
  corrected and the final 144-test run passed.
- No Docker build or target-host operations were run. This avoids changing or
  executing the GX10 runtime as required.

## Remaining gates

- Run `bash infra/gx10-director/fetch-wheels.sh` on the intended GX10 checkout to
  populate `infra/gx10-director/wheels/`. The empty `wheels/` directory is expected
  before that step; it is not `~/projects/gx10-director-package/wheels`.
- Inspect `comfyui:local`, capture its exact image ID and configured user, and build
  only if the base ID and ARM64 checks pass. Set `PYTHON` if ComfyUI does not use
  `/usr/bin/python3`.
- Smoke-test imports/resampling and media executables in the derived image on the
  target. Then separately complete ComfyUI startup, model readiness, H3 execution,
  native output, continuity, and all existing qualification gates. Do not call this
  runtime qualified until those succeed.
- C2C workspace info/status confirmed the connected `ai-video-studio` workspace and
  branch. Independent ChatGPT review of this iteration is pending; execution output
  will be recorded and sent through the existing C2C conversation.

## Iteration 2 — independent review corrections

ChatGPT review found four P2 gaps: incomplete OpenCV conflict detection, missing
direct imports for selected native/media dependencies, an all-zero audio smoke signal,
and generated wheels not explicitly ignored. These corrections were made without
changing the pinned upstream source or its manifest hashes:

- `verify_opencv_provider` now rejects `opencv-python`, `opencv-contrib-python`, and
  `opencv-contrib-python-headless`; it permits only the locked headless distribution
  after validating its version against Ultralytics' declared constraint.
- The derived-image verifier imports `imageio_ffmpeg`, `scenedetect`, `numpy`, and
  `ultralytics` (the latter with a temporary `YOLO_CONFIG_DIR`) in addition to `cv2`.
  No model is loaded or fetched.
- Installer and image smoke tests now resample a nonzero 1 kHz sine wave and check
  output length, finiteness, RMS range, and signal similarity. The unit tests reject
  zero output, a wrong-frequency output, non-finite output, and a wrong output shape.
- Build-time `pip check` allows either a clean result or exactly one expected
  `ultralytics <locked-version> requires opencv-python, which is not installed.` line.
  Any extra or different dependency error fails the image verification.
- `.gitignore` excludes only generated wheel files in `infra/gx10-director/wheels/`;
  the directory marker remains trackable. `git check-ignore` verified both cases.
- Contract coverage was expanded for all rejected OpenCV distributions, successful
  and failing dependency imports, temporary YOLO config cleanup, strict `pip check`,
  nonzero resampling behavior, and wheel ignore scope.

Iteration 2 verification, run from `backend`:

- Python `py_compile`: passed for the installer, verifier, and changed tests.
- Pytest collection: passed for the same three targeted suites.
- Focused tests: **155 passed** across
  `test_gx10_director_dependencies.py`, `test_gx10_node_package.py`, and
  `test_director_native_manifest.py`.
- Ruff: passed for changed Python files.
- Git Bash `bash -n`: passed for `fetch-wheels.sh` and `build-image.sh`.
- `git diff --check`: passed; only Git's LF-to-CRLF working-copy notices appeared.
- `git check-ignore`: generated `*.whl` ignored and `wheels/.gitkeep` not ignored.
- No Docker build or GX10/runtime operations were run. The second independent review
  was pending at this checkpoint; see the final review result below. No commit or push
  was performed.

## Independent closeout review

The connected ChatGPT reviewer returned `STATE: DONE` for iteration 2 on the exact
workspace, branch, and HEAD above. It confirmed the 155/155 tests, Ruff, Python and
shell syntax, diff check, wheel-ignore policy, and the prior 30/30 wheel hash/tag audit.
It found no blocking issue in the reviewed source. The approval is source-level only;
the reviewer explicitly left the following target gates open:

- Build `comfyui:gx10-director` from the verified ARM64 base image on GX10.
- Verify Torchaudio 2.11.0 with the existing Torch 2.14.1+cu130 stack on ARM64.
- Check runtime imports, OpenCV/FFmpeg, ComfyUI startup, and the four `/object_info`
  nodes, then run the existing GPU/media/continuity qualification suite.

No commit, push, merge, Docker build, or GX10 operation was performed. Existing
unrelated changes remain untouched.

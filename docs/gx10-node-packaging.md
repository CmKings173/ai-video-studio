# GX10 Director source package

This package adds Director and the Studio bridge to an **existing ARM64 ComfyUI**
environment. Use that environment's Python interpreter. It never invokes pip,
changes torch/CUDA, downloads weights, starts ComfyUI, or runs generation.
The existing CUDA 13 torch installation remains operator-managed.

## Provenance and inputs

Official repository: [AIMixer/ComfyUI_MiniMaxH3_Director](https://github.com/AIMixer/ComfyUI_MiniMaxH3_Director/tree/a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb).
The only network input is the [exact-commit source ZIP](https://codeload.github.com/AIMixer/ComfyUI_MiniMaxH3_Director/zip/a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb).

- Commit: `a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb`.
- ZIP SHA-256: `c3d1193ec6d41b19b53e7f05493fde85829cbea71077ace2b1d768e6c595c05a`.
- ZIP size inspected: 770713 bytes; 105 regular files.
- `backend/comfy_nodes/gx10_manifest.json` records every upstream file checksum,
  the local bridge's two input checksums, and the four expected output contracts.
  Hashes were calculated from downloaded official bytes, not inferred from Git IDs.
- `backend/comfy_nodes/.gitattributes` enforces LF checkout bytes so Windows Git
  autocrlf cannot silently invalidate bridge hashes when the package is transferred.
- Ship `gx10_package.py`, `gx10_manifest.json`, and `studio_director_bridge/`
  together, preserving bytes. The manifest is a trusted reviewed deployment input;
  it is not a signed publisher attestation. Do not regenerate it from target files.

The official mappings include `MiniMaxH3Director`, `MiniMaxH3DirectorRefine`, and
`MiniMaxH3DirectorFaceRefine`. The bridge adds `StudioMiniMaxH3Director`.
Director and bridge return `IMAGE,AUDIO,FLOAT,INT,IMAGE,STRING,IMAGE,IMAGE`;
Refine returns `MMX_DIR_REFINE,INT,INT`; FaceRefine returns `MMX_DIR_FACE_REFINE`.
Additional upstream nodes remain present; the upstream package is copied intact,
including its license and web assets.

## Install on the target (operator action)

Stop ComfyUI before changing its node directories. From a copy of this repository,
use the Python executable from the existing ComfyUI environment:

```sh
/path/to/comfy-venv/bin/python backend/comfy_nodes/gx10_package.py install \
  --comfy-root /path/to/ComfyUI > gx10-install-receipt.json
```

For offline deployment, transfer the exact official ZIP and add
`--archive /path/to/director-source.zip`. The same archive and per-file checks run.
No source-fetch command executes repository hooks or setup scripts.

The installer checks all source hashes, rejects extra/missing files, symlinks and
unsafe ZIP members, parses upstream Python, checks dependency metadata, and refuses
to overwrite either destination. It creates only:

```text
ComfyUI/custom_nodes/ComfyUI_MiniMaxH3_Director/
ComfyUI/custom_nodes/studio_director_bridge/
```

Any failure exits nonzero. Validation errors before copying leave destinations
untouched. An I/O failure during copying may leave an incomplete installation;
keep ComfyUI stopped and have the operator inspect those directories. A retry
refuses to overwrite them. There is no automatic cleanup or upgrade operation.

The pinned upstream requirements are `opencv-python-headless>=4.8`,
`imageio-ffmpeg>=0.4`, `scenedetect>=0.6.4,<0.8`, and `ultralytics>=8.0`.
The installer also requires torch, torchaudio, numpy, Pillow, scipy, safetensors,
aiohttp, and packaging. It now requires `torchaudio==2.11.0`, imports it, and runs
the exact 32 kHz to 44.1 kHz functional resample on a synthetic CPU waveform before
copying any node files. This is a dependency smoke test, not H3 runtime qualification.
Upstream declares ComfyUI `>=0.30.0`; the target must also expose the MiniMax H3
core APIs used by this commit.

## GX10 dependency image

The upstream `audio_export.py` resamples source audio with `torchaudio`. If that
call raises, the pinned code logs a warning and returns the original waveform;
Motion Context instead raises when a rate mismatch requires unavailable
`torchaudio`. There is no safe missing-resampler degraded mode, so do not install
or advertise the Director until the resampler check succeeds.

An exact `torchaudio==2.14.1` wheel was not found on PyPI. A compatible candidate
is the official `torchaudio==2.11.0` CPython 3.10 `manylinux_2_28_aarch64` wheel.
[TorchAudio's compatibility matrix](https://docs.pytorch.org/audio/stable/installation.html)
says 2.11 uses PyTorch's stable ABI and supports PyTorch 2.11 and later; the
[PyPI release](https://pypi.org/project/torchaudio/2.11.0/#files) has no declared
dependencies that could ask pip to replace the existing Torch stack. The selected
wheel SHA-256 is pinned in
[`infra/gx10-director/requirements.lock`](../infra/gx10-director/requirements.lock).
This establishes a documented compatibility candidate, not proof on the GB10.

The same lock pins all 30 direct and transitive Python wheels, one SHA-256 per
artifact, for CPython 3.10 on ARM64. The lock deliberately omits `torch`,
`torchvision`, and `opencv-python`. Ultralytics and SceneDetect declare `opencv-python`, while the
pinned Director declares `opencv-python-headless`; the image uses the headless wheel
as the single `cv2` provider. The build fails if a desktop `opencv-python` distribution
or either `opencv-contrib-python` distribution is also present, or if the headless
version does not satisfy either consumer's active dependency declaration. It imports `cv2`,
`imageio_ffmpeg`, `scenedetect`, and `ultralytics` (with a temporary YOLO config
directory) without loading a model. This deliberate package-name substitution avoids
overlapping native files. Upstream metadata does not recognize this alias, so the
runtime accepts failing `pip check` only when its output consists of exactly the
two missing `opencv-python` lines for the installed Ultralytics and SceneDetect
versions (in either order), after revalidating the headless provider against both
packages' actual metadata. Missing, duplicate, malformed, URL-based or inactive-only
OpenCV declarations fail closed; other errors, warnings, duplicates and exit codes
remain fatal. SceneDetect 0.7.2 legitimately declares unversioned `opencv-python`;
Ultralytics must retain a version constraint.
Every other `pip check` failure remains fatal. `pip download --require-hashes --only-binary` verified
all 30 artifacts from PyPI on 2026-10-15: 159,177,563 bytes. No source builds were
needed. The wheel files are generated inputs and are not committed. An empty
`wheels/` directory is expected until the fetch step below runs; `.gitignore` excludes
only generated `*.whl` files and keeps the directory marker tracked.

The follow-up metadata audit matched all 30 lock hashes to official PyPI release
JSON and read the selected artifacts' published wheel metadata for Linux aarch64 /
Python 3.10 (without extras). The only OpenCV aliases were
`ultralytics==8.4.177`: `opencv-python!=4.13.0.90,>=4.7.0`, and
`scenedetect==0.7.2`: `opencv-python`. All other active requirements were satisfied
by the lock plus the supplied Torch/Torchvision base versions. This metadata audit
does not validate native ABI compatibility or the entire existing base image.
TorchAudio's metadata declares no Torch dependency; runtime smoke tests remain
mandatory. Base scipy/safetensors/aiohttp versions and their dependency closure
still require the actual GX10 inventory and final `pip check`.

Docker layers install pinned FFmpeg first, then copy the lock/wheels and install
them with the existing exact Torch/Torchvision/CUDA checks before and after pip.
Only afterward is `verify_runtime.py` copied and executed in a separate layer.
Changing the verifier therefore reuses the dependency-install layers when the
base image, build arguments, lock, wheels and Docker cache are unchanged. The first
build after this layer reorder may rebuild those layers once; cache reuse itself
must be verified on GX10. Do not clear the build cache to apply a verifier fix.

Ubuntu's [Jammy ARM64 package](https://packages.ubuntu.com/jammy/ffmpeg) supplies
FFmpeg `7:4.4.2-0ubuntu0.22.04.1`; its [ffprobe tool](https://manpages.ubuntu.com/manpages/jammy/man1/ffprobe-all.1.html)
is provided by the same package. The image recipe pins that package and an
[Ubuntu archive snapshot](https://snapshot.ubuntu.com/), rather than depending on
whichever apt version is current at build time. The target's glibc 2.35 satisfies
the selected manylinux 2.28 floor.

On the GX10, after choosing to build the derived image:

```sh
# From the ai-video-studio checkout; this downloads wheels only, not models.
bash infra/gx10-director/fetch-wheels.sh

# Capture the exact local base image ID, then require the same ID at build time.
docker image inspect --format '{{.Id}} {{.Config.User}}' comfyui:local
EXPECTED_BASE_IMAGE_ID='sha256:<captured-image-id>' \
  bash infra/gx10-director/build-image.sh
```

If ComfyUI uses a Python other than `/usr/bin/python3`, set `PYTHON` to that
interpreter when invoking `build-image.sh`.

`build-image.sh` refuses a changed or non-ARM64 base image, detects and preserves its configured
user, and builds for `linux/arm64`. The Dockerfile installs only the hash-locked
wheelhouse with `--no-index --no-deps --require-hashes`, installs the pinned Ubuntu
FFmpeg package from the fixed snapshot, then verifies the exact Torch, torchvision,
and CUDA versions are unchanged. Its CPU smoke test imports OpenCV and TorchAudio,
checks Pillow and the ComfyUI-provided SciPy, safetensors, and aiohttp dependencies,
round-trips a synthetic NumPy waveform through TorchAudio on CPU, and checks both
`ffmpeg` and `ffprobe`; it does not query or execute a GPU. Change the recorded base
ID, apt snapshot, or wheel lock only as a reviewed dependency update.

This is a dependency-only image layer. Install the hash-verified Director source
and Studio bridge with the existing source installer into the intended ComfyUI
`custom_nodes` location; do not assume an existing custom-node volume will expose
files baked into an image. Restart, fresh `/object_info`, model readiness, native
media paths, GPU execution, and all H3 qualification gates still require operator
verification on the target. No claim of GX10 runtime qualification is made here.

NVIDIA VFX is optional upstream and is not installed or validated here. Do not
enable `nvidia_rtx_vsr` without separate qualification. Dependency metadata does not
prove native-library imports, FFmpeg execution, detector availability, or GPU ABI
compatibility. Supply model files separately through the existing governed process.
Do not use automatic model-fetch behavior as a readiness check.

## Verify after an operator-controlled restart

Save a fresh `/object_info` response from the intended ComfyUI instance to
`object-info.json`. This tooling deliberately makes no runtime network requests.

```sh
/path/to/comfy-venv/bin/python backend/comfy_nodes/gx10_package.py verify-installed \
  --comfy-root /path/to/ComfyUI --object-info object-info.json \
  > gx10-verify-receipt.json
```

This rechecks installed source/bridge integrity, dependency versions, and all four
node output contracts. Root, node, input, required, and optional containers must be
mappings. Every required/optional socket descriptor must contain a nonempty type
string or a scalar choice list, plus an optional options mapping. Duplicate socket
names across required/optional sections are rejected.

Required socket names and types match the pinned upstream INPUT_TYPES methods:
Director requires model/video_vae/audio_vae/clip; Refine requires mode,
upscale_method, latent_upscale_model, sampler, and passes; FaceRefine requires its
13 face detection, canvas, selection, sampling, and scheduler fields. Essential
optional sockets are required in their original optional section: Director
refine/face_refine, Refine refine_model/sigmas, FaceRefine sigmas, and the Studio
bridge's studio_output_prefix. These schemas were compared against the
checksum-verified official source AST without importing upstream code.
A node-only check is also available:

```sh
/path/to/comfy-venv/bin/python backend/comfy_nodes/gx10_package.py validate-nodes \
  --object-info object-info.json
```

An inventory from another instance or an old process is not deployment evidence.
Python `__pycache__` files are excluded from source inventories; deploy while
stopped and restart with the verified source. The inventory does not authenticate
the running process or detect a second custom node overriding mappings.
Schema checks do not validate every widget option, numeric bound, or combo value;
empty combo lists remain structurally valid when model files are unavailable.
Model readiness and execution evidence remain separate qualification requirements.

The bridge's former seam hashes matched Windows CRLF copies of this exact pin.
They now match the official LF blobs, with CRLF normalized only for the four bridge
seam checks. Full source/package validation still uses exact raw bytes. This fixes
Linux source deployment without accepting changed Python content.

## Qualification and local evidence

Installation and registry checks remain **UNQUALIFIED** for generation. Preserve
all H3 graph/profile, ownership, provenance, model, geometry, native-output,
Motion Context, Refine, FaceRefine, and runtime qualification gates. In particular,
do not enable candidates or rewrite ownership based on this package's receipt.
See [production qualification](production-generation-qualification.md) and
[architecture contracts](architecture/).

Local validation uses `backend/.venv/Scripts/python.exe`: syntax parsing, pytest
collection, and scoped unit tests; scoped Ruff checks cover installer, bridge,
and tests. Official ZIP verification checks all 105 file hashes and all four
bridge seam hashes without importing upstream code. Negative tests cover source
drift, missing/extra files, symlinks, corrupt archives, unsafe paths, missing or
incompatible dependencies, destination preservation, and node contract failures.
The source-package regression suite was expanded after malformed-schema review;
an independent re-review approved that package iteration with 133 scoped tests.
No independent GX10 runtime review was performed.

Source-package validation from the earlier iteration on 2026-10-15 (repository root):

```powershell
# ast.parse on installer, bridge, new tests, and existing native-manifest tests: OK
backend/.venv/Scripts/python.exe -m pytest --collect-only -qq backend/tests/unit/test_gx10_node_package.py backend/tests/unit/test_director_native_manifest.py
# 130 package tests + 3 existing native-manifest tests collected
backend/.venv/Scripts/python.exe -m pytest -q backend/tests/unit/test_gx10_node_package.py backend/tests/unit/test_director_native_manifest.py
# 133 passed, no skips
backend/.venv/Scripts/ruff.exe check backend/comfy_nodes/gx10_package.py backend/comfy_nodes/studio_director_bridge/__init__.py backend/tests/unit/test_gx10_node_package.py
# All checks passed
```

The initial eight negative tests were collected and run before the installer
existed, and failed as expected. The official ZIP was then unpacked to synthetic
temporary storage using the installer verifier: all 105 raw file hashes, upstream
Python syntax, the bridge package inventory, and all four official seam hashes
passed. The same run exposed and led to the CRLF compatibility correction above.
The review follow-up first reproduced six failures covering malformed inventory
roots and shape-correct bogus required sockets. The expanded negative cases cover
all four nodes' malformed node/input/required/optional containers, bad descriptors
and options, malformed Refine connections, missing known sockets, wrong socket
types, and duplicate required/optional sockets. The positive fixture now reflects
the official required schemas and essential optional sockets rather than using
the same placeholder fields for all nodes.

No SSH, container build/run, GPU, live database, migrations, model downloads, commit,
push, reset, or clean operations were used for the dependency task. ARM64 imports,
native codecs, ComfyUI startup, and GPU execution remain untested. Current recipe
verification and target-side gates are recorded in
[`tasks/gx10-director-dependencies-2026-10-15.md`](../tasks/gx10-director-dependencies-2026-10-15.md).

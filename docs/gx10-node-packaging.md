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
The installer also requires installed metadata for torch, torchaudio, numpy,
Pillow, scipy, safetensors, aiohttp, and packaging. Missing/incompatible declared
dependencies fail closed before copying. The receipt records the exact installed
versions; this is an environment inventory, not a wheel lock or ABI certification.
No dependency artifacts are fetched or resolved. Provision any missing dependencies
separately with reviewed ARM64 artifacts and constraints that protect the existing
torch/CUDA stack. Upstream declares ComfyUI `>=0.30.0`; the target must also expose
the MiniMax H3 core APIs used by this commit.

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
Review: implementation self-review completed. Dalton's P2 malformed-schema finding
was reproduced and addressed; independent re-review approved the final package,
including all 34 known socket deletion cases and 133 passing scoped tests.
No independent runtime review was performed.

Validation on 2026-10-15 (repository root):

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

No SSH, containers, GPU, live database, migrations, model downloads, commit, push,
reset, or clean operations were used. ARM64 imports, native codecs, ComfyUI startup,
and GPU execution remain untested. The main task owns the cross-task ledger and
Docker/compose validation; this scoped task writes no shared ledger or infra files.

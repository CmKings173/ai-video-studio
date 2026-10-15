# GX10 Director OpenCV aliases and build cache

Task: `gx10-director-opencv-alias-and-cache`, iteration 1.
Baseline: `codex/production-safety-fixes`, `db0ca2d0190c4fbe3b0a01bc79770d1f907093c5`.

## Reproduction and cause

The supplied GX10 build passed dependency installation and CPU/native smoke
checks, then failed because pip reported both Ultralytics and SceneDetect missing
`opencv-python`. The verifier previously accepted only the Ultralytics line and
validated only that consumer's OpenCV requirement.

## Correction

- Validate the sole headless provider against both consumers' active metadata.
  Preserve rejection of desktop/contrib providers. SceneDetect's actual pinned
  declaration has no version constraint; Ultralytics' constraint remains required.
- Waive only exactly the two known missing-package lines for installed versions,
  in either order, with exit code 1 and after provider revalidation. Other failures
  remain fatal. Clean pip check remains accepted.
- Install pinned FFmpeg before copying the wheel inputs. Keep offline/hash-locked
  pip installation and exact Torch/Torchvision/CUDA pre/post checks together.
  Copy and execute the verifier only after these cacheable layers.
- Update packaging documentation and contract regressions. Lock and pins unchanged.

## Metadata audit

Sartre audited all 30 selected artifacts through official PyPI release JSON plus
published wheel `.metadata`, matching each selected artifact's release digest to
the lock. This did not download/re-hash all wheel binaries again.

- Ultralytics 8.4.177: `opencv-python!=4.13.0.90,>=4.7.0`.
- SceneDetect 0.7.2: `opencv-python` (unconstrained).
- No other OpenCV aliases or unsatisfied active requirements were found against
  the lock plus supplied base Torch/Torchvision versions for Linux ARM64/Python
  3.10 without extras. TorchAudio declares no Torch requirement.
- Base image dependency closure and unknown scipy/safetensors/aiohttp versions
  remain target-side checks. Metadata compatibility does not prove native ABI.
- Unchanged lock SHA-256:
  `25cac461f9eada19a3fb7dc42fa6efa05df7d3007a312a6f330b32aa4edffadf`.

Sources: <https://pypi.org/pypi/ultralytics/8.4.177/json>,
<https://pypi.org/pypi/scenedetect/0.7.2/json> and corresponding selected wheel metadata.

## Verification

From `backend`:

```text
.venv/Scripts/python.exe -m py_compile ../infra/gx10-director/verify_runtime.py tests/contract/test_gx10_director_dependencies.py
.venv/Scripts/python.exe -m pytest tests/contract/test_gx10_director_dependencies.py --collect-only -q
.venv/Scripts/python.exe -m pytest tests/unit/test_gx10_node_package.py tests/contract/test_gx10_director_dependencies.py -q
.venv/Scripts/ruff.exe check ../infra/gx10-director/verify_runtime.py tests/contract/test_gx10_director_dependencies.py
```

Syntax and collection passed; 184 focused tests passed; Ruff passed.
`git diff --check` passed; no lock changes. Initial expanded contract suite before
implementation: 24 failed, 24 passed (including the new interface/cache cases).
Independent source review: Hubble approved the four source/test/documentation
diffs with no blockers; independently ran 128 relevant tests and adversarial
probes plus syntax/collection, targeted Ruff and diff checks. The lead's 184-test
run is separately recorded; the reviewer did not reproduce that exact selection.
Git Bash `bash -n -c` passed for all three extracted Dockerfile RUN instructions
(syntax only; no instruction executed). C2C independently reviewed the five task
files and recorded test evidence, returning `STATE: DONE`, source PASS, 184 tests
passed, GX10 runtime UNVERIFIED. No commit or push was performed.

## Remaining gates

No GX10 operations, Docker builds/runtime changes, model/GPU runs, commits or
pushes in this task. Actual ARM64 build, first-build cache population, second-build
cache hits, native ABI/imports, FFmpeg/audio smoke and H3 qualification remain
unverified here. The first build after layer reordering can rebuild expensive
layers once; later verifier-only changes should reuse them when inputs/cache persist.
Unrelated existing frontend declaration, pytest directory and reconnect image
are preserved.

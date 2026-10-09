# Independent delivery helper review

Reviewed 2026-10-05. Scope: `backend/apps/api/app/services/delivery_presets.py`, `backend/tests/unit/test_delivery_presets.py`, and `backend/tests/integration/test_delivery_geometry.py`, against the production plan sections 10.2, 12, Phase 7, K–M, and `tasks/archive/reports/production-delivery-brief.md`. The brief/user ruling controls the truthful ultrawide name/64:27 ratio and bounds. Pending API integration, enhancement, assembly/audio, Phase 0, and GPU qualification are excluded. No implementation edits, subagents, or commits.

Final-byte follow-up: reviewed extraction of plain `find_binaries()` (lines 13-22) and the `binaries` fixture wrapper (25-27). Discovery, explicit skip behavior, geometry logic, and assertions are preserved. No additional finding from the refactor; the P2 SAR finding remains. The source fixture cited below is now at lines 47-49. Scoped pytest and Ruff were rerun after extraction: 28 passed in 0.57s, Ruff check passed and all three files already formatted. Helper and unit-test hashes remain unchanged. Prior SAR/validation probe evidence is retained for the unchanged helper.

## Findings

- **[P2] Preserve source display aspect ratio before resetting SAR** — `backend/apps/api/app/services/delivery_presets.py:42–43`. The scale filter fits/crops according to stored pixel dimensions without accounting for non-square source pixels; the subsequent `setsar=1` then distorts the displayed image. This violates the plan's preserve-aspect/never-stretch requirement (1191–1193, 2415–2416). Reproduced on real FFmpeg using a 320×320 H.264 source with SAR 2:1 / DAR 2:1, blue background, and a central 160×320 red stripe (displayed as a square). Both modes produced a 256×256 SAR 1:1 output with a 128×256 red stripe, red top-center, and blue left-center. FIT_PAD should preserve the 2:1 display ratio with approximately 256×128 content and black top/bottom; CENTER_CROP should scale to approximately 512×256 before cropping, removing the blue side margins. Normalize square pixels using source SAR during scaling before pad/crop, and add a real non-square-SAR regression case. The current integration fixture at `backend/tests/integration/test_delivery_geometry.py:43–45` only exercises SAR 1:1, so its passing pixel assertions do not detect this defect.

No other actionable spec or quality findings in the scoped files. Static review confirms the seven canonical presets, exact 64:27 ultrawide, legacy 1080×1920 default/raw resolution, explicit conflict rejection, supported FPS/modes, and even axis/area bounds; all presets fit the ruling.

## Actual independent verification

Commands below ran from `D:/project/ai-video-studio/backend`:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_delivery_presets.py tests/integration/test_delivery_geometry.py -q -p no:cacheprovider
ruff check --no-cache apps/api/app/services/delivery_presets.py tests/unit/test_delivery_presets.py tests/integration/test_delivery_geometry.py
ruff format --check --no-cache apps/api/app/services/delivery_presets.py tests/unit/test_delivery_presets.py tests/integration/test_delivery_geometry.py
```

- Pytest: **28 passed in 0.63s** (26 unit cases, 2 actual CPU FFmpeg cases; no skips). Those integration cases measured 256×256, 25 FPS, SAR 1:1, H.264/yuv420p and decoded pad/crop boundary pixels.
- Ruff: **All checks passed**; **3 files already formatted**; exit 0.
- Additional Python stdin probes: NaN width, infinite height/FPS rejected with ValueError; 256×256, 4096×256 and 2560×1440 accepted; 2560×1442 rejected by area limit.
- SAR reproduction: Python stdin with `tempfile.TemporaryDirectory` and actual subprocess encode/probe/decode calls, exit 0. Source lavfi `color=blue:size=320x320:rate=24:duration=1`; source filter `drawbox=x=80:y=0:w=160:h=320:color=red:t=fill,setsar=2`; encode libx264/yuv420p, then apply each helper filter for 256×256/25 FPS. ffprobe confirmed source SAR/DAR 2:1 and output SAR/DAR 1:1. Decoded RGB24 red ranges were x=64..191 and y=0..255 for both modes; top-center RGB=(253,0,0), left-center=(0,0,254).
- Binary: `workspace/tools/ffmpeg-9.0.2/ffmpeg-9.0.2-essentials_build/bin/ffmpeg.exe`, version `9.0.2-essentials_build-www.gyan.dev`.

Controller-reported missing-module RED was not rerun or independently verified. No GPU, production qualification, or future integration claim is made. Reviewed file SHA256 values were unchanged before report creation:

```text
delivery_presets.py       385E5D892E2A29B1A7E111D4B28CE7E110DF9CFCBE5B80FDAF0C810478A954B6
test_delivery_presets.py  75EEB3B3961BBDC5970D22D10B7C8B1C85F530A58C333B1435BA0B65712E8096
test_delivery_geometry.py 8F41C1CDDB5AE4E6710769307B4117FB3AF7201235B7E12D60C5C76142258BA4
```

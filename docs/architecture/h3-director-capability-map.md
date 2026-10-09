# H3 Director capability map

Director pin: `a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb`.

This document separates source capability from production qualification.

## Task and reference capability

| Capability | Source evidence | Product decision |
|---|---|---|
| T2V | **DIRECTOR_SOURCE** task `t2v` | expose after runtime qualification |
| I2V first frame | **DIRECTOR_SOURCE** i2v external group | expose after qualification |
| FL2V first+last | **DIRECTOR_SOURCE** `fl2v` | expose after qualification |
| FL2V last-only | **DIRECTOR_SOURCE** external group supports last-only | preserve legacy `i2v_last` as business alias |
| R2V | **DIRECTOR_SOURCE** `r2v` with image/video/audio references | qualify each reference combination |
| V2V | **DIRECTOR_SOURCE** `v2v` source-video timeline mode | new business mode; qualify separately |
| RV2V | **DIRECTOR_SOURCE** `rv2v` | new business mode; qualify separately |
| Mixed | **DIRECTOR_SOURCE** task key exists | keep internal/provider-facing until product semantics are defined |
| simultaneous i2v_groups + r2v_groups | **DIRECTOR_SOURCE** forbidden | reject during plan building |
| reference images | **DIRECTOR_SOURCE** max 9 | advertised max <= 9 and <= qualified max |
| reference videos | **DIRECTOR_SOURCE** max 3 | advertised max <= 3 and <= qualified max |
| reference audios | **DIRECTOR_SOURCE** max 3 | advertised max <= 3 and <= qualified max |

Prompt tags `<Picture N>`, `<Video N>` and `<Audio N>` are provider semantics. Stable reference ordering participates in the frozen execution hash.

## Continuity and enhancement

| Capability | Source evidence | Product decision |
|---|---|---|
| Motion Context | **DIRECTOR_SOURCE** windows 5/22/39/56, default 22 | execute through aggregate Director run |
| continuity task coverage | **DIRECTOR_SOURCE** t2v/i2v/fl2v/r2v/v2v/rv2v | qualify representative combinations |
| Refine | **DIRECTOR_SOURCE** internal Director pass with refine/upscale/latent-upscale paths | model as Director config, not FFmpeg resize |
| FaceRefine | **DIRECTOR_SOURCE** detect -> track/crop -> H3 re-sample -> paste | optional qualified capability |
| face detector | **DIRECTOR_SOURCE** ultralytics, default `face_yolov8m.pt` | dependency belongs to release identity |

## Resolution and aspect ratio

| Item | Source evidence | Status |
|---|---|---|
| width/height 32..8192 | main node schema | SOURCE_CAPABILITY |
| dimensions multiple of 32 | source validation/snap | SOURCE_CAPABILITY |
| aspect presets 1:1, 2:3, 3:2, 3:4, 4:3, 9:16, 16:9, 21:9 + Custom | Director browser | SOURCE_CAPABILITY |
| MP selector 0.1..16.0 MP | Director browser | SOURCE_CAPABILITY |
| 1080p generation | no executed evidence in this design phase | REQUIRES_RUNTIME_VERIFICATION |
| 2K generation | no executed evidence | REQUIRES_RUNTIME_VERIFICATION |
| 4K generation | no executed evidence | REQUIRES_RUNTIME_VERIFICATION |

Schema maxima are not a safe hardware envelope.

## Audio and formats

**DIRECTOR_SOURCE:** internal AV code has 32 kHz fallback/model-audio-rate handling in several paths. Separately, `director/audio_export.py` defines `SILENT_SAMPLE_RATE = 44100` and explicitly normalizes reference audio to 44.1 kHz stereo before mux. Therefore “native stereo 32 kHz” is not a universal exported-media contract.

Source upload/reference support includes at least:

- images: PNG/JPG/JPEG/WEBP/GIF/BMP/TIF/TIFF;
- videos: MP4/WEBM/MOV/MKV/AVI, with more server-side extensions;
- audio: WAV/MP3/FLAC/OGG/M4A/AAC, with WMA in server-side paths.

Director segment export is MP4-oriented. Product delivery formats remain an ai-video-studio delivery policy.

## Master-spec discrepancy table

| SPEC CLAIM | DIRECTOR SOURCE EVIDENCE | STATUS | DECISION |
|---|---|---|---|
| canvas up to 8192 | widget/schema max 8192, 32-grid | CONFIRMED as UI/schema bound | do not treat as runtime-safe max |
| selector up to 16 MP | browser selector clamps 0.1..16 MP | CONFIRMED as UI bound | advertise qualified subset |
| Full HD / 2K / 4K production generation | generic custom canvas exists; no executed safety proof | REQUIRES_RUNTIME_VERIFICATION | qualify each release/profile |
| Motion Context 22 frames | `DEFAULT_CONTEXT_FRAMES = 22`; choices 5/22/39/56 | CONFIRMED | default 22 when enabled |
| Face Refine YOLOv8 tracking | ultralytics detector + per-frame detection/track-crop/stitch path | PARTIALLY_CONFIRMED | capability exists; quality/perf needs runtime evidence |
| native stereo 32 kHz output | internal 32 kHz paths; export normalization uses 44.1 kHz stereo | DIFFERS | distinguish internal/model rate from exported stream |
| PNG/JPG/WEBP input | upload paths include these and more | CONFIRMED subset | backend MIME allowlist stays authoritative |
| MP4/MOV/WEBM input | accepted by Director paths | CONFIRMED subset | qualify codecs separately |
| FLAC/WAV/MP3 input | accepted by Director paths | CONFIRMED subset | qualify decode paths separately |
| 9 image refs | `MAX_REFERENCE_IMAGES = 9` | CONFIRMED | product max <= 9 |
| 3 video refs | `MAX_REFERENCE_VIDEOS = 3` | CONFIRMED | product max <= 3 |
| 3 audio refs | `MAX_REFERENCE_AUDIOS = 3` | CONFIRMED | product max <= 3 |
| ~43.6 GB static / ~91.6 GB peak | no runtime evidence in inspected source | NOT_FOUND / REQUIRES_RUNTIME_VERIFICATION | benchmark on target GPU |
| 35-45 s per 5 s 1080p shot | no executed evidence | NOT_FOUND / REQUIRES_RUNTIME_VERIFICATION | benchmark |
| ~700 videos/day | no executed evidence | NOT_FOUND / REQUIRES_RUNTIME_VERIFICATION | derive from measured throughput |

## Capability response model

Recommended backend shape:

```json
{
  "provider": "minimax_h3_director",
  "provider_revision": "a8f57b8e...",
  "source_capabilities": {},
  "qualified_capabilities": {},
  "qualification_id": "...",
  "unqualified_reasons": {}
}
```

The frontend renders runnable controls from `qualified_capabilities`.

# MiniMax H3 Director: pinned source analysis

## Evidence boundary

This is documentation-only integration research dated 2026-10-06. The reference checkout is `D:/project/ComfyUI_MiniMaxH3_Director`, repository [AIMixer/ComfyUI_MiniMaxH3_Director](https://github.com/AIMixer/ComfyUI_MiniMaxH3_Director), clean branch `main`, HEAD `a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb` (verified by read-only Git commands with `-c safe.directory=D:/project/ComfyUI_MiniMaxH3_Director -C D:/project/ComfyUI_MiniMaxH3_Director`). All source links below pin that SHA. The package declares Apache-2.0 in [`__init__.py`, module header, L1-L5](https://github.com/AIMixer/ComfyUI_MiniMaxH3_Director/blob/a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb/__init__.py#L1-L5); license-file presence and dependency metadata are discussed below.

The two attachments were read completely: [research request](C:/Users/Admin/.codex/attachments/8e3d0250-d2e8-433a-9a3b-c07e969c9b1b/Pasted%20text.txt) and [SPEC-ENG-H3-UNCONSTRAINED-V3](C:/Users/Admin/.codex/attachments/59d83a6f-8549-4d0e-92a6-ec77a0bc487f/Pasted%20text.txt). The current instruction narrows their wider architecture assignment to this one file; current studio implementation/design remains the parent task's responsibility. Its dirty working tree is authoritative and was not refactored. No model execution, benchmark, migration, commit, push, reset, or clean was performed.

**DIRECTOR_SOURCE** denotes behavior established by the pinned code; **MASTER_SPEC** denotes attachment proposals; **INFERENCE** denotes integration implications; **REQUIRES_RUNTIME_VERIFICATION / UNVERIFIED** denotes an untested property. Reading code establishes implemented paths, not successful execution or quality on a particular ComfyUI/model/GPU build. GitHub fetching was unavailable in this session; the clean local source was inspected directly and permalinks were constructed from its verified origin/SHA and exact line numbers.

## Registration and execution boundary

**DIRECTOR_SOURCE:** `NODE_CLASS_MAPPINGS` exports the following API `class_type` keys. Display names are separate from these keys; the legacy key is an alias, not a second implementation. Source: [`__init__.py`, mappings/imports, L7-L51](https://github.com/AIMixer/ComfyUI_MiniMaxH3_Director/blob/a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb/__init__.py#L7-L51).

| API class_type | Python class / role |
|---|---|
| `MiniMaxH3Director` | Main timeline planner/executor |
| `ComfyMiniMaxH3Director` | Alias of `MiniMaxH3Director` |
| `MiniMaxH3DirectorGroupImageToVideo` | External keyframe/generation group |
| `MiniMaxH3DirectorGroupReferenceToVideo` | External reference group |
| `MiniMaxH3DirectorGroupsCombine` | Ordered group concatenation |
| `MiniMaxH3DirectorRefine` | Second-pass configuration provider |
| `MiniMaxH3DirectorFaceRefine` | Face-pass configuration provider |
| `MiniMaxH3DirectorSelfLift` | Optional progressive first-pass configuration |
| `MiniMaxH3DirectorSemanticBridge` | Optional conditioning-token transformation configuration |
| `MiniMaxH3DirectorConditioning` | Wrapper returning positive conditioning and latent |
| `MiniMaxH3DirectorPlannerConditioning` | Same wrapper plus task-mode report string |

The main `FUNCTION` is `execute`, category `MiniMaxH3`; its required model/VAEs/CLIP are graph connections. It receives configuration from Refine and Face Refine and runs those passes itself. It does not expose a direct `first_frame`, `last_frame`, `source_video`, `motion_context`, `audio_mode`, `aspect_ratio`, or `filename_prefix` socket. Those concepts reside in groups, timeline JSON, configuration nodes, or downstream exporters. Sources: [`nodes/director.py`, `INPUT_TYPES`, L50-L196](https://github.com/AIMixer/ComfyUI_MiniMaxH3_Director/blob/a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb/nodes/director.py#L50-L196), [`execute`, L274-L371](https://github.com/AIMixer/ComfyUI_MiniMaxH3_Director/blob/a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb/nodes/director.py#L274-L371).

## Complete main-node input contract

**DIRECTOR_SOURCE:** The required schema below combines `MiniMaxH3Director.INPUT_TYPES`, `director_timeline_required_inputs`, and `timeline_required_inputs`. Bounds are declared schema/widget bounds unless separately called runtime constraints. Sources: [`nodes/director.py`, L17-L71](https://github.com/AIMixer/ComfyUI_MiniMaxH3_Director/blob/a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb/nodes/director.py#L17-L71), [`nodes/director_common.py`, `timeline_required_inputs`, L29-L77](https://github.com/AIMixer/ComfyUI_MiniMaxH3_Director/blob/a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb/nodes/director_common.py#L29-L77).

| Required input | Type | Default / bounds |
|---|---|---|
| `model` | `MODEL` | Connected H3 UNET; no model filename widget |
| `video_vae` | `VAE` | Connected H3 video VAE |
| `audio_vae` | `VAE` | Required in main schema, even when output audio is muted |
| `clip` | `CLIP` | Connected minimax/Qwen3-VL text encoder |
| `task_type` | combo | Default t2v option; exact options are formatted `key - localized label`, including `t2v`, `i2v`, `fl2v`, `r2v`, `v2v`, `rv2v`, `mixed` |
| `global_prompt` | `STRING` | `A cinematic scene with natural motion and synchronized ambience`; multiline |
| `bd_grp_sample` | `BDGROUP` | Cosmetic grouping widget; consumed by `**kwargs`, not generation intent |
| `cfg` | `FLOAT` | 1.0; 0.0..30.0, step 0.01 |
| `seed` | `INT` | 0; 0..18446744073709551615; UI control-after-generate |
| `frame_rate` | `FLOAT` | 24.0; 1.0..240.0, step 0.01; tooltip says H3 trained at 24 |
| `width` | `INT` | 864; 32..8192, step 32 |
| `height` | `INT` | 480; 32..8192, step 32 |
| `ref_max_size` | `INT` | 864; 32..8192, step 32 |
| `total_frames` | `INT` | 124; 5..100000; timeline total, not per-shot allowance |
| `timeline_data` | `STRING` | Empty; serialized JSON string, multiline |

The task combo builder excludes the internal `default` option, includes `mixed`, and normalizes decorated labels with `resolve_task_key`; this does not prove bare keys pass ComfyUI combo validation. **INFERENCE:** obtain exact combo values from the installed `/object_info` or pinned builder rather than inventing display labels. Source: [`lib/task_prompts.py`, `TASK_PROMPT_SPECS`, `task_type_combo_options`, `resolve_task_key`, L16-L108](https://github.com/AIMixer/ComfyUI_MiniMaxH3_Director/blob/a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb/lib/task_prompts.py#L16-L108).

**DIRECTOR_SOURCE:** All optional inputs are enumerated here. Sources: [`nodes/director.py`, `INPUT_TYPES`, L72-L196](https://github.com/AIMixer/ComfyUI_MiniMaxH3_Director/blob/a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb/nodes/director.py#L72-L196), [`nodes/director_common.py`, `director_perf_inputs`, L80-L148](https://github.com/AIMixer/ComfyUI_MiniMaxH3_Director/blob/a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb/nodes/director_common.py#L80-L148).

| Optional input | Type | Default / bounds |
|---|---|---|
| `i2v_groups` | `MMX_DIR_GROUP` | Unconnected; external generation/keyframe group pack |
| `r2v_groups` | `MMX_DIR_GROUP` | Unconnected; external reference group pack |
| `semantic_bridge` | `MMX_DIR_SEMANTIC_BRIDGE` | Unconnected |
| `selflift` | `MMX_DIR_SELFLIFT` | Unconnected |
| `refine` | `MMX_DIR_REFINE` | Unconnected |
| `face_refine` | `MMX_DIR_FACE_REFINE` | Unconnected |
| `bd_grp_advanced` | `BDGROUP` | Cosmetic grouping widget |
| `steps` | `INT` | 25; 1..200 |
| `sampler` | installed `KSampler.SAMPLERS` combo | `res_multistep` |
| `scheduler` | installed `KSampler.SCHEDULERS` combo | `simple` |
| `shift_video` | `FLOAT` | 12.0; 0.01..100.0, step 0.01 |
| `shift_audio` | `FLOAT` | 3.0; 0.01..100.0, step 0.01 |
| `bd_grp_perf` | `BDGROUP` | Cosmetic grouping widget |
| `clear_vram_between_segments` | `BOOLEAN` | true |
| `clear_vram_before_refine` | `BOOLEAN` | false |
| `clear_vram_before_face_refine` | `BOOLEAN` | false |
| `cache_frames_codec` | combo | `raw` or `ffv1`; default `raw` |
| `export_source_images` | `BOOLEAN` | false |
| `export_pre_face_refine` | `BOOLEAN` | false |
| `sigmas` | `SIGMAS`, forceInput | Unconnected; external schedule |

Hidden input: `unique_id: UNIQUE_ID`. `execute` deletes remaining `**kwargs`, normalizes cache codec to raw/ffv1, and passes sampling controls into the executor. `VALIDATE_INPUTS` explicitly checks connected model/VAE/CLIP, SIGMAS, Semantic Bridge, SelfLift and Face Refine types and otherwise returns true; it is not a complete business-policy validator. Sources: [`nodes/director.py`, L195-L232](https://github.com/AIMixer/ComfyUI_MiniMaxH3_Director/blob/a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb/nodes/director.py#L195-L232), [`execute`, L274-L354](https://github.com/AIMixer/ComfyUI_MiniMaxH3_Director/blob/a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb/nodes/director.py#L274-L354).

## Complete main-node output contract

**DIRECTOR_SOURCE:** The eight positional outputs and list flags are fixed by [`nodes/director.py`, return metadata, L244-L257](https://github.com/AIMixer/ComfyUI_MiniMaxH3_Director/blob/a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb/nodes/director.py#L244-L257).

| Index | Name | Type | OUTPUT_IS_LIST | Meaning |
|---|---|---|---|---|
| 0 | `images` | `IMAGE` | true | Final image batches, after configured passes; may be blocked on confirmation hold |
| 1 | `audio` | `AUDIO` | true | Corresponding audio dictionaries |
| 2 | `fps` | `FLOAT` | false | Resolved plan FPS |
| 3 | `frame_count` | `INT` | false | Export frame count, with special handling for released segments |
| 4 | `source_images` | `IMAGE` | true | Source comparison only if requested; otherwise neutral placeholders |
| 5 | `report` | `STRING` | false | Planning/execution/export notes |
| 6 | `images_pre_refine` | `IMAGE` | true | First-pass batches; may share/fall back to final images |
| 7 | `images_pre_face_refine` | `IMAGE` | true | Pre-stitch comparison if enabled/requested; otherwise ExecutionBlocker |

`IMAGE` batches are four-dimensional frame tensors. Layout is one combined batch unless segment export or a non-video batch requires split outputs. Continuity with `keepTail` can preserve more frames than the UI timeline total. Released segment pixel slots may be omitted from IMAGE output while full MP4s stay in the Director export directory; summed frame counts can therefore describe more media than remaining tensors. Sources: [`nodes/director_common.py`, `_layout_image_batches`, L407-L426](https://github.com/AIMixer/ComfyUI_MiniMaxH3_Director/blob/a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb/nodes/director_common.py#L407-L426), [`finalize_director_outputs`, L429-L676](https://github.com/AIMixer/ComfyUI_MiniMaxH3_Director/blob/a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb/nodes/director_common.py#L429-L676). **INFERENCE:** downstream collection must honor batch/list behavior and actual exported artifacts, not assume one filename or trust comparison sockets as generated results.

## Resolution: schema, browser selector, and runtime checks

**DIRECTOR_SOURCE:** Main widget limits are 32..8192 on each dimension; browser aspect presets are 1:1, 2:3, 3:2, 3:4, 4:3, 9:16, 16:9, 21:9 plus Custom. The browser MP selector clamps 0.1..16.0 and computes pixels with `megapixels * 1024 * 1024`, then rounds each dimension independently to a multiple of 32. These are not an H3 hardware safety envelope or an exact ratio guarantee. Sources: [`nodes/director_common.py`, L61-L70](https://github.com/AIMixer/ComfyUI_MiniMaxH3_Director/blob/a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb/nodes/director_common.py#L61-L70), [`web/js/minimax_gen_timeline.js`, constants/clamp, L5-L32](https://github.com/AIMixer/ComfyUI_MiniMaxH3_Director/blob/a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb/web/js/minimax_gen_timeline.js#L5-L32), [`resolutionFromSelector`, L143-L164](https://github.com/AIMixer/ComfyUI_MiniMaxH3_Director/blob/a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb/web/js/minimax_gen_timeline.js#L143-L164).

Custom updates fixed output width/height, node width/height and ref maximum; it does not recompute dimensions from MP. Python `snap_dimension` uses nearest-grid `max(32, round(value/32)*32)`; Python `round` uses ties-to-even, whereas browser `Math.round` differs at half-grid ties. `assert_minimax_canvas` requires positive dimensions divisible by 32; it does not enforce 8192 or 16 MP. Fixed output resolution is snapped by `resolve_output_dimensions`; source long-edge resolution preserves/downscales source proportions with grid snap. Sources: [`web/js/minimax_timeline.js`, `applyCustomResolution`, L6736-L6764](https://github.com/AIMixer/ComfyUI_MiniMaxH3_Director/blob/a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb/web/js/minimax_timeline.js#L6736-L6764), [`lib/image_prep.py`, `snap_dimension`/`assert_minimax_canvas`, L9-L33](https://github.com/AIMixer/ComfyUI_MiniMaxH3_Director/blob/a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb/lib/image_prep.py#L9-L33), [`resolve_output_dimensions`, L159-L192](https://github.com/AIMixer/ComfyUI_MiniMaxH3_Director/blob/a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb/lib/image_prep.py#L159-L192).

**INFERENCE:** freeze explicit normalized generation dimensions and independently freeze Refine targets and delivery dimensions. Do not infer 4K safety, FPS quality, concurrency, or memory capacity from schema maxima. Runtime qualification is still required.

## Conditioning and model ownership

**DIRECTOR_SOURCE:** `run_minimax_conditioning` dispatches to ComfyUI's `MiniMaxH3ReferenceToVideo` for r2v/v2v/rv2v or supplied reference tensors; it requires `audio_vae` and passes prompt, dimensions, length and reference dictionaries by keyword. Otherwise it calls `MiniMaxH3ImageToVideo` with optional first/last frame. It returns `positive, [], latent, hint`. The package does not load or switch UNETs here: the main connected MODEL is reused by the executor. Sources: [`nodes/conditioning.py`, `_load_minimax_nodes`, L41-L52](https://github.com/AIMixer/ComfyUI_MiniMaxH3_Director/blob/a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb/nodes/conditioning.py#L41-L52), [`run_minimax_conditioning`, L108-L175](https://github.com/AIMixer/ComfyUI_MiniMaxH3_Director/blob/a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb/nodes/conditioning.py#L108-L175), [`director/executor_core.py`, `_run_one_segment`, L610-L624](https://github.com/AIMixer/ComfyUI_MiniMaxH3_Director/blob/a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb/director/executor_core.py#L610-L624).

**INFERENCE:** the graph builder owns qualification/selection of FL2VA versus Ref2VA weights; selecting `task_type` alone is not a model-loader switch. Mixed segments can change conditioning paths while sharing a MODEL, so model compatibility must be qualified separately. Sampling applies H3 sigma shifts and uses BasicGuider when cfg≈1 and negative conditioning is empty, otherwise CFGGuider. Source: [`director/core_sampling.py`, `_use_basic_guider`, L71-L75](https://github.com/AIMixer/ComfyUI_MiniMaxH3_Director/blob/a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb/director/core_sampling.py#L71-L75), [`sample_single_stage`, L137-L185](https://github.com/AIMixer/ComfyUI_MiniMaxH3_Director/blob/a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb/director/core_sampling.py#L137-L185).

The separate conditioning wrappers expose required `clip`, `vae`, `prompt`, `width`, `height`, `length`, optional `audio_vae`, first/last frame, `reference_image_0`..`reference_image_8`, and ref-image sizing. Their outputs are `CONDITIONING,LATENT`, or additionally task-mode `STRING`. They do not replace the main Director execution contract. Source: [`nodes/conditioning.py`, `_shared_optional_inputs`, L9-L38](https://github.com/AIMixer/ComfyUI_MiniMaxH3_Director/blob/a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb/nodes/conditioning.py#L9-L38), [wrapper classes, L178-L244](https://github.com/AIMixer/ComfyUI_MiniMaxH3_Director/blob/a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb/nodes/conditioning.py#L178-L244).

## External groups, task behavior, and prompt transforms

**DIRECTOR_SOURCE:** the task family includes `t2v`, `i2v`, `fl2v`, `r2v`, `v2v`, `rv2v`, and `mixed`. The task-system-prompt seam does not prepend a T5-style task prefix to the positive prompt. External i2v groups cover prompt-only generation, first-only I2V, last-only FL2V, and first+last FL2V. External r2v groups accept ordered image/video/audio references. The planner rejects a run that supplies both external i2v groups and external r2v groups.

V2V/RV2V are timeline/source-video modes rather than aliases for external group sockets. Group durations are converted to MiniMax-aligned frame counts; FL2V has a minimum aligned segment length of five frames.

**DIRECTOR_SOURCE:** prompt text may change after ai-video-studio has accepted it. External group prompts are trimmed; common R2V prompt text may be prepended to segment text; FL2V/I2V and R2V have reinforcement paths; R2V reference tags such as `<Picture N>`, `<Video N>`, and `<Audio N>` can be inserted/normalized, and unusable audio tags may be removed.

**INFERENCE:** integration must preserve an immutable accepted/user prompt and separately track Director-effective conditioning metadata. It must not promise that the provider-conditioned string is byte-identical to the accepted prompt.

## Timeline and source-video behavior

**DIRECTOR_SOURCE:** blank `timeline_data` is expanded to a versioned default timeline structure containing output geometry, video/global blocks, and segment data. Source-video modes resolve source media from the timeline plan. The planner distinguishes I2V/FL2V keyframes, V2V source clips, and R2V references.

**INFERENCE:** ai-video-studio should freeze canonical timeline JSON as part of the provider execution contract and distinguish `SOURCE_VIDEO` from reference video roles.

## Motion Context continuity

**DIRECTOR_SOURCE:** [`director/h3_motion_context.py`](https://github.com/AIMixer/ComfyUI_MiniMaxH3_Director/blob/a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb/director/h3_motion_context.py) defines:

```text
CONTEXT_FRAME_CHOICES = (5, 22, 39, 56)
DEFAULT_CONTEXT_FRAMES = 22
DEFAULT_AUDIO_CONTEXT_FRAMES = 24
CONTINUITY_TASK_KEYS = {t2v, i2v, fl2v, r2v, v2v, rv2v}
CONTINUITY_PIPELINE_ID = minimax_h3_motion_context_v9
```

The implementation pins the previous segment AV tail into the next segment's conditioning, trims the repeated prefix from decoded output, and contains alignment logic for the H3 latent frame cycle. This is a native multi-segment execution concern.

**INFERENCE:** `SceneSpec.continuity = CUT` should disable previous-segment context at that boundary; `CONTINUOUS` should map to Director continuity metadata. Preserving native behavior requires an aggregate durable Director execution boundary rather than unrelated per-scene prompts.

## Reference counts and ordering

**DIRECTOR_SOURCE:** the pinned helpers define exact maxima:

- `MAX_REFERENCE_IMAGES = 9` in `lib/ref_images.py`;
- `MAX_REFERENCE_VIDEOS = 3` in `lib/ref_videos.py`;
- `MAX_REFERENCE_AUDIOS = 3` in `lib/ref_audios.py`.

The helpers preserve slot ordering and map provider tags one-based (`<Picture 1>`, `<Video 1>`, `<Audio 1>`) over zero-based internal slots.

## Refine and FaceRefine

**DIRECTOR_SOURCE:** `MiniMaxH3DirectorRefine` and `MiniMaxH3DirectorFaceRefine` are configuration nodes connected to the main Director. Director executes the configured passes internally rather than requiring ai-video-studio to schedule separate Comfy jobs. Refine includes refine/upscale/latent-upscale paths and source contains Lanczos, NVIDIA RTX VSR, and H3 latent upscaling implementation paths.

FaceRefine performs face detection/tracking/cropping, H3 re-sampling of the crop, and stitching/pasting back into the decoded frames. The detector loader searches ultralytics model locations; the default model name is `face_yolov8m.pt`. FaceRefine canvas settings support manual and auto modes, with source-side capped behavior including `auto_capped_768`.

**INFERENCE:** H3 Refine target geometry, FaceRefine crop canvas, and ai-video-studio FFmpeg delivery geometry are separate contracts. FFmpeg resize is not a substitute for Director Refine.

## Audio rates and export semantics

**DIRECTOR_SOURCE:** audio behavior is not represented by one universal 32 kHz output constant. Internal AV paths contain 32 kHz fallbacks and query `audio_vae.audio_sample_rate` when encoding continuity/reference audio. Separately, [`director/audio_export.py`](https://github.com/AIMixer/ComfyUI_MiniMaxH3_Director/blob/a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb/director/audio_export.py) defines `SILENT_SAMPLE_RATE = 44100`, and its reference-audio normalization explicitly forces 44.1 kHz stereo before mux.

Therefore the master-spec statement “native stereo 32 kHz” **DIFFERS** from the inspected export contract. Generated/internal H3 audio rate and final muxed/exported stream metadata must be measured and stored separately.

## Upload/reference formats versus generated exports

**DIRECTOR_SOURCE:** browser/server input paths accept at least:

- images: PNG/JPG/JPEG/WEBP/GIF/BMP/TIF/TIFF;
- video: MP4/WEBM/MOV/MKV/AVI, with additional server-side M4V/MPG/MPEG/MTS/TS support;
- audio: WAV/MP3/FLAC/OGG/M4A/AAC, with server-side WMA support.

These are input/reference capabilities. Segment export code and diagnostics are MP4-oriented, and internal lossless frame cache may use FFV1/MKV. **INFERENCE:** ai-video-studio must own the public delivery-format contract and must not infer generated output containers from upload extension lists.

## Output mode and collector implication

**DIRECTOR_SOURCE:** export mode can produce a combined output or segment-oriented results. In segment mode the executor may release prior segment pixel tensors after writing segment MP4s. Optional pre-refine and pre-face-refine outputs add more artifact roles.

**INFERENCE:** the ai-video-studio collector needs an artifact-manifest contract and measured media probes. A single guessed Comfy filename or remaining IMAGE tensor cannot be the durable output source of truth.

## Runtime claims not established by source inspection

The following remain **REQUIRES_RUNTIME_VERIFICATION** for the target deployment:

- safe/performant 1080p, 2K, or 4K generation;
- VRAM figures such as ~43.6 GB static or ~91.6 GB peak;
- 35-45 seconds for a 5-second 1080p shot;
- ~700 videos/day;
- quality/performance of Motion Context, Refine, or FaceRefine on the production model/dependency set.

Source-visible bounds such as 8192 dimensions or 16 MP are UI/schema capabilities only.

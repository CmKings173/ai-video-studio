# H3 qualification research — 2026-10-05

**Research result: native local integration is source-backed; target execution is NOT_RUN.** Native ComfyUI H3 nodes load local H3-Base weights and generate joint video/audio. ComfyUI Partner H3 nodes call hosted APIs. H3-Context-IR and H3-Regenerate-2K are hosted components in the inspected MiniMax release. The official R2V template and native Autogrow schema support construction of a full dynamic references graph, but no live target schema, exported mixed-media graph, or GPU execution was obtained.

This report resumes `tasks/archive/reports/h3-qualification-brief.md` using the downloaded artifacts. All 14 original upstream source/template files were reused and their recorded SHA-256 hashes matched. Writes are limited to this report and research artifacts. No production edits, subagents, installs, model payload downloads, service starts, prompt submissions, GPU jobs, commits, or pushes were performed. Final writing resumed after the earlier approval-review usage-limit failure; the observation times below have not been relabeled as new probes.

## 1. Evidence date, provenance, and reachability

Original source retrievals: **2026-10-05 10:33–10:36 UTC**. Resumption source-head checks: **12:57:47 UTC**. Supplemental sources and model metadata: **12:57:48–13:02:30 UTC**. Runtime recheck: **12:57:49–12:58:09 UTC**. Asia/Bangkok is UTC+7. These are dated snapshots, not claims that repository heads remained unchanged through final report writing.

| Primary source | Inspected commit | Commit date UTC / reachability |
| --- | --- | --- |
| [MiniMax-AI/MiniMax-H3](https://github.com/MiniMax-AI/MiniMax-H3/tree/d21241f0a4b3acbb34c97dae47fa417b7065e438) | `d21241f0a4b3acbb34c97dae47fa417b7065e438` | 2026-08-15 08:31:16; main recheck HTTP 200, same SHA |
| [Comfy-Org/ComfyUI](https://github.com/Comfy-Org/ComfyUI/tree/5c460d8172fe30761ff67c0df3d5643bb74e0d70) | `5c460d8172fe30761ff67c0df3d5643bb74e0d70` | 2026-10-05 01:20:55; master recheck HTTP 200, same SHA |
| [Comfy-Org/workflow_templates](https://github.com/Comfy-Org/workflow_templates/tree/0e5c5efb32ba6f3365d6da07da64aaf668157042) | `0e5c5efb32ba6f3365d6da07da64aaf668157042` | 2026-10-02 19:28:52; main recheck HTTP 200, same SHA |
| [Comfy-Org/docs](https://github.com/Comfy-Org/docs/tree/fd96fcede2e8be50670b386a62ab19e87e809f99) | `fd96fcede2e8be50670b386a62ab19e87e809f99` | 2026-10-05 07:40:51; main recheck HTTP 200, same SHA |
| [vLLM-Omni saved source](https://github.com/vllm-project/vllm-omni/tree/6dd0d1f9310f7598b773c796b434c8000c2816ec) | `6dd0d1f9310f7598b773c796b434c8000c2816ec` | 2026-10-05 09:40:07; saved recipe/test snapshot |
| [vLLM-Omni resumed head](https://github.com/vllm-project/vllm-omni/tree/091b256674afa1bae476553c35ab1d22d155608a) | `091b256674afa1bae476553c35ab1d22d155608a` | 2026-10-05 12:20:25; main HTTP 200, advanced; relevant pipeline, Turbo loader and matrix test bytes unchanged |
| [vllm-project/recipes](https://github.com/vllm-project/recipes/tree/8faeba99ff3445c151d4758abb06060388cba9f3) | `8faeba99ff3445c151d4758abb06060388cba9f3` | 2026-10-05 04:06:48; HTTP 200, unchanged; H3 recipe used here belongs to vllm-omni |

Evidence manifests under `tasks/h3-reference-artifacts/`:

- `artifact-provenance.json`: original immutable source URLs, bytes, retrieval timestamps and SHA-256.
- `artifact-hash-audit.json`: **14/14 original source/template hashes matched** at resumption.
- `source-revisions.json`, `resumption-source-provenance.json`, `follow-up-source-provenance.json`: commit observations and supplementary downloads, including hashes and HTTP status.
- `qualification-static-audit.json`: real template IDs/types, link endpoint inspection, local graph hashes, and arithmetic results. All inspected template scopes have no unresolved link endpoints; this is structural inspection only.
- `runtime-inventory.json`, `runtime-inventory-resumed.json`: original and resumed runtime failures, kept separately.

Pinned model-repository metadata returned HTTP 200 at approximately 13:02:29 UTC:

| Publisher / purpose | Verified revision | Primary metadata URL |
| --- | --- | --- |
| MiniMax original checkpoints/components | `42ed227ee7df40d41602854ae760620d6eb651fe` | [MiniMaxAI metadata](https://huggingface.co/api/models/MiniMaxAI/MiniMax-H3/revision/42ed227ee7df40d41602854ae760620d6eb651fe), 280 listed files |
| Comfy packaged native weights | `e5eb578a89295337b8ff433a035929ce0279e0b6` | [Comfy-Org metadata](https://huggingface.co/api/models/Comfy-Org/MiniMax-H3/revision/e5eb578a89295337b8ff433a035929ce0279e0b6), 37 files |
| LightX2V Turbo adapters | `3ec17a324ced54151364f24f8b5fb6bf7e26414f` | [LightX2V metadata](https://huggingface.co/api/models/lightx2v/Minimax-h3-Turbo/revision/3ec17a324ced54151364f24f8b5fb6bf7e26414f), 18 files |

Metadata responses are saved as `pinned-model-metadata-*.json`; their response hashes are recorded in the follow-up manifest. This establishes **pinned metadata reachability and filenames**, not weight installation, payload SHA verification, access to every weight blob, or latest model-repository heads. No safetensors payload was requested.

## 2. Official local modes and supported envelope

MiniMax releases two BF16, CFG-distilled base checkpoints. **FL2VA** supports text-only or zero/one/two first/last-frame images. **Ref2VA** accepts multimodal references. Published output is 4–15 seconds at 24 FPS with 32 kHz stereo audio and a default 768-pixel short edge. Reference limits are nine images, three videos, three audio clips, and twelve files overall. Video/audio clips are 2–15 seconds each; each modality has its own 15-second aggregate duration limit. Context-IR is hosted, and Regenerate-2K is not open-sourced in this revision. [MiniMax release README](https://github.com/MiniMax-AI/MiniMax-H3/blob/d21241f0a4b3acbb34c97dae47fa417b7065e438/README.md#model-variants-and-input-specifications)

**Reject audio-only Ref2VA at the integration boundary.** Comfy's official Partner reference node requires an image or video; vLLM's H3 recipe explicitly rejects audio-only requests. Native Comfy's permissive reference node does not enforce this restriction. Optional sockets are not a guarantee that every combination is supported. [Partner validation](https://github.com/Comfy-Org/ComfyUI/blob/5c460d8172fe30761ff67c0df3d5643bb74e0d70/comfy_api_nodes/nodes_minimax.py#L1055-L1061), [vLLM limitations](https://github.com/vllm-project/vllm-omni/blob/6dd0d1f9310f7598b773c796b434c8000c2816ec/recipes/MiniMaxAI/MiniMax-H3.md#limitations)

Source mechanics and limits that must not be conflated:

| Topic | Exact native implementation | Practical consequence |
| --- | --- | --- |
| Canvas | `CANVAS_MULTIPLE=32`, `BASE_SHORT_EDGE=768`, `MAX_PIXELS=768*1344=1,032,192` | Validate post-rounding dimensions and area independently of a generic resolution widget |
| Generated duration | Snap frame count upward until `frames % 17 == 5`, at 24 FPS | Persist requested seconds and resolved frames / encoded seconds separately |
| Latent timing | Video time grid `5k+2`; audio time `round((frames/24)*40)` | Joint video/audio sampling has a shared output duration; audio latent rate is 40 Hz |
| Length widget | Schema allows 5–3600 frames; tooltips describe roughly 124–362 as trained and longer lengths as untested | The widget maximum is not supported arbitrary-length generation evidence |
| Reference image sizing | `match`: downscale toward target pixel area; `max`: downscale toward 2048-pixel short edge; keep aspect and round to 32 | `max` increases reference conditioning detail, not output to 2K |
| Reference video | IMAGE frame batch presumed 24 FPS; trim to target length if longer, then truncate down to `17k+5`; fewer than five frames raises | Normalize FPS first; retain original and effective reference duration; native code does not itself enforce all published 2–15-second limits |
| Reference audio | Resample to audio VAE rate, default 32000, then encode | Supply audio VAE for acoustic conditioning |

Evidence: [native constants/time/canvas](https://github.com/Comfy-Org/ComfyUI/blob/5c460d8172fe30761ff67c0df3d5643bb74e0d70/comfy_extras/nodes_minimax_h3.py#L26-L91), [reference schema/execution](https://github.com/Comfy-Org/ComfyUI/blob/5c460d8172fe30761ff67c0df3d5643bb74e0d70/comfy_extras/nodes_minimax_h3.py#L243-L364).

The template's duration expression is:

```text
max(5, round(seconds * 24)) + (5 - (max(5, round(seconds * 24)) % 17)) % 17
```

Static calculations for requested 4/5/6/8/10/15 seconds give **107/124/158/192/243/362 frames**, encoded as **4.4583/5.1667/6.5833/8/10.125/15.0833 seconds**. These are arithmetic, not measured media. The Partner regeneration validator accepts 107–362 frames in increments of 17 and approximately 24 FPS. Therefore a published “15 seconds” envelope is not exactly 360 encoded frames. The 4-second mapped value of 107 also differs from the native tooltip's approximate 124-frame trained lower bound; qualification should explicitly cover that boundary. [Regenerate frame validation](https://github.com/Comfy-Org/ComfyUI/blob/5c460d8172fe30761ff67c0df3d5643bb74e0d70/comfy_api_nodes/nodes_minimax.py#L1611-L1639)

Native Ref2VA exposes separate maxima of nine image, three video, three paired soundtrack and three standalone audio slots. It does not enforce the twelve-file limit, require visual input, or fully validate clip/aggregate durations. Do not add all slot maxima together as a certified supported request. Keep an original-file manifest; an extracted soundtrack from a video is part of that original video, while separately supplied audio is a separate file. The combined soundtrack/standalone-audio boundary still needs runtime qualification. [Native schema and execution](https://github.com/Comfy-Org/ComfyUI/blob/5c460d8172fe30761ff67c0df3d5643bb74e0d70/comfy_extras/nodes_minimax_h3.py#L269-L364)

## 3. Native local nodes versus external Partner nodes

| Route | Exact registered class IDs | Execution |
| --- | --- | --- |
| Local T2VA / FL2VA | `MiniMaxH3ImageToVideo`, `EmptyMiniMaxH3LatentAV` | Native AV latent/conditioning, FL2VA local weights, native samplers and decoders |
| Local Ref2VA | `MiniMaxH3ReferenceToVideo` | Local image/video/audio conditioning with Ref2VA weights |
| Local guides/patches | `MiniMaxH3AddGuide`, `MiniMaxH3SigmaShift`, `MiniMaxH3FunControlNetApply` | Core interfaces; Fun control models are extra dependencies if that advanced route is chosen |
| Hosted generation | `MinimaxHailuo03TextToVideoNode`, `MinimaxHailuo03FirstLastFrameNode`, `MinimaxHailuo03ReferenceNode` | Partner API, hidden Comfy auth/API-key inputs, upload/poll external jobs, return VIDEO |
| Hosted preprocessing / regeneration | `MinimaxHailuo03ContextIRNode`, `MinimaxHailuo03RegenerateNode` | Partner API; no offline 2K-regeneration counterpart is registered by the inspected native extension |

Sources: [native extension](https://github.com/Comfy-Org/ComfyUI/blob/5c460d8172fe30761ff67c0df3d5643bb74e0d70/comfy_extras/nodes_minimax_h3.py), [Partner source](https://github.com/Comfy-Org/ComfyUI/blob/5c460d8172fe30761ff67c0df3d5643bb74e0d70/comfy_api_nodes/nodes_minimax.py).

Both reference classes display “MiniMax H3 Reference to Video.” Distinguish them by **class ID, schema and provenance**. Native media keys are `ref_images.ref_image_0` etc.; Partner inputs sit under a dynamic `model` selection and its `reference_images`/`reference_videos`/`reference_audios` groups. Partner schemas have `is_api_node=True` and category `partner/video/MiniMax`.

Partner endpoint constants are `/proxy/minimax/v2/video_generation`, `/proxy/minimax/v2/query/video_generation`, `/proxy/minimax/v2/h3_context_ir`, and `/proxy/minimax/v2/video_regeneration`. H3 Max / Max Turbo choices route through `/proxy/fal/minimax/h3-max` and `/proxy/fal/minimax/h3-max-turbo`. These external products are not a local Turbo LoRA implementation; reference options in this snapshot include H3/H3 Max, while text and first/last-frame options also include Max Turbo. [Endpoint/model routing](https://github.com/Comfy-Org/ComfyUI/blob/5c460d8172fe30761ff67c0df3d5643bb74e0d70/comfy_api_nodes/nodes_minimax.py#L461-L474)

**H3-Regenerate-2K local/offline availability: absent from the inspected official release.** MiniMax describes regeneration using the base result and original context, with hosted APIs in its hybrid reproduction workflow. Comfy's Regenerate node implements the hosted POST/poll route. Increasing native dimensions, selecting `ref_image_size=max`, or running a generic local upscaler does not establish execution of H3-Regenerate-2K. [MiniMax regeneration](https://github.com/MiniMax-AI/MiniMax-H3/blob/d21241f0a4b3acbb34c97dae47fa417b7065e438/README.md#h3-regenerate-2k), [Comfy hosted regeneration](https://github.com/Comfy-Org/ComfyUI/blob/5c460d8172fe30761ff67c0df3d5643bb74e0d70/comfy_api_nodes/nodes_minimax.py#L1764-L1781)

## 4. Official templates, dependencies and local graph audit

The four reused UI templates are format **0.4**, with `nodes`/`links` and subgraph definitions where applicable. Their hashes are pinned below; their source repository head matched the saved revision during resumption. They cannot be POSTed directly as API prompt dictionaries.

| Official template | Real node instances | SHA-256 |
| --- | --- | --- |
| [video_minimax_h3_t2v.json](https://github.com/Comfy-Org/workflow_templates/blob/0e5c5efb32ba6f3365d6da07da64aaf668157042/templates/video_minimax_h3_t2v.json) | Root subgraph instance `140`, inner `131 MiniMaxH3ImageToVideo`, root `115 ResolutionSelector`, `92 SaveVideo` | `8a5eb23b0dd2df9e99b6f7af9684e770f188feca820d241364b729e01dedf3b6` |
| [video_minimax_h3_i2v.json](https://github.com/Comfy-Org/workflow_templates/blob/0e5c5efb32ba6f3365d6da07da64aaf668157042/templates/video_minimax_h3_i2v.json) | Root `105` subgraph, inner `104 MiniMaxH3ImageToVideo`, root `114 LoadImage` | `34ee39544808fd3b0dc8de9df082940d4d41c3771beb80d3988c1ea5531cec0d` |
| [video_minimax_h3_r2v.json](https://github.com/Comfy-Org/workflow_templates/blob/0e5c5efb32ba6f3365d6da07da64aaf668157042/templates/video_minimax_h3_r2v.json) | Root `136 MiniMaxH3ReferenceToVideo`, `137/139 LoadImage`; unused mixed-media slots declared | `afeea99e9fd5a1456df348e0573e289c1243f2f28546fc95c1c963f808d61ddb` |
| [video_minimax_h3_i2v_continuation.json](https://github.com/Comfy-Org/workflow_templates/blob/0e5c5efb32ba6f3365d6da07da64aaf668157042/templates/video_minimax_h3_i2v_continuation.json) | Root `105`, inner `104`; native image continuation variant | `332184c45587d70c502fe67caf1c87c17a42eea0be4188c83f7e32be84e810e7` |

Executable template nodes carry `cnr_id=comfy-core`; notes are not executable dependencies. These baseline templates do not require KJNodes or VideoHelperSuite. Comfy's pinned guide says the base templates need **0.30.0+**. This is a documented minimum, not the observed target version. Pin a tested core/frontend/template set rather than relying only on that minimum. [Pinned guide](https://github.com/Comfy-Org/docs/blob/fd96fcede2e8be50670b386a62ab19e87e809f99/tutorials/video/minimax/minimax-h3.mdx)

| Comfy model directory | T2V / I2V / continuation filenames | R2V filenames |
| --- | --- | --- |
| `models/diffusion_models` | `minimax_h3_fl2va_pruned_int8_convrot.safetensors` | `minimax_h3_ref2va_pruned_int8_convrot.safetensors` |
| `models/text_encoders` | `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` | Same |
| `models/vae` | `minimax_h3_video_vae_int8_convrot.safetensors`, `minimax_h3_audio_vae_fp32.safetensors` | Same |
| `models/loras` | `minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors` | `minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors` |

These names are in the templates and pinned model inventories. Replace template model URLs using `resolve/main` with pinned repository revisions for reproducibility. The official native guide notes the optional LoRA can still be required by a workflow model scan; preserve the unmodified template dependency or deliberately prune/requalify its inactive branch. [Native model/storage guide](https://github.com/Comfy-Org/docs/blob/fd96fcede2e8be50670b386a62ab19e87e809f99/tutorials/video/minimax/minimax-h3-native.mdx)

**ResolutionSelector is an authoritative native core class in this snapshot.** The three repository graphs use compatible `aspect_ratio`, `megapixels`, `multiple` inputs. Calling the class itself a third-party custom node is incorrect. Its formula uses `megapixels*1024*1024` and nearest-multiple rounding; it does not enforce H3's area cap. The UI preview input can be omitted because `execute(..., preview=None)` permits that. [Selector source](https://github.com/Comfy-Org/ComfyUI/blob/5c460d8172fe30761ff67c0df3d5643bb74e0d70/comfy_extras/nodes_resolution.py)

Calculated at 16:9 and multiple 32: **0.4 MP = 864×480; 0.8 MP = 1216×672; 0.98 MP = 1344×768; 1.0 MP = 1376×768**. The 0.4 preset is a preview; 1.0 exceeds H3's native area budget after rounding. Use explicit native dimensions or enforce post-rounding area. These are calculations, not measured outputs. [Selector formula](https://github.com/Comfy-Org/ComfyUI/blob/5c460d8172fe30761ff67c0df3d5643bb74e0d70/comfy_extras/nodes_resolution.py), [Comfy resolution guidance](https://github.com/Comfy-Org/docs/blob/fd96fcede2e8be50670b386a62ab19e87e809f99/tutorials/video/minimax/minimax-h3.mdx#setting-the-output-resolution)

Read-only local graph hashes at the recorded static audit:

| Repository API graph | SHA-256 | Assessment |
| --- | --- | --- |
| `backend/workflows/h3/video_minimax_h3_t2v.api.json` | `0da483cdccbb3f36a8d22d52d7a70d7c22b6094f23b811a3d1b0718c79e26056` | Native core route, execution unverified |
| `backend/workflows/h3/video_minimax_h3_i2v.api.json` | `3eb8474945c5c5ed6f81cddf21daf0c6465e35d8bb7079f4fcbb40b5a2997842` | Native core route, execution unverified |
| `backend/workflows/h3/video_minimax_h3_r2v_1ref.api.json` | `ddab6a41c4b1855f0b32565facee575bc91ea9920989cba246f9d3a72461a1a5` | Customized one-image graph, not authoritative full Ref2VA |

The local R2V graph binds only `178.inputs['ref_images.ref_image_0']` to LoadImage `148`. It uses unpruned Ref2VA int8 weights, Qwen int8 encoder and fp16 video VAE, plus a realism LoRA/prompt prefix, `StringConcatenate`, `PathchSageAttentionKJ`, explicit sigma shift and `VHS_VideoCombine`. It selects Euler/beta **eight steps** despite loading the named **4-step v0.1** Ref2VA Turbo adapter. Those are observed customizations/settings mismatches, not a demonstrated runtime failure. Its backslash/subdirectory model names must match target loader enumeration. `PathchSageAttentionKJ` is the exact spelling in the file; do not invent a corrected class ID.

That graph additionally needs the KJ Sage patch and VideoHelperSuite output node, with the corresponding attention dependency if used. Exact installed revisions, import success and ownership of other utilities including `StringConcatenate` are **NOT_OBSERVED**; obtain target node/source manifests. The official baseline avoids these additions and uses native CreateVideo/SaveVideo. [Comfy KJ/Sage guidance](https://github.com/Comfy-Org/docs/blob/fd96fcede2e8be50670b386a62ab19e87e809f99/tutorials/video/minimax/minimax-h3.mdx#speeding-up-generation-with-sage-attention), [VideoHelperSuite class registration source](https://raw.githubusercontent.com/Kosinkadink/ComfyUI-VideoHelperSuite/main/videohelpersuite/nodes.py). The latter was checked during final writing on 2026-10-05 only to verify ownership; it is an unpinned moving reference, not an installed-runtime revision or part of the pinned native baseline.

## 5. Construct the full native dynamic references graph

Start with the pinned official **R2V UI template**. It demonstrates two images, but conditioning node `136` already exposes additional image, video, paired soundtrack and standalone audio inputs. Two demonstrated images are not a two-reference capability limit.

### Actual graph spine and instance IDs

| Function | Exact template nodes and edges |
| --- | --- |
| Model / encoder / VAEs | `127 UNETLoader`; `128 CLIPLoader`; `119 VAELoader` video; `120 VAELoader` audio |
| Prompt / geometry / duration | `138 PrimitiveStringMultiline -> 136.prompt`; `115 ResolutionSelector` outputs 0/1 -> width/height; `132 PrimitiveFloat -> 131 ComfyMathExpression`, output **1** -> length |
| Existing images | `137 LoadImage` output 0 -> `136.ref_images.ref_image_0`; `139 LoadImage` output 0 -> `136.ref_images.ref_image_1` |
| Full conditioning | `128/119/120` output 0 -> `136.clip/vae/audio_vae`; `136` output 0 -> `126 BasicGuider.conditioning`; output **1** -> `125 SamplerCustomAdvanced.latent_image` |
| Turbo branch | `145 LoraLoaderModelOnly`, `141 ComfySwitchNode` choose model for guider; `143 PrimitiveInt=20`, `144 PrimitiveInt=4`, `142 ComfySwitchNode` choose steps; `146 PrimitiveBoolean=False` drives both switches |
| Sampling | `129 RandomNoise`, `126 BasicGuider`, `123 KSamplerSelect=res_multistep`, `124 BasicScheduler=simple` -> `125 SamplerCustomAdvanced` |
| AV decode and save | `125` output **0** -> `122 VAEDecode` and `121 VAEDecodeAudio`; decoded images/audio -> `130 CreateVideo` at fps 24 -> `92 SaveVideo` |

The actual template connects `127` directly to `124.model`, while the guider receives the model-switch result. Do not rewrite this provenance as though both inputs came from the switch. A future adapter needing changed shifts requires coherent sampling/guider model configuration and a separately qualified derived graph. [Exact R2V source](https://github.com/Comfy-Org/workflow_templates/blob/0e5c5efb32ba6f3365d6da07da64aaf668157042/templates/video_minimax_h3_r2v.json)

### Source-derived dynamic interface

| API path on `MiniMaxH3ReferenceToVideo` | Type / slot range | Source-backed upstream path |
| --- | --- | --- |
| `clip`, `prompt`, `width`, `height`, `length`, `ref_image_size` | CLIP, STRING, INT, INT, INT, `match`/`max` | Retain encoder and scalar settings |
| `vae`, `audio_vae` | Optional VAE in schema | Bind both for full visual/acoustic conditioning |
| `ref_images.ref_image_0` … `_8` | IMAGE, nine slots | Separate `LoadImage` output 0 |
| `ref_videos.ref_video_0` … `_2` | IMAGE frame batch, three slots | `LoadVideo(file)` VIDEO output 0 -> `GetVideoComponents(video)` IMAGE output **0** |
| `ref_video_audios.ref_video_audio_0` … `_2` | AUDIO, three paired slots | Same video's `GetVideoComponents` AUDIO output **1**, only if a usable soundtrack exists |
| `ref_audios.ref_audio_0` … `_2` | AUDIO, three standalone slots | `LoadAudio(audio)` output 0 |

The classes, input names and output indices for additional media nodes come from official code. They have **no instance IDs in the saved two-image template**. Add them using the actual frontend/export, and retain its assigned IDs rather than fabricating upstream instances. `GetVideoComponents` output 2 reports source FPS; it does not resample to 24 FPS. The reference node takes an IMAGE batch, not VIDEO. [H3 schema](https://github.com/Comfy-Org/ComfyUI/blob/5c460d8172fe30761ff67c0df3d5643bb74e0d70/comfy_extras/nodes_minimax_h3.py#L253-L287), [video loader/components](https://github.com/Comfy-Org/ComfyUI/blob/5c460d8172fe30761ff67c0df3d5643bb74e0d70/comfy_extras/nodes_video.py#L308-L366), [audio loader](https://github.com/Comfy-Org/ComfyUI/blob/5c460d8172fe30761ff67c0df3d5643bb74e0d70/comfy_extras/nodes_audio.py#L359-L382)

**Why the dotted paths are authoritative:** `Autogrow.TemplatePrefix` makes zero-based names from prefix and maximum; `finalize_prefix` joins enclosing IDs with dots; `_expand_schema_for_dynamic` uses supplied live keys to expand optional inputs and records dynamic paths/default dictionaries. These become dictionaries such as `ref_images={'ref_image_0': tensor}` for the native `execute` signature. Serialize the **flat dotted keys in API `inputs`**, not JSON lists under `ref_images`. [Autogrow/prefix implementation](https://github.com/Comfy-Org/ComfyUI/blob/5c460d8172fe30761ff67c0df3d5643bb74e0d70/comfy_api/latest/_io.py#L1067-L1217)

Construction sequence for a later implementation/qualification task:

1. Open the pinned UI template in a compatible frontend; capture live schemas for every executable class. Add real media-loader/component nodes for supplied references. Omit absent dynamic inputs.
2. Normalize video to 24 FPS and enforce clip/aggregate limits. Retain original files, hashes and effective trims. Comfy loader filenames must refer to runtime-visible input media; backend-local paths are not automatically visible on the Comfy host.
3. Export **API format**, retaining assigned IDs, dotted dynamic inputs and exact output indices. For T2V/I2V, let the frontend expand UI subgraphs; a UUID subgraph type is not an executable registered H3 class. Do not rename UI `type` fields into API `class_type` and assume conversion is complete.
4. Bind images, video frames, paired soundtracks and standalone audio deterministically from a media manifest. Keep soundtrack index N aligned with video index N. Avoid duplicating that soundtrack as another standalone reference unless explicitly intended.
5. Retain the complete reference -> conditioning/AV latent -> guider/sampler -> separate video/audio decoders -> CreateVideo -> SaveVideo chain. Validate visual-input requirement, counts, durations, post-rounding canvas and adapter family/settings outside the permissive native node.
6. Hash the final API graph and pin actual core/frontend/node/model revisions before a separately coordinated GPU run.

Binding illustration, **pseudocode only**, using links from the actual export rather than invented numeric IDs:

```python
inputs = api_graph[ref_node_id]["inputs"]  # ID read from the real export
for i, image_link in enumerate(image_links):
    inputs[f"ref_images.ref_image_{i}"] = image_link
for i, (frames_link, soundtrack_link) in enumerate(video_component_links):
    inputs[f"ref_videos.ref_video_{i}"] = frames_link
    if soundtrack_link is not None:
        inputs[f"ref_video_audios.ref_video_audio_{i}"] = soundtrack_link
for i, audio_link in enumerate(standalone_audio_links):
    inputs[f"ref_audios.ref_audio_{i}"] = audio_link
```

Socket numbering is zero-based; prompt tags are one-based **`<Picture i>` / `<Video k>` / `<Audio j>`**. Native presentation order is images, then videos with each soundtrack's audio label immediately before that video, then standalone audio. Each soundtrack consumes an audio ordinal; standalone numbering must account for them. Keep a labeling manifest. Native execution truncates video frames but passes the supplied soundtrack to audio encoding without an equivalent explicit duration trim; align soundtrack/video trims before binding. Without video VAE, references condition the encoder but do not add native visual reference latents; without audio VAE, audio labels do not add acoustic latents. [Reference ordering/execution](https://github.com/Comfy-Org/ComfyUI/blob/5c460d8172fe30761ff67c0df3d5643bb74e0d70/comfy_extras/nodes_minimax_h3.py#L243-L364)

### Executable-format evidence and remaining gap

`upstream-benchmark-h3-t2v.api.json` is a real official API-format prompt, with expanded IDs such as `140:131`, native AV sampling and SaveVideo. SHA-256: **`fd8ff2e893d010adb7e59ea0054477b08d6def2f82271d9dc310bda26d6545d7`**. It demonstrates upstream executable **format for T2VA**, not GPU execution or a mixed-media Ref2VA export. [Official benchmark JSON](https://github.com/Comfy-Org/workflow_templates/blob/0e5c5efb32ba6f3365d6da07da64aaf668157042/benchmarks/video_minimax_h3_t2v.json)

The saved MiniMax `upstream-minimax-ref2va-request.sh` uses JSON `conditions` for **SGLang's** local `/v1/videos` route. vLLM's official H3 recipe separately documents `/v1/videos` / `/v1/videos/sync` reference requests. These are primary-source full-reference API routes, not Comfy `/prompt` dictionaries. Neither server was reached or started. [MiniMax local Ref2VA request](https://github.com/MiniMax-AI/MiniMax-H3/blob/d21241f0a4b3acbb34c97dae47fa417b7065e438/scripts/readme/reproducible-768p-ref2va-request.sh), [vLLM H3 recipe](https://github.com/vllm-project/vllm-omni/blob/6dd0d1f9310f7598b773c796b434c8000c2816ec/recipes/MiniMaxAI/MiniMax-H3.md)

Full native mixed-media construction is available from source. A live-exported mixed-media API graph, target schema comparison and execution remain **NOT_CAPTURED / NOT_RUN**. No invented mixed-media artifact is labeled as an official export or executed PASS.

## 6. Turbo / Lightning family and version contracts

Official templates call the switch **Enable Lightning LoRA**, but load LightX2V **Turbo** filenames. This does not identify a separate MiniMax Lightning checkpoint family. Template base default is Turbo off, 20 steps, `res_multistep` / `simple`; enabling it selects FL2VA **8-step v1.0** for text/image/continuation or Ref2VA **4-step v0.1** for R2V. T2V/I2V expose `turbo_mode` / `turbo_steps` on the subgraph; honor linked/promoted settings rather than stale inner widget values. [Template sources](https://github.com/Comfy-Org/workflow_templates/tree/0e5c5efb32ba6f3365d6da07da64aaf668157042/templates), [Comfy step-count guide](https://github.com/Comfy-Org/docs/blob/fd96fcede2e8be50670b386a62ab19e87e809f99/tutorials/video/minimax/minimax-h3.mdx#step-count)

Published Diffusers-layout inventory and pinned vLLM family contracts:

| Exact filename | Family | Denoiser evaluations | Video/audio shifts |
| --- | --- | --- | --- |
| `minimax_h3_fl2v_turbo_4step_v0.1.safetensors` | T2VA / FL2VA | 4 | 12 / 3 |
| `minimax_h3_fl2v_turbo_8step_v1.0_bf16.safetensors` | T2VA / FL2VA | 8 | 12 / 3 |
| `minimax_h3_fl2v_turbo_4step_v1.0_768p_bf16.safetensors` | T2VA / FL2VA | 4 | 6 / 3 |
| `minimax_h3_fl2v_turbo_4step_v1.1_768p_bf16.safetensors` | T2VA / FL2VA | 4 | 6 / 3 |
| `minimax_h3_fl2v_turbo_4step_v1.2_768p_bf16.safetensors` | T2VA / FL2VA | 4 | 6 / 3 |
| `minimax_h3_fl2v_turbo_8step_v1.0_768p_bf16.safetensors` | T2VA / FL2VA | 8 | 6 / 3 |
| `minimax_h3_ref2v_turbo_4step_v0.1_bf16.safetensors` | Ref2VA | 4 | 12 / 3 |
| `minimax_h3_ref2v_turbo_8step_v1.0_768p_bf16.safetensors` | Ref2VA | 8 | 6 / 3 |

All eight appear in the pinned publisher inventory and vLLM matrix. The older publisher README lists only five variants, including 544p/768p training resolutions, so it cannot exclude newer Ref2VA 768p or FL2VA v1.1/v1.2 files. The official Comfy R2V template still uses 4-step v0.1 despite the 8-step 768p adapter's presence. Treat a replacement as a new qualified version. [Publisher README](https://github.com/ModelTC/Minimax-H3-Turbo/blob/02e26d591f7a04d5d1a074c9566d5dd4f22f6225/README.md), [pinned inventory](https://huggingface.co/api/models/lightx2v/Minimax-h3-Turbo/revision/3ec17a324ced54151364f24f8b5fb6bf7e26414f), [vLLM matrix](https://github.com/vllm-project/vllm-omni/blob/091b256674afa1bae476553c35ab1d22d155608a/tests/diffusion/models/minimax_h3/test_minimax_h3_turbo_matrix.py)

Comfy adapters use fused-QKV `_comfyui_bf16` exports, including the exact template filenames in section 4. A newer example is `minimax_h3_ref2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors`. vLLM's inspected loader recognizes the Diffusers naming/layout and rejects `_comfyui_` exports. Do not rename one to masquerade as the other. The legacy FL2VA 4-step v0.1 Comfy conversion linked by the publisher is Kijai's `minimax_h3_fl2v_lightx2v_turbo_4step_v0.1_comfy.safetensors`, distinct from the current template default. [Publisher links](https://github.com/ModelTC/Minimax-H3-Turbo/blob/02e26d591f7a04d5d1a074c9566d5dd4f22f6225/README.md), [vLLM loader](https://github.com/vllm-project/vllm-omni/blob/091b256674afa1bae476553c35ab1d22d155608a/vllm_omni/diffusion/models/minimax_h3/lora.py)

**Resolved source discrepancy:** the saved vLLM recipe asks for **5/9** `num_inference_steps` in Turbo examples, counting sigma boundaries. Its pipeline at both inspected commits requires `sampling.num_inference_steps == spec.denoise_steps`, and its tests require **4/8**, rejecting 5/9. Time-schedule code constructs N+1 boundaries for N evaluations. Use **4/8 for this pinned implementation**, with artifact-specific shifts; the recipe examples are stale relative to the implementation. This is source analysis, not an executed vLLM test. [Pipeline validation](https://github.com/vllm-project/vllm-omni/blob/091b256674afa1bae476553c35ab1d22d155608a/vllm_omni/diffusion/models/minimax_h3/pipeline_minimax_h3.py#L815-L840), [matrix tests](https://github.com/vllm-project/vllm-omni/blob/091b256674afa1bae476553c35ab1d22d155608a/tests/diffusion/models/minimax_h3/test_minimax_h3_turbo_matrix.py#L87-L105), [schedule implementation](https://github.com/vllm-project/vllm-omni/blob/6dd0d1f9310f7598b773c796b434c8000c2816ec/vllm_omni/diffusion/models/minimax_h3/time_request.py)

Begin reference-fidelity qualification with the native base schedule; compare each adapter/family separately. Record adapter layout/version/strength, sampler, scheduler, shifts, resolved frames and reference sizing. A generic “Turbo enabled” flag is insufficient provenance.

## 7. Runtime probe summary — GPU generation NOT_RUN

Only `COMFYUI_BASE_URL` was read from dotenv candidates. The selected setting was root `.env`: **`http://host.docker.internal:8188`**. There was no process override or backend-dotenv value for that key. No full dotenv/secrets dump was captured. Read-only inspection of `backend/apps/api/app/core/config.py` and `infra/compose.yaml` confirms the same default/Compose propagation.

| Observation | Original 10:36:06 UTC record | Resumed 12:57:49–12:58:09 UTC record |
| --- | --- | --- |
| GET `/system_stats` | Connection refused | **UNREACHABLE**, 10-second timeout, no HTTP JSON/status |
| GET `/object_info` | Connection refused | **UNREACHABLE**, 10-second timeout, no node inventory |
| `nvidia-smi` inventory | Quadro T1000, 4096 MiB, driver 610.60 | Same; exit 0 |
| `docker ps` inventory | Linux engine pipe unavailable | Exit 1; `dockerDesktopLinuxEngine` pipe not found |
| GPU job / output inspection | NOT_RUN | **NOT_RUN** |
| Target core/frontend version, node imports, installed weights | Unavailable | **NOT_OBSERVED** |

Evidence: `runtime-inventory.json` and `runtime-inventory-resumed.json`. Refusal and timeout are separate observed failure signatures; no cause is inferred from their difference. A preliminary restricted-shell request hit Windows socket permission error 10013; the recorded recheck above used approved network access, so its timeout is not presented as that sandbox denial.

The probes originated on the **Windows host**. `host.docker.internal` is the configured Docker-to-host application route; host-process failure does not prove identical results from the API/worker namespace. Docker was unavailable, so active container configuration and container-vantage reachability were not observed. No alternate endpoint was substituted and no service was started.

The observed 4 GiB GPU does not establish capacity for H3. For context, the official vLLM BF16/offload recipe discusses 2×24/32 GiB and substantially larger memory profiles; these are vLLM profiles, not a formal minimum for quantized Comfy. No measured fit, throughput, quality, memory peak, generated media, prompt ID or executed PASS exists here. [vLLM deployment profiles](https://github.com/vllm-project/vllm-omni/blob/6dd0d1f9310f7598b773c796b434c8000c2816ec/recipes/MiniMaxAI/MiniMax-H3.md#two-2432-gb-gpus-tp2-distributed-layerwise-offload)

### Exact read-only resources needed later

Run from the intended application namespace using its actual configured URL. Individual-class requests below were **not attempted** in this research run. Do not use POST `/prompt` as a reachability probe.

```powershell
$h3BaseUrl = 'http://host.docker.internal:8188'
Invoke-RestMethod -Method Get -Uri "$h3BaseUrl/system_stats" -TimeoutSec 10
Invoke-RestMethod -Method Get -Uri "$h3BaseUrl/object_info" -TimeoutSec 10
Invoke-RestMethod -Method Get -Uri "$h3BaseUrl/object_info/MiniMaxH3ReferenceToVideo" -TimeoutSec 10
Invoke-RestMethod -Method Get -Uri "$h3BaseUrl/object_info/MiniMaxH3ImageToVideo" -TimeoutSec 10
Invoke-RestMethod -Method Get -Uri "$h3BaseUrl/object_info/ResolutionSelector" -TimeoutSec 10
Invoke-RestMethod -Method Get -Uri "$h3BaseUrl/object_info/LoadVideo" -TimeoutSec 10
Invoke-RestMethod -Method Get -Uri "$h3BaseUrl/object_info/GetVideoComponents" -TimeoutSec 10
Invoke-RestMethod -Method Get -Uri "$h3BaseUrl/object_info/LoadAudio" -TimeoutSec 10
```

The pinned server registers `/system_stats`, `/object_info`, `/object_info/{node_class}` and serializes class input definitions/order and outputs. V3 nodes may expose dynamic descriptors rather than every expanded socket. Compare descriptors plus the real export's dotted keys against the pinned source, and require every sampler, guider, noise, loader, decoder and output class in the final graph. [Server node-info routes](https://github.com/Comfy-Org/ComfyUI/blob/5c460d8172fe30761ff67c0df3d5643bb74e0d70/server.py#L759-L836)

## 8. Integration recommendation and outstanding qualification

Use native H3-Base as a versioned local provider; configure hosted Partner/MiniMax API as a separate provider with its own auth, schema and capabilities. Preserve the distinction between base 768p output, generic local upscale and hosted H3-Regenerate-2K in generation metadata. A Partner node's presence does not locally qualify its hosted capability.

For first local qualification, use the official core R2V base graph and a deterministic binding manifest from an actual API export. Replacement of the customized one-image production artifact belongs to a separate implementation task. Attach graph hash, runtime/core/frontend revisions, actual weight digests and node revisions to each eventual qualification result.

Required later evidence, all outstanding:

- Live reachability/schemas from the worker namespace, actual runtime version, GPU inventory, model payload hashes and dependency/import manifests.
- Real API exports for image-only, image+audio, video-only, video+soundtrack and mixed image/video/audio, including multiple references/ordinals; first-only, last-only and first+last-frame cases belong to FL2VA.
- Validation of visual-input requirement, reference counts/totals, clip durations, 24-FPS normalization, frame-grid boundaries, post-rounding canvas, absent soundtracks and adapter-family/settings mismatches.
- Separately coordinated GPU submissions on a capable runtime, preserving prompt IDs, final submitted graphs, full history/status and output hashes. Measure dimensions, rational FPS, frame count, duration, codecs, audio presence/sample rate/channels with ffprobe. Record memory/latency only when measured. Qualify Turbo separately from base and preserve failures as failures.
- If 2K is required, separately qualify the hosted Regenerate route using original context and unmodified native output under its stricter canvas/frame validator.

**Read-only research is complete. Target graph execution, GPU generation and output qualification remain NOT_RUN.**

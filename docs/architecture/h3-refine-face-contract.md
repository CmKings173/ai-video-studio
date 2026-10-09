# Pinned Refine / Face Refine integration

Source: `AIMixer/ComfyUI_MiniMaxH3_Director`, clean local checkout verified at
`a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb` on 2026-10-07.

- [Refine node](https://github.com/AIMixer/ComfyUI_MiniMaxH3_Director/blob/a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb/nodes/director_refine.py)
- [Refine pack and resolution semantics](https://github.com/AIMixer/ComfyUI_MiniMaxH3_Director/blob/a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb/director/refine_pack.py)
- [Face node](https://github.com/AIMixer/ComfyUI_MiniMaxH3_Director/blob/a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb/nodes/director_face_refine.py)
- [Face pack](https://github.com/AIMixer/ComfyUI_MiniMaxH3_Director/blob/a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb/director/face_refine/pack.py)
- [Director sockets](https://github.com/AIMixer/ComfyUI_MiniMaxH3_Director/blob/a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb/nodes/director.py)

## Topology

`MiniMaxH3DirectorRefine` output 0 is `MMX_DIR_REFINE`; outputs 1/2 are INT geometry outputs, not configuration outputs. Output 0 connects to `Director.refine`.
`MiniMaxH3DirectorFaceRefine` output 0 is `MMX_DIR_FACE_REFINE`, connected to `Director.face_refine`.
The same sockets are inherited by the native aggregate bridge.

Neither config node accepts an `enabled` input. A disabled feature disconnects its optional Director socket in the submitted graph. The immutable source template retains both paths. This requires no separate registry combinations for base, refine, face, or combined execution.

Refine/upscale requires a SIGMAS input; latent_upscale does not resample. The bounded template uses the pinned accelerated example's BasicScheduler (`beta`, 3 steps, denoise 0.2), connected to the same MODEL as Director and Refine. Its output 0 feeds Refine.sigmas. The source example's latent model filename is `minimax_h3_latent_upscaler_3d_bf16.safetensors`; this is a graph binding, not a claim that weights are installed or qualified.

## Application field mapping

| Refine application field | Exact pinned mapping / supported behavior |
| --- | --- |
| enabled | Optional Director.refine connection; no config-node enabled field |
| mode | mode: refine / upscale / latent_upscale |
| upscale_method | upscale_method: h3_latent / lanczos / nvidia_rtx_vsr; meaningful for upscale |
| passes | passes, default 1; application limit 4 is stricter than source limit 9999; latent_upscale does not resample |
| seed_mode | seed_mode: inherit / offset / independent; graph seed uses frozen job seed for independent mode |
| aspect_ratio | follow_director alias to source FOLLOW_DIRECTOR_ASPECT; other values rejected because pack forces follow-Director |
| megapixels | megapixels; source default 1.0, existing application 0 sentinel uses source fallback |
| width / height | Source exposes fields but forced follow-Director resolution ignores them; enabled custom dimensions rejected, existing zero sentinel retained |
| skip_fl2v | skip_fl2v, default true; true explicitly skips refine on FL2V segments |
| enable_latent_chunking | Exact boolean socket, default false; meaningful for H3 latent upscaling |
| enable_tiling | Exact boolean socket, default false; applies to resampling, not latent-only enlargement |

Fixed audited graph inputs: sampler euler, confirm_first_pass false, tile_count 2, tile_overlap 128. No UI setting for a separate refine seed, arbitrary sampler, external upscale model, or first-pass confirmation is advertised. Changing these fixed topology bindings requires a new audited graph/profile identity.

| Face application field | Exact pinned mapping / values |
| --- | --- |
| enabled | Optional Director.face_refine connection |
| detector | detector; default face_yolov8m.pt, actual installed detector choices checked through object_info |
| confidence | confidence, default 0.35, range 0.05-0.95 |
| crop_factor | crop_factor, default 2.5, range 1.2-8 |
| canvas_width / canvas_height | Same names, default 768, range 128-1344, application stride 32 |
| canvas_mode | manual / auto_capped_768 |
| select | largest_face / centre_most |
| denoise | denoise, default 0.40, range 0.02-1 |
| steps | steps, default 8, range 1-50 |
| seed_mode | inherit / offset |
| paste_region | face_only / full_crop; source also has face_ellipse, which the current application deliberately does not expose |
| mask_dilation | mask_dilation, default 16, range 0-256 |
| feather | feather, default 24, range 0-256 |
| colour_match / blend | Same names, default 1.0, range 0-1 |

Face required inputs also include BDGROUP presentation fields and sampler/scheduler. The graph records exact source presentation defaults and uses source defaults euler/simple. Face uses its internal scheduler; external Face SIGMAS are outside this bounded topology.

## Qualification and frozen execution

Source-supported and graph-wired are separate from static validity and runtime qualification. Importing these graphs creates disabled candidates; no execution evidence is synthesized. Each exact normalized configuration is bound to graph/profile identity, intent hash, execution hash and aggregate qualification settings. A worker uses the persisted frozen spec, never the current editor state.

Individually qualified Refine and Face examples cannot qualify a combined request. Changes to meaningful passes, geometry, method, denoise, detector or blend require their own matching evidence. Backend profile validation checks actual semantic paths before advertising qualified feature settings. Frontend availability requires scope-qualified support and matching Director settings evidence.

GPU/H3 Refine runtime qualification: NOT_RUN - no GPU available.

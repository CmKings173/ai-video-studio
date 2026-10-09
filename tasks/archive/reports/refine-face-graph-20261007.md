# Refine / Face Refine backend graph slice — 2026-10-07

Implemented directly in the shared workspace. Initial live connector snapshot: root `D:/project/ai-video-studio`, branch `codex/production-safety-fixes`, HEAD `01b740d8be4d58d3bd15bc46dd9842afb48af8bb`; 0 staged, 52 unstaged, 155 untracked. This is the initial snapshot, not a claim about the final shared dirty state. Other-agent work was preserved.

The pinned upstream checkout was verified clean at `a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb`. No model execution, GPU benchmark, runtime qualification, render packaging, lockfile changes, commits, or Git history/checkout changes were performed.

## Topology and registry

Each of the six task templates (t2v, i2v, fl2v, r2v, v2v, rv2v), for both single_scene and dynamic aggregate, now has exactly one MiniMaxH3DirectorRefine, one MiniMaxH3DirectorFaceRefine, and one BasicScheduler. There are 12 current Director entries. Features use the same graph rather than additional workflow variants.

- Director.model and Refine.refine_model consume the same UNETLoader MODEL output zero.
- BasicScheduler.model consumes that same model; its SIGMAS output zero feeds Refine.sigmas.
- Refine output zero (MMX_DIR_REFINE) feeds Director.refine.
- Face output zero (MMX_DIR_FACE_REFINE) feeds Director.face_refine.
- Aggregate Director class remains StudioMiniMaxH3Director with native all_members artifact coverage, without a fixed count.
- Disabled application features remove their corresponding optional Director socket. Neither source config node has an enabled input.

Standalone version: `director-a8f57b8e23c4-config-template-v2`. Aggregate appends `-aggregate-dynamic-bridge-v1`. Hashes were recomputed. Registry replacement matches owned code **or regenerated file**, so older versions/aliases cannot point to overwritten graphs with stale hashes. Other registry entries and historical `_aggregate_2` graphs were preserved. All 12 owned current entries pass graph, slot hash, config topology, and exporter validation; unrelated historical/native entries were not repaired or promoted.

Every regenerated entry keeps auto_approve=false, poc_verified=false, qualification_status=SOURCE_TEMPLATE_ONLY, empty dependency_versions and weight_hashes. Feature state records only source_supported=true, graph_wired=true, statically_valid=true, runtime_qualified=false, advertised=false. A filename is not evidence that a model/detector is installed.

## Exact application mapping

The builder revalidates frozen settings through the current API Pydantic models, even without object_info. Unknown keys/enums, out-of-range values, alternate enabled refine aspect, and enabled custom width/height fail with DirectorWorkflowError. Face select is limited to largest_face and centre_most. Inputs are patched by semantic class/socket names, without fixed business node IDs.

### refine

| Application field | Source binding / behavior | Application default |
|---|---|---|
| `enabled` | Director.refine connected only when true; no enabled node input | `false` |
| `mode` | exact socket mode | `"refine"` |
| `upscale_method` | exact socket upscale_method | `"h3_latent"` |
| `passes` | exact socket passes | `1` |
| `seed_mode` | exact socket seed_mode | `"inherit"` |
| `aspect_ratio` | aspect_ratio: follow_director maps to 跟随导演台 (U+8DDF U+968F U+5BFC U+6F14 U+53F0) | `"follow_director"` |
| `megapixels` | megapixels: zero preserved; source replaces values below 0.1 with 1.0 MP; controls relative target geometry | `0.0` |
| `width` | width: zero preserved; nonzero forbidden when enabled; source ignores these in forced follow mode | `0` |
| `height` | height: zero preserved; nonzero forbidden when enabled; source ignores these in forced follow mode | `0` |
| `skip_fl2v` | exact socket skip_fl2v | `true` |
| `enable_latent_chunking` | exact socket enable_latent_chunking | `false` |
| `enable_tiling` | exact socket enable_tiling | `false` |

### face_refine

| Application field | Source binding / behavior | Application default |
|---|---|---|
| `enabled` | Director.face_refine connected only when true; no enabled node input | `false` |
| `detector` | exact socket detector | `"face_yolov8m.pt"` |
| `confidence` | exact socket confidence | `0.35` |
| `crop_factor` | exact socket crop_factor | `2.5` |
| `canvas_width` | exact socket canvas_width | `768` |
| `canvas_height` | exact socket canvas_height | `768` |
| `canvas_mode` | exact socket canvas_mode | `"manual"` |
| `select` | exact socket select | `"largest_face"` |
| `denoise` | exact socket denoise | `0.4` |
| `steps` | exact socket steps | `8` |
| `seed_mode` | exact socket seed_mode | `"inherit"` |
| `paste_region` | exact socket paste_region | `"face_only"` |
| `mask_dilation` | exact socket mask_dilation | `16` |
| `feather` | exact socket feather | `24` |
| `colour_match` | exact socket colour_match | `1.0` |
| `blend` | exact socket blend | `1.0` |

Refine seed is not an additional API setting: its seed socket is patched to spec.seed, including independent seed_mode. Upstream inherit uses the Director seed; offset uses Director seed + 1 + pass index; independent uses packed refine seed + pass index. Face seed_mode supports inherit/offset only.

The source accepts other aspect labels but Refine.pack unconditionally passes FOLLOW_DIRECTOR_ASPECT. resolve_refine_target returns (0, 0) in that mode and ignores width/height, including source fallback 1280/720. The app retains zero defaults and fails closed for nonzero enabled custom geometry; it does not advertise meaningful 1280/720 target control. MP remains the useful geometry control. App passes remains capped at 4 although the pinned source maximum is 9999. Source face_ellipse paste exists but is outside the app's existing face_only/full_crop contract.

## Fixed graph defaults and complete socket values

Values below are literal config sockets in the templates, not availability or execution evidence. Refine required sockets are mode, upscale_method, latent_upscale_model, sampler, passes. Face required sockets include both detection/sample BDGROUP decorations, detector/confidence/crop/canvas/select, denoise/steps/sampler/scheduler. Face paste BDGROUP is optional upstream but explicitly included. Extra application patchable fields are present as source optional inputs.

### Refine

```json
{
  "mode": "refine",
  "upscale_method": "h3_latent",
  "latent_upscale_model": "minimax_h3_latent_upscaler_3d_bf16.safetensors",
  "sampler": "euler",
  "passes": 1,
  "seed_mode": "inherit",
  "aspect_ratio": "跟随导演台",
  "megapixels": 0.0,
  "width": 0,
  "height": 0,
  "skip_fl2v": true,
  "confirm_first_pass": false,
  "enable_latent_chunking": false,
  "enable_tiling": false,
  "tile_count": 2,
  "tile_overlap": 128,
  "seed": 0
}
```

### Face Refine

```json
{
  "bd_grp_face_detect": "脸部检测设置",
  "detector": "face_yolov8m.pt",
  "confidence": 0.35,
  "crop_factor": 2.5,
  "canvas_width": 768,
  "canvas_height": 768,
  "canvas_mode": "manual",
  "select": "largest_face",
  "bd_grp_face_sample": "采样设置",
  "denoise": 0.4,
  "steps": 8,
  "sampler": "euler",
  "scheduler": "simple",
  "seed_mode": "inherit",
  "bd_grp_face_paste": "贴回设置",
  "paste_region": "face_only",
  "mask_dilation": 16,
  "feather": 24,
  "colour_match": 1.0,
  "blend": 1.0
}
```

### BasicScheduler

```json
{
  "scheduler": "beta",
  "steps": 3,
  "denoise": 0.2
}
```

Refine additionally wires refine_model and sigmas as above. Refine.upscale_model is unconnected; confirm_first_pass=false, tile_count=2 and tile_overlap=128 are fixed, not newly exposed API knobs. Face.sigmas remains unconnected, using the source node's euler/simple internal scheduler. Fixed weight: minimax_h3_latent_upscaler_3d_bf16.safetensors. BasicScheduler beta/3 steps/0.2 denoise is taken from the audited accelerated refine example, not the main Director's sampler/scheduler.

## Validation seams and integration API

`config_graph.validate_config_topology(graph, director, feature)` accepts refine or face_refine and returns the unique semantic node ID/node; raises ValueError on invalid topology. It does not mutate the graph or grant qualification. Parent require_contract now calls this for present, mapped, or qualified features; no service/contract/schema changes were made in this slice.

The validator rejects empty/missing primitive inputs, config enabled/unknown sockets, primitive values replaced by links, invalid API settings, duplicates, wrong Director output index, wrong SIGMAS source/index, boolean indices, missing/mismatched scheduler/model wiring, altered fixed defaults, and invalid unsigned refine seed. Bounded graphs require a unique BasicScheduler and a UNETLoader MODEL output zero shared by Director/refine/scheduler.

`DirectorWorkflowBuilder.build(...)` revalidates the frozen settings before patching enabled configs and wraps failures as DirectorWorkflowError. It checks installed config/Director/SIGMAS/MODEL types and required socket names when object_info is supplied. Disabled old config-free historical graphs remain usable; no config nodes are invented during frozen dispatch.

`import_templates(source, output, aggregate=False|True)` still requires the exact clean pinned checkout. It validates node/link uniqueness, class allowlist, input names, source/target indices/types, reciprocal input/output link declarations, required widget values and seed control. Unknown executable classes are rejected. It validates all six graphs and existing registry before writing any graph, preventing a late malformed source from overwriting earlier artifacts. This is validation-before-write, not a claim of a transactional filesystem update on I/O failure.

The accelerated refine example contains extra attention/LoRA classes. Its links are audited, but only the unique refine node, scheduler defaults, sampler, latent filename, and model/sigma wiring are extracted as source facts. Those extra executable classes are never imported or added to the API graph allowlist. The Face values/decorations were audited from nodes/director_face_refine.py and director/face_refine/pack.py.

## Source hashes (source bytes only)

| Pinned source file | SHA256 |
|---|---|
| `D:/project/ComfyUI_MiniMaxH3_Director/nodes/director_refine.py` | `e323890a8ba0fece140b2582bb9ffe66d58c6cd08ae3bd3ee2624f3d81e14dc7` |
| `D:/project/ComfyUI_MiniMaxH3_Director/nodes/director_face_refine.py` | `84939cc1c1bd7a22e1dc99b7b7fe114e729e60471e584c20bc53c7ded1988711` |
| `D:/project/ComfyUI_MiniMaxH3_Director/director/refine_pack.py` | `3577719c79544d47e25a43813379042aa2258f2f88805cded94dbbd7743d7c91` |
| `D:/project/ComfyUI_MiniMaxH3_Director/director/face_refine/pack.py` | `908639b85afd082cbd80b1b3e36fed05de86069060c833753e9ed0ea386f281a` |
| `D:/project/ComfyUI_MiniMaxH3_Director/example_workflows/minimax_h3_director_二采_加速.json` | `8f928ac4005ab289a25ae809bccf5374bb9d4301d6d12f0d4e76f12cca93ab2a` |
| `D:/project/ComfyUI_MiniMaxH3_Director/example_workflows/minimax_h3_director_t2v.json` | `80cbdf926249344fdfd29e9294881981e3ed2099881960a851f16f436ae9455c` |
| `D:/project/ComfyUI_MiniMaxH3_Director/example_workflows/minimax_h3_director_fl2v.json` | `4ca62c90726c480a2f20e35bfb41ce44d5b231299152bfff0c041819882245c0` |
| `D:/project/ComfyUI_MiniMaxH3_Director/example_workflows/minimax_h3_director_r2v.json` | `060403fa0d99cf1ff763575c78c6d03444a5104e28a26be8156db209dec61eae` |
| `D:/project/ComfyUI_MiniMaxH3_Director/example_workflows/minimax_h3_director_v2v.json` | `41e2f44ad5a53bbe34da444b9b7d4cd4838d57b543a270cc27b0cb00eac64fb3` |
| `D:/project/ComfyUI_MiniMaxH3_Director/example_workflows/minimax_h3_director_rv2v.json` | `772957fe9ef71d8d0361228f518ca593bbb5cc4f613c2b01216a91a00516b771` |

I2V intentionally uses the FL2V source example, then changes the semantic task_type binding. Source example and config example hashes are recorded separately in each profile. Source code hashes here are audit references only, not dependency/weight/runtime evidence.

## Regenerated artifact hashes

| Current workflow code | Workflow hash | Slot-map hash |
|---|---|---|
| `H3_DIRECTOR_T2V_BASE` | `3a06b1fb1743509734d6d2c5c5c2a43c7b43cf4aa396e56599bede9b93f7aa43` | `9b4ed217a2bc0d35ad62e98b8e717056a429cc2040a523dc494484e194cf64f0` |
| `H3_DIRECTOR_I2V_BASE` | `dafada9d9095f8ae3e11bf7ca8185b0ddafe92d073d90c849be352a3181d7505` | `9b4ed217a2bc0d35ad62e98b8e717056a429cc2040a523dc494484e194cf64f0` |
| `H3_DIRECTOR_FL2V_BASE` | `d72ee4262f27bc1ecb33ea47b3f0d9b37012de6c6e67679e5ff802b2b5929d36` | `9b4ed217a2bc0d35ad62e98b8e717056a429cc2040a523dc494484e194cf64f0` |
| `H3_DIRECTOR_R2V_BASE` | `639b50714feebdcb16133532a3472c045b1997e5e34416f4125e138ff5932b47` | `9b4ed217a2bc0d35ad62e98b8e717056a429cc2040a523dc494484e194cf64f0` |
| `H3_DIRECTOR_V2V_BASE` | `9f844f435cceeb3fb455478242c6277c1ab696b9b95d2c467617635f17026f2f` | `9b4ed217a2bc0d35ad62e98b8e717056a429cc2040a523dc494484e194cf64f0` |
| `H3_DIRECTOR_RV2V_BASE` | `0d173222b53b5ca756f21a9bb8983b227a2a62401bc437cefbf7d8469d020353` | `9b4ed217a2bc0d35ad62e98b8e717056a429cc2040a523dc494484e194cf64f0` |
| `H3_DIRECTOR_T2V_BASE_AGGREGATE` | `c1afc1ddd048cbdb8d57b13f619b373fc4940b367e4d333dddd6bddb870955cb` | `7a7d0847be7ea2113bdf260e57a827da9d239e9b88ef1311f700fe87d9ee684f` |
| `H3_DIRECTOR_I2V_BASE_AGGREGATE` | `4c8313ab6f50cfda13ee8a24beac8eed1771320a3bdfae5829412305d36d30d9` | `7a7d0847be7ea2113bdf260e57a827da9d239e9b88ef1311f700fe87d9ee684f` |
| `H3_DIRECTOR_FL2V_BASE_AGGREGATE` | `57e96eca93412ecfc9068b15f7fc5691b74ef889d4790299c72dc06854df4fc3` | `7a7d0847be7ea2113bdf260e57a827da9d239e9b88ef1311f700fe87d9ee684f` |
| `H3_DIRECTOR_R2V_BASE_AGGREGATE` | `4fc0e8dd065da58b69cddbf2df74b42606aba76860d486edd95fef9c3acf26ae` | `7a7d0847be7ea2113bdf260e57a827da9d239e9b88ef1311f700fe87d9ee684f` |
| `H3_DIRECTOR_V2V_BASE_AGGREGATE` | `f6f6ab33f7b64e1110af28ab9de5fca7135eb0c6ee01de9d7ac44b6b2079431b` | `7a7d0847be7ea2113bdf260e57a827da9d239e9b88ef1311f700fe87d9ee684f` |
| `H3_DIRECTOR_RV2V_BASE_AGGREGATE` | `065b3d16fa1416f6936db5fb1b9594f74c7d26e24f53a9714f2dbab25d9ab9a5` | `7a7d0847be7ea2113bdf260e57a827da9d239e9b88ef1311f700fe87d9ee684f` |

## Verification

- Initial parent graph regressions: 11 failed, 5 passed, showing absent config topology.
- Source topology adversarial test caught boolean SIGMAS index (False == 0); now rejects non-integer indices.
- Late-invalid importer regression failed because earlier graphs had already been overwritten; after deferred writes, it passes.
- Focused builder/importer/dynamic suite: **85 passed**.
- Broader Director unit/integration suite: **190 passed in 14.42s**, including the scope integration lane (21 passed independently).
- Ruff check: all checks passed on the eight owned/explicitly permitted Python files; Ruff format --check: all eight already formatted.
- The permitted existing dynamic importer test now supplies minimal audited source fixture data and bare UI graphs, while retaining aggregate coverage/no-count/no-auto-approval assertions plus repeated-import determinism. Parent subsequently authorized the two execution-scope importer fixture variants. They now share the audited refine example fixture and add a valid MODEL link while preserving node IDs, existing registry entries and deterministic repeat-import assertions; the full scope integration module passes (21 tests).

Broader command (working directory D:/project/ai-video-studio/backend):

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/test_refine_face_graph.py tests/unit/test_director_config_graph.py tests/unit/test_director_config_importer.py tests/unit/test_refine_face_qualification.py tests/unit/test_director_execution_contract.py tests/unit/test_director_graph_identity.py tests/unit/test_director_dynamic_aggregate.py tests/unit/test_director_native_manifest.py tests/integration/test_director_dynamic_aggregate.py tests/integration/test_director_dispatcher.py tests/integration/test_director_corrections.py tests/integration/test_workflow_execution_scope.py -q
```

Only simulated qualification fixtures were exercised; no real GPU/runtime qualification was run.

## Exact files written by this slice

- `D:/project/ai-video-studio/backend/apps/api/scripts/import_director_templates.py`
- `D:/project/ai-video-studio/backend/apps/api/app/providers/minimax_h3_director/config_graph.py`
- `D:/project/ai-video-studio/backend/apps/api/app/providers/minimax_h3_director/workflow_builder.py`
- `D:/project/ai-video-studio/backend/workflows/h3/director_t2v.api.json`
- `D:/project/ai-video-studio/backend/workflows/h3/director_i2v.api.json`
- `D:/project/ai-video-studio/backend/workflows/h3/director_fl2v.api.json`
- `D:/project/ai-video-studio/backend/workflows/h3/director_r2v.api.json`
- `D:/project/ai-video-studio/backend/workflows/h3/director_v2v.api.json`
- `D:/project/ai-video-studio/backend/workflows/h3/director_rv2v.api.json`
- `D:/project/ai-video-studio/backend/workflows/h3/director_t2v_aggregate.api.json`
- `D:/project/ai-video-studio/backend/workflows/h3/director_i2v_aggregate.api.json`
- `D:/project/ai-video-studio/backend/workflows/h3/director_fl2v_aggregate.api.json`
- `D:/project/ai-video-studio/backend/workflows/h3/director_r2v_aggregate.api.json`
- `D:/project/ai-video-studio/backend/workflows/h3/director_v2v_aggregate.api.json`
- `D:/project/ai-video-studio/backend/workflows/h3/director_rv2v_aggregate.api.json`
- `D:/project/ai-video-studio/backend/workflows/h3/registry.json`
- `D:/project/ai-video-studio/backend/tests/unit/test_refine_face_graph.py`
- `D:/project/ai-video-studio/backend/tests/unit/test_director_config_graph.py`
- `D:/project/ai-video-studio/backend/tests/unit/test_director_config_importer.py`
- `D:/project/ai-video-studio/backend/tests/unit/test_director_dynamic_aggregate.py`
- `D:/project/ai-video-studio/tasks/archive/reports/refine-face-graph-20261007.md`
- `D:/project/ai-video-studio/backend/tests/integration/test_workflow_execution_scope.py`

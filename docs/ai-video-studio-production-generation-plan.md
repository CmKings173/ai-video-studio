# AI Video Studio — Production Video Generation Implementation Plan

**Project:** `ai-video-studio`\
**Repository baseline reviewed:** `codex/production-safety-fixes`\
**Reviewed HEAD:** `01b740d`\
**Date:** 2026-10-05\
**Primary goal:** Build a production-ready AI video generation system focused on **fast generation, complete H3 generation modes, correct resolution/aspect-ratio handling, reusable generation profiles, full reference input support, reliable long-video generation, final-quality enhancement, assembly, and export formats**.

> This document intentionally does **not** prioritize advanced collaboration, agents, translation, avatar, multi-tenant SaaS, Kubernetes, Kafka, or a Premiere-like editor.\
> The priority is: **generate video correctly → generate fast → control input/output size → support the useful H3 modes → assemble/export reliably → prove it on the target GPU.**

---

## 0. Executive summary

The current repository already has a strong application foundation:

- FastAPI + PostgreSQL + SQLAlchemy async + Alembic.
- MinIO asset lifecycle.
- Generation state machine, idempotency, retries, reconciliation.
- ComfyUI adapter.
- T2V/I2V/First+Last/R2V logical modes.
- Storyboard and scene management.
- SSE progress.
- FFmpeg assembly.
- Final-version history.
- Auth/CSRF/RBAC.

The main remaining problem is that the application contract currently promises more H3 functionality than the executable ComfyUI workflows actually provide.

The production work should focus on these five pillars:

1. **Correctness**
   - Fix the 5 verified review defects.
   - Fix generation-resolution truth.
   - Prevent unverified workflows from being enabled.
   - Make Generate All scene-aware and revision-aware.

2. **Complete H3 video-generation modes**
   - T2V.
   - First-frame → video.
   - Last-frame → video.
   - First + last frame → video.
   - Full Ref2VA with image/video/audio references.

3. **Fast generation**
   - Draft/Turbo profile.
   - Standard profile.
   - High-quality profile only where useful.
   - Warmup and persistent model/runtime.
   - Benchmark on target GPUs before setting concurrency.
   - Never make editor choose low-level sampler internals as the primary UX.

4. **Resolution and format pipeline**
   - Separate **generation resolution** from **delivery resolution**.
   - H3 generates at benchmark-approved native dimensions.
   - Selected outputs can be enhanced/upscaled.
   - FFmpeg exports exact delivery presets such as 1080×1920, 1920×1080, 1080×1080, 1080×1350, etc.

5. **Production proof**
   - Every enabled workflow must have executed PASS evidence.
   - Record model/workflow/custom-node hashes.
   - Run latency/VRAM benchmark matrix.
   - Run backup/restore.
   - Run 5-editor UAT.

---

# 1. Hard scope

## 1.1 Must build now

### Generation
- T2V.
- First-frame I2V.
- Last-frame I2V.
- First + last frame.
- Full Ref2VA:
  - up to 9 images.
  - up to 3 videos.
  - up to 3 audio clips.
  - maximum 12 mixed files.
- Regenerate.
- Variation.
- Per-scene generation configuration.
- Generate All that respects each scene's config.
- Prompt Preview → Accept → freeze exact execution prompt.

### Speed
- Draft/Turbo generation profile.
- Standard generation profile.
- High-quality generation profile only after benchmark.
- Runtime warmup.
- No unnecessary model reload per request.
- Benchmark-driven concurrency.
- Queue fairness.
- Accurate ETA/latency collection if practical.

### Resolution / aspect ratio
- Correct H3 generation-resolution contract.
- Native 24 FPS H3 generation.
- Mandatory ratios:
  - 9:16.
  - 16:9.
  - 1:1.
- Additional supported ratios:
  - 4:5.
  - 3:4.
  - 4:3.
  - 21:9.
- Delivery presets:
  - 1080×1920.
  - 1920×1080.
  - 1080×1080.
  - 1080×1350.
  - 1080×1440.
  - 1440×1080.
  - 2560×1080 where final delivery requires ultrawide.
- Optional custom delivery size within safe configured bounds.

### Final video pipeline
- Scene candidate selection.
- FFmpeg normalization.
- CUT.
- CROSSFADE.
- Native scene audio keep/mute.
- Background music.
- Final FPS 24/25/30.
- Final H.264 MP4.
- Immutable final versions.
- Selected-candidate quality enhancement.
- Optional 2K final-quality path.
- Simple reframe/export pipeline.

### Production reliability
- Fix all 5 verified findings.
- Workflow executed-PoC gate.
- Real target-GPU tests.
- Benchmark.
- Backup + restore drill.
- Five-editor UAT.
- Deterministic frontend dependencies.

---

## 1.2 Explicitly not priority now

Do not spend major implementation time on:

- AI agent orchestration.
- Avatar presenter.
- Lip sync.
- Translation/localization.
- Full timeline editor.
- Keyframe animation editor.
- Mask editor.
- Color grading suite.
- Advanced multi-track audio editor.
- Comments/review workflow.
- SaaS billing.
- Multi-tenant isolation.
- Kubernetes.
- Kafka.
- RabbitMQ/Celery unless future scale proves necessary.
- Multiple workstations/distributed cluster scheduler.

These can be backlog items after the video-generation pipeline is production-qualified.

---

# 2. External technical basis to respect

The current plan should be grounded in current H3 capabilities rather than old assumptions.

## 2.1 MiniMax H3 official capabilities

Current official H3 documentation describes:

### Output
- 4–15 second video.
- 24 FPS.
- native stereo audio.
- short edge defaults to 768 px.
- broad aspect-ratio support including:
  - 21:9.
  - 16:9.
  - 4:3.
  - 1:1.
  - 3:4.
  - 9:16.
- 2K output through `H3-Regenerate-2K`.

### FL2VA family
- 0 images → text-to-video.
- 1 image → first-frame **or** last-frame generation.
- 2 images → first + last frame generation.

### Ref2VA family
- images <= 9.
- videos <= 3.
- each video 2–15 seconds.
- total reference-video duration <= 15 seconds.
- audio <= 3.
- each audio clip 2–15 seconds.
- total reference-audio duration <= 15 seconds.
- audio cannot be the sole reference input.
- maximum mixed files = 12.

### Current official runtime direction
MiniMax documents multi-GPU serving examples with a speed-oriented runtime mode.\
ComfyUI's current MiniMax H3 native workflow documentation also distinguishes:
- normal generation around 20 steps.
- optional Turbo/Lightning behavior around 8 steps for much faster generation, with a quality trade-off.

**Production rule:** treat any exact speed/quality values as benchmark candidates, not guarantees.

### Reference sources
- MiniMax H3 official repository: `https://github.com/MiniMax-AI/MiniMax-H3`
- ComfyUI MiniMax H3 native workflow docs: `https://docs.comfy.org/tutorials/video/minimax/minimax-h3`
- vLLM-Omni H3 recipes: `https://github.com/vllm-project/vllm-omni`

---

## 2.2 Product/workflow patterns worth copying

### Runway
Useful ideas:
- persistent image/video/audio references.
- reference labels.
- chained generation workflows.
- image/keyframe first, video second.
- video edit workflows.

Use these as UX/workflow inspiration only.

### Luma
Useful ideas:
- Modify Video.
- Reframe.
- ratio conversion as a separate operation from generation.

### ComfyUI
Use as:
- technical workflow execution engine.
- versioned graph provider.
- not business source of truth.

---

# 3. Current live-source baseline

At reviewed HEAD `01b740d`:

## 3.1 Current logical workflow registry

The repository currently contains:

- `H3_T2V_STANDARD`
- `H3_I2V_STANDARD`
- `H3_FIRST_LAST_STANDARD`
- `H3_R2V_1_IMAGE_REFERENCE`

Important limitations:

### T2V / I2V / First+Last
Present as executable or derived workflows.

### First+Last
Implemented by deriving a graph from the I2V workflow and adding the last-frame loader.

### R2V
Current executable profile is only:

`H3_R2V_1_IMAGE_REFERENCE`

Current profile explicitly limits:
- max reference images = 1.
- max reference videos = 0.
- max reference audio = 0.

This conflicts with the frontend/API contract that exposes up to 9 image, 3 video, and 3 audio references.

---

## 3.2 Current H3 PoC state

Current checked-in evidence says:

- preflight = PASS.
- executed = NO.
- `poc_verified = false`.

Therefore:

**No H3 workflow should be considered production-enabled until target-GPU executed evidence exists.**

---

## 3.3 Current resolution mismatch

Current backend request/schema accepts `width` and `height`.

Current generation snapshot stores requested width/height.

However current workflow registry patches:
- aspect ratio.
- duration.
- steps.
- prompt.
- seed.
- output prefix.

It does **not** patch actual width/height.

Actual ComfyUI graphs use `ResolutionSelector`.

Current graph defaults differ by workflow:
- T2V/I2V around 0.4 MP.
- R2V around 0.8 MP.

Therefore the application can currently record a width/height that is not the actual generated output resolution.

This must be fixed before production.

---

# 4. Mandatory verified defect fixes

These five defects are already source-verified and must be closed before release.

---

## FIX-01 — [P1] Preserve Product context when editing

### Current failure

`frontend/app/products/[productId]/page.tsx`

Current patch payload replaces `context` with either:

```ts
{ tone: data.tone }
```

or:

```ts
{}
```

Backend patch semantics replace the entire field.

Example existing context:

```json
{
  "tone": "premium",
  "packaging": "glass",
  "constraints": ["keep logo"]
}
```

Rename-only edit can reduce it to:

```json
{
  "tone": "premium"
}
```

### Required implementation

Preferred minimal fix:

```ts
const nextContext = {
  ...(product.context ?? {}),
  tone: data.tone ?? ""
};
```

If empty tone should mean delete only `tone`, remove only that key.

Do not delete unknown existing context keys.

### Required tests
- rename product preserves full context.
- update tone preserves unrelated keys.
- remove tone only removes tone.
- stale revision still produces revision conflict.

### Acceptance
No Product edit may silently delete unrelated context keys.

---

## FIX-02 — [P1] Repair MinIO backup command

### Current failure

`infra/scripts/backup.ps1`

Compose volume option is placed after the `api` service token, so it can be interpreted as container command arguments.

### Required implementation

All `docker compose run` options must be before service name:

```powershell
docker compose -f $ComposeFile run `
  --rm `
  --no-deps `
  -T `
  -v "${target}:/backup" `
  -v "${scriptRoot}:/infra-scripts:ro" `
  api `
  python /infra-scripts/minio_snapshot.py ...
```

### Also required
- check `$LASTEXITCODE` after native Docker commands.
- never write success status if DB or MinIO backup failed.
- validate manifest output exists.
- validate snapshot report exists.

### Acceptance
A backup contains:
- valid PostgreSQL dump.
- MinIO snapshot.
- report.
- checksum manifest.
- success status only after every stage passes.

---

## FIX-03 — [P1] Repair restore after backend/frontend split

### Current failures

`infra/scripts/restore.ps1`

Problems:
- default Compose path is wrong.
- MinIO helper path is not inside backend image.

### Required implementation

Default:

```powershell
[string] $ComposeFile = (Join-Path $PSScriptRoot "..\compose.yaml")
```

Mount script path similarly to backup:

```powershell
-v "${scriptRoot}:/infra-scripts:ro"
```

Run:

```text
python /infra-scripts/minio_snapshot.py restore ...
```

### Required verification
Actual isolated restore:
- new DB.
- new MinIO restore bucket.
- row counts.
- object counts.
- checksums.
- reconciliation.
- at least one generated/final asset downloadable.

### Acceptance
The documented restore command works from arbitrary current working directory.

---

## FIX-04 — [P1] Generate All stale idempotency replay

### Current failure

Frontend currently calls:

```ts
generateAll(videoId, {}, generateAllKeyRef.current)
```

If:
1. server creates batch.
2. response is lost.
3. storyboard changes.
4. editor presses Generate All again.

The old idempotency key + `{}` payload can replay the old batch.

### Required design

Generate All semantic fingerprint must contain at least:

```json
{
  "video_revision": 12,
  "scenes": [
    {
      "id": "scene-a",
      "revision": 4,
      "generation_config_hash": "..."
    }
  ]
}
```

Idempotency policy:

- same exact fingerprint retry → same key.
- scene revision changes → new key.
- video revision changes → new key.
- scene generation config changes → new key.
- eligible scene set changes → new key.
- successful action → reset.

Backend should also include semantic batch inputs in the idempotency payload rather than relying on empty `{}`.

### Required tests
- lost response + unchanged storyboard → replay old batch.
- lost response + changed scene → create new batch.
- added scene → create new batch.
- reordered/disabled scene → fingerprint behavior explicitly tested.
- config changes → new batch.

---

## FIX-05 — [P2] Trusted client identity for login throttle

### Current failure

Next proxy forwards requests to FastAPI.

Backend sees frontend service as client host.

Multiple real editor browsers can therefore share one IP throttle bucket.

### Required implementation

Choose one production-safe model.

Preferred:

```text
Trusted reverse proxy
    ↓
sets sanitized client identity
    ↓
Next server
    ↓
FastAPI trusts only Next/ingress internal hop
```

Do not trust arbitrary browser `X-Forwarded-For`.

Possible design:
- ingress replaces incoming forwarded headers.
- Next passes a trusted internal client-IP header.
- backend only reads it when request came from configured trusted proxy address/network.
- otherwise uses `request.client.host`.

### Required tests
- editor A wrong password does not throttle editor B.
- same editor/IP repeated failure is throttled.
- forged browser forwarded header is ignored.
- identity bucket still protects distributed password guessing.

---

# 5. Additional generation-specific correctness fixes

These are required for the production generation pipeline.

---

## GEN-FIX-01 — Replace fake width/height contract

### Problem
Current API accepts width/height but executable workflow does not use them directly.

### Required change
For normal editor generation requests:

**remove or deprecate direct free-form width/height as the primary contract.**

Use:

```text
aspect_ratio
quality_profile
```

Backend resolves the actual generation canvas from the approved workflow profile.

If custom dimensions remain available:
- advanced/admin-only.
- workflow must explicitly expose `WIDTH` and `HEIGHT` slots.
- validator must reject them otherwise.

### Snapshot
Persist both:

```text
requested_aspect_ratio
requested_quality_profile
resolved_width
resolved_height
actual_output_width
actual_output_height
```

`actual_output_*` comes from ffprobe/media inspection after generation.

---

## GEN-FIX-02 — Never enable non-executed H3 workflow

### Problem
Admin can currently approve a workflow based on static/hash checks while `poc_verified=false`.

### Required production gate

A workflow must have lifecycle similar to:

```text
REGISTERED
→ STATIC_VALIDATED
→ POC_EXECUTED
→ BENCHMARKED
→ APPROVED
→ ENABLED
```

Minimal implementation can reuse existing DB fields plus additional profile/release evidence fields.

At minimum, enable operation must reject when:

```text
profile.poc_verified != true
```

Production should also record:
- model hashes.
- custom-node version hashes.
- workflow hash.
- Comfy version.
- execution timestamp.
- successful output metadata.

---

## GEN-FIX-03 — UI capability must match active workflow capability

Do not show:
- video reference selector.
- audio reference selector.
- 9-image claim.

unless currently selected/enabled workflow supports them.

Preferred:
- backend returns active generation capabilities.
- frontend renders controls from capability DTO.

Example:

```json
{
  "mode": "r2v",
  "max_reference_images": 9,
  "max_reference_videos": 3,
  "max_reference_audio": 3,
  "supports_turbo": false,
  "supported_aspect_ratios": ["9:16", "16:9", "1:1"]
}
```

---

## GEN-FIX-04 — Negative prompt semantics

Current UI exposes Negative Prompt, but current workflow registry does not inject a `NEGATIVE_PROMPT` slot.

H3 is CFG-distilled; do not pretend this is a classical Stable-Diffusion negative-conditioning control unless the actual approved workflow supports it.

Choose one:
1. merge negative constraints into creative/context prompt.
2. rename UI to "Avoid / Constraints".
3. only expose a true negative control if workflow runtime explicitly supports it.

Preferred product wording:

`Avoid / Visual constraints`

and include it in execution prompt composition.

---

# 6. Target generation-mode contract

Use explicit, reproducible modes.

## 6.1 Supported editor modes

```text
AUTO
T2V
FIRST_FRAME
LAST_FRAME
FIRST_LAST
REF2VA
```

Internally map these to model families.

### AUTO router

```text
if reference image/video/audio exists:
    REF2VA
elif first_frame and last_frame:
    FIRST_LAST
elif first_frame:
    FIRST_FRAME
elif last_frame:
    LAST_FRAME
else:
    T2V
```

Do not force normal editors to understand technical model names.

---

## 6.2 Backward-compatible API recommendation

Current API uses:

```text
t2v
i2v
i2v_first_last
r2v
```

To avoid unnecessarily breaking old records:

- keep `i2v` = first-frame legacy mode.
- add `i2v_last`.
- keep `i2v_first_last`.
- keep `r2v`.

Optional editor-facing `AUTO` is resolved before persistence.

Persist resolved mode, not just `AUTO`.

---

# 7. Build Last-Frame → Video

H3 FL2VA supports a single image as either first or last frame.

## Backend
Update:
- schema literals.
- workflow router.
- H3 validator.
- workflow registry/loader.
- tests.

### Validation

`i2v_last`:
- requires last frame.
- forbids first frame.
- forbids Ref2VA references.

### Workflow
Reuse FL2VA graph and patch the `last_frame` input.

Do not create a totally separate model runtime.

### Frontend
Generation config:

```text
Starting frame: optional
Ending frame: optional
```

Mode can be auto-resolved.

### Acceptance
- last-frame-only request reaches H3 graph.
- generated output ends consistently toward supplied image.
- snapshot records input asset checksum and role.

---

# 8. Build Full Ref2VA

This is one of the highest-priority features.

---

## 8.1 Replace current one-image workflow

Current:

`H3_R2V_1_IMAGE_REFERENCE`

Target:

`H3_REF2VA_STANDARD`

Potential optional profile:

`H3_REF2VA_FAST`

Only create FAST if real benchmark shows acceptable identity retention.

Use current official ComfyUI Ref2VA template or a verified compatible graph.

Do not invent node IDs from memory.

Agent must:
1. inspect current official ComfyUI template.
2. inspect `/object_info` on target runtime.
3. export API-format workflow.
4. define symbolic slots.
5. static validate.
6. execute real PoC.
7. record hashes.

---

## 8.2 Ref2VA validator

Extend `H3Profile` with:

```text
video_min_seconds
video_max_seconds
video_total_max_seconds
max_total_reference_files
audio_requires_visual_reference
```

Target defaults based on official H3 limits:

```text
images <= 9
videos <= 3
audio <= 3
all refs <= 12

video each: 2–15 s
video total: <=15 s

audio each: 2–15 s
audio total: <=15 s

audio-only Ref2VA: reject
```

---

## 8.3 Asset metadata

Reference videos require:
- duration.
- width.
- height.
- FPS.
- codec.
- checksum.

Reference audio requires:
- duration.
- codec.
- channels if available.
- checksum.

Do not run H3 with unvalidated media metadata.

---

## 8.4 Reference ordering

Persist ordered references.

Snapshot example:

```json
{
  "references": [
    {
      "kind": "IMAGE",
      "role": "REFERENCE_IMAGE",
      "position": 1,
      "asset_id": "...",
      "checksum": "..."
    }
  ]
}
```

Do not treat references as unordered sets.

---

## 8.5 Frontend UX

Separate panels:

### Images
- thumbnail.
- filename.
- dimensions.
- ordered index.
- selected count / max.

### Video
- thumbnail if available.
- duration.
- dimensions.
- selected count / max.

### Audio
- duration.
- selected count / max.

Show validation before submit:
- invalid duration.
- too many files.
- audio-only invalid.
- total duration exceeded.

---

# 9. Generation profiles for speed

This is critical.

Do not expose raw `steps=1..100` as the main editor UX.

Use:

```text
DRAFT
STANDARD
HIGH
```

---

## 9.1 Draft profile

Goal:
**fast iteration**.

Initial benchmark candidate for FL2VA/T2V/I2V:
- Turbo/Lightning mode where current official Comfy runtime supports it.
- around 8 steps.
- native H3 canvas.
- 24 FPS.
- normal duration.

Do not hardcode final production settings until benchmark.

UI:

```text
Draft — fastest preview
```

Use for:
- prompt iteration.
- storyboard candidate.
- testing camera/motion.

---

## 9.2 Standard profile

Goal:
**normal candidate generation**.

Initial benchmark candidate:
- non-Turbo/native.
- around 20 steps for T2V/I2V based on current Comfy workflow default.
- native H3 resolution.
- 24 FPS.

UI:

```text
Standard — recommended
```

---

## 9.3 High profile

Goal:
**higher-quality candidate when needed**.

Initial benchmark candidate:
- 20–25 steps depending workflow.
- no experimental LoRA by default.
- native H3 canvas.

Only keep this profile if creative benchmark shows useful gain.

Do not automatically make all generations High.

---

## 9.4 Ref2VA speed policy

Current repo's Ref2VA graph hardcodes Turbo + realism LoRA.

This must not silently become production default.

Required:
- build a clean Standard Ref2VA profile.
- benchmark any Turbo Ref2VA separately.
- benchmark reference identity degradation.
- realism LoRA must be explicit/versioned, not hidden inside default workflow.

---

# 10. Generation-resolution model

The correct production pipeline is:

```text
editor chooses:
aspect ratio + quality profile
        ↓
approved workflow resolves generation canvas
        ↓
H3 generates native candidate
        ↓
ffprobe actual result
        ↓
persist actual dimensions
```

---

## 10.1 Mandatory generation ratios

First production set:

```text
9:16
16:9
1:1
```

Additional ratios after PoC:

```text
4:5
3:4
4:3
21:9
```

Add only when:
- workflow accepts them.
- resolution selector maps correctly.
- benchmark passes.
- output inspection confirms correct dimensions.

---

## 10.2 Do not conflate generation resolution with delivery resolution

Example:

```text
H3 generation:
1344×768, 24 FPS
```

can become:

```text
final delivery:
1920×1080, 30 FPS
```

FFmpeg resize is not the same thing as native H3 high-resolution generation.

---

# 11. Selected-candidate quality enhancement

Only enhance the chosen generation.

Pipeline:

```text
Generate candidates
→ compare
→ select
→ enhance selected
→ assemble
```

Never expensive-upscale every candidate by default.

---

## 11.1 2K path

H3 full system includes `H3-Regenerate-2K`.

Implement via abstraction:

```text
EnhancementProvider
```

Possible providers:

```text
H3_REGENERATE_2K
LOCAL_UPSCALER
NONE
```

Do not label a normal FFmpeg scale operation as H3 2K regeneration.

If official H3 2K path requires external MiniMax API and production policy requires fully offline operation:
- make it optional.
- provide local upscaler separately.
- clearly display which provider generated the enhanced result.

---

## 11.2 Asset lineage

Enhanced output must be a new immutable Asset.

Persist:

```text
source_asset_id
operation = ENHANCE
provider
profile
source_checksum
output_checksum
actual_width
actual_height
```

---

# 12. Reframe and delivery-format pipeline

Keep it simple.

Production requirement now is mainly **reliable size conversion**, not advanced generative editing.

---

## 12.1 V1 production reframe

Support deterministic delivery conversion:

```text
FIT_PAD
CENTER_CROP
SMART_CROP   optional later
```

For now:
- scale.
- preserve aspect.
- pad or crop based on preset.
- never stretch.

AI outpainting can remain future backlog.

---

## 12.2 Delivery presets

Create typed presets.

### Vertical Social
```text
code: SOCIAL_VERTICAL_1080
ratio: 9:16
width: 1080
height: 1920
fps: 30 or configurable 24/30
container: mp4
video_codec: h264
pixel_format: yuv420p
audio_codec: aac
audio_rate: 48000
```

### Landscape Full HD
```text
1920×1080
16:9
```

### Square
```text
1080×1080
1:1
```

### Portrait Feed
```text
1080×1350
4:5
```

### Portrait 3:4
```text
1080×1440
3:4
```

### Landscape 4:3
```text
1440×1080
4:3
```

### Ultrawide
```text
2560×1080
21:9
```

Current assembly size constraints must be updated if a preset exceeds current maximum.

---

## 12.3 Final output codecs

Mandatory production output:

```text
MP4
H.264
yuv420p
AAC
faststart
```

Optional later:
- H.265.
- ProRes master.
- WebM.

Do not prioritize these before core generation quality/speed.

---

# 13. FFmpeg assembly improvements

Keep current architecture.

Add/verify:

- exact target dimensions.
- ratio-safe scaling.
- pad/crop policy.
- final FPS.
- scene audio normalization.
- background music loop.
- background volume.
- scene audio mute.
- crossfade validation.
- output playable validation.
- output duration validation.
- output checksum.
- output width/height/fps metadata.

---

## 13.1 Fix background-audio selection

Generation-focused production cleanup:

Frontend Assembly should query:
- `status=READY`
- audio kind/content type.
- project/product scope as appropriate.

Backend should also validate allowed asset scope.

Never rely only on frontend filtering.

---

# 14. Prompt Preview → exact generation

Current preview is informative but not authoritative.

Target:

```text
Preview Prompt
→ editor accepts
→ execution_prompt frozen
→ scene revision frozen
→ video revision frozen
→ Generate
```

Generation request must contain:

```text
execution_prompt
source_scene_revision
source_video_revision
```

Backend:
- reject stale preview.
- never silently recompose a different prompt after editor approved one.

Add UI controls:
- Accept & Generate.
- Edit execution prompt.
- Regenerate preview.
- Cancel.

---

# 15. Regenerate vs Variation vs Retry

These must be explicit.

## Technical retry
- same Generation.
- new GenerationAttempt.
- only for execution failure/recovery.

## Regenerate
- new Generation.
- same semantic snapshot.
- normally new seed unless seed locked.

## Variation
- new Generation.
- parent_generation_id.
- inherit parent's frozen execution snapshot.
- explicit overrides only.

Backend must not rebuild variation from mutable current scene unless user explicitly chooses "use current scene".

---

# 16. Per-scene generation configuration

Long video must not default all scenes to T2V.

Persist each scene's desired generation config.

Suggested shape:

```json
{
  "mode": "AUTO",
  "quality_profile": "DRAFT",
  "first_frame_asset_id": null,
  "last_frame_asset_id": null,
  "reference_image_asset_ids": [],
  "reference_video_asset_ids": [],
  "reference_audio_asset_ids": [],
  "seed_policy": "RANDOM"
}
```

Implementation choice:
- dedicated typed JSON column on Scene, or
- separate `scene_generation_configs` table.

Prefer the simplest migration that preserves validation and revision semantics.

Changing generation config must bump scene/video revision.

---

# 17. Generate All production behavior

Target:

```text
Generate All
→ gather enabled eligible scenes
→ read each scene's saved generation config
→ freeze semantic batch snapshot
→ create one generation per scene
```

It must not apply one global T2V default to all scenes.

UI should show preflight summary:

```text
Scene 1 — T2V — Draft
Scene 2 — First Frame — Standard
Scene 3 — Ref2VA — Draft
Scene 4 — First+Last — Standard
```

Then user confirms.

---

# 18. Fast runtime requirements

Fast generation is a primary product requirement.

---

## 18.1 Persistent runtime

Do not reload H3 model per request.

ComfyUI/H3 service must remain warm.

Record:
- cold first request.
- warmed request latency.

Warm the runtime after startup before measuring production SLA.

---

## 18.2 Separate model-family cost

FL2VA and Ref2VA may have different loading/VRAM characteristics.

Benchmark:
- FL2VA warm latency.
- Ref2VA warm latency.
- switching family cost.
- resident-both feasibility.
- VRAM peak.

Do not guess.

---

## 18.3 Benchmark matrix

Minimum:

### Modes
```text
T2V
FIRST_FRAME
LAST_FRAME
FIRST_LAST
REF2VA_IMAGE
REF2VA_VIDEO
REF2VA_AUDIO+IMAGE
REF2VA_MIXED
```

### Duration
```text
5s
10s
15s
```

### Ratio
```text
9:16
16:9
1:1
```

### Profiles
```text
DRAFT
STANDARD
HIGH (if retained)
```

### Measurements
```text
cold latency
warm latency
queue wait
H3 execution time
total wall time
peak VRAM
GPU utilization
failure rate
actual width/height
actual duration
FPS
audio present
```

Run >=3 warmed repeats after excluding warmup.

Persist JSON results.

---

## 18.4 Profile promotion rule

A profile cannot become default just because it is fast.

Evaluate:
- product identity.
- logo/packaging fidelity.
- motion.
- audio quality.
- visible artifacts.
- reference consistency.

Draft can trade quality for speed.

Standard must meet creative acceptance.

---

## 18.5 Concurrency

Current safe baseline is one admitted Comfy prompt.

Keep it until benchmark proves more concurrency.

Test:
- 1 concurrent generation.
- 2.
- 3.
- etc.

Stop increasing when:
- OOM.
- instability.
- severe latency collapse.
- poor interactive fairness.

For a 5-editor system, stable queueing is better than aggressive OOM-prone parallelism.

---

# 19. Workflow registry production upgrade

Each production workflow version should carry:

```text
code
mode
version
workflow_hash
slot_map_hash
profile
model hashes
LoRA hashes
ComfyUI version/commit
custom-node version/commit
poc_verified
benchmark_verified
approved_at
```

Admin enable must enforce verification gates.

Do not allow arbitrary graph drift.

---

# 20. Frontend generation UX target

The normal editor dialog should become simple.

## Basic controls

```text
Mode:
  Auto
  Text
  Starting frame
  Ending frame
  Start + End
  References

Quality:
  Draft
  Standard
  High

Ratio:
  inherit from Video

Duration:
  inherit from Scene

Seed:
  random / optional fixed
```

Advanced controls can be collapsed:
- exact seed.
- workflow version for admin/debug.
- advanced profile diagnostics.

Do not show sampler/scheduler by default.

---

# 21. Candidate history improvements

Each generation card should show:

```text
operation
mode
quality profile
duration
actual resolution
FPS
seed
workflow version
elapsed time
output size
status
```

Actions:

```text
Preview
Select
Regenerate
Variation
Reuse Settings
Download
Enhance
```

---

# 22. Production metadata truth

After generation, inspect output.

Store actual:

```text
width
height
duration
fps
has_audio
audio sample rate if available
codec
file size
checksum
```

Do not infer these only from request.

---

# 23. Schema/API changes

The agent should implement the smallest clean migration set needed.

Likely changes:

## Generation request
Add:
- `quality_profile`
- `i2v_last` mode.
- capability-driven validation.

Deprecate editor-controlled arbitrary width/height.

## Scene
Add saved generation config.

## Generation snapshot
Persist:
- quality profile.
- resolved technical profile.
- requested ratio.
- resolved generation dimensions.
- actual output media metadata.
- full ordered refs.
- frozen execution prompt.

## Workflow records
Persist PoC/benchmark evidence or sufficient gate metadata.

## Asset derivation
If enhancement/reframe built now:
- source asset relationship.
- operation/provider metadata.

---

# 24. Tests required

No feature is complete without automated coverage.

---

## 24.1 Unit tests

### H3 validator
- T2V rejects assets.
- first-frame mode.
- last-frame mode.
- first+last mode.
- Ref2VA counts.
- Ref2VA duplicate IDs.
- video duration.
- audio duration.
- total duration.
- max 12 mixed refs.
- audio-only reject.
- resolution/profile validation.

### Router
Every input combination maps to exactly one mode.

### Profile resolution
- Draft.
- Standard.
- High.
- supported ratios.

---

## 24.2 Integration tests

- create each generation mode.
- workflow slots patched correctly.
- frozen snapshot.
- generated asset metadata.
- variation inheritance.
- regenerate semantics.
- Generate All per-scene configs.
- idempotency after scene revision changes.
- workflow cannot enable without PoC.
- asset scope.
- background audio validation.
- assembly presets.

---

## 24.3 Frontend tests / source-level checks

At minimum ensure:
- mode form validation matches backend capabilities.
- Last Frame selectable.
- Ref2VA limits visible.
- quality profile replaces raw step primary UX.
- Generate All fingerprint changes with semantic inputs.
- Product context preserved.
- proxy client identity design tested.

---

## 24.4 Runtime tests

Required before production:
- real ComfyUI.
- real H3.
- real MinIO.
- PostgreSQL.
- worker restart.
- cancel.
- retry.
- five editor clients.
- backup and restore.

---

# 25. Production acceptance criteria

The release is production-ready only when all conditions below are true.

## Generation
- [ ] T2V executed PASS.
- [ ] first-frame executed PASS.
- [ ] last-frame executed PASS.
- [ ] first+last executed PASS.
- [ ] Ref2VA image executed PASS.
- [ ] Ref2VA video executed PASS.
- [ ] Ref2VA audio+visual executed PASS.
- [ ] mixed Ref2VA executed PASS.
- [ ] selected workflow/profile hash persisted.
- [ ] actual output dimensions persisted.

## Speed
- [ ] Draft benchmarked.
- [ ] Standard benchmarked.
- [ ] High retained only if useful.
- [ ] warmup behavior documented.
- [ ] p50/p95 latency recorded.
- [ ] peak VRAM recorded.
- [ ] stable concurrency determined.

## Resolution / formats
- [ ] 9:16 generation works.
- [ ] 16:9 generation works.
- [ ] 1:1 generation works.
- [ ] extra ratios only exposed after PASS.
- [ ] 1080×1920 export works.
- [ ] 1920×1080 export works.
- [ ] 1080×1080 export works.
- [ ] 1080×1350 export works.
- [ ] 1080×1440 export works.
- [ ] assembly never stretches source incorrectly.

## Reliability
- [ ] all 5 verified defects closed.
- [ ] frontend build deterministic.
- [ ] workflow enable gate enforced.
- [ ] no duplicate batch after lost response.
- [ ] backup works.
- [ ] restore works.
- [ ] 5-editor login throttle isolation works.
- [ ] 5-editor generation UAT passes.

---

# 26. Recommended implementation phases

---

## PHASE 0 — Fix verified production defects

Implement:
- Product context preserve.
- backup script.
- restore script.
- Generate All idempotency semantics.
- trusted client identity.
- `yarn.lock`.
- generated-cache ignore.

Exit:
- all regression tests pass.

---

## PHASE 1 — Establish real H3 execution baseline

Tasks:
- pin ComfyUI version.
- pin custom nodes.
- pin H3 models.
- record hashes.
- run existing T2V/I2V/First+Last/R2V preflight.
- execute real target-GPU PoC.
- record actual output dimensions, FPS, audio, VRAM, latency.

Do not expand features before this baseline is understood.

---

## PHASE 2 — Generation profile + resolution rewrite

Tasks:
- remove misleading width/height editor contract.
- introduce Draft/Standard/High.
- resolve actual generation dimensions from approved profile.
- expand ratio metadata.
- persist requested/resolved/actual output properties.
- add capability endpoint.

Exit:
- snapshot accurately matches graph and output.

---

## PHASE 3 — Complete FL2VA

Tasks:
- T2V cleanup.
- First Frame.
- Last Frame.
- First+Last.
- unified router.
- profile-driven Turbo/Standard behavior.
- UI update.

Exit:
- all FL2VA modes executed PASS.

---

## PHASE 4 — Full Ref2VA

Tasks:
- replace one-image reference graph.
- implement full image/video/audio support.
- validation.
- ordered references.
- capability-driven UI.
- real mixed-reference test matrix.

Exit:
- full H3 Ref2VA limits correctly enforced and executed.

---

## PHASE 5 — Fast-generation optimization

Tasks:
- Draft Turbo benchmark.
- Standard benchmark.
- runtime warmup.
- model residency study.
- queue/concurrency benchmark.
- remove hidden unverified LoRA from defaults.
- publish default profile decisions.

Exit:
- fast profile chosen from data.

---

## PHASE 6 — Editor generation workflow

Tasks:
- Prompt Accept/freeze.
- Regenerate.
- Variation inheritance.
- Reuse Settings.
- per-scene generation config.
- Generate All preflight.
- semantic idempotency.

Exit:
- long video can mix generation modes safely.

---

## PHASE 7 — Final quality + delivery formats

Tasks:
- selected-output enhancement abstraction.
- optional H3 2K provider.
- deterministic delivery resize.
- delivery presets.
- final metadata inspection.
- update assembly dimension bounds.
- reframe FIT_PAD/CENTER_CROP.

Exit:
- one selected candidate can become all required delivery outputs.

---

## PHASE 8 — Production qualification

Tasks:
- 5 editors.
- benchmark.
- fault injection.
- restart.
- cancel/retry.
- MinIO loss/recovery.
- backup.
- restore.
- final UAT evidence.

Exit:
- production release.

---

# 27. Definition of done for code quality

Agent must not stop after UI looks correct.

Every phase must include:

1. source changes.
2. schema/migration where needed.
3. backend tests.
4. frontend typecheck.
5. frontend lint.
6. frontend production build.
7. OpenAPI/type alignment.
8. docs update.
9. runtime verification where environment permits.
10. explicit `NOT_RUN` where external GPU/Docker/runtime is unavailable.

Never fabricate PASS.

---

# 28. Git safety

Current workspace is dirty.

At reviewed state:

```text
unstaged:
  frontend/next-env.d.ts

untracked:
  tasks/archive/reports/review-2026-10-05.md
```

Hard rules:

- no `git clean -fd`.
- no `git reset --hard`.
- do not delete unrelated untracked files.
- do not overwrite user's local work.
- inspect `git status` first.
- no commit/push unless user explicitly asks.

---

# 29. Agent execution prompt

Copy the entire prompt below into the coding agent after placing this plan inside the repository.

---

## MASTER IMPLEMENTATION PROMPT

```text
You are continuing the `ai-video-studio` repository.

Your task is not to produce another high-level plan. You must inspect the live source, implement the production video-generation roadmap, test it, review your own changes, and fix defects until the repository is in the strongest state that can be achieved in the available environment.

PRIMARY REFERENCE
Read the implementation specification first:

    ai-video-studio-production-generation-plan.md

Treat it as the requested target scope, but verify every current-source assumption before editing.

CURRENT KNOWN BASELINE
Previously reviewed:
- branch: codex/production-safety-fixes
- HEAD at planning time: 01b740d
- workspace was dirty

Do NOT assume branch/HEAD are still unchanged.
Start by calling/printing:
- workspace/repo identity
- git status
- current branch
- current HEAD
- relevant execution/test evidence

GIT SAFETY — HARD RULES
- NEVER run `git clean -fd`.
- NEVER run `git reset --hard`.
- Do not delete unrelated untracked files.
- Do not overwrite pre-existing user changes.
- Do not commit or push unless explicitly requested.
- Preserve the monorepo split.
- Use Yarn for frontend; do not migrate package managers.

PRODUCT GOAL
Turn the current application into a production-ready AI video generation studio focused on:
1. complete H3 video generation modes,
2. fast generation,
3. correct resolution/aspect-ratio handling,
4. full Ref2VA,
5. per-scene generation configuration,
6. long-video generation,
7. final-quality enhancement,
8. reliable multi-format final delivery,
9. production verification.

DO NOT spend major effort on:
- AI agents,
- avatar/lip-sync,
- translation,
- full timeline editor,
- Kubernetes,
- Kafka,
- SaaS/multi-tenant,
unless required to complete the specified generation pipeline.

MANDATORY VERIFIED BUGS
You MUST fix and add regression coverage for all five:

1. Product edit destroys unrelated Product.context keys.
   Known area:
   frontend/app/products/[productId]/page.tsx

2. backup.ps1 passes the helper `-v` mount after the `api` service token, so the MinIO backup helper is not run correctly.
   Known area:
   infra/scripts/backup.ps1

3. restore.ps1 still uses obsolete Compose/helper paths after backend/frontend split.
   Known area:
   infra/scripts/restore.ps1

4. Generate All can replay an old idempotent batch after storyboard/scene state changes because the frontend sends `{}` and retains the same key after an ambiguous request.
   Known areas:
   frontend/app/videos/[videoId]/page.tsx
   backend/apps/api/app/api/generations.py

5. All editors may share a login IP throttle bucket through the Next proxy because trusted client identity is not preserved.
   Known areas:
   frontend/app/api/[...path]/route.ts
   backend/apps/api/app/api/auth.py
   backend/apps/api/app/core/login_throttle.py

In addition, fix any new regressions you discover while implementing the roadmap.

H3 PRODUCTION REQUIREMENTS

A. GENERATION MODES

Production must support:
- T2V
- First Frame -> Video
- Last Frame -> Video
- First + Last Frame -> Video
- Full Ref2VA

Keep backward compatibility where reasonable with existing mode values.
Add `i2v_last` or an equally explicit persisted representation for last-frame-only generation.

AUTO mode may exist at the UI/business layer, but persist the resolved technical mode.

Routing logic should be unambiguous:

refs -> Ref2VA
first + last -> First+Last
first -> First Frame
last -> Last Frame
none -> T2V

B. FULL REF2VA

Current source may expose 9 image / 3 video / 3 audio in API/UI while the executable workflow supports only 1 image. Correct this mismatch.

Implement actual executable Full Ref2VA using a current verified H3/ComfyUI workflow.

Official limits to enforce:
- images <= 9
- videos <= 3
- audio <= 3
- mixed files <= 12
- each video 2-15 seconds
- total video duration <= 15 seconds
- each audio 2-15 seconds
- total audio duration <= 15 seconds
- audio cannot be the only reference category

Do not invent Comfy node IDs.
Inspect the real official/current workflow and target `/object_info`.
Version symbolic slots and workflow hashes.

C. GENERATION PROFILES / SPEED

Replace raw editor-facing "steps 1-100" as the primary quality UX with:

- DRAFT
- STANDARD
- HIGH

Use exact parameters only after benchmark.

Initial benchmark candidates for FL2VA:
- Draft: current official Turbo/Lightning path around 8 steps where supported
- Standard: normal path around 20 steps
- High: test around 20-25 only if quality improves meaningfully

Do not assume these are production values without measurement.

Ref2VA:
- create a clean Standard profile
- do not silently make the current hard-coded Turbo + realism LoRA the production default
- benchmark Turbo Ref2VA separately
- version every LoRA and hash

D. RESOLUTION CONTRACT

This is a critical correctness requirement.

Current source may accept width/height that are not actually patched into the graph because the workflow uses ResolutionSelector.

Fix this.

Normal editor generation must use:

    aspect_ratio + quality_profile

The approved workflow/profile resolves actual generation dimensions.

Persist:
- requested_aspect_ratio
- requested_quality_profile
- resolved_width
- resolved_height
- actual_output_width
- actual_output_height
- actual_output_fps
- actual_output_duration

Actual output metadata must come from media inspection/ffprobe, not request assumptions.

Required generation ratios first:
- 9:16
- 16:9
- 1:1

Then add only after executed PASS:
- 4:5
- 3:4
- 4:3
- 21:9

E. PROMPT FREEZE

Current Prompt Preview must become an exact-input approval flow.

Required flow:
Preview
-> Accept/Edit
-> freeze execution_prompt + scene revision + video revision
-> Generate exact approved prompt

Frontend must submit:
- execution_prompt
- source_scene_revision
- source_video_revision

Backend must reject stale accepted preview.

F. REGENERATE / VARIATION / RETRY

Keep semantics separate:

Technical retry:
- same SceneGeneration
- new GenerationAttempt

Regenerate:
- new SceneGeneration
- same frozen semantic input
- usually new seed unless explicitly locked

Variation:
- new SceneGeneration
- parent_generation_id
- default from parent's frozen snapshot
- only explicit overrides differ

Do not rebuild a Variation from mutable current Scene by default.

G. PER-SCENE GENERATION CONFIG

Long videos must allow different modes per scene.

Persist per-scene config containing at least:
- mode/AUTO
- quality profile
- frame assets
- reference assets
- seed policy

Changing config must participate in optimistic revision semantics.

Generate All must:
- read every eligible scene config
- show/return a deterministic semantic batch
- create one generation per scene using that config

H. GENERATE ALL IDEMPOTENCY

Semantic fingerprint must include current video/scene/config state.

Same unchanged retry -> same idempotency key.
Changed scene/video/config/eligible set -> new action/key.

Backend idempotency payload must not remain effectively `{}`.

I. FAST RUNTIME

Do not reload H3 per request.

Measure:
- cold first request
- warm requests
- FL2VA latency
- Ref2VA latency
- switching family cost
- peak VRAM
- queue wait
- total wall time

Run benchmark matrix across:
- modes
- 5/10/15 seconds
- 9:16 / 16:9 / 1:1
- Draft / Standard / High

Use >=3 warmed repeats after warmup.

Do not increase concurrency until target-GPU evidence proves it safe.

J. WORKFLOW RELEASE GATE

Current static validation is not enough.

An enabled production workflow must have executed PoC evidence.

At minimum reject enable if:
    poc_verified != true

Prefer lifecycle:
REGISTERED
STATIC_VALIDATED
POC_EXECUTED
BENCHMARKED
APPROVED
ENABLED

Record:
- workflow hash
- slot-map hash
- H3 model hash
- LoRA hash
- ComfyUI version/commit
- custom-node versions
- executed test timestamp
- output metadata
- benchmark evidence

K. FINAL QUALITY

Only enhance selected candidates.

Implement an enhancement abstraction:
- NONE
- H3_REGENERATE_2K if available/allowed
- LOCAL_UPSCALER if used

Do not call a normal FFmpeg resize "H3 2K".

Enhanced output is a new immutable Asset with source lineage.

L. DELIVERY FORMATS

Separate generation canvas from delivery resolution.

Create delivery presets at least:

1. SOCIAL_VERTICAL_1080
   1080x1920
   9:16

2. LANDSCAPE_FHD
   1920x1080
   16:9

3. SQUARE_1080
   1080x1080
   1:1

4. PORTRAIT_4_5
   1080x1350
   4:5

5. PORTRAIT_3_4
   1080x1440
   3:4

6. LANDSCAPE_4_3
   1440x1080
   4:3

7. ULTRAWIDE_21_9 if supported
   e.g. 2560x1080

Mandatory final container:
- MP4
- H.264
- yuv420p
- AAC
- +faststart

Support final FPS:
- 24
- 25
- 30

Use ratio-safe scale + pad/crop.
Never stretch.

M. ASSEMBLY

Preserve existing immutable final-version architecture.

Verify/improve:
- CUT
- CROSSFADE
- scene audio keep/mute
- background music
- scoped READY audio assets
- final media metadata
- output checksum
- final actual resolution/FPS/duration
- dimension limits compatible with delivery presets

N. FRONTEND

The normal editor UI should primarily expose:

Mode:
- Auto
- Text
- Starting frame
- Ending frame
- Start + End
- References

Quality:
- Draft
- Standard
- High

Seed:
- random
- optional fixed

References:
- image/video/audio selectors that dynamically match active backend capability

Do not show controls that the active workflow cannot execute.

Generation history cards should show:
- operation
- mode
- profile
- seed
- actual resolution
- FPS
- duration
- workflow version
- execution time
- output size
- status

Actions:
- Preview
- Select
- Regenerate
- Variation
- Reuse Settings
- Download
- Enhance

O. PRODUCT CONTEXT

While fixing verified context loss, preserve arbitrary future Product.context keys.

Do not collapse existing context to `{tone}`.

P. BACKUP / RESTORE

Repair both scripts and run an actual isolated restore if Docker/runtime is available.

Backup success is valid only if:
- PostgreSQL dump exists
- MinIO snapshot exists
- checksum manifest exists
- helper exit code == 0

Restore verification:
- DB rows restored
- media objects restored
- reconciliation clean
- downloadable media works

Q. TRUSTED CLIENT IDENTITY

Fix cross-editor login throttle coupling using a trusted-proxy design.

Do not trust arbitrary browser-supplied forwarded IP headers.

R. FRONTEND DEPENDENCIES

Use Yarn.
Create/commit yarn.lock if absent.
Docker must use `yarn install --frozen-lockfile`.
Ignore generated `*.tsbuildinfo`.

S. TESTS

Add or update:
- H3 validator unit tests
- workflow router tests
- generation preparation tests
- full Ref2VA validation tests
- last-frame mode tests
- profile/resolution tests
- prompt freeze/stale revision tests
- Regenerate semantics
- Variation snapshot inheritance
- Generate All semantic-idempotency regressions
- Product context preservation
- login throttle proxy regressions
- assembly delivery preset tests
- backup/restore command tests where feasible

Run everything the environment permits.

FRONTEND VERIFICATION
Run:
- yarn install --frozen-lockfile
- yarn typecheck
- yarn lint
- yarn build

BACKEND VERIFICATION
Use the repository's supported Docker/test path.
Run unit/integration/contract/migration checks relevant to changed code.

H3 EXTERNAL GATE
If target GPU/ComfyUI is unavailable:
- do NOT claim executed PASS
- leave `poc_verified=false`
- provide exact commands/media needed for user to run
- keep workflows disabled

If target GPU is available:
- run real PoC
- collect hashes
- collect actual media metadata
- run benchmark matrix
- update evidence documents

SELF REVIEW
After implementation:
1. run git status
2. inspect full diff
3. independently look for:
   - stale API/frontend types
   - invalid migrations
   - wrong idempotency semantics
   - misleading UI capability
   - unhandled old data
   - workflow/profile drift
   - source/output resolution mismatch
   - asset scope bugs
   - race conditions
   - backup/restore regressions
4. fix all issues found
5. rerun verification

FINAL REPORT
Return:

1. Workspace / branch / HEAD
2. Changed files grouped by phase
3. Features implemented
4. Verified 5 original defects and their fixes
5. Additional defects found/fixed
6. H3 mode matrix
7. Generation profile matrix
8. Resolution/aspect-ratio matrix
9. Delivery preset matrix
10. Tests actually run with exact PASS/FAIL counts
11. H3 runtime/benchmark evidence actually measured
12. Anything NOT_RUN and why
13. Remaining blockers
14. Final verdict:
    - NOT_READY
    - READY_FOR_TARGET_GPU_POC
    - READY_FOR_UAT
    - PRODUCTION_READY

Do not stop at planning.
Do not fake runtime evidence.
Implement as much of the roadmap as the environment allows, and leave externally blocked steps as explicit gated tasks rather than pretending they passed.
```

---

# 30. Final priority order

If implementation capacity becomes constrained, do work in this order:

```text
1. Fix 5 verified defects
2. Real H3 PoC
3. Correct resolution/profile contract
4. Draft/Standard speed profiles
5. Last-frame mode
6. Full Ref2VA
7. Prompt freeze
8. Regenerate/Variation semantics
9. Per-scene config + correct Generate All
10. Final delivery presets
11. Selected-output enhancement/2K
12. Production benchmark + UAT + restore
```

Everything else can wait.

---

# 31. Final production target

The system is considered complete for the current business need when an editor can reliably do:

```text
Create video
→ choose ratio
→ create/edit scenes
→ choose Draft/Standard
→ T2V / First / Last / First+Last / Ref2VA
→ generate quickly
→ watch progress
→ retry/cancel safely
→ regenerate/variation
→ select best candidate
→ optional final-quality enhancement
→ assemble
→ choose delivery size
→ export MP4
```

with backend guarantees that:

```text
the exact prompt is reproducible
the exact references are snapshotted
the exact workflow/profile is recorded
actual output resolution is known
idempotent retries do not create the wrong batch
workflows cannot be enabled before real PoC
backup can actually be restored
five editors do not interfere with each other's login throttling
```

That is the production target for this phase.

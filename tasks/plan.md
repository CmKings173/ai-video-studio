# AI Advertising Video Studio Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development or executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Xây dựng backend V1 chạy được cho AI Advertising Video Studio, tích hợp H3/ComfyUI qua một adapter kiểm soát được, với PostgreSQL là source of truth và MinIO là durable media store.

**Architecture:** FastAPI + Pydantic v2 + SQLAlchemy async + Alembic. Backend lưu domain state, prompt/input snapshots, queue state và final assembly manifest; ComfyUI chỉ thực thi workflow. V1 chạy trên một workstation, một dispatcher và một assembler worker; không thêm Redis/Django-Q.

**Tech Stack:** Python 3.12+, FastAPI, SQLAlchemy 2.x async, Alembic, PostgreSQL 16, MinIO S3 API, httpx, FFmpeg/ffprobe, pytest, Docker Compose.

**Spec:** `AI_Advertising_Video_Studio_Backend_Technical_Spec.md`

## Global Constraints

- PostgreSQL là source of truth cho users, projects, scenes, generations và final versions.
- MinIO dùng một bucket `ai-video`; object key của asset/output là immutable và không dùng tên file người dùng làm path.
- ComfyUI là execution engine bên ngoài; browser không gọi thẳng ComfyUI.
- Redis nằm ngoài V1; queue application chỉ là claim ngắn hạn trong PostgreSQL.
- Chỉ workflow đã approve mới được chạy trong production path; workflow JSON và slot map phải có version/hash.
- V1 giả định 5 editor dùng chung workspace; ADMIN quản lý user và runtime. Nếu business muốn private workspace, phải thay authz task trước khi code.
- H3/ComfyUI PoC là gate trước khi đóng băng state machine, schema và concurrency.
- Mọi thay đổi code chạy/test trong Docker Compose; không chạy host Python/npm cho backend.

## Những gì chắt lọc từ 3 repo

| Nguồn local | Lấy lại bằng cách reimplement | Không bê nguyên |
|---|---|---|
| `Calliope/calliope-backend/src/calliope/comfyui/roles.py`, `parser.py`, `patcher.py` | Role tag `(Input:prompt)`, `(Input:image)`, `(Output:video)`; parser/slot map; prompt preview/history; HTTP-only ComfyUI | SQLite queue, folder media, global `/interrupt`, arbitrary workflow import |
| `h3-webui/webui/server.py`, `scripts/*_smoke.py` | H3 preflight, token/count validation, WS → SSE progress, reconnect/keepalive, last-frame extraction | `JOBS` in-memory, `generations.json`, shared ComfyUI folders, unauthenticated routes |
| `minimax-h3-frontend/backend/generation/models.py`, `tasks.py`, `integrations/comfyui.py`, `director/models.py` | Immutable job snapshot, phase/progress, queue/history recovery, benchmark, dirty cascade/continuity | Django-Q2, hard-coded node IDs, Django media volume, four-state `done` model |

Detailed evidence is in `docs/research/h3-reference-repos.md`.

## Target source structure

```text
apps/api/app/
  main.py
  core/{config.py,security.py,errors.py,logging.py}
  db/{base.py,session.py,models/}
  schemas/{common.py,auth.py,resources.py,generation.py,assembly.py}
  api/{auth.py,projects.py,products.py,assets.py,videos.py,scenes.py,generations.py,events.py,admin.py}
  repositories/
  services/{prompt_engine.py,workflow_registry.py,workflow_router.py,generation_service.py,assembly_service.py}
  integrations/{comfy_adapter.py,minio.py,ffmpeg.py}
workers/{dispatcher.py,comfy_listener.py,reconciliation.py,assembler.py}
workflows/h3/{registry.yaml,*.api.json}
migrations/
tests/{unit,integration,contract}
infra/docker-compose.yml
```

## Task List

### Task 0: H3/ComfyUI compatibility spike

**Files:**
- Create: `workflows/h3/registry.yaml`
- Create: `workflows/h3/*.api.json` (chọn và chuẩn hóa từ ba repo local)
- Create: `apps/api/scripts/h3_probe.py`
- Create: `tests/integration/test_h3_probe.py`
- Create: `docs/h3-poc/results.md`

**Interfaces:**
- `H3Probe.run(mode: str, workflow_path: Path, profile: str) -> ProbeResult`
- `ProbeResult` gồm `mode`, `accepted`, `prompt_id`, `output_kind`, `width`, `height`, `frames`, `elapsed_seconds`, `gpu_memory_mb`, `error_code`.

**Steps:**
- [ ] Chọn một API-format workflow cho T2V, I2V, first+last và Ref2VA; ghi commit/hash model và custom nodes trong `registry.yaml`.
- [ ] Chạy preflight geometry/frame/reference trước khi submit.
- [ ] Submit một job thật (nếu ComfyUI khả dụng), lấy `/history`, thu output và ghi kết quả; nếu môi trường chưa có model, chạy fake adapter test để khóa contract.
- [ ] Đo concurrency an toàn ở mức 1 trước; không đặt `max_pending > 1` khi chưa có số đo.

**Acceptance criteria:**
- [ ] Có kết quả hoặc failure reason cụ thể cho cả 4 mode; không còn giả định “H3 hỗ trợ” chỉ dựa vào README.
- [ ] Xác định được output node, progress node, cancel behavior và cách tìm lại job sau restart.
- [ ] PoC report có model/custom-node/workflow hash và quyết định mode nào là V1, mode nào feature-flag.

**Verification:** `docker compose run --rm api python scripts/h3_probe.py --all`; kiểm tra `docs/h3-poc/results.md`.

**Dependencies:** None. **Estimated scope:** Large (spike, không phải production code).

### Task 1: Backend skeleton và local runtime

**Files:**
- Create: `pyproject.toml`, `apps/api/app/main.py`, `apps/api/app/core/config.py`
- Create: `infra/docker-compose.yml`, `infra/.env.example`
- Create: `tests/contract/test_health.py`

**Interfaces:**
- `GET /api/v1/health/live -> {"status":"ok"}`
- `GET /api/v1/health/ready -> {"status":"ok","postgres":true,"minio":true,"comfyui":true}`

**Steps:**
- [ ] Tạo FastAPI app, settings từ environment và structured request ID.
- [ ] Tạo Compose services `api`, `worker`, `assembler`, `postgres`, `minio`, `comfyui` placeholder.
- [ ] Health readiness kiểm tra dependency nhưng không fail liveness khi ComfyUI tạm down.

**Acceptance criteria:** API container khởi động bằng `.env.example`; `/live` và `/ready` trả schema ổn định; secrets không nằm trong source.

**Verification:** `docker compose config`; `docker compose up -d postgres minio api`; `docker compose exec api pytest tests/contract/test_health.py -q`.

**Dependencies:** Task 0. **Estimated scope:** Medium.

### Task 2: Workflow registry, symbolic slots và H3 validator

**Files:**
- Create: `apps/api/app/schemas/workflow.py`
- Create: `apps/api/app/services/workflow_registry.py`
- Create: `apps/api/app/services/h3_validator.py`
- Create: `tests/unit/test_workflow_registry.py`, `tests/unit/test_h3_validator.py`

**Interfaces:**
- `WorkflowRegistry.resolve(mode: GenerationMode, version: str) -> ApprovedWorkflow`
- `ApprovedWorkflow.patch(slots: Mapping[str, Any]) -> dict[str, Any]`
- `H3Validator.validate(request: H3Request, workflow: ApprovedWorkflow) -> H3ValidatedRequest`

**Steps:**
- [ ] Parse role tags hoặc manifest slot map một lần khi import; business code không biết node ID.
- [ ] Validate required/optional roles theo mode và class type từ PoC.
- [ ] Validate dimension/frame/reference/audio; coi `32` và `17n+5` là profile data, không hard-code global.
- [ ] Tính `workflow_hash` và `slot_map_hash`; từ chối workflow thiếu role bắt buộc.

**Acceptance criteria:** cùng input + cùng workflow version tạo cùng patched JSON; unknown/missing role trả error code ổn định; validator có test cho boundary hợp lệ/không hợp lệ.

**Verification:** `docker compose run --rm api pytest tests/unit/test_workflow_registry.py tests/unit/test_h3_validator.py -q`.

**Dependencies:** Task 0-1. **Estimated scope:** Medium.

### Task 3: Canonical PostgreSQL schema và migrations

**Files:**
- Create: `apps/api/app/db/models/*.py`, `apps/api/app/db/session.py`
- Create: `migrations/versions/0001_initial.py`
- Create: `tests/integration/test_schema_invariants.py`
- Create: `docs/database.md`

**Interfaces:**
- Models: `User`, `Project`, `Brand`, `Product`, `Asset`, `Video`, `Scene`, `SceneGeneration`, `GenerationAttempt`, `GenerationAsset`, `WorkflowRegistry`, `FinalVideo`, `FinalVideoScene`, `IdempotencyKey`.
- `SceneGeneration` bắt buộc có `revision`, `attempt_count`, `dispatch_state`, `claimed_by`, `lease_expires_at`, `comfy_prompt_id`, `input_snapshot`.
- `FinalVideoScene` là immutable manifest row: `final_video_id`, `scene_id`, `order_index`, `generation_id`, `asset_id`, `asset_checksum`, `transition_config`.

**Steps:**
- [ ] Chốt enum/check, nullability, FK `ON DELETE`, indexes và unique constraints trong migration, không để “tùy team”.
- [ ] Dùng `GenerationAttempt` nhỏ gọn cho từng external submission; không xây event-sourcing.
- [ ] Thêm revision cho video/scene và idempotency key storage.
- [ ] Tạo migration từ empty DB và test rollback/upgrade.

**Acceptance criteria:** invalid status/progress/parent reference bị DB từ chối; hai attempt không dùng chung external correlation; final manifest không đổi sau khi enqueue; concurrent version allocation không tạo duplicate.

**Verification:** `docker compose exec api alembic upgrade head`; `docker compose exec api pytest tests/integration/test_schema_invariants.py -q`.

**Dependencies:** Task 2. **Estimated scope:** Large, nên chia commit theo models → migration → tests.

### Task 4: OpenAPI v1, error envelope, idempotency và optimistic concurrency

**Files:**
- Create: `apps/api/app/schemas/common.py`, `apps/api/app/schemas/generation.py`, `apps/api/app/schemas/assembly.py`
- Create: `apps/api/app/core/errors.py`
- Create: `docs/openapi.yaml`, `tests/contract/test_openapi.py`

**Interfaces:**
- Error: `{"code": "SCENE_REVISION_CONFLICT", "message": "...", "details": {}, "request_id": "..."}`
- Mutations generation/storyboard/assemble yêu cầu `Idempotency-Key`.
- Mutations scene/video nhận `If-Match: <revision>`; mismatch trả `412 PRECONDITION_FAILED`.
- Lists dùng `page`, `page_size` (default 20, max 100), sort ổn định theo `(created_at,id)`.

**Steps:**
- [ ] Viết DTO request/response trước router; enum mode/status dùng chung DB và OpenAPI.
- [ ] Định nghĩa endpoint tối thiểu: videos list/detail, scene CRUD/reorder/select, generate, generate-all, cancel, generations, assemble, final versions, SSE.
- [ ] Implement idempotency replay cùng payload và `409 IDEMPOTENCY_KEY_REUSED` khác payload.

**Acceptance criteria:** OpenAPI sinh từ app khớp `docs/openapi.yaml`; FE có đủ DTO cho workspace/history; mọi business error map đúng HTTP status + code.

**Verification:** `docker compose exec api pytest tests/contract/test_openapi.py -q`; diff OpenAPI trong CI không có thay đổi ngoài ý muốn.

**Dependencies:** Task 3. **Estimated scope:** Large.

### Task 5: Authentication, authorization và business resources

**Files:**
- Create: `apps/api/app/core/security.py`, `apps/api/app/api/auth.py`
- Create: `apps/api/app/api/projects.py`, `apps/api/app/api/products.py`, `apps/api/app/api/videos.py`
- Create: `docs/authz-matrix.md`, `tests/integration/test_authz.py`

**Interfaces:**
- `POST /api/v1/auth/login`, `POST /api/v1/auth/logout`, `GET /api/v1/auth/me`.
- `require_editor()` và `require_admin()` là dependency duy nhất để bảo vệ route.
- V1 policy: mọi active EDITOR truy cập business resources trong shared workspace; ADMIN có user/runtime operations.

**Steps:**
- [ ] Dùng DB-backed opaque session cookie HttpOnly/SameSite; logout revoke server-side.
- [ ] Enforce authorization trên nested resource bằng parent query, không chỉ kiểm tra UUID tồn tại.
- [ ] Implement create/archive project, brand, product, video và revision bump.

**Acceptance criteria:** user disabled không gọi được mutation; editor không truy cập resource ngoài policy; archive chặn generate/assemble nhưng không xóa history.

**Verification:** `docker compose exec api pytest tests/integration/test_authz.py -q`.

**Dependencies:** Task 3-4. **Estimated scope:** Medium.

### Task 6: Asset upload, MinIO lifecycle và safe staging

**Files:**
- Create: `apps/api/app/integrations/minio.py`, `apps/api/app/services/asset_service.py`
- Create: `apps/api/app/api/assets.py`
- Create: `workers/reconciliation.py`, `tests/integration/test_asset_lifecycle.py`

**Interfaces:**
- `POST /api/v1/assets/upload-url -> {asset_id, object_key, upload_url, expires_at}`
- `POST /api/v1/assets/{id}/complete -> AssetDTO`
- `AssetStore.put_staging()`, `verify_object()`, `promote_immutable()`, `reconcile_orphans()`.

**Steps:**
- [ ] Presign key UUID-based; không nhận object key tùy ý từ client.
- [ ] Complete phải kiểm tra magic bytes, checksum, dimensions/duration bằng decoder/ffprobe.
- [ ] Generated output dùng staging key + checksum; DB link chỉ trỏ object đã verify.
- [ ] Reconciliation báo object-without-row và row-without-object; cleanup chỉ xóa staging quá hạn.

**Acceptance criteria:** upload retry không tạo asset duplicate; DB failure sau upload không làm mất khả năng repair; path traversal và MIME giả bị từ chối.

**Verification:** `docker compose exec api pytest tests/integration/test_asset_lifecycle.py -q`; kiểm tra object trong MinIO test bucket.

**Dependencies:** Task 3-5. **Estimated scope:** Large.

### Task 7: Prompt engine và generation request preparation

**Files:**
- Create: `apps/api/app/services/prompt_engine.py`, `apps/api/app/services/workflow_router.py`
- Create: `tests/unit/test_prompt_engine.py`, `tests/unit/test_workflow_router.py`

**Interfaces:**
- `PromptEngine.compose(context: PromptContext, enhancer: EnhancerConfig | None) -> PromptResult`
- `WorkflowRouter.select(request: GenerationRequest) -> ApprovedWorkflow`
- `GenerationPreparation.prepare(...) -> GenerationSnapshot`

**Steps:**
- [ ] Compose deterministic six-layer context: brand/product/scene/style/technical/audio; enhancer optional và có fallback deterministic.
- [ ] Router decision table disjoint giữa T2V, I2V, first+last và Ref2VA; loại trừ frame assets khỏi reference list.
- [ ] Snapshot raw prompt, execution prompt, asset IDs/order/checksum, workflow hash, seed, dimensions, duration, steps, runtime profile.

**Acceptance criteria:** enhancer down không chặn generation; cùng snapshot không bị thay đổi khi Product/Brand sửa; invalid combination trả `GENERATION_INPUT_INVALID`.

**Verification:** `docker compose run --rm api pytest tests/unit/test_prompt_engine.py tests/unit/test_workflow_router.py -q`.

**Dependencies:** Task 2-6. **Estimated scope:** Medium.

### Task 8: Quick Clip vertical slice qua ComfyUI

**Files:**
- Create: `apps/api/app/integrations/comfy_adapter.py`
- Create: `apps/api/app/services/generation_service.py`, `apps/api/app/api/generations.py`
- Create: `workers/dispatcher.py`
- Create: `tests/integration/test_quick_generation.py`

**Interfaces:**
- `ComfyAdapter.submit(workflow, client_id) -> ExternalPrompt`
- `ComfyAdapter.get_history(prompt_id) -> ExternalHistory`
- `POST /api/v1/scenes/{scene_id}/generations -> GenerationDTO` (202)
- `GET /api/v1/generations/{id}` và `GET /api/v1/scenes/{scene_id}/generations`.

**Steps:**
- [ ] Transaction tạo `SceneGeneration` + `GenerationAttempt` ở `CREATED`, commit snapshot trước dispatch.
- [ ] Dispatcher claim bằng lease ngắn (`CREATED → DISPATCHING`), submit ngoài transaction, lưu `comfy_prompt_id` theo CAS.
- [ ] Poll `/history` làm baseline; output được copy về MinIO immutable key và link vào `Asset`.
- [ ] Chỉ ORIGINAL đầu tiên có thể auto-select bằng compare-and-set; variation không tự ghi đè selected.

**Acceptance criteria:** double-click cùng idempotency key chỉ có một generation; happy path tạo đúng DB row + MinIO object + selected policy; Comfy error trả structured node error.

**Verification:** chạy fake adapter integration rồi chạy một workflow thật từ Task 0; kiểm tra DB/object/history count.

**Dependencies:** Task 3-7. **Estimated scope:** Large.

### Task 9: Progress, cancel, retry và restart reconciliation

**Files:**
- Modify: `apps/api/app/integrations/comfy_adapter.py`, `workers/dispatcher.py`
- Create: `workers/comfy_listener.py`, `apps/api/app/api/events.py`
- Create: `tests/integration/test_generation_races.py`

**Interfaces:**
- States: `CREATED`, `DISPATCHING`, `QUEUED`, `RUNNING`, `COLLECTING`, `COMPLETED`, `FAILED`, `CANCEL_REQUESTED`, `CANCELLED`.
- `POST /api/v1/generations/{id}/cancel -> GenerationDTO`.
- SSE event: `{event_id, schema_version, resource_revision, type, state, phase, current, total, occurred_at}`.

**Steps:**
- [ ] Worker restart quét lease hết hạn và gọi `/queue` + `/history` trước khi resubmit.
- [ ] Cancel queued dùng dequeue; cancel running ghi `CANCEL_REQUESTED`, active worker mới quyết định terminal state.
- [ ] Terminal state monotonic; duplicate/out-of-order event không regress hoặc tạo Asset thứ hai.
- [ ] Technical retry tối đa theo profile; mỗi external submission là một `GenerationAttempt`.

**Acceptance criteria:** test crash sau Comfy submit trước DB update không tạo blind duplicate; cancel-vs-complete deterministic; SSE reconnect REST-resync được; late event không đổi terminal state.

**Verification:** `docker compose exec api pytest tests/integration/test_generation_races.py -q`; fault-injection fake adapter cho submit/history/interrupt.

**Dependencies:** Task 8. **Estimated scope:** Large.

### Task 10: Storyboard và scene editing vertical slice

**Files:**
- Create: `apps/api/app/services/storyboard_service.py`, `apps/api/app/api/scenes.py`
- Create: `tests/integration/test_storyboard_revision.py`
- Modify: `apps/api/app/db/models/scene.py`, `apps/api/app/schemas/resources.py`

**Interfaces:**
- `POST /api/v1/videos/{video_id}/storyboard/preview`
- `POST /api/v1/videos/{video_id}/storyboard/publish` yêu cầu `If-Match`.
- `PATCH /api/v1/scenes/{scene_id}`, `POST /api/v1/videos/{video_id}/scenes/reorder`, `POST /api/v1/scenes/{id}/select-generation`.

**Steps:**
- [ ] Planner chạy ngoài transaction; publish chỉ thành công nếu video revision chưa đổi.
- [ ] Regenerate storyboard không xóa scene/generation cũ nếu chưa có command replace rõ ràng.
- [ ] Scene edit/reorder/select bump revision và đánh dấu video dirty.

**Acceptance criteria:** concurrent storyboard publish không overwrite bản mới; editor sửa prompt không làm mất generation history; selection trỏ đúng scene parent.

**Verification:** `docker compose exec api pytest tests/integration/test_storyboard_revision.py -q`.

**Dependencies:** Task 4, 8-9. **Estimated scope:** Medium.

### Task 11: Long Video assembly và immutable final version

**Files:**
- Create: `apps/api/app/services/assembly_service.py`, `apps/api/app/integrations/ffmpeg.py`
- Create: `workers/assembler.py`, `apps/api/app/api/assembly.py`
- Create: `tests/integration/test_assembly_manifest.py`

**Interfaces:**
- `POST /api/v1/videos/{video_id}/final-versions -> FinalVideoDTO` (202)
- `GET /api/v1/videos/{video_id}/final-versions`
- `AssemblyService.snapshot_manifest(video_id, config) -> FinalManifest`
- `Assembler.run(final_video_id) -> Asset`

**Steps:**
- [ ] Trong một transaction, snapshot ordered enabled scenes, selected generation/assets/checksums, trim, transition và audio config vào `FinalVideoScene`.
- [ ] Worker chỉ đọc manifest, không đọc live scene selection.
- [ ] FFmpeg output ghi staging key; verify bằng ffprobe rồi publish immutable final asset.
- [ ] Reorder/select sau enqueue chỉ làm video dirty cho version kế tiếp, không đổi version đã tạo.

**Acceptance criteria:** assemble đồng thời tạo tối đa một version cho cùng idempotency key; thay đổi scene sau enqueue không đổi manifest; output final có checksum và playable metadata.

**Verification:** `docker compose exec api pytest tests/integration/test_assembly_manifest.py -q`; chạy ffprobe trên output test.

**Dependencies:** Task 6, 8-10. **Estimated scope:** Large.

### Task 12: Operations, benchmark, backup và UAT gate

**Files:**
- Create: `apps/api/management/benchmark_h3.py`, `workers/health.py`
- Create: `infra/monitoring/prometheus.yml`, `docs/runbook.md`, `docs/uat.md`
- Create: `tests/contract/test_nfr.py`

**Steps:**
- [ ] Ghi benchmark theo mode/resolution/duration/steps/GPU; chỉ tăng concurrency khi pass OOM/fidelity.
- [ ] Metrics: queue age, stale lease, generation failure, disk watermark, MinIO/Postgres health, backup age.
- [ ] Chốt RPO/RTO V1, backup ngoài workstation và restore drill; ghi kết quả vào runbook.
- [ ] UAT test crash-after-submit, cancel race, MinIO/DB partial failure, SSE reconnect, archive authorization và assembly reproducibility.

**Acceptance criteria:** mọi NFR có số đo hoặc trạng thái “chưa hỗ trợ”; restore tạo DB/object nhất quán; UAT có pass/fail evidence chứ không chỉ demo.

**Verification:** `docker compose exec api pytest tests/contract/test_nfr.py -q`; chạy benchmark/restore theo `docs/runbook.md`.

**Dependencies:** Task 0-11. **Estimated scope:** Large.

## Checkpoints

### Checkpoint A — PoC gate (sau Task 0)

- [ ] H3 modes, model hashes, node types, output/progress/cancel/recovery đã được đo.
- [ ] Quyết định V1 mode/concurrency được ghi trong `docs/h3-poc/results.md`.

### Checkpoint B — Foundation gate (sau Task 4)

- [ ] Docker stack khởi động.
- [ ] Migration từ empty DB pass.
- [ ] OpenAPI v1, error envelope, idempotency và revision semantics được freeze.

### Checkpoint C — First vertical slice (sau Task 9)

- [ ] Một Quick Clip chạy từ API → DB → ComfyUI → MinIO → history.
- [ ] Crash/restart/cancel/progress tests pass.

### Checkpoint D — Product flow gate (sau Task 11)

- [ ] Storyboard → edit/select → generate → assemble chạy end-to-end.
- [ ] Final manifest bất biến và reproducible.

### Checkpoint E — V1 release gate (sau Task 12)

- [ ] UAT/NFR/backup restore có evidence.
- [ ] Không còn blocker PoC chưa được chấp nhận trong `docs/h3-poc/results.md`.

## Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| H3 dùng cả 4 GPU hoặc không hỗ trợ concurrency | High | PoC trước schema freeze; mặc định một job tại một thời điểm |
| Comfy nhận prompt nhưng API chết trước khi lưu ID | High | `GenerationAttempt`, correlation token, `/queue` + `/history` reconciliation |
| Workflow node ID drift | High | Versioned symbolic slot map + workflow hash + startup validation |
| MinIO/DB dual-write lệch | High | Immutable staging/checksum + orphan scanner + repair command |
| Editor sửa scene trong lúc assemble | High | Transactional immutable final manifest + revision precondition |
| Scope phình theo các repo tham khảo | Medium | Chỉ reimplement pattern đã map vào acceptance criteria; không copy nguyên app |

## Open Questions to resolve at Checkpoint A

- Exact ComfyUI/H3 commit, custom-node versions và model hashes trên workstation là gì?
- Ref2VA video/audio reference có chạy end-to-end không, hay chỉ image/audio V1?
- ComfyUI `/interrupt` có an toàn với topology một job hay cần chỉ dequeue pending?
- Shared editor workspace có đúng business policy không?
- RPO/RTO và retention cụ thể cho asset/generation/final version là bao nhiêu?

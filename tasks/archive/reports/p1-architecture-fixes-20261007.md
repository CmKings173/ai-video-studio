# Báo cáo sửa kiến trúc P1 — 2026-10-07

## Source state

- Workspace: `ai-video-studio`; repo root: `D:\project\ai-video-studio`.
- Branch: `codex/production-safety-fixes`.
- HEAD: `01b740d8be4d58d3bd15bc46dd9842afb48af8bb`.
- Trước sửa: dirty, 0 staged, 46 unstaged tracked files, 121 untracked paths.
- Sau sửa và thêm báo cáo: dirty, 0 staged, 48 unstaged tracked files, 142 untracked paths.
- Hai action connector `workspace_info` và `git_status` đã được gọi trước sửa nhưng trả lỗi Unknown tool. Source live được xác minh trực tiếp bằng Git và filesystem của workspace; không dùng snapshot cũ làm bằng chứng.
- Không reset, stash, checkout, clean, revert, commit hay push. Giữ nguyên các thay đổi ngoài phạm vi P1.

## Architecture changes

### P1.1 — Canonical Director graph identity

Root cause: app-layer nhận diện thiếu `StudioMiniMaxH3Director` và có nhiều bản sao tập class.

Exact fix: một `DIRECTOR_CLASS_TYPES` và `is_director_graph` tại provider layer. GenerationService, workflow contracts/frozen validation, capability API và workflow builder dùng cùng nguồn. Wrapper Studio vẫn thuộc provider `minimax_h3_director`.

Files: `backend/apps/api/app/providers/minimax_h3_director/graph_identity.py`, `workflow_builder.py`, `backend/apps/api/app/services/generation_service.py`, `workflow_contracts.py`, `backend/apps/api/app/api/generations.py`.

Why: phân loại graph không còn lệch giữa API, lập kế hoạch và dispatch. Regression chạy qua GenerationService thực, persisted intent/spec và capability provider.

### P1.2 — First-class execution scope

Root cause: enabled uniqueness/approval theo mode + profile làm standalone và aggregate loại nhau; selection suy luận identity từ export transport.

Exact fix: field NOT NULL `execution_scope` với check constraint; enabled partial uniqueness theo `(mode, quality_profile, execution_scope)`. Admin chỉ thay thế workflow cùng ba thành phần. Selection query đúng scope, kể cả workflow ID chỉ định; profile/scope không khớp bị từ chối. Manifest/loader/importer/schema/types giữ explicit scope.

Files: `backend/apps/api/app/db/models.py`, `schemas/api.py`, `api/admin.py`, `services/workflow_registry.py`, `workflow_loader.py`, `workflow_contracts.py`, `generation_service.py`, `api/generations.py`, `backend/apps/api/scripts/import_director_templates.py`, migration `a4c8e0f2b6d1`, `frontend/lib/api/types.ts`.

Why: hai execution domains có thể cùng enabled mà không disable nhau; transport configuration chỉ dùng để kiểm tra tính nhất quán, không quyết định registry identity lúc selection.

### P1.3 — Dynamic aggregate coverage and aggregate frozen validation

Root cause: template/binding gắn cứng hai thành viên. Đồng thời dispatch đưa tổng duration/frame của aggregate qua validator của một scene.

Exact fix: sáu template aggregate dùng identity không chứa member count và native binding `coverage: all_members`. Freeze member count trong run/spec; collector yêu cầu indexes chính xác 0..N-1. Qualification vẫn exact theo count, continuities, settings, graph/slot/profile hashes và source/dependency/weight provenance. Frozen aggregate validator kiểm tra từng member đã resolve, timeline, tổng frame/duration và evidence. Dispatcher dùng validation hook riêng cho aggregate rồi dùng chung pipeline staging/upload/integrity.

Files: `backend/apps/api/app/providers/minimax_h3_director/contracts.py`, `collector.py`, `workflow_builder.py`, `services/director_run_service.py`, `backend/workers/dispatcher.py`, `director_dispatcher.py`, importer, `backend/workflows/h3/registry.json` và sáu `director_*_aggregate.api.json`.

Why: graph identity độc lập số thành viên; measured qualification vẫn cụ thể từng execution. Aggregate không bị giới hạn bằng duration/frame resolver của một scene. Các binding static của lịch sử vẫn đọc được; không nới qualification.

### P1.4 — Plan execution groups before job persistence

Root cause: chỉ một cờ aggregate cho toàn batch khiến CUT/mixed task bị gom vào cùng run.

Exact fix: planner giữ CONTINUOUS chain trong native aggregate, tách ở CUT, motion-context singleton dùng aggregate. Chuẩn bị toàn bộ generation/run dưới dạng transient objects, kiểm tra compatibility/qualification trước khi ghi bất kỳ job nào. Persist đúng prompt, seed, ID và snapshot đã chuẩn bị; không tính lại. Response `execution_groups` map scene/generation/run IDs; field singular cũ được mô tả deprecated và chỉ có giá trị khi có đúng một aggregate run.

Files: `backend/apps/api/app/services/execution_groups.py`, `generation_service.py`, `director_run_service.py`, `api/generations.py`, `schemas/api.py`, `frontend/lib/api/types.ts`, `docs/openapi.yaml`, `docs/director-execution-contract.md`.

Why: mixed standalone/aggregate và nhiều native chains cùng tồn tại trong một transaction. CONTINUOUS không tương thích bị từ chối trước partial persistence. Tests kiểm tra zero job ngay trong transaction, không dựa vào rollback để che ghi sớm.

### P1.5 — Semantic source freshness and coherent native selections

Root cause: selected ID tồn tại bị coi là output current; revision counters không chứng minh source semantic. Fingerprint riêng một member cũng bỏ sót phụ thuộc của native chain.

Exact fix: fingerprint schema 2 cho scene inputs, generation-affecting video config, Product/Brand context và toàn bộ continuity source group. Bind mọi run/member snapshot với execution group và rehash các frozen clones. Selected native outputs phải thuộc cùng completed run; không trộn outputs có cùng source semantics nhưng từ các random-seed executions khác nhau. Generate All tự mở rộng stale eligibility ra full native chain trước revision preconditions; frontend dùng cùng nguyên tắc cho ID/revision payload và summary. Assembly từ chối stale/mixed selections. Promotion của final kiểm tra source hiện hành và khóa dependencies; rendering vẫn dùng immutable manifest. Derivatives kế thừa source identity của parent. Lịch sử/selected IDs giữ lại.

Files: `backend/apps/api/app/services/generation_freshness.py`, `continuity_groups.py`, `generation_service.py`, `director_run_service.py`, `batch_identity.py`, `assembly_service.py`, `api/generations.py`, `api/scenes.py`, `api/videos.py`, `schemas/api.py`, `backend/workers/assembler.py`; frontend eligibility/summary/types/video/assembly projections.

Why: source changes lan đúng tới member outputs phụ thuộc; display title và assembly-only settings không làm generation stale. Fresh predecessors được đưa vào native regeneration group khi cần. Chọn từng member vẫn được phép; group readiness chỉ thành current khi toàn bộ selections coherent.

## Migration

- Old invariant: một enabled workflow cho mỗi mode/profile.
- New invariant: một enabled workflow cho mỗi mode/profile/scope.
- Backfill: profile export_mode = segments → aggregate; còn lại → single_scene.
- Migration revision: `a4c8e0f2b6d1`, parent `f3a7c9e1d2b4`.
- Downgrade từ chối trước DDL nếu enabled variants không biểu diễn được trong invariant cũ, hoặc explicit scope khác legacy ingest rule. Không âm thầm disable/làm mất identity.
- SQLite tests đã chạy upgrade, backfill, uniqueness hai scope, downgrade refusal và safe downgrade/re-upgrade.
- PostgreSQL migration/invariant tests đã thêm/cập nhật nhưng môi trường hiện chưa chạy được.
- Snapshot thiếu freshness schema 2 được coi là historical/stale; không xóa history. Clients dùng execution_groups; response replay cũ vẫn đọc được với default groups rỗng.

## Tests

Các lệnh Python dưới đây chạy từ `D:\project\ai-video-studio\backend`; npm chạy từ `D:\project\ai-video-studio\frontend`. Kết quả lấy từ lượt chạy mới trong task này.

| Command | Status | Meaningful output |
|---|---|---|
| `.\.venv\Scripts\python.exe -m pytest -q` | PASS | 525 passed, 13 skipped, 126.28s; không failure |
| `.\.venv\Scripts\python.exe -m pytest tests/integration/test_execution_group_freshness.py tests/integration/test_generation_freshness.py tests/integration/test_assembly_dependency_freshness.py tests/integration/test_director_dynamic_aggregate.py -q` | PASS | 59 passed sau final import/format cleanup |
| `.\.venv\Scripts\python.exe -m pytest tests/contract/test_openapi.py tests/integration/test_execution_group_freshness.py -q` | PASS | 16 passed |
| `.\.venv\Scripts\python.exe -m pytest tests/integration/test_workflow_execution_scope_migration.py -q` | PASS / PostgreSQL NOT_RUN | 2 passed, 1 skipped |
| `.\.venv\Scripts\python.exe -m pytest -m postgres -q` | NOT_RUN | 12 skipped do thiếu disposable PostgreSQL URL; không gọi skipped là PASS |
| `.\.venv\Scripts\python.exe -m ruff check .` | PASS | All checks passed |
| `ruff format --check --output-format concise <P1 targets>` | PASS | 43 files already formatted; exact command ở dưới |
| `.\.venv\Scripts\python.exe -m ruff format --check --output-format concise .` | FAIL | 31 files ngoài phạm vi sửa P1 cần format; giữ nguyên để bảo toàn dirty source ngoài task |
| `.\.venv\Scripts\python.exe -m compileall -q apps workers tests migrations` | PASS | exit 0 |
| `git -c core.safecrlf=false diff --check` | PASS | exit 0, không whitespace error |
| `npm.cmd test` | PASS | 63 passed, 0 failed |
| `npm.cmd run typecheck` | PASS | tsc exit 0 |
| `npm.cmd run lint` | PASS | ESLint exit 0 |
| `npm.cmd run build` | PASS | Next 16.3.5 compiled, TypeScript/static pages complete, exit 0 |
| Docker/PostgreSQL runtime lane | NOT_RUN | Docker khởi động crash ở dockerInference socket; không có test DB hoạt động |
| GPU/Comfy/H200 benchmark và runtime qualification | NOT_RUN | Ngoài scope theo yêu cầu |
| Linux UID/mode boundary | NOT_RUN | Một test skipped trên Windows |

Exact scoped formatting command:

```powershell
.\.venv\Scripts\python.exe -m ruff format --check --output-format concise apps/api/app/providers/minimax_h3_director apps/api/app/services/generation_service.py apps/api/app/services/continuity_groups.py apps/api/app/services/execution_groups.py apps/api/app/services/generation_freshness.py apps/api/app/services/director_run_service.py apps/api/app/services/workflow_contracts.py apps/api/app/services/workflow_registry.py apps/api/app/services/workflow_loader.py apps/api/app/services/batch_identity.py apps/api/app/services/assembly_service.py apps/api/app/api/generations.py apps/api/app/api/scenes.py apps/api/app/api/videos.py apps/api/app/api/admin.py apps/api/app/db/models.py apps/api/app/schemas/api.py apps/api/scripts/import_director_templates.py workers/dispatcher.py workers/director_dispatcher.py workers/assembler.py migrations/versions/a4c8e0f2b6d1_workflow_execution_scope.py tests/integration/test_director_dynamic_aggregate.py tests/integration/test_generate_all.py tests/integration/test_generation_freshness.py tests/integration/test_execution_group_freshness.py tests/integration/test_assembly_dependency_freshness.py tests/integration/test_workflow_execution_scope.py tests/integration/test_workflow_execution_scope_migration.py tests/unit/test_execution_groups.py tests/unit/test_director_graph_identity.py tests/unit/test_director_dynamic_aggregate.py tests/integration/test_assembly_manifest.py tests/unit/test_generation_contracts.py tests/postgres/test_postgres_invariants.py
```

Các regression bao gồm: Studio intent/spec/provider; dual enabled scopes và uniqueness; dynamic 1/2/3/5 members; actual dispatch preparation; missing/duplicate/out-of-range segments; unqualified count; mixed CUT tasks; 3-member chain; hai chains; incompatible CONTINUOUS zero partial jobs; stale prompt/config/dependencies; chain-wide invalidation; mixed-run rejection; sequential member selection; unrelated CUT edit; immutable assembly và dependency change sau enqueue; idempotency/recovery/auth/lifecycle/retention regression.

Intermediate failures đã được sửa trước lượt full PASS: identity thiếu Studio; OpenAPI artifact cũ; aggregate dispatch qua single-scene resolver; frontend bỏ fresh native prerequisites. Không dùng các lượt failed trước đó làm bằng chứng hoàn tất.

Review độc lập đã tái kiểm tra hai P1 freshness findings và đóng cả hai; không còn blocker trong phạm vi đã review.

## Remaining risks

- Chưa xác minh thực thi PostgreSQL migration/locking/invariants trên DB thật vì Docker startup lỗi. Bộ tests đã sẵn sàng với `POSTGRES_TEST_DATABASE_URL`.
- Không có bằng chứng GPU/Comfy runtime qualification; template registry vẫn fail-closed theo evidence thật.
- Full-repository format check còn 31 file ngoài phạm vi P1. Scoped P1 format và lint toàn backend đã sạch.
- Connector tools hiện vẫn không callable trong phiên này; task này xác minh và sửa source local trực tiếp, không tuyên bố reconnect thành công.

## Final verdict

```text
P1_FIXES_COMPLETE
```

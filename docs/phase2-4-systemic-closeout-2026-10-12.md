# Báo cáo sửa và kiểm chứng Phase 2–4 — 12/10/2026

## A. Kho mã và phạm vi

- Workspace: `D:\project\ai-video-studio`.
- Branch: `codex/production-safety-fixes`.
- HEAD: `50071aeb6c2ae9b76d8ac0e1e61904ffc94a04b9`.
- Giữ toàn bộ thay đổi chưa commit có sẵn; không reset, commit hoặc push.
- Kiểm thử dùng database/fixture riêng. Không chạy migration trên database ứng dụng, xóa object MinIO thật hoặc chạy generation GPU.
- Sổ bằng chứng qua từng vòng: `tasks/phase2-4-systemic-closeout-2026-10-12.md`.

## B. Lỗi đã xác nhận và cách sửa

| Nhóm | Nguyên nhân và vị trí | Sửa và bằng chứng | Kiểm tra độc lập |
|---|---|---|---|
| Khởi động API | Bốn service deletion import tên constant không tồn tại | Dùng `DELETION_HIDDEN_STATUSES` chuẩn; regression import RED → 1 passed | Lead kiểm tra startup/collection |
| Ownership output | Asset không scope vẫn có thể mang owner bất nhất; output cũ của generation/final khác bị nhận nhầm | `asset_service.py` và kiểm tra provenance yêu cầu owner/quan hệ output chuẩn; không backfill tự động | Euclid |
| Admin deletion | Service chưa phân biệt actor Admin với resource owner; operation visibility cần owner scope | Bốn deletion service và `api/deletions.py`: quyền Admin tin cậy, kiểm tra dependency theo owner thật, giữ attribution actor | Euclid |
| Tiến độ cleanup | Output dùng chung hoặc output lỗi đứng đầu batch khiến output an toàn phía sau không tiến triển | `deletion_cleanup.py`: ưu tiên candidate READY đủ điều kiện, khóa candidate trước giới hạn, kiểm tra lại sau khóa, không bỏ qua unresolved/missing/locked để báo hoàn tất | Turing: 8 kiểm tra riêng qua; SQL PostgreSQL compile qua |
| Giao diện quality | Canvas STANDARD đang lưu loại lựa chọn HIGH khỏi danh sách | `frontend/lib/generation/capabilities.ts`: lựa chọn quality dựa vào profile đã qualify; đổi canvas vẫn phải được qualify rõ ràng | Mencius |
| Canvas backend | Resolver dùng output baseline chung thay vì output combination tương ứng | `generation_display.py`: dùng output của combination được xác minh; optional settings giữ yêu cầu evidence đúng input | Plato |
| Capability backend | Parser canvas kỳ vọng list/tuple nhưng evidence producer ghi dictionary | `api/generations.py`: dùng typed resolver `verified_settings` theo task/canvas | Plato |
| Generate All revision | Guard không có scene che lỗi optimistic revision | Kiểm tra revision trước guard rỗng, giữ row locks/replay | Plato; qualification/Generate All 34 passed |
| JWT sau đổi tài khoản | JWT cũ giữ quyền Admin sau hạ quyền, khóa hoặc đổi mật khẩu | Theo quyết định rõ ràng của người dùng: xác minh DB role/status/version mỗi request; SSE kiểm tra mỗi poll; migration/version claim và regression thật | Confucius: không còn finding sau sửa double increment |
| Fixture backend | Hash preview giả, canvas không khớp, persist preparation chưa finalize, transaction StaticPool chưa commit, output ID trùng seed | Cập nhật fixture đúng producer/contract; giữ các assertion bảo mật, immutability, collision và concurrency | Turing/Plato/Confucius kiểm tra chéo |

### Chính sách JWT đã triển khai

`User.auth_version` tăng khi role, trạng thái active hoặc password hash đổi. Token chứa version được ký. Request cần đăng nhập đọc DB và từ chối token có version/role không khớp hoặc account bị khóa bằng 401. Quyền không dựa riêng vào role claim. SSE đọc lại account trước khi mở và trên mỗi snapshot poll.

ORM hook dùng version đã lưu làm sàn, tránh tăng hai lần khi vừa tăng version chủ động vừa đổi quyền. Migration `e5a7c9b1d3f6` thêm version và trigger PostgreSQL/SQLite cho cập nhật SQL trực tiếp. SQLite đã kiểm thử upgrade, ba loại mutation SQL, monotonic version, preincrement, downgrade và dữ liệu account được giữ nguyên. Trigger PostgreSQL chưa được thực thi trên server thật.

Đăng xuất vẫn xóa cookie với CSRF đã ký kể cả cookie bị revoked; đăng nhập mới lấy state hiện tại. Token cũ không có version claim cần đăng nhập lại. Request đã được authorize trước thời điểm đổi account có thể hoàn tất; cơ chế này không hủy transaction đang thực thi. Cần chạy migration trước khi triển khai API mới.

### Các tệp tác động chính trong vòng closeout

- Auth: `backend/apps/api/app/api/{auth,deps,events}.py`, `core/security.py`, `db/models.py`, migration `e5a7c9b1d3f6_account_auth_version.py`, `docs/authentication.md`.
- Ownership/deletion: `services/asset_service.py`, `services/{video,scene,generation,final_version}_deletion.py`, `services/deletion_cleanup.py`, `api/deletions.py`.
- Generation: `services/generation_display.py`, `api/generations.py`.
- Frontend: `lib/generation/capabilities.ts`, deletion control và các fixture/test liên quan.
- Hợp đồng/tài liệu: `docs/openapi.yaml`, `AGENTS.md`, sổ bằng chứng và báo cáo này.
- Regression auth: `test_auth_revocation.py`, `test_auth_version_migration.py`, `test_jwt_sse.py`, `test_authz.py`, `test_jwt_security.py`, `test_events.py`, `test_phase2_api_ownership_boundaries_6ac84.py`.
- Regression output/deletion: `test_phase2_asset_owner_provenance.py`, `test_phase2_systemic_output_conflict.py`, `test_phase4_deletion_safety.py`.
- Fixture generation: contract API/edges, races, Generate All, preparation, execution freshness, dynamic aggregate, lifecycle replay, dependency invalidation, assembly freshness và các fixture workflow được ghi trong sổ bằng chứng.

Nhiều tệp trên đã có diff từ trước. Danh sách này mô tả phần tiếp tục xử lý, không coi toàn bộ diff so với HEAD là thay đổi mới của vòng này.

## C. Kiểm chứng

Các lệnh backend chạy từ `backend`, frontend từ `frontend`.

| Gate | Kết quả |
|---|---|
| Python AST parse apps/workers/migrations/tests | 266 file, không lỗi syntax |
| `python -m pytest --collect-only -q` | 2.089 test, không lỗi collection |
| `ruff check .` | Qua |
| `git -c core.safecrlf=false diff --check` | Qua |
| Auth/JWT/SSE/ownership/OpenAPI focused | 101 passed |
| Auth revocation + migration rereview | 8 passed |
| Cleanup/retention regression expanded | 57 passed, 1 PostgreSQL skipped |
| Output conflict module | RED 5 failed/17 passed → GREEN 22 passed |
| Race fixture/socket rejection | 11 passed |
| Historical replay/quality-only edges | 15 passed |
| `npm test` | 532 passed |
| `npm run typecheck`, `npm run lint`, `npm run build` | Qua |
| `node --test` authentication-client/auth-context/auth-proxy | 46 passed |
| `docker compose --env-file .env.example -f infra/compose.yaml config --quiet` | Exit 0; không khởi động container |
| Full backend trước cleanup cuối | 1.996 passed, 62 skipped; 474,76 giây |
| Full backend trên source cuối: `python -m pytest -q --tb=short` | **2.026 passed, 63 skipped, 0 failed**; **452,90 giây**, exit 0 |

Không có signing key thật trong tám tệp auth/migration mới sửa theo kiểm tra pattern giới hạn; đây không phải chứng nhận quét toàn bộ repository.

## D. Đánh giá theo phase

- **Phase 2:** Các ownership/output/replay regression và thu hồi JWT đã qua trên database test SQLite; không sửa owner hàng loạt. PostgreSQL concurrency chưa được xác minh.
- **Phase 3:** Fixture và resolver được đưa về đúng input/output qualification contract; các gate API và frontend qua. Không thay workflow ComfyUI/H3 trong vòng sửa này.
- **Phase 4A:** Startup, Admin deletion, attribution và bounded cleanup có regression; không xóa dữ liệu thật. Lock behavior PostgreSQL cần chạy lane riêng.
- **Phase 4B:** Chọn quality/resolution và hiển thị capability theo evidence đã kiểm tra bằng code/tests; chưa chạy GPU qualification mới.
- **Toàn hệ thống:** Import/collection/OpenAPI/Compose resolve và frontend build qua. Không thực hiện deployment hoặc nghiệm thu live GPU/MinIO; không coi đăng ký connector là bằng chứng đọc source thành công.

## E. Bàn giao và giới hạn

**Final verdict: `PARTIALLY_VERIFIED`.** Các lỗi đã xác nhận trong phạm vi sửa có regression qua và review độc lập; không có failure trong full backend cuối. Giới hạn PostgreSQL/GPU dưới đây vẫn còn, nên chưa nghiệm thu production đầy đủ.

`POSTGRES_TEST_DATABASE_URL` chưa được cấu hình. Các test PostgreSQL chưa chạy phải thực hiện trên database test riêng, không dùng database ứng dụng. Test Linux UID/mode boundary không chạy được trên Windows. GPU và workflow execution thật chưa được kiểm chứng. Vì thế chưa kết luận sẵn sàng triển khai production dù các gate local xanh.

Bước tiếp theo: cung cấp database PostgreSQL test riêng, chạy migration/auth trigger và các lane locking/concurrency; sau đó chạy qualification GPU theo runbook đã có. Migration mới chưa áp dụng lên dữ liệu ứng dụng.

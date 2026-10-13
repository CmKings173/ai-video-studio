# Review bổ sung Phase 2–4 — 12/10/2026

**Verdict: REQUEST_CHANGES.** Hai lỗi P2 mới được xác nhận ở hợp đồng trạng thái deletion giữa backend và frontend. Lượt review này không chỉnh source production.

Repo: `D:\project\ai-video-studio`, branch `codex/production-safety-fixes`, HEAD `50071aeb6c2ae9b76d8ac0e1e61904ffc94a04b9`. Giữ nguyên diff có sẵn; không commit/push.

## 1. P2 — Retention chờ bình thường bị hiển thị thành lỗi

Vị trí: `frontend/components/deletion/deletion-control.tsx:68`, cùng phân loại cảnh báo ở dòng 290 và 391–397.

Backend cleanup dùng outcome `CLEANUP_PENDING` với reason `RETENTION_PENDING` khi output hợp lệ đang chờ retention. `_set_outcome` lưu reason vào `last_error_code`; `DeletionOperationDTO.from_operation` trả nó trong `cleanup_error_code`.

Frontend `cleanupIsComplete` trả true cho mọi `cleanup_error_code`, và `hasCleanupError` bật cho mọi mã. Kết quả bình thường này vì thế tắt chỉ báo pending và bật alert destructive với tiêu đề “Quy trình xóa gặp vấn đề”. Đây không phải kết luận cleanup đã hoàn tất từ backend.

Tái hiện bằng helper lấy trực tiếp từ source TS và transpile với TypeScript đang cài:

```json
{"status":"CLEANUP_PENDING","cleanup_error_code":"RETENTION_PENDING","helperReportsComplete":true,"pendingIndicator":false,"destructiveAlert":true}
```

Cách sửa: dùng trạng thái cleanup chuẩn để phân biệt pending/blocked/failed/complete; coi `RETENTION_PENDING` là lý do chờ, không phải failure hoặc completion. Bổ sung regression component cho DTO thật này và trạng thái `CLEANUP_FAILED`/`CLEANUP_BLOCKED`.

## 2. P2 — UI chờ cleanup vật lý mới áp dụng logical deletion

Vị trí: `frontend/components/deletion/deletion-control.tsx:216–218` và `:264–269`.

Nhánh nhận operation hợp lệ chỉ lưu operation ID và return, không invalidate các query của tài nguyên. Invalidation và `onComplete` chỉ chạy khi `operationIsSettled` true. Nhưng tập terminal status không chứa `LOGICALLY_DELETED`, `CLEANUP_PENDING`, `CLEANUP_FAILED`, `CLEANUP_BLOCKED`. Nó chỉ khớp `CLEANUP_COMPLETE` trong các trạng thái backend đang dùng.

Backend `DELETION_HIDDEN_STATUSES` ẩn tài nguyên trong cả bốn trạng thái trên. Vì vậy deletion đã được chấp nhận nhưng danh sách/history còn cached, trang video không gọi `router.push('/videos')`, và các thao tác trên tài nguyên đã ẩn có thể trả 404. Nếu cleanup bị blocker không tự giải quyết, việc cập nhật UI bị giữ lại vô thời hạn; pending retention cũng trì hoãn UI không cần thiết.

Tái hiện helper từ source với preview `async_cleanup=true` và output count 1:

```text
LOGICALLY_DELETED -> settled=false
CLEANUP_PENDING  -> settled=false
CLEANUP_FAILED   -> settled=false
CLEANUP_BLOCKED  -> settled=false
CLEANUP_COMPLETE -> settled=true
```

Cách sửa: áp dụng logical deletion/invalidate cache khi operation hợp lệ được backend xác nhận đã ẩn tài nguyên; theo dõi cleanup trong một trạng thái độc lập. Không dùng completion của cleanup vật lý làm điều kiện duy nhất cho cập nhật danh sách hoặc rời trang tài nguyên. Regression phải kiểm tra callback/cache khi backend trả `LOGICALLY_DELETED`, kể cả cleanup sau đó pending/blocked.

## Bằng chứng và giới hạn

- Hai reproduction Node dùng các helper/status set trích trực tiếp từ source, không mô phỏng lại các điều kiện. Chưa mount component trong browser thật; lỗi được xác nhận ở helper và luồng callback/cache trong source.
- Backend đầy đủ trước lượt review: **2.026 passed, 63 skipped**, exit 0, **452,90 giây**.
- Frontend đầy đủ: **532 passed**; typecheck/lint/build qua. Gate auth frontend bổ sung: **46 passed**.
- Chưa có test frontend trực tiếp cho `DeletionControl` và các trạng thái cleanup backend thật trong tập test đã tìm.
- Giả thuyết graph generation/scene khác Video được kiểm tra trên fixture SQLite có foreign keys: mutation bị chặn bởi composite FK. Không đưa giả thuyết này vào danh sách lỗi.
- Auth/JWT/migration và các helper ownership/resolution đã được đọc lại; không có lỗi P1 mới được xác nhận trong phần đã rà. Không coi đây là chứng nhận toàn bộ repository không còn bug.
- PyJWT cài và lockfile cùng `2.15.1`; đã đối chiếu [changelog chính thức](https://pyjwt.readthedocs.io/en/stable/changelog.html). Không đổi dependency trong lượt review.
- Ba subagent được giao review bổ sung đều lỗi capacity hoặc stream dịch vụ trước khi trả kết quả; không ghi nhận lượt đó là review độc lập hoàn tất. Các review độc lập của vòng sửa trước vẫn có bằng chứng trong sổ closeout.
- PostgreSQL thực tế, lock/concurrency/trigger và GPU chưa được xác minh. Không chạy database ứng dụng, MinIO thật, migration production hoặc GPU.

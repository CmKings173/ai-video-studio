# Code review và testing — 2026-10-06

Workspace: `D:\project\ai-video-studio`; branch `codex/production-safety-fixes`.
HEAD: `01b740d8be4d58d3bd15bc46dd9842afb48af8bb`.
Review áp dụng cho working tree hiện tại, gồm cả file chưa được Git track.
Không commit, push, merge hoặc reset các thay đổi của người dùng.

## Finding còn tồn tại

**[P2] DirectorProbe có thể submit sau khi hết timeout tổng.**
`backend/apps/api/scripts/director_probe.py:125` await `object_info`, sau đó
`:134` submit mà chưa kiểm tra deadline. Timeout chỉ được kiểm tra trong vòng
poll tại `:144`; upload, submit, download và inspect cũng chưa có deadline tổng.
Reproducer cô lập đặt timeout 0.01 giây và cho adapter giả trả `object_info`
sau 0.05 giây: submit vẫn được gọi một lần, rồi script trả
`RECONCILIATION_REQUIRED` sau khoảng 0.062 giây. Không có network hoặc job GPU thật.

Cần áp dụng deadline cho từng await và không submit khi ngân sách thời gian đã
hết trong bước chuẩn bị. Nếu timeout xảy ra sau khi submission có thể đã được
chấp nhận, giữ correlation/ledger để resume; tuyệt đối không tự submit lại.
Evidence: `.artifacts/final-ui-media-closeout/testing-director-probe-timeout-20261006.json`.
Finding này chưa được sửa trong lượt review.

## Lỗi đã sửa khi kiểm tra

- Request Generate All trước migration không replay được vì fingerprint legacy
  chứa thêm field Director mới. Chỉ bỏ các field mới khi không được gửi tường minh;
  request thay đổi thật vẫn bị từ chối khi dùng lại idempotency key.
- Resolver ResolutionSelector legacy mất giới hạn pixel sau làm tròn. Khôi phục
  giới hạn 1,032,192 pixel riêng cho selector legacy; Director explicit canvas
  vẫn dùng bounds/evidence của profile riêng.
- Reuse history bỏ qua `snapshot.audio_policy`; bổ sung field typed và giữ đúng
  audio policy hiện hành, đồng thời chuyển cấu hình audio legacy sang schema mới.
- Probe H3 legacy đi vào graph Director khi chạy `--all`. Tách lane legacy và
  trả `DIRECTOR_PROBE_REQUIRED` trước submission khi gọi nhầm provider.
- UI Assembly hiện kích thước custom cũ khi chọn preset. Chỉ hiện width/height
  khi chọn Custom; validation preset không bị chặn bởi custom values không dùng.
- UI đọc `source_capabilities.modes` trong khi API trả `tasks`; sửa type/count và
  shape `custom_canvas.multiple`, `supports` theo hợp đồng API thực tế.
- Sửa fixture production guards để test đúng guard định kiểm tra, thay expectation
  metadata/audio theo hợp đồng hiện hành và ratio âm tính thành ratio không hỗ trợ.
- Fixture PostgreSQL gọi nhầm `asyncio.to_thread(subprocess.run(...))`, reuse
  connection asyncpg qua nhiều event loop, phụ thuộc SQL index spelling, và để
  job queued/identity cũ gây nhiễu test khác. Sửa callable, dùng NullPool, đọc
  predicate semantics, tạo identity riêng, kết thúc scheduler fixtures và để
  workflow claim-only disabled. Không nới guard production để làm test pass.

## Kết quả kiểm tra

| Lane | Kết quả | Evidence |
| --- | --- | --- |
| Backend full suite, gồm PostgreSQL thật | 422 passed, 1 skipped; 98.16s | `.artifacts/final-ui-media-closeout/testing-backend-20261006.log` |
| PostgreSQL riêng, gồm Director/standalone contention | 11 passed; 3.81s | `.artifacts/final-ui-media-closeout/testing-postgres-20261006.log` |
| PostgreSQL migrations | Upgrade head, downgrade d1e4f7a8c2b9, upgrade head/current PASS trên database mới trống | `.artifacts/final-ui-media-closeout/testing-migration-20261006.log` |
| Frontend tests | 61 passed, 0 failed | `.artifacts/final-ui-media-closeout/testing-frontend-20261006.log` |
| Frontend TypeScript / ESLint / production build | PASS | `tsc --noEmit`, `eslint .`, `.artifacts/final-ui-media-closeout/testing-build-20261006.log` |
| Docker backend / frontend builds | Cả hai PASS trên source hiện tại | `.artifacts/final-ui-media-closeout/testing-docker-api-20261006.log`, `.artifacts/final-ui-media-closeout/testing-docker-frontend-20261006.log` |
| MinIO thật | Health, put/get, replay cùng bytes PASS; thay bytes bị từ chối | `.artifacts/final-ui-media-closeout/testing-minio-20261006.json` |
| MinIO snapshot helper thật | Backup → restore vào bucket mới → verify PASS | `.artifacts/final-ui-media-closeout/testing-storage-backup-20261006.json`, `.artifacts/final-ui-media-closeout/testing-storage-restore-20261006.json`, `.artifacts/final-ui-media-closeout/testing-storage-verify-20261006.json` |
| Backend Ruff check | PASS | `python -m ruff check .` |
| Alembic heads | Một head: f3a7c9e1d2b4 | `python -m alembic heads` |
| Director graph/static source seam | 6 tasks STATIC_PASS; 4 source hashes PASS | `.artifacts/final-ui-media-closeout/testing-director-static-20261006.json` |
| Git whitespace | PASS | `git diff --check` |

PostgreSQL đã chạy trong container tạm `studio-review-pg-20261006`, loopback
15439, các database chỉ dành cho lượt kiểm tra này. SQLite migration/backfill,
FFmpeg geometry/audio/full decode, auth/CSRF/trusted ingress, API contracts,
asset lifecycle và các race regressions chạy trong full suite. Media CPU tests
dùng FFmpeg thật; chúng không chứng minh model H3 chạy được trên GPU.

Test POSIX UID/mode của backup helper bị skip trên Windows. Full-repo formatter
check còn báo mixed line endings/formatting ở nhiều file có sẵn; không format
hàng loạt working tree để tránh ghi đè/churn các thay đổi khác.

## Browser và runtime

API fixture có database SQLite riêng, mọi workflow generation disabled. Browser
đã xác minh login, sửa prompt/revision, lưu và mở lại ratio 21:9, capability gate,
assembly thiếu clip bị khóa, và preset Ultrawide không hiện custom dimensions cũ.
Console không ghi nhận error trong các thao tác đã kiểm tra. Đây là smoke test
được thực hiện bằng thao tác thật, không phải UAT toàn bộ hoặc GPU end-to-end.

Frontend image cuối cùng đã chạy trong Docker và nối API fixture thật. Browser
xác minh API/UI hiển thị đúng 6 source tasks, 0 qualified combinations. Các tính
năng chưa qualified vẫn disabled; cấu hình scene vẫn lưu/reopen được.

Backend image mới dùng Python 3.12, FFmpeg và dependencies được cài từ pyproject.
API đã startup thật với PostgreSQL và MinIO của lượt test: `/health/live` HTTP 200;
`/health/ready` HTTP 503, body `postgres=true, minio=true, comfyui=false`.
Readiness degraded là trạng thái thực tế do thiếu ComfyUI, không phải readiness
PASS. Evidence: `.artifacts/final-ui-media-closeout/testing-docker-runtime-20261006.json`.

Backup/restore ở trên chỉ kiểm tra utility snapshot S3 với một object test 27 bytes,
metadata và checksum. Không thay thế backup/restore toàn bộ PostgreSQL + media,
không đo RPO/RTO và không chứng minh restore dữ liệu production.

ComfyUI standard local endpoint 127.0.0.1:8188 không phản hồi. Chưa test GPU thật
cho T2V/I2V/FL2V/R2V/V2V/RV2V, bridge loading trên ComfyUI, Motion Context trim,
Refine/FaceRefine output/dependencies, VRAM/throughput hoặc UAT model. Các candidate
vẫn disabled, static PASS không phải qualification. Chưa chạy backup/restore
end-to-end trên môi trường production.

Cleanup: đã đăng xuất/đóng tab test, dừng API fixture và xóa đúng bốn container
tạm được tạo trong lượt review. Giữ log, báo cáo, evidence và hai review images;
không thay đổi container/database production.

Verdict: các regression chạy được đã được kiểm tra; còn finding P2 ở probe và
runtime qualification bên ngoài. Báo cáo này không phê duyệt production release.

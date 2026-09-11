# AI Advertising Video Studio - Tài liệu kỹ thuật / IT BA Specification

**Phiên bản:** 1.1 - Backend Implementation Ready  
**Ngày:** 09/09/2026  
**Mục đích:** Baseline kỹ thuật dùng để triển khai Backend/API cho Requirement 1. Tài liệu tổng hợp domain, luồng nghiệp vụ, data flow, AI orchestration, database, API contract, SSE progress, MinIO storage, trace/correlation, queue/recovery, deployment, evaluation và tiêu chí nghiệm thu đã thống nhất.

> Tài liệu này được viết đủ chi tiết để một backend coding agent/team Dev có thể dựng skeleton, database, API, worker/orchestrator và integration contract. Các thông số hiệu năng MiniMax H3/runtime vẫn phải được xác nhận bằng PoC/benchmark trên workstation thực tế trước khi cam kết SLA. Các API/field dưới đây là baseline; thay đổi contract phải được version và đồng bộ với FE.

## 1. Tóm tắt dự án

Xây dựng một **AI Advertising Video Studio on-premise** để nhân viên nội bộ tạo video quảng cáo cho chính sản phẩm của doanh nghiệp. Người dùng thao tác qua web portal; không thao tác trực tiếp với graph ComfyUI. Hệ thống hỗ trợ clip ngắn 4–15 giây và long video 30/60 giây được tạo từ nhiều scene, có thể review, regenerate, tạo variation và assemble thành final MP4.

| Hạng mục | Quyết định |
| --- | --- |
| Đối tượng dùng | Khoảng 5 editor nội bộ; ADMIN và EDITOR |
| Mô hình triển khai | On-premise, Docker Compose trên 1 workstation |
| GPU | 4× NVIDIA RTX PRO 5000 Blackwell 48GB |
| AI model | MiniMax H3 |
| AI workflow engine | ComfyUI |
| Queue | ComfyUI built-in execution queue; backend chỉ điều phối nhẹ/fairness |
| Redis | **Không dùng trong V1**; chỉ bổ sung khi multi-backend/multi-worker/high fan-out thực sự phát sinh |
| Realtime FE | **SSE** từ FastAPI; REST/PostgreSQL vẫn là source of truth |
| ORM | **SQLAlchemy 2.x async + asyncpg**, migration bằng **Alembic** |
| Storage | **MinIO**, một bucket `ai-video`; Local NVMe chỉ làm working/temp |
| Trace | `X-Request-ID/trace_id` + durable `generation_id` + `comfy_prompt_id` correlation |
| Video ngắn | Quick Clip 4–15s: T2V, I2V, First+Last, Ref2VA |
| Video dài | 30/60s = storyboard → nhiều scene ≤15s → generate từng scene → FFmpeg assemble |
| Editing | Scene-level; không xây timeline editor kiểu Premiere/CapCut |
| Variation | Scene variation; final video dùng version V1/V2/... |
| Template | Không thuộc V1; để backlog/phase sau |
| Batch CSV/Excel | Không thuộc V1; để backlog |

## 2. Mục tiêu và phạm vi

### 2.1 Mục tiêu business

- Rút ngắn thời gian tạo creative quảng cáo từ prompt và ảnh/reference sản phẩm.
- Cho phép editor tạo, thử lại và lựa chọn nhiều generation của cùng một scene mà không phải mở ComfyUI.
- Hỗ trợ long video nhưng vẫn giữ khả năng kiểm soát ở cấp scene.
- Quản lý project/campaign, sản phẩm, asset, video, scene, generation và final version một cách truy vết được.
- Chạy hoàn toàn on-premise và tận dụng workstation 4 GPU đã đầu tư.
- Có evaluation, benchmark và monitoring để đảm bảo chất lượng/ổn định thay vì chỉ chạy được demo.

### 2.2 In-scope V1

- Authentication; role ADMIN và EDITOR.
- Brand/Product master data cơ bản.
- Project/Campaign management.
- Asset upload và asset metadata.
- Quick Clip 4–15s.
- T2V, I2V/First Frame, First+Last Frame, Ref2VA (advanced).
- Long Video 30/60s bằng Hybrid Storyboard Planner.
- Prompt Engine: deterministic composer + optional local LLM enhancer.
- Scene workspace: preview, edit prompt, regenerate, variation, reorder, enable/disable, download.
- ComfyUI workflow registry + versioning.
- ComfyUI built-in queue + lightweight fair dispatch.
- FFmpeg assembly, CUT/CROSSFADE, audio đơn giản.
- Final video versioning.
- PostgreSQL business state; MinIO là durable object storage với **một bucket `ai-video`**; Local NVMe chỉ dùng làm working/temp.
- REST API + SSE progress; Trace/Correlation ID; structured JSON logging.
- SQLAlchemy 2.x async + asyncpg + Alembic.
- Monitoring GPU/storage/queue/failures và evaluation/UAT.

### 2.3 Out-of-scope V1 / Backlog

- Ad Template Management và thư viện hàng chục/hàng trăm template.
- CSV/Excel batch generation.
- Multi-product video nếu chưa có business requirement cụ thể.
- Google Drive/DAM connectors.
- Full timeline editor, keyframe, mask, color grading, multi-track editing.
- Advanced TTS/voice-over editor và audio timeline.
- Multi-workstation distributed scheduler/RabbitMQ/Celery/Kafka.
- Fine-tuning/LoRA, model marketplace, billing/multi-tenant.
- Full-video Variant A/B/C tự động; V1 chỉ scene variation + final version.

## 3. Thuật ngữ và domain model

| Thuật ngữ | Định nghĩa trong hệ thống |
| --- | --- |
| Brand | Thương hiệu/brand identity dùng làm context cho prompt và sản phẩm. |
| Product | Master data sản phẩm, tồn tại độc lập với campaign/project và có thể được reuse qua nhiều project. |
| Project | Campaign/workspace để gom các video và asset liên quan; không phải software project. |
| Asset | Tài nguyên file: image/video/audio/logo/frame/reference/generated output/final output. |
| Video | Business object của một creative đang được sản xuất; không đồng nghĩa với file MP4. |
| Scene | Một cảnh/đơn vị video ngắn, thường 4–15s, có prompt và generation riêng. |
| Generation | Một lần AI/H3 thực sự chạy để sinh output cho một scene. |
| Variation | Một generation thay thế/sáng tạo khác của cùng scene. |
| Final Video Version | Một lần assemble các selected scene; V1, V2... không overwrite bản cũ. |
| Workflow | ComfyUI graph kỹ thuật dùng để chạy H3 cho một generation mode. |
| Registry | Danh mục workflow/version được backend cho phép dùng trong production. |
| Execution Prompt | Prompt cuối cùng được gửi xuống ComfyUI/H3 sau khi Prompt Engine compose/enhance. |

![Hình 1 - Domain hierarchy và business object chính](assets/01_domain_hierarchy.png)

*Hình 1 - Domain hierarchy và business object chính*

## 4. Người dùng và quyền

| Role | Quyền chính |
| --- | --- |
| ADMIN | Quản lý user; xem project/video; xem lỗi/usage; cấu hình business setting cơ bản; không cần expose graph ComfyUI. |
| EDITOR | Tạo/sửa project, product, asset, video; generate scene; variation/regenerate; assemble; download. |

Không đưa **AI_ENGINEER** thành customer-facing role. Việc chỉnh graph ComfyUI, model/runtime và workflow version là hoạt động kỹ thuật/vận hành, có thể truy cập qua ComfyUI/admin endpoint riêng bởi team triển khai hoặc technical owner của khách hàng.

## 5. Kiến trúc tổng thể

![Hình 2 - Kiến trúc logical tổng thể](assets/02_system_architecture.png)

*Hình 2 - Kiến trúc logical tổng thể*

| Thành phần | Vai trò |
| --- | --- |
| Frontend Web Portal | UI cho editor/admin; project/product/video/scene management; preview; trạng thái generation. |
| Backend API | Business orchestration, authorization, DB state, asset metadata, dispatch, routing, API cho frontend. |
| Storyboard Planner | Chỉ cho Long Video; tạo structured scene specifications bằng rule + optional local LLM. |
| Prompt Engine | Biến structured context thành execution prompt phù hợp H3. |
| Workflow Router | Chọn approved workflow T2V/I2V/First+Last/Ref2VA. |
| ComfyUI | AI Workflow Execution Engine; built-in queue; thực thi H3 graph. |
| MiniMax H3 | Model sinh video/audio. |
| PostgreSQL | Source of truth cho business state/metadata/history/progress hiện hành. |
| SQLAlchemy 2.x + asyncpg + Alembic | ORM async, PostgreSQL driver và schema migration. |
| MinIO | Durable binary storage; **một bucket `ai-video`**; DB lưu `object_key`/metadata, không lưu binary. |
| Local NVMe Workspace | Materialize input từ MinIO, temp/intermediate/output trước khi upload lại MinIO. |
| SSE | Backend → Frontend realtime progress/status; không thay REST/DB source of truth. |
| Trace/Structured Logs | X-Request-ID/trace_id + generation/video/scene/comfy IDs để debug end-to-end. |
| FFmpeg Worker | Normalize scene và assemble final MP4. |
| Monitoring | GPU/VRAM/temp, disk, queue, generation latency/failure. |

## 6. Vai trò của ComfyUI

ComfyUI là **AI Workflow Execution Engine**, không phải business application. Backend quyết định cần làm gì; ComfyUI thực thi graph AI; H3 là model sinh video.

- Nhận workflow JSON đã được backend chọn.
- Nhận prompt, input image/reference, duration, ratio, seed và technical parameters.
- Quản lý execution queue phía AI.
- Thực thi các node: load model/encoder, conditioning, sampling, decode, save output.
- Kết nối H3 runtime/PyTorch/CUDA với 4 GPU.
- Trả prompt_id/history/output cho backend theo dõi.

ComfyUI **không** quản lý Project, Product, Video, Scene, user permission, final version, storyboard, business history hoặc asset lifecycle. PostgreSQL + Backend mới là source of truth.

## 7. Quản lý Project / Product / Asset / Video

### 7.1 Product là master data, không sở hữu cứng bởi Project

Một sản phẩm có thể được quảng cáo qua nhiều campaign. Vì vậy Product nên tồn tại độc lập; Video tham chiếu Product và Project. Nếu sau này cần giới hạn product trong từng project có thể thêm mapping `project_products`.

```text
Brand XYZ
  └─ Product: XYZ Perfume
       ├─ Summer Campaign → Video A
       ├─ Valentine Campaign → Video B
       └─ TikTok Campaign → Video C
```

### 7.2 Project

Project là campaign/workspace. Một project có nhiều video và có thể có asset project-scoped. Project hỗ trợ ACTIVE/ARCHIVED, search/filter và soft-delete/archive thay vì xóa vật lý ngay.

### 7.3 Asset

Asset là metadata của mọi file. File durable nằm trong **MinIO**; local NVMe chỉ là working/temp. Một asset có thể được reuse trong nhiều generation; không duplicate binary khi reuse. Bucket MinIO không lộ ra UI.

### 7.4 Video

Video là business object đang được sản xuất. Quick Clip cũng là một Video có đúng 1 Scene; Long Video có N Scene. Cách này giúp toàn bộ hệ thống dùng một data model thống nhất.

## 8. Quick Clip 4–15s

![Hình 3 - Quick Clip end-to-end flow](assets/03_quick_clip_flow.png)

*Hình 3 - Quick Clip end-to-end flow*

### 8.1 Generation modes

| Mode | Input | Ý nghĩa / use case |
| --- | --- | --- |
| T2V | Text prompt | Tạo scene từ text; phù hợp B-roll/environment hoặc cảnh không cần giữ chính xác product image. |
| I2V / First Frame | Image + prompt | Ảnh là điểm bắt đầu/conditioning; phù hợp product hero, packshot animation. |
| First + Last | First frame + last frame + prompt | Kiểm soát điểm đầu và điểm kết thúc; hữu ích transition/continuity. |
| Ref2VA | Prompt + image/video/audio references | Advanced reference generation; dùng khi cần nhiều reference/identity/style/motion/audio. Giữ trong thiết kế nhưng cần PoC riêng. |

### 8.2 Router rule đề xuất

```python
if has_reference_assets:
    mode = REF2VA
elif first_frame and last_frame:
    mode = FIRST_LAST
elif first_frame_or_product_image:
    mode = I2V
else:
    mode = T2V
```

UI không bắt editor hiểu T2V/I2V/Ref2VA ở mức kỹ thuật. UI hiển thị Prompt, Product/Starting Frame, Ending Frame và Advanced References; backend tự route.

## 9. Long Video 30/60s

![Hình 4 - Long Video: storyboard → scene → generation → final](assets/04_long_video_flow.png)

*Hình 4 - Long Video: storyboard → scene → generation → final*

Long Video không phải một inference H3 60s. Đây là application orchestration: master brief được chuyển thành nhiều scene ≤15s, từng scene generate riêng, editor chọn output và FFmpeg assemble.

### 9.1 Scene-level editing

- Edit scene prompt
- Regenerate
- Generate variation
- Select preferred generation
- Reorder
- Enable/disable scene
- Preview/download individual scene
- Reassemble final video

### 9.2 Variation và final version

![Hình 5 - Scene variation và Final Video versioning](assets/08_scene_variation_final_version.png)

*Hình 5 - Scene variation và Final Video versioning*

V1 hỗ trợ **variation ở cấp scene** và **version ở cấp final video**. Không xây full-video variant entity A/B/C trong V1.

## 10. Hybrid Storyboard Planner

Hybrid = **system rule kiểm soát constraint + local LLM sáng tạo nội dung**. Không phụ thuộc Ad Template feature.

![Hình 6 - Hybrid Storyboard Planner](assets/05_storyboard_hybrid.png)

*Hình 6 - Hybrid Storyboard Planner*

### 10.1 System rules

- Mỗi scene không vượt giới hạn generation được cấu hình (baseline thiết kế ≤15s).
- Tổng duration scene phải phù hợp requested 30/60s.
- Scene order duy nhất, không âm/zero duration.
- Đảm bảo các scene specification có đủ field bắt buộc.
- Có thể có minimum structure: opening/hook, product exposure, body/benefit, ending; đây là rule, không phải template library.

### 10.2 Local LLM

LLM chỉ đề xuất creative content: title, purpose, description, subject, action, environment, camera, lighting, style, continuity. Nếu LLM không mang lại improvement đủ lớn có thể disable và dùng RulePlanner; Quick Clip vẫn hoạt động bình thường.

### 10.3 Scene Specification đề xuất

```json
{
  "scene_order": 3,
  "title": "Product Detail",
  "purpose": "PRODUCT_DETAIL",
  "duration_sec": 10,
  "description": "Macro detail of the perfume bottle",
  "subject": "XYZ perfume bottle",
  "action": "slow rotation",
  "environment": "dark premium studio",
  "camera": "macro slow push-in",
  "lighting": "soft golden rim light",
  "style": "luxury cinematic commercial",
  "continuity": "CUT"
}
```

Storyboard V1 không cần bảng `storyboards` riêng. Kết quả planner được persist trực tiếp thành các row trong `scenes`. Storyboard versioning có thể bổ sung sau nếu business cần restore/compare nhiều storyboard.

## 11. Prompt Engine

Prompt Engine chuyển business/creative context có cấu trúc thành execution prompt gửi H3. Phương án phù hợp nhất là **deterministic composer + optional local LLM enhancer**.

![Hình 7 - Prompt Engine hybrid](assets/06_prompt_engine.png)

*Hình 7 - Prompt Engine hybrid*

### 11.1 Sáu lớp context

| Lớp | Ví dụ |
| --- | --- |
| Product Context | Tên sản phẩm, mô tả, đặc điểm visual quan trọng, selling points. |
| Brand Context | Luxury/minimal/premium, màu/tone. |
| Scene Intent | HOOK / PRODUCT_DETAIL / LIFESTYLE / ENDING... |
| Visual Direction | Camera, lighting, environment, motion. |
| User Instruction | Prompt/brief của editor. |
| Constraints | Giữ product identity/geometry; không thêm object ngoài ý định; continuity rules. |

### 11.2 Composer

Python/Jinja2 compose base prompt theo cấu trúc xác định. Đây là đường chạy bắt buộc và deterministic.

### 11.3 Optional LLM Enhancer

LLM chỉ rewrite/diễn đạt cinematic hơn; không được đổi product/intent. Nếu enhancer lỗi hoặc bị disable, base prompt vẫn được gửi H3. Không để LLM failure làm cả hệ thống mất khả năng generate.

### 11.4 Prompt snapshot

Mỗi `scene_generation` nên lưu `scene_prompt_snapshot` và `execution_prompt` để biết editor/planner đã thấy gì và prompt thực tế nào được gửi xuống H3. Đây là dữ liệu quan trọng khi debug/eval/reproduce.

## 12. Workflow Router và Workflow Registry

| Workflow code đề xuất | Mode | Ghi chú |
| --- | --- | --- |
| H3_T2V_STANDARD | T2V | Text-only generation. |
| H3_I2V_STANDARD | I2V | Product image/first-frame generation. |
| H3_FIRST_LAST | FIRST_LAST | First + last frame conditioning. |
| H3_REF2VA | REF2VA | Advanced references; PoC riêng. |
| H3_FAST_DRAFT (optional) | Tùy mode | Chỉ bổ sung sau benchmark nếu draft profile mang lại lợi ích rõ. |

Không tạo workflow theo từng loại quảng cáo/perfume/food/fashion. Product template/business pattern và ComfyUI workflow là hai khái niệm khác nhau. Workflow phải được version (`v1.0`, `v1.1`...) và generation lưu đúng version đã dùng.

## 13. Queue, dispatch và trạng thái generation

V1 sử dụng **ComfyUI built-in queue** và **không dùng Redis/RabbitMQ/Celery/Kafka**. PostgreSQL không thay execution queue; DB giữ durable business state. Backend chỉ có lightweight dispatcher để chọn record `CREATED` đủ điều kiện và submit một lượng nhỏ sang ComfyUI nhằm đảm bảo fairness, sau đó ComfyUI mới là nơi xếp hàng/thực thi.

![Hình 8 - ComfyUI queue và lightweight fairness](assets/07_comfyui_queue_fairness.png)

*Hình 8 - ComfyUI queue và lightweight fairness*

### 13.1 Vì sao vẫn lưu generation trong PostgreSQL?

- ComfyUI không biết prompt_id thuộc Project/Video/Scene/User nào.
- Backend restart vẫn cần biết generation nào đang QUEUED/RUNNING và output nào thuộc scene nào.
- Cần history prompt/workflow/seed/input/output/error/timestamps để debug và reproduce.
- Cần selected generation cho final assembly và variation history.

Không cần generic `jobs` table trong V1. `scene_generations` chính là durable record của H3 generation job; `final_videos` chính là durable record của FFmpeg assembly job.

### 13.2 Fairness

Không dump toàn bộ 5–6 scene của một Long Video vào ComfyUI trước các request khác. Khi user bấm **Generate All**, backend tạo N `scene_generations` ở trạng thái `CREATED`. Dispatcher chỉ submit lượng pending nhỏ sang ComfyUI và ưu tiên không chọn cùng `video_id` liên tiếp nếu có video khác đang chờ. Sau khi ComfyUI nhận prompt, record chuyển `QUEUED` và lưu `comfy_prompt_id`.

V1 có thể chạy dispatcher trong cùng backend instance hoặc một process chuyên dụng nhưng vẫn dùng PostgreSQL làm coordination. Nếu dùng process riêng, query nên khóa record bằng `SELECT ... FOR UPDATE SKIP LOCKED` để tránh double-dispatch. **Không biến dispatcher thành một queue thứ hai.**

## 14. State machine, cancel, retry và recovery

![Hình 9 - Generation state, retry và recovery](assets/09_failure_retry_recovery.png)

*Hình 9 - Generation state, retry và recovery*

### 14.1 Scene Generation state

```text
CREATED (pending dispatch) → QUEUED (accepted by ComfyUI) → RUNNING → COMPLETED
                     ├→ FAILED
                     └→ CANCELLED
RUNNING + runtime restart → INTERRUPTED → reconcile → COMPLETED/FAILED
```

### 14.2 Video state

```text
Quick Clip:
DRAFT → GENERATING → READY | FAILED | CANCELLED

Long Video:
DRAFT → PLANNING → STORYBOARD_READY → GENERATING → SCENES_READY → ASSEMBLING → READY
READY + scene change → DIRTY → ASSEMBLING → READY
```

### 14.3 Retry policy đề xuất

| Lỗi | Policy V1 |
| --- | --- |
| ComfyUI temporary unavailable / network timeout | Auto retry tối đa 1 lần. |
| Temporary file access / FFmpeg transient error | Auto retry tối đa 1 lần. |
| Invalid/unsupported input | Fail; user phải sửa input. |
| Invalid workflow/config | Fail + technical alert; không auto retry loop. |
| GPU OOM | Fail + log/alert; không âm thầm hạ quality vì sẽ đổi expectation. |
| Disk full | Fail + alert P0 vận hành. |
| User cancel | Không retry. |

### 14.4 Cancel

QUEUED generation có thể remove/cancel trước khi chạy. RUNNING cancellation là best-effort theo khả năng ComfyUI/runtime; DB phải phản ánh `CANCELLED` hoặc lỗi rõ ràng, không fake thành success.

### 14.5 Startup reconciliation

Khi backend/ComfyUI restart, backend quét generation `RUNNING/QUEUED`, dùng `comfy_prompt_id` để hỏi ComfyUI/history. Nếu output đã có thì complete; nếu prompt mất thì chuyển INTERRUPTED/FAILED và cho phép retry.

## 15. Video Assembly và Audio

### 15.1 Assembly pipeline

```text
Selected Scene Outputs
  ↓ Validate files
  ↓ Normalize resolution / FPS / codec / audio format
  ↓ Order by scene_order
  ↓ CUT or CROSSFADE
  ↓ Handle audio policy
  ↓ Encode H.264/AAC MP4
  ↓ FFprobe validation
  ↓ Final Video Version
```

### 15.2 Audio V1

| Use case | Policy |
| --- | --- |
| Quick Clip | Giữ H3 native audio nếu có; editor có thể mute. |
| Long Video | Không mặc định nối audio từng scene một cách mù quáng. Cho phép mute/keep theo policy và upload 1 background audio file. |
| Advanced TTS/VO/multi-track | Backlog; không xây audio editor trong V1. |

Transition V1 nên giới hạn **CUT** và **CROSSFADE**. Logo/CTA overlay chỉ bổ sung nếu business xác nhận là must-have.

## 16. Data architecture và ERD

![Hình 10 - Conceptual ERD V1](assets/11_erd_conceptual.png)

*Hình 10 - Conceptual ERD V1*

Nguyên tắc: PostgreSQL lưu metadata/business state; binary PNG/MP4/WAV không lưu trong DB. `assets` là registry metadata thống nhất cho input, reference, generated scene, thumbnail và final video.

### 16.1 Cardinality chính

| Quan hệ | Cardinality | Ý nghĩa |
| --- | --- | --- |
| User → Project | 1:N | Một user tạo nhiều project. |
| Brand → Product | 1:N | Một brand có nhiều product. |
| Project → Video | 1:N | Một campaign có nhiều creative/video. |
| Product → Video | 1:N trong V1 | Một video quảng cáo một product; multi-product để sau. |
| Video → Scene | 1:N | Quick Clip có 1 scene; Long Video có N scene. |
| Scene → Scene Generation | 1:N | Mỗi lần regenerate/variation tạo generation mới. |
| Workflow → Scene Generation | 1:N | Nhiều generation dùng cùng approved workflow/version. |
| Generation <-> Asset | N:N qua generation_assets | Một generation dùng nhiều references; asset có thể reuse nhiều generation. |
| Generation → Output Asset | 0/1:1 | Generation completed tạo generated video asset. |
| Video → Final Video | 1:N | Một video có nhiều assembly version. |
| Final Video → Asset | 1:1 | Một version tham chiếu một MP4 asset. |

## 17. Thiết kế database chi tiết

Data type bên dưới là logical PostgreSQL design. UUID dùng cho business entity; timestamps nên dùng `TIMESTAMPTZ`. Các enum có thể triển khai bằng PostgreSQL ENUM hoặc VARCHAR + application validation tùy convention của team.

### 17.1 `users`

| Field | Type | Mô tả | Vì sao cần |
| --- | --- | --- | --- |
| id | UUID PK | ID user | Primary key ổn định cho FK. |
| username | VARCHAR(100) UNIQUE | Tên đăng nhập | Authentication. |
| email | VARCHAR(255) UNIQUE NULL | Email | Login/contact nếu business dùng. |
| password_hash | TEXT | Password đã hash | Không lưu plain password. |
| display_name | VARCHAR(255) | Tên hiển thị | UI/audit. |
| role | VARCHAR(30) | ADMIN/EDITOR | Authorization. |
| status | VARCHAR(30) | ACTIVE/DISABLED | Khóa user mà không xóa lịch sử. |
| last_login_at | TIMESTAMPTZ NULL | Lần login gần nhất | Audit cơ bản. |
| created_at | TIMESTAMPTZ | Ngày tạo | Audit. |
| updated_at | TIMESTAMPTZ | Ngày sửa | Audit. |

### 17.2 `projects`

| Field | Type | Mô tả | Vì sao cần |
| --- | --- | --- | --- |
| id | UUID PK | Project ID | Primary key. |
| name | VARCHAR(255) | Tên campaign/workspace | Hiển thị và quản lý. |
| description | TEXT NULL | Mô tả | Context business. |
| status | VARCHAR(30) | ACTIVE/ARCHIVED | Archive thay vì hard delete. |
| created_by | UUID FK users.id | Người tạo | Ownership/audit. |
| created_at | TIMESTAMPTZ | Ngày tạo | Audit. |
| updated_at | TIMESTAMPTZ | Ngày sửa | Audit. |

### 17.3 `brands`

| Field | Type | Mô tả | Vì sao cần |
| --- | --- | --- | --- |
| id | UUID PK | Brand ID | Primary key. |
| name | VARCHAR(255) | Tên brand | Brand context. |
| description | TEXT NULL | Mô tả | Prompt context. |
| logo_asset_id | UUID FK assets.id NULL | Logo | Reuse asset. |
| visual_style | JSONB NULL | Style: luxury/minimal... | Prompt composition linh hoạt. |
| brand_colors | JSONB NULL | Màu brand | Consistency/context. |
| tone | VARCHAR(100) NULL | Tone | Prompt/creative direction. |
| default_cta | VARCHAR(255) NULL | CTA mặc định | Có thể dùng về sau. |
| created_at | TIMESTAMPTZ | Ngày tạo | Audit. |
| updated_at | TIMESTAMPTZ | Ngày sửa | Audit. |

### 17.4 `products`

| Field | Type | Mô tả | Vì sao cần |
| --- | --- | --- | --- |
| id | UUID PK | Product ID | Primary key. |
| brand_id | UUID FK brands.id NULL | Brand | Tách master product khỏi campaign. |
| name | VARCHAR(255) | Tên sản phẩm | UI/prompt. |
| description | TEXT NULL | Mô tả | Prompt/Planner context. |
| selling_points | JSONB NULL | USP/selling points | Planner/Prompt Engine. |
| product_category | VARCHAR(100) NULL | Perfume/Food/... | Filter; future template routing. |
| status | VARCHAR(30) | ACTIVE/ARCHIVED | Lifecycle. |
| created_by | UUID FK users.id | Người tạo | Audit. |
| created_at | TIMESTAMPTZ | Ngày tạo | Audit. |
| updated_at | TIMESTAMPTZ | Ngày sửa | Audit. |

### 17.5 `assets`

MinIO dùng **một bucket duy nhất `ai-video`** được khai báo bằng environment/config; không cần lưu bucket ở từng row. DB chỉ lưu `object_key` và metadata.

| Field | Type | Mô tả | Vì sao cần |
| --- | --- | --- | --- |
| id | UUID PK | Asset ID | Registry key cho mọi binary file. |
| project_id | UUID FK projects.id NULL | Project scope | Asset campaign-specific nếu có. |
| product_id | UUID FK products.id NULL | Product scope | Product image/reference reuse. |
| asset_type | VARCHAR(50) | PRODUCT_IMAGE/PROJECT_REFERENCE/REFERENCE_VIDEO/REFERENCE_AUDIO/GENERATED_VIDEO/THUMBNAIL/FINAL_VIDEO/... | Biết semantic role của file. |
| status | VARCHAR(30) | PENDING_UPLOAD/READY/FAILED | Quản lý presigned upload lifecycle; chỉ READY asset được dùng để generate. |
| original_file_name | VARCHAR(512) | Tên file user upload hoặc tên output logic | UI/download/audit. |
| mime_type | VARCHAR(100) | MIME | Validation/serve file. |
| object_key | TEXT UNIQUE | Key trong bucket `ai-video`, ví dụ `generated/V100/S003/G019/output.mp4` | Source of truth vị trí object trong MinIO. |
| size_bytes | BIGINT | Dung lượng | Storage monitoring/quota. |
| etag | VARCHAR(255) NULL | ETag từ MinIO/S3 | Integrity/cache/debug. |
| checksum_sha256 | VARCHAR(128) NULL | SHA-256 nếu bật | Integrity/deduplicate nếu cần. |
| width | INTEGER NULL | Width px | Validation/assembly. |
| height | INTEGER NULL | Height px | Validation/assembly. |
| duration_ms | BIGINT NULL | Duration | Video/audio metadata. |
| fps | NUMERIC NULL | FPS | Normalize/assembly. |
| uploaded_by | UUID FK users.id NULL | Người upload | Audit; generated asset có thể NULL/system. |
| created_at | TIMESTAMPTZ | Ngày tạo | Audit. |
| deleted_at | TIMESTAMPTZ NULL | Soft delete | Không xóa physical object ngay; cleanup theo policy. |

**Invariant đề xuất:** `project_id` và `product_id` không nên cùng có giá trị cho một source asset; generated/final output có thể để cả hai NULL vì quan hệ business đã đi qua generation/final video. Có thể enforce bằng CHECK constraint.

### 17.6 `videos`

| Field | Type | Mô tả | Vì sao cần |
| --- | --- | --- | --- |
| id | UUID PK | Video business ID | Container logical, không phải MP4. |
| project_id | UUID FK projects.id | Campaign | Tổ chức creative. |
| product_id | UUID FK products.id NULL | Sản phẩm chính | V1 một product/video. |
| brand_id | UUID FK brands.id NULL | Brand snapshot/reference | Dễ truy vấn context. |
| title | VARCHAR(255) | Tên video | UI. |
| video_type | VARCHAR(30) | QUICK_CLIP/LONG_VIDEO | Quyết định flow. |
| master_prompt | TEXT | Brief/prompt chính | Quick Clip prompt hoặc Long Video brief. |
| requested_duration_sec | INTEGER | 4–15/30/60 theo config | Planner/generation. |
| aspect_ratio | VARCHAR(20) | 9:16/16:9/... | AI generation/export. |
| quality_profile | VARCHAR(30) | STANDARD/DRAFT nếu enable | Runtime profile. |
| status | VARCHAR(40) | DRAFT/PLANNING/.../READY/DIRTY | UI và orchestration. |
| current_final_video_id | UUID FK final_videos.id NULL | Final version hiện hành | Preview/download nhanh. |
| created_by | UUID FK users.id | Editor tạo | Audit. |
| created_at | TIMESTAMPTZ | Ngày tạo | Audit. |
| updated_at | TIMESTAMPTZ | Ngày sửa | Audit. |

### 17.7 `scenes`

| Field | Type | Mô tả | Vì sao cần |
| --- | --- | --- | --- |
| id | UUID PK | Scene ID | Primary key. |
| video_id | UUID FK videos.id | Video cha | 1:N. |
| scene_order | INTEGER | Thứ tự | Assembly. |
| title | VARCHAR(255) NULL | Tên scene | Storyboard UI. |
| scene_type | VARCHAR(50) NULL | HOOK/PRODUCT_DETAIL/LIFESTYLE/... | Planner/analysis; không phải template. |
| prompt | TEXT | Current scene prompt | Editor sửa được. |
| duration_sec | INTEGER | Scene duration | H3 input; validate max. |
| continuity_type | VARCHAR(30) | CUT/CONTINUOUS | First-frame chaining/assembly context. |
| status | VARCHAR(30) | DRAFT/GENERATING/READY/FAILED | Scene UI state. |
| selected_generation_id | UUID FK scene_generations.id NULL | Generation được chọn | Input cho assembly. |
| is_enabled | BOOLEAN DEFAULT TRUE | Có đưa scene vào final không | Disable không mất history. |
| created_at | TIMESTAMPTZ | Ngày tạo | Audit. |
| updated_at | TIMESTAMPTZ | Ngày sửa | Audit. |

### 17.8 `scene_generations`

| Field | Type | Mô tả | Vì sao cần |
| --- | --- | --- | --- |
| id | UUID PK | Generation ID | Mỗi row = một lần H3 chạy; durable async record. |
| scene_id | UUID FK scenes.id | Scene cha | Mapping business. |
| generation_no | INTEGER | Số lần generate | History/UI. |
| generation_mode | VARCHAR(30) | T2V/I2V/FIRST_LAST/REF2VA | Workflow routing. |
| variation_type | VARCHAR(30) | ORIGINAL/VARIATION/REGENERATE | Hiểu quan hệ creative. |
| workflow_id | UUID FK workflow_registry.id | Workflow/version | Reproduce/debug. |
| scene_prompt_snapshot | TEXT | Prompt scene tại thời điểm chạy | Không bị thay đổi khi editor sửa sau. |
| execution_prompt | TEXT | Prompt thực tế gửi H3 | Debug/eval/reproduce. |
| seed | BIGINT NULL | Seed | Reproduce/variation nếu runtime hỗ trợ. |
| duration_sec | INTEGER | Duration thực request | Audit. |
| aspect_ratio | VARCHAR(20) | Ratio thực request | Audit. |
| quality_profile | VARCHAR(30) | Runtime profile | Performance/quality comparison. |
| parameters | JSONB NULL | Steps/sampler/runtime params | Linh hoạt, không biến mọi param thành column. |
| status | VARCHAR(30) | CREATED/QUEUED/RUNNING/COMPLETED/FAILED/CANCELLED/INTERRUPTED | Durable execution state. |
| request_trace_id | VARCHAR(64) NULL | Trace ID của HTTP request khởi tạo generation | Nối request gốc với execution dài hạn; `generation_id` vẫn là correlation ID durable chính. |
| comfy_prompt_id | VARCHAR(255) NULL | Prompt ID ComfyUI | Bridge business state <-> execution engine. |
| progress_stage | VARCHAR(50) NULL | PREPARING/GENERATING/DECODING/SAVING... | Cho SSE/reconnect hiển thị stage hiện hành. |
| progress_percent | SMALLINT NULL | 0–100 nếu runtime cung cấp đáng tin cậy | Không fake %; NULL nếu chỉ có stage. |
| progress_updated_at | TIMESTAMPTZ NULL | Lần progress cuối | SSE/status freshness. |
| output_asset_id | UUID FK assets.id NULL | Generated video asset | Result. |
| error_code | VARCHAR(100) NULL | Mã lỗi | Support/metrics. |
| error_message | TEXT NULL | Chi tiết lỗi | Debug. |
| retry_count | INTEGER DEFAULT 0 | Số lần retry | Enforce policy. |
| queued_at | TIMESTAMPTZ NULL | Vào queue | Queue latency. |
| started_at | TIMESTAMPTZ NULL | Bắt đầu | Execution latency. |
| completed_at | TIMESTAMPTZ NULL | Kết thúc | Metrics. |
| created_at | TIMESTAMPTZ | Ngày tạo | Audit. |

### 17.9 `generation_assets`

| Field | Type | Mô tả | Vì sao cần |
| --- | --- | --- | --- |
| id | UUID PK | Mapping ID | Junction table. |
| generation_id | UUID FK scene_generations.id | Generation | Parent. |
| asset_id | UUID FK assets.id | Input asset | Reference/input. |
| role | VARCHAR(50) | FIRST_FRAME/LAST_FRAME/PRODUCT_REFERENCE/IMAGE_REFERENCE/VIDEO_REFERENCE/AUDIO_REFERENCE | Nói H3/router asset được dùng như thế nào. |
| sort_order | INTEGER NULL | Thứ tự reference | Hỗ trợ multi-reference. |
| created_at | TIMESTAMPTZ | Ngày tạo | Audit. |

### 17.10 `workflow_registry`

| Field | Type | Mô tả | Vì sao cần |
| --- | --- | --- | --- |
| id | UUID PK | Workflow ID | Primary key. |
| code | VARCHAR(100) | H3_I2V_STANDARD | Machine-readable identifier. |
| name | VARCHAR(255) | Tên hiển thị | Technical admin. |
| generation_mode | VARCHAR(30) | T2V/I2V/FIRST_LAST/REF2VA | Router. |
| version | VARCHAR(50) | v1.0/v1.1 | Versioning. |
| workflow_path | TEXT | Đường dẫn JSON | Backend load/inject workflow. |
| model_name | VARCHAR(255) | MiniMax H3 | Trace. |
| model_version | VARCHAR(255) NULL | Checkpoint/runtime version | Reproducibility. |
| quality_profile | VARCHAR(30) | STANDARD/DRAFT... | Router/config. |
| config | JSONB NULL | Default technical config | Flexible workflow params. |
| enabled | BOOLEAN | Cho phép production dùng | Rollback/disable. |
| created_at | TIMESTAMPTZ | Ngày tạo | Audit. |
| approved_at | TIMESTAMPTZ NULL | Ngày approve | Workflow governance. |

### 17.11 `final_videos`

| Field | Type | Mô tả | Vì sao cần |
| --- | --- | --- | --- |
| id | UUID PK | Final version ID | Mỗi row = một lần FFmpeg assembly. |
| video_id | UUID FK videos.id | Video cha | 1:N versions. |
| version_no | INTEGER | V1/V2/... | Không overwrite. |
| asset_id | UUID FK assets.id NULL | Final MP4 asset | Output. |
| status | VARCHAR(30) | CREATED/QUEUED/ASSEMBLING/READY/FAILED/CANCELLED | Assembly async state; record này chính là durable assembly job. |
| request_trace_id | VARCHAR(64) NULL | Trace ID request assemble/rebuild | Debug/correlation. |
| progress_stage | VARCHAR(50) NULL | PREPARING/NORMALIZING/COMBINING/ENCODING/VALIDATING | SSE progress stage. |
| progress_percent | SMALLINT NULL | 0–100 nếu worker có thể tính đáng tin cậy | Optional; không fake. |
| duration_ms | BIGINT NULL | Duration thực | Validation. |
| width | INTEGER NULL | Width | Export metadata. |
| height | INTEGER NULL | Height | Export metadata. |
| fps | NUMERIC NULL | FPS | Export metadata. |
| codec | VARCHAR(50) NULL | H264/H265... | Trace/validation. |
| assembly_config | JSONB NULL | CUT/CROSSFADE/audio config | Reproduce final. |
| error_message | TEXT NULL | Lỗi FFmpeg | Debug. |
| started_at | TIMESTAMPTZ NULL | Bắt đầu | Performance. |
| completed_at | TIMESTAMPTZ NULL | Kết thúc | Performance. |
| created_at | TIMESTAMPTZ | Ngày tạo | Audit. |

### 17.12 Index đề xuất

```sql
CREATE INDEX idx_videos_project_status ON videos(project_id, status);
CREATE INDEX idx_videos_created_by ON videos(created_by);
CREATE UNIQUE INDEX uq_scenes_video_order ON scenes(video_id, scene_order);
CREATE UNIQUE INDEX uq_generations_scene_no ON scene_generations(scene_id, generation_no);
CREATE INDEX idx_generations_scene ON scene_generations(scene_id);
CREATE INDEX idx_generations_status ON scene_generations(status);
CREATE UNIQUE INDEX idx_generations_comfy_prompt ON scene_generations(comfy_prompt_id) WHERE comfy_prompt_id IS NOT NULL;
CREATE INDEX idx_assets_project_type ON assets(project_id, asset_type) WHERE deleted_at IS NULL;
CREATE INDEX idx_assets_product_type ON assets(product_id, asset_type) WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX idx_assets_object_key ON assets(object_key) WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX uq_final_video_version ON final_videos(video_id, version_no);
```

### 17.13 JSONB guideline

Không biến DB thành JSON. Các field query/filter quan trọng phải là column. JSONB chỉ dùng cho technical parameters, selling_points/visual_style và config linh hoạt.


### 17.14 Constraint / integrity đề xuất

- `UNIQUE(scenes.video_id, scenes.scene_order)` để không có hai scene cùng thứ tự trong một video.
- `UNIQUE(scene_generations.scene_id, generation_no)` để history generation ổn định.
- `UNIQUE(final_videos.video_id, version_no)` để rebuild không sinh trùng version.
- Chỉ generation `COMPLETED` và có `output_asset_id` READY mới được gán `scenes.selected_generation_id`.
- `generation_assets` phải thuộc đúng generation; asset phải READY trước khi dispatch.
- Reorder/disable/select sau khi đã có Final READY phải chuyển `videos.status=DIRTY` trong cùng transaction.
- `videos.status` và `scenes.status` là summary state phục vụ UI; service layer là nơi duy nhất thay state theo state machine, tránh endpoint tự set enum tùy ý.
- `assets.object_key` không được nhận trực tiếp từ client; backend sinh key để tránh path traversal/collision.

## 18. MinIO Object Storage - quyết định V1

> **Bucket** chỉ là “thùng chứa file” của MinIO. V1 dùng đúng **một bucket `ai-video`** để tránh làm hệ thống phức tạp.

![Hình 12 - Asset upload/generation flow với MinIO](assets/14_minio_asset_flow.png)

*Hình 12 - Asset upload/generation flow với MinIO và Local NVMe staging*

### 18.1 Cấu trúc object key

Không cần nhiều bucket. Dùng prefix như thư mục logic:

```text
ai-video/
├── products/{product_id}/...
├── projects/{project_id}/...
├── uploads/{asset_id}/...
├── generated/{video_id}/{scene_id}/{generation_id}/output.mp4
├── thumbnails/{video_id}/{scene_id}/{generation_id}/thumbnail.jpg
├── final/{video_id}/v{version_no}/final.mp4
└── temp/...   # nếu temp thật sự cần đưa lên MinIO; ưu tiên local NVMe
```

Trong DB chỉ lưu `object_key`, ví dụ:

```text
generated/V100/S003/G019/output.mp4
```

Bucket `ai-video` nằm trong application config `MINIO_BUCKET=ai-video`, không cần lặp lại trong từng record.

### 18.2 Upload từ Frontend

Để file lớn không đi xuyên qua process FastAPI:

```text
FE → POST /api/assets/upload-url
BE → tạo asset PENDING_UPLOAD + trả MinIO presigned PUT URL
FE → upload trực tiếp MinIO
FE → POST /api/assets/{asset_id}/complete
BE → HEAD object / validate MIME-size / đọc metadata → asset READY
```

`/complete` phải idempotent: gọi lại không tạo asset mới.

### 18.3 Download/preview

```text
FE → GET /api/assets/{asset_id}/download
BE → authorization → presigned GET URL thời hạn ngắn
FE → preview/download từ MinIO
```

MinIO không public; FE không biết credential, bucket nội bộ hoặc object path nếu không cần.

### 18.4 ComfyUI và MinIO

Không ép custom node ComfyUI đọc S3. Backend/worker materialize input về local NVMe:

```text
MinIO
→ /workspace/generations/{generation_id}/input/
→ ComfyUI/H3
→ /workspace/generations/{generation_id}/output/
→ upload MinIO
→ create/update assets row
→ cleanup workspace
```

Local NVMe = working/cache; MinIO = durable storage.

### 18.5 Lifecycle / Cleanup

| Loại | Policy baseline |
| --- | --- |
| Local NVMe temp/intermediate | Cleanup ngay sau success/failure; periodic cleanup orphan workspace. |
| PENDING_UPLOAD quá hạn | Mark FAILED và dọn object nếu có sau thời gian cấu hình. |
| Failed generated output | Cleanup candidate; không xóa log/DB generation record. |
| Unselected generation output | Giữ theo retention config; không xóa ngay để editor còn compare/history. |
| Selected generation | Giữ ít nhất tới hết lifecycle Video/Project. |
| Final video | Persistent theo business retention. |

Cleanup service phải kiểm tra reference trong DB trước khi xóa object MinIO. Soft-delete metadata trước; physical delete có thể chạy background. Admin chỉ có action “Cleanup Temporary Files”, không expose bulk destructive delete mặc định.

## 19. Redis - quyết định và boundary

**Không dùng Redis trong V1.** Đây là quyết định có chủ đích, không phải thiếu component.

Hiện có ba trách nhiệm đã được giải quyết:

| Nhu cầu | Thành phần V1 |
| --- | --- |
| Durable business/execution state | PostgreSQL |
| AI execution queue | ComfyUI built-in queue |
| FE realtime progress | SSE từ FastAPI dựa trên DB/ComfyUI event |

Thêm Redis ở V1 sẽ tạo thêm state/ops nhưng không giải quyết bottleneck hiện tại của 5 editor + 1 backend + 1 ComfyUI/H3 worker group.

**Chỉ thêm Redis khi xuất hiện một trong các trigger:** nhiều FastAPI instance cần fan-out SSE; nhiều ComfyUI worker/workstation; distributed lock; application-level priority queue; rate-limit shared state; traffic realtime lớn. Khi thêm, Redis có thể làm Pub/Sub/cache/coordination nhưng **PostgreSQL vẫn là source of truth**.

## 20. Trace ID, Correlation ID và Structured Logging

![Hình 13 - Trace/Correlation end-to-end](assets/12_trace_correlation.png)

*Hình 13 - Theo dõi từ HTTP request → generation → ComfyUI → MinIO output*

### 20.1 Các ID phải phân biệt

| ID | Scope | Có durable không? | Mục đích |
| --- | --- | --- | --- |
| `trace_id` / `X-Request-ID` | Một HTTP request | Không phải business identity | Tìm toàn bộ log của request. |
| `video_id` | Business Video | Có | Correlate toàn creative. |
| `scene_id` | Scene | Có | Correlate một scene. |
| `generation_id` | Một lần H3 chạy | **Có, ID chính** | Correlation dài hạn cho async generation. |
| `comfy_prompt_id` | ComfyUI execution | Có trong generation record | Map application ↔ ComfyUI. |
| `asset_id` | File metadata | Có | Map output/input ↔ MinIO object. |
| `final_video_id` | Một lần FFmpeg assembly | Có | Correlation assembly/rebuild. |

Mỗi request middleware:

1. nhận `X-Request-ID` từ upstream nếu hợp lệ hoặc sinh UUID mới;
2. bind `trace_id` vào logging context;
3. trả `X-Request-ID` trong response;
4. error response cũng luôn trả `trace_id`.

### 20.2 Structured log

Đề xuất `structlog` JSON. Log generation nên chứa tối đa các field đang biết:

```json
{
  "level": "info",
  "event": "generation_submitted_to_comfyui",
  "trace_id": "9d5d...",
  "user_id": "U001",
  "project_id": "P001",
  "video_id": "V100",
  "scene_id": "S003",
  "generation_id": "G019",
  "comfy_prompt_id": "CP8879",
  "workflow_code": "H3_I2V_STANDARD",
  "workflow_version": "v1.3"
}
```

Không log password/token/presigned URL. Execution prompt có thể chứa business content; chỉ log ID/hash hoặc truncate theo policy, vì prompt đầy đủ đã có snapshot trong DB.

### 20.3 OpenTelemetry

Code nên “OTel-ready”, nhưng full distributed tracing là **optional** cho V1. Nếu bật sau, HTTP/FastAPI/SQLAlchemy spans có thể instrument; `generation_id` và `comfy_prompt_id` vẫn cần vì ComfyUI không phải business tracing system.

## 21. SSE Realtime Progress

![Hình 14 - SSE status/progress flow](assets/13_sse_progress.png)

*Hình 14 - ComfyUI event/DB state → FastAPI SSE → Frontend*

### 21.1 Vì sao SSE

FE chủ yếu cần one-way update: backend → editor. Không có collaborative editing/chat realtime nên SSE đơn giản hơn WebSocket cho public contract.

Endpoint chính:

```http
GET /api/videos/{video_id}/events
Accept: text/event-stream
```

Optional endpoint scene/generation-level có thể bổ sung, nhưng video-level đủ cho Video Workspace.

### 21.2 Source of truth

SSE **không** là source of truth. PostgreSQL vẫn lưu status/progress. Khi reconnect FE phải REST refetch video/scenes/generations.

V1 đơn giản nhất:

```text
ComfyUI WebSocket/status
→ Comfy adapter cập nhật scene_generations
→ SSE endpoint đọc/diff trạng thái DB mỗi ~1s hoặc nhận in-process signal
→ stream event cho FE
```

Với 5 editor, DB polling nhẹ này hợp lý và tránh Redis/PubSub. Khi scale nhiều API instance mới thay event transport.

### 21.3 Event contract

Ví dụ:

```text
event: generation.progress
data: {"video_id":"V100","scene_id":"S003","generation_id":"G019","status":"RUNNING","stage":"GENERATING","progress":45}
```

Các event baseline:

- `generation.queued`
- `generation.started`
- `generation.progress`
- `generation.completed`
- `generation.failed`
- `generation.cancelled`
- `scene.updated`
- `video.updated`
- `assembly.started`
- `assembly.progress`
- `assembly.completed`
- `assembly.failed`
- heartbeat comment/event 15–30s để giữ connection.

Nếu runtime không có phần trăm đáng tin cậy, `progress=null`; chỉ stream `stage`. **Không fake percentage.**

### 21.4 SSE và authentication

Khuyến nghị V1 dùng authentication cookie `HttpOnly + Secure + SameSite=Lax/Strict` để native EventSource gửi credential cùng origin thuận tiện. Nginx phục vụ FE/API cùng origin. Nếu chọn Bearer token thay cookie thì FE nên dùng fetch-stream abstraction thay native EventSource.

## 22. Backend Stack và Data Access

| Layer | Công nghệ chốt | Ghi chú |
| --- | --- | --- |
| API | FastAPI | Async REST + SSE. |
| Validation/DTO | Pydantic v2 | Request/response/schema Storyboard. |
| ORM | **SQLAlchemy 2.x async** | Tách ORM entity khỏi Pydantic DTO. |
| PostgreSQL driver | **asyncpg** | Async driver. |
| Migration | **Alembic** | Schema versioning; migration trong CI/deploy. |
| HTTP client | httpx.AsyncClient | ComfyUI/optional LLM HTTP. |
| Object Storage | MinIO S3-compatible | Một bucket `ai-video`. |
| S3 client | boto3 hoặc aiobotocore wrapper | Khuyến nghị abstraction `ObjectStorage`. |
| Logs | structlog JSON | Bind trace/correlation context. |
| Realtime | SSE | `text/event-stream`. |
| Video | FFmpeg + ffprobe | Worker/process riêng. |

### 22.1 Layering trong backend

```text
API Router
  ↓ Pydantic DTO
Service / Use Case
  ↓
Repository
  ↓ SQLAlchemy 2.x
PostgreSQL
```

Không để router gọi ORM query lung tung. Business transaction nằm ở service/use-case layer.

### 22.2 Transaction boundary

Một API mutation phải commit business state trước/đúng thời điểm và không giữ DB transaction mở trong khi gọi H3/FFmpeg dài phút.

Ví dụ create generation:

```text
BEGIN
  validate scene/video/input
  create scene_generations(status=CREATED)
  create generation_assets mappings
COMMIT

return generation_id ngay

background dispatcher submit ComfyUI sau
```

Không giữ request HTTP chờ H3 chạy xong.

### 22.3 Async session

Mỗi request/use case dùng `AsyncSession`; repository không tự commit tùy tiện. Service kiểm soát `commit/rollback`. Query list cần eager loading có chủ đích, tránh N+1.

### 22.4 Migration lưu ý FK vòng

Có hai relationship vòng logic:

- `scenes.selected_generation_id` ↔ `scene_generations.scene_id`
- nếu giữ `videos.current_final_video_id` ↔ `final_videos.video_id`

Migration có thể tạo bảng trước rồi `ALTER TABLE ADD CONSTRAINT`, hoặc bỏ cache FK nếu team muốn model đơn giản hơn. Insert flow luôn tạo Scene/Video trước với FK selected/current = NULL, sau khi output tồn tại mới update.

## 23. Authentication và Authorization V1

Role business chỉ có `ADMIN`, `EDITOR`.

### 23.1 Auth đề xuất

- Username/email + password.
- Password hash bằng Argon2id hoặc bcrypt theo library chuẩn.
- JWT access token lưu trong `HttpOnly Secure SameSite` cookie; cùng origin qua Nginx.
- V1 có thể dùng session dài theo business-hours (ví dụ 8h) và bắt login lại; không cần Redis session store.
- `/api/auth/me` trả user/role hiện tại.

Nếu doanh nghiệp đã có SSO/AD thì thay auth adapter sau; domain role vẫn giữ.

### 23.2 Authorization

- EDITOR: CRUD business data trong scope được phép; generate/assemble/download.
- ADMIN: thêm user management + system/storage status.
- Backend enforce quyền; FE hide button chỉ là UX, không phải security.
- Presigned URL chỉ cấp sau authorization.

## 24. API Contract - Backend Implementation Baseline

### 24.1 Convention chung

Base path: `/api`.

Headers:

```http
Content-Type: application/json
X-Request-ID: optional from client/upstream
Idempotency-Key: recommended for create-generation / assemble actions
```

Response luôn có `X-Request-ID`.

Date/time ISO-8601 UTC, DB `TIMESTAMPTZ`.

Pagination baseline:

```json
{
  "items": [],
  "page": 1,
  "page_size": 20,
  "total": 123
}
```

Error schema:

```json
{
  "error": {
    "code": "GENERATION_NOT_FOUND",
    "message": "Generation was not found.",
    "trace_id": "9d5d...",
    "details": {}
  }
}
```

HTTP status theo chuẩn: 400 validation/business rule, 401 unauthenticated, 403 forbidden, 404 not found, 409 state conflict/idempotency conflict, 422 schema validation nếu giữ mặc định FastAPI, 500 unexpected.

### 24.2 Authentication

| Method | Endpoint | Request/Response chính |
| --- | --- | --- |
| POST | `/api/auth/login` | `{username_or_email,password}` → user + set auth cookie. |
| POST | `/api/auth/logout` | Clear cookie. |
| GET | `/api/auth/me` | Current user summary. |

### 24.3 Projects

| Method | Endpoint | Ý nghĩa |
| --- | --- | --- |
| GET | `/api/projects` | Search/filter/page project. |
| POST | `/api/projects` | Create project. |
| GET | `/api/projects/{project_id}` | Detail. |
| PATCH | `/api/projects/{project_id}` | Update name/description. |
| POST | `/api/projects/{project_id}/archive` | Set ARCHIVED. |
| GET | `/api/projects/{project_id}/videos` | List videos của project. |
| GET | `/api/projects/{project_id}/assets` | Project-scoped assets. |

### 24.4 Products

| Method | Endpoint | Ý nghĩa |
| --- | --- | --- |
| GET | `/api/products` | Search/filter/page master product. |
| POST | `/api/products` | Create product. |
| GET | `/api/products/{product_id}` | Detail. |
| PATCH | `/api/products/{product_id}` | Update. |
| POST | `/api/products/{product_id}/archive` | Archive. |
| GET | `/api/products/{product_id}/assets` | Product assets. |
| GET | `/api/products/{product_id}/videos` | Cross-project videos dùng product. |

### 24.5 Assets / MinIO

**Request upload URL:**

```http
POST /api/assets/upload-url
```

```json
{
  "scope": "PRODUCT",
  "product_id": "...",
  "asset_type": "PRODUCT_IMAGE",
  "file_name": "front.png",
  "mime_type": "image/png",
  "size_bytes": 1827364
}
```

Response:

```json
{
  "asset_id": "...",
  "upload_url": "<presigned-put-url>",
  "expires_in_sec": 900
}
```

Complete:

```http
POST /api/assets/{asset_id}/complete
```

Backend HEAD object, validate size/type, extract metadata, mark asset ready.

Download/preview:

```http
GET /api/assets/{asset_id}/download
```

Response contains short-lived presigned GET URL.

### 24.6 Create Video

```http
POST /api/videos
```

Quick Clip example:

```json
{
  "project_id": "P001",
  "product_id": "PR001",
  "title": "XYZ Perfume Hero",
  "video_type": "QUICK_CLIP",
  "master_prompt": "Luxury cinematic product reveal",
  "requested_duration_sec": 10,
  "aspect_ratio": "9:16",
  "quality_profile": "STANDARD"
}
```

Backend tạo `videos` + đúng một `scenes` record cho Quick Clip và trả cả `video_id`, `scene_id`.

Long Video create chỉ tạo `videos`; storyboard endpoint tạo scenes sau.

### 24.7 Storyboard

```http
POST /api/videos/{video_id}/storyboard
```

Precondition: `video_type=LONG_VIDEO`, state phù hợp. Backend set `PLANNING`, chạy Hybrid Planner, validate tổng duration/scene max, transaction replace/create scene set, sau đó `STORYBOARD_READY`.

### 24.8 Scenes

| Method | Endpoint | Ý nghĩa |
| --- | --- | --- |
| GET | `/api/videos/{video_id}/scenes` | Ordered scene list + selected generation summary. |
| GET | `/api/scenes/{scene_id}` | Detail. |
| PATCH | `/api/scenes/{scene_id}` | Update title/prompt/duration/continuity/creative controls. |
| POST | `/api/videos/{video_id}/scenes/reorder` | Body ordered list scene IDs; validate ownership/duplicates. |
| POST | `/api/scenes/{scene_id}/disable` | Exclude khỏi final nhưng giữ history. |
| POST | `/api/scenes/{scene_id}/enable` | Enable lại. |

Khi scene selection/order/prompt ảnh hưởng final đã READY, video chuyển `DIRTY`/user-facing `Needs Rebuild`.

### 24.9 Generation

Create original/regenerate:

```http
POST /api/scenes/{scene_id}/generations
```

```json
{
  "operation": "ORIGINAL",
  "reference_asset_ids": ["A001"],
  "first_frame_asset_id": "A001",
  "last_frame_asset_id": null
}
```

Variation:

```http
POST /api/scenes/{scene_id}/variations
```

Backend common behavior:

1. authorization + scene/video state validation;
2. Prompt Engine build `execution_prompt`;
3. Workflow Router chọn approved workflow;
4. insert `scene_generations(status=CREATED)` + snapshot + input mappings;
5. commit;
6. trả `202 Accepted` + `generation_id` ngay;
7. dispatcher submit ComfyUI async.

Example response:

```json
{
  "generation_id": "G019",
  "scene_id": "S003",
  "status": "CREATED"
}
```

History/select/cancel:

| Method | Endpoint | Ý nghĩa |
| --- | --- | --- |
| GET | `/api/scenes/{scene_id}/generations` | List generations newest-first. |
| GET | `/api/generations/{generation_id}` | Detail/status/error/progress. |
| POST | `/api/scenes/{scene_id}/select-generation` | `{generation_id}`; only COMPLETED output. |
| POST | `/api/generations/{generation_id}/cancel` | Cancel queued/running best-effort. |

### 24.10 Generate All Long Video

Đề xuất endpoint explicit:

```http
POST /api/videos/{video_id}/generate-all
```

Backend tạo generation `CREATED` cho các enabled scenes chưa có selected output (hoặc theo request filter). Không submit tất cả ngay; dispatcher fairness xử lý dần.

### 24.11 Assembly / Final Versions

```http
POST /api/videos/{video_id}/assemble
```

```json
{
  "transition": "CROSSFADE",
  "audio_mode": "MUTE_SCENE_AUDIO",
  "background_audio_asset_id": "A500"
}
```

Backend validate mọi enabled scene có selected COMPLETED generation, tạo `final_videos(status=QUEUED, version_no=max+1)`, commit và trả `202`.

Assembler worker chọn row QUEUED, materialize selected scene assets từ MinIO, FFmpeg normalize/assemble/ffprobe, upload final MinIO, create asset, update `final_videos=READY`, video `READY`. Nếu scene thay đổi sau đó video `DIRTY`; rebuild tạo version mới, không overwrite.

GET:

```http
GET /api/videos/{video_id}/final-versions
```

### 24.12 Realtime SSE

```http
GET /api/videos/{video_id}/events
```

Content-Type `text/event-stream`; auth cookie required. SSE trả events ở mục 21. Client reconnect phải refetch REST state.

### 24.13 Dashboard/Admin

| Method | Endpoint |
| --- | --- |
| GET | `/api/dashboard/summary` |
| GET | `/api/system/status` |
| GET/POST/PATCH | `/api/admin/users...` |
| GET | `/api/admin/system/status` |
| GET | `/api/admin/storage/summary` |
| POST | `/api/admin/storage/cleanup` |

## 25. Backend Orchestration Flow chi tiết

### 25.1 Quick Clip

```text
POST /videos
→ create Video + one Scene
→ POST /scenes/{id}/generations
→ create Generation(CREATED) + prompt snapshot + asset mappings
→ dispatcher
→ materialize MinIO inputs to NVMe
→ inject approved ComfyUI workflow
→ POST /prompt
→ save comfy_prompt_id, status QUEUED
→ Comfy event listener: RUNNING/progress
→ H3 output local
→ upload MinIO
→ create output Asset
→ generation COMPLETED
→ scene READY + selected generation (có thể auto-select first success)
→ video READY
→ SSE updates FE
```

### 25.2 Long Video

```text
POST /videos (LONG_VIDEO)
→ POST /videos/{id}/storyboard
→ Hybrid Planner → validate → create Scenes
→ editor edits/reorders
→ POST /videos/{id}/generate-all
→ create N Generations(CREATED)
→ dispatcher fairness → ComfyUI one by one / limited pending
→ each completion updates scene
→ all enabled scenes have selected output → SCENES_READY
→ editor variation/regenerate/select
→ POST /videos/{id}/assemble
→ final_videos QUEUED
→ FFmpeg worker
→ MinIO final asset
→ Final V1 READY
→ later scene change → Video DIRTY
→ rebuild → Final V2
```

## 26. Dispatcher và ComfyUI Adapter - không Redis

### 26.1 Dispatcher algorithm baseline

Configuration:

```text
COMFY_MAX_PENDING_PROMPTS=2   # tune by benchmark
DISPATCH_INTERVAL_MS=500-1500
```

Pseudo flow:

```text
if ComfyUI healthy and current pending < limit:
    find CREATED generations ordered by created_at
    prefer video_id != last_dispatched_video if possible
    lock candidate
    prepare local inputs
    submit workflow to ComfyUI
    save comfy_prompt_id + queued_at + status=QUEUED
```

Nếu submit fail transient: tăng `retry_count`, để CREATED/FAILED theo retry policy. Không tạo generation row mới khi auto retry technical execution; user-triggered Regenerate/Variation mới tạo generation mới.

### 26.2 ComfyUI Adapter interface

Backend không gọi ComfyUI trực tiếp từ nhiều module. Dùng abstraction:

```text
ComfyUIAdapter
- health()
- queue_status()
- submit(workflow, inputs) -> comfy_prompt_id
- get_status(comfy_prompt_id)
- get_history(comfy_prompt_id)
- cancel(comfy_prompt_id)
- stream_events()/listen_events()
- collect_output(comfy_prompt_id)
```

Adapter normalize ComfyUI-specific event thành domain status/stage. Business service không phụ thuộc node IDs.

### 26.3 Workflow injection

Workflow Registry chỉ giữ approved JSON/version. Adapter/Workflow service map symbolic input slots (`PROMPT`, `FIRST_FRAME`, `LAST_FRAME`, `DURATION`, `SEED`...) sang node IDs/config trong workflow version đó. Mapping nằm cùng workflow config và phải version cùng JSON; không hardcode node number rải khắp codebase.

## 27. FFmpeg Assembly Worker

Không cần Celery. Một worker process/container polling `final_videos(status=QUEUED)` là đủ.

Dùng `SELECT ... FOR UPDATE SKIP LOCKED` nếu có khả năng >1 worker process, dù V1 chỉ chạy 1.

Processing:

1. claim final row → ASSEMBLING;
2. query enabled scenes theo `scene_order`;
3. resolve selected generation → output asset;
4. download MinIO → local assembly workspace;
5. ffprobe validate input;
6. normalize resolution/fps/codec/audio;
7. CUT/CROSSFADE;
8. apply audio mode/background audio;
9. encode MP4 H.264/AAC;
10. ffprobe final validation;
11. upload `final/{video_id}/v{n}/final.mp4`;
12. create asset + update final row READY;
13. cleanup local workspace;
14. on error update FAILED + error, do not delete previous final versions.

## 28. Frontend screens và API mapping cho Backend Agent

Frontend đã chốt **10 route-level screens**. Backend agent dùng bảng này để đảm bảo không thiếu contract.

![Hình 15 - FE screen → Backend API groups](assets/15_fe_backend_contract.png)

*Hình 15 - Mỗi màn FE gọi domain API; backend không tổ chức source code theo screen*

| Screen | Route | Backend API chính |
| --- | --- | --- |
| Login | `/login` | `/auth/login`, `/auth/logout`, `/auth/me` |
| Dashboard | `/dashboard` | `/dashboard/summary`, `/videos`, `/system/status` |
| Projects | `/projects` | `/projects` list/create/update/archive |
| Project Detail | `/projects/:projectId` | project detail, project videos/assets, asset presigned upload |
| Products | `/products` | product list/create/update/archive |
| Product Detail | `/products/:productId` | product detail/assets/videos |
| Videos | `/videos` | video list/filter |
| Create Video | `/videos/new` | create video, asset upload, storyboard hoặc create generation |
| Video Workspace | `/videos/:videoId` | video/scenes/generations/select/cancel/reorder/assemble/finals/SSE |
| Admin | `/admin` | users/system/storage |

**Không có page riêng V1** cho Asset Library, Queue, Workflow Registry, Storyboard, Generations hay Final Video; các phần này nằm trong màn cha. Backend vẫn có domain modules tương ứng.

### 28.1 FE data contract nguyên tắc

- ID dùng UUID; FE không dùng name/title làm identifier.
- Backend trả user-friendly summary object cho nested relation; không buộc FE N+1 API call.
- Backend enum có mapping UI; ví dụ `DIRTY` → “Needs Rebuild”.
- Mutation trả resource/state mới hoặc ID đủ để TanStack Query update/refetch.
- Upload/download luôn qua presigned URL contract.
- Progress realtime qua SSE; REST refetch khi reconnect.

## 29. Backend Source Structure đề xuất

```text
ai-video-studio/
├── apps/
│   └── api/
│       └── app/
│           ├── main.py
│           ├── core/
│           │   ├── config.py
│           │   ├── security.py
│           │   ├── logging.py
│           │   ├── tracing.py
│           │   └── exceptions.py
│           ├── db/
│           │   ├── base.py
│           │   ├── session.py
│           │   └── models/
│           ├── schemas/              # Pydantic v2 DTOs
│           ├── api/
│           │   ├── auth.py
│           │   ├── projects.py
│           │   ├── products.py
│           │   ├── assets.py
│           │   ├── videos.py
│           │   ├── scenes.py
│           │   ├── generations.py
│           │   ├── admin.py
│           │   └── events.py
│           ├── repositories/
│           ├── services/
│           │   ├── project_service.py
│           │   ├── product_service.py
│           │   ├── asset_service.py
│           │   ├── video_service.py
│           │   ├── generation_service.py
│           │   ├── storyboard_service.py
│           │   ├── prompt_engine.py
│           │   ├── workflow_router.py
│           │   ├── comfy_adapter.py
│           │   └── event_service.py
│           └── workers/
│               ├── dispatcher.py
│               ├── comfy_listener.py
│               └── reconciliation.py
├── workers/
│   └── assembler/
├── workflows/
│   └── h3/
├── migrations/                       # Alembic
├── tests/
│   ├── unit/
│   ├── integration/
│   └── contract/
└── infra/
    ├── docker-compose.yml
    ├── nginx/
    └── monitoring/
```

Nguyên tắc: API router mỏng; business state transition ở service; DB access ở repository; ComfyUI/MinIO/LLM là adapter/interface có thể mock trong test.

## 30. Configuration / Environment baseline

Không hardcode credential/path/workflow node IDs trong source.

```text
APP_ENV=production
APP_BASE_URL=https://ai-video.internal
DATABASE_URL=postgresql+asyncpg://...

MINIO_ENDPOINT=http://minio:9000
MINIO_ACCESS_KEY=...
MINIO_SECRET_KEY=...
MINIO_BUCKET=ai-video
MINIO_SECURE=false   # true nếu TLS trực tiếp tới MinIO

COMFYUI_BASE_URL=http://comfyui:8188
COMFY_MAX_PENDING_PROMPTS=2
DISPATCH_INTERVAL_MS=1000

WORKSPACE_ROOT=/workspace
MODEL_ROOT=/models

JWT_SECRET=...
JWT_EXPIRE_MINUTES=480

SSE_POLL_INTERVAL_MS=1000
SSE_HEARTBEAT_SEC=20

LOG_FORMAT=json
```

Secret production dùng Docker secret/env management phù hợp on-prem; không commit `.env` thật vào Git.

## 31. Deployment on-premise

![Hình 16 - Docker Compose deployment V1](assets/10_deployment.png)

*Hình 16 - Một workstation; Docker Compose; MinIO một bucket; không Redis/Kubernetes*

Services baseline:

- `nginx`
- `frontend`
- `backend` - FastAPI, **1 instance/process baseline** cho SSE/in-process simplicity
- `postgres`
- `minio`
- `comfyui` + H3
- `assembler`
- optional `prometheus`, `grafana`, `dcgm-exporter`

Volumes:

- PostgreSQL data volume;
- MinIO data volume;
- `/opt/models` model checkpoint;
- `/workspace` local NVMe working/temp;
- `/workflows` approved ComfyUI JSON/config.

Backup:

- PostgreSQL scheduled backup;
- MinIO bucket backup/snapshot theo policy;
- workflow/config/version backup;
- model checkpoint có thể re-download nếu version/source pin rõ.

## 32. Monitoring và Observability

| Metric/alert | Mục đích |
| --- | --- |
| GPU utilization / VRAM / temperature | Bottleneck/OOM/thermal. |
| ComfyUI queue depth / wait time | User waiting. |
| Generation P50/P95 | Performance trend. |
| Generation failure/OOM rate | Runtime stability. |
| Dispatcher pending CREATED count | Application backlog trước ComfyUI. |
| SSE active connections/reconnect rate | Realtime health. |
| MinIO used/free storage | Capacity. |
| Local NVMe free/temp usage | Tránh generation fail vì temp full. |
| FFmpeg assembly failures | Final output reliability. |
| API latency/error rate | Application health. |

Tracing/logging: `trace_id`, `video_id`, `scene_id`, `generation_id`, `comfy_prompt_id`, `final_video_id` khi có. Metrics qua Prometheus/Grafana; GPU qua DCGM Exporter. Loki/OpenTelemetry optional.

## 33. GPU/runtime và performance

Phần uncertainty kỹ thuật lớn nhất vẫn là H3 runtime trên 4×48GB. 192GB VRAM vật lý không phải một GPU 192GB; phải PoC model fit/multi-GPU/offload/precision và benchmark thật.

### 33.1 Baseline trước optimization

```text
Known-good H3/ComfyUI workflow
→ pin driver/PyTorch/CUDA/workflow/model versions
→ warm-up
→ benchmark
→ identify bottleneck
→ tune
→ benchmark again
→ Creative regression eval
→ freeze approved configuration
```

### 33.2 Benchmark matrix tối thiểu

| Mode | Duration | Ratio | Đo |
| --- | --- | --- | --- |
| T2V | 5/10/15s | 9:16; 16:9 khi cần | P50/P95, VRAM/RAM, OOM, success, quality. |
| I2V | 5/10/15s | 9:16 | Tương tự. |
| First+Last | 5/10/15s | 9:16 | Tương tự. |
| Ref2VA | 5/10/15s | 9:16 | PoC riêng. |
| Long Video | 30/60s E2E | 9:16 | Queue wait, total turnaround, scene success, assembly. |

Optimization phải tối ưu **quality × latency × stability**, không chỉ seconds/job.

## 34. Evaluation / Quality Plan

Ba lớp evaluation:

| Nhóm | Tiêu chí |
| --- | --- |
| Creative | Product fidelity, prompt adherence, visual quality, motion, scene consistency, commercial usability. |
| Runtime | Latency, VRAM/RAM, OOM, failure rate, throughput, thermal. |
| System | Queue/fairness, cancel, retry, restart recovery, SSE reconnect, MinIO integrity, assembly, authorization. |

Creative Evaluation Set: khoảng 20–50 scenario sản phẩm/cảnh thật. Mỗi workflow/model/runtime/prompt strategy thay đổi đáng kể phải regression so với baseline.

## 35. Non-functional Requirements

| Nhóm | Baseline V1 |
| --- | --- |
| Users | ~5 internal editors; 1 backend instance baseline. |
| AI concurrency | Không cam kết 5 H3 generation đồng thời; ComfyUI queue. |
| API | Async request không chờ generation/assembly dài; trả 202 + durable ID. |
| Realtime | SSE; reconnect + REST resync; heartbeat. |
| Security | HTTPS, secure cookie, hashed password, ADMIN/EDITOR authorization. |
| Data locality | PostgreSQL + MinIO on-premise. |
| Recovery | Backend/ComfyUI restart không mất business history; reconciliation. |
| Traceability | Request → generation → comfy prompt → asset/final truy vết được. |
| Upload | MIME/extension/size/resolution/duration validation; presigned URL. |
| Storage | MinIO + local NVMe capacity alert/cleanup. |
| Maintainability | Alembic migration; workflow/model config pin/version. |
| Browser contract | FE target Chrome/Edge current; API not browser-specific. |

## 36. Acceptance Criteria / UAT baseline

| Area | Acceptance baseline |
| --- | --- |
| Auth | Login/logout/me; EDITOR bị chặn admin API. |
| Project/Product | CRUD/archive; Product reuse được qua nhiều project. |
| MinIO Asset | Presigned upload/complete/download; invalid upload reject; metadata đúng. |
| Quick Clip | Create Video + 1 Scene + async Generation; output MinIO; SSE status đúng. |
| Long Video | Hybrid Planner tạo scene hợp lệ; edit/reorder; Generate All không dump vô hạn vào ComfyUI. |
| Variation | Mỗi variation/regenerate tạo generation mới; không overwrite output cũ. |
| Select | Chỉ COMPLETED generation có output mới select được. |
| Versioning | Scene/order/selection change sau READY → DIRTY; rebuild sinh V2 giữ V1. |
| Queue | No lost/duplicate generation; `generation_id ↔ comfy_prompt_id` mapping đúng. |
| Retry/Recovery | transient retry max policy; startup reconciliation. |
| Cancel | queued cancel; running best-effort; DB state nhất quán. |
| SSE | Progress không reload; disconnect/reconnect không mất source of truth. |
| Trace | Error response/log có trace_id; từ generation ID tìm được Comfy prompt/output. |
| Assembly | Final decode được, order đúng, validation pass. |
| Storage | Cleanup không xóa asset còn được reference. |
| Evaluation | Creative/runtime/system test và business sign-off. |

## 37. Backend Test Strategy

Backend coding agent phải có ít nhất:

**Unit tests**
- Workflow Router rules;
- Prompt Composer/guard;
- Storyboard validator duration/scene count;
- state transition guards;
- asset object-key builder;
- permission checks.

**Integration tests (PostgreSQL/MinIO mocked or test containers)**
- create Project/Product/Video;
- upload-url → complete lifecycle;
- create generation transaction;
- select generation;
- video DIRTY behavior;
- final version increment;
- retry/reconciliation.

**Contract tests**
- API response/error schemas;
- enum/user-friendly mapping fields;
- SSE event shape.

**Adapter tests**
- fake ComfyUI server: submit/status/error/cancel;
- fake MinIO/S3;
- FFmpeg fixture clip assembly.

Không cần chạy H3 thật trong CI thông thường; H3 end-to-end nằm ở staging/PoC benchmark suite.

## 38. Backend Implementation Order cho Coding Agent

Khuyến nghị code theo thứ tự để tránh build AI integration trước khi domain/state ổn:

1. Project skeleton + config/logging/trace/error middleware.
2. PostgreSQL async + SQLAlchemy models + Alembic initial migration.
3. Auth + ADMIN/EDITOR authorization.
4. Project/Product CRUD.
5. MinIO abstraction + presigned upload/complete/download + Assets.
6. Video/Scene CRUD + state machines.
7. Prompt Engine + Workflow Registry/Router (mock Comfy adapter trước).
8. Generation creation + dispatcher + ComfyUI Adapter + reconciliation.
9. SSE status/progress.
10. Storyboard Planner hybrid + validator.
11. Scene variation/select/reorder/disable + DIRTY behavior.
12. FFmpeg assembler + final versioning.
13. Dashboard/Admin system/storage APIs.
14. Metrics/evaluation hooks + integration/UAT tests.
15. H3 real workflow PoC/tuning và freeze approved config.

Mỗi bước phải giữ migrations/test pass; không sửa DB thủ công ngoài Alembic.

## 39. Rủi ro / điểm cần PoC hoặc business confirmation

| Rủi ro/điểm mở | Xử lý |
| --- | --- |
| H3 4×48GB fit/performance | PoC trước SLA. |
| Ref2VA | Giữ advanced; feature flag nếu PoC chưa đạt. |
| Long-video product/style consistency | Shared context/reference + creative eval. |
| Exact H3 progress % | Chỉ expose nếu runtime thật sự cung cấp đáng tin cậy; nếu không stage-only. |
| Running cancel | Best-effort theo ComfyUI/runtime. |
| Một video nhiều Product | V1 1 product/video; future `video_products`. |
| Audio continuity | V1 simple audio; không hứa full soundtrack continuity. |
| Logo/CTA overlay | Backlog trừ khi khách xác nhận must-have. |
| Redis | Không V1; thêm khi scale trigger xuất hiện. |

## 40. Backlog sau V1

- Ad Template Management / template scenes.
- CSV/Excel batch generation.
- Multi-product video.
- Brand Kit nâng cao, logo/CTA overlay.
- TTS/voice-over/audio management nâng cao.
- Google Drive/DAM connector.
- Full-video creative variants A/B/C.
- Multiple FastAPI/ComfyUI workers + Redis Pub/Sub/application queue nếu scale yêu cầu.
- Approval/review workflow.
- Advanced analytics.

## 41. Kết luận kiến trúc

```text
Frontend          = Next.js UI; REST + SSE
FastAPI           = business/API/orchestration
SQLAlchemy/asyncpg= PostgreSQL async data access
PostgreSQL        = durable source of truth
MinIO             = durable binary storage, one bucket ai-video
Local NVMe        = working/temp staging
Storyboard Planner= rules + optional local LLM
Prompt Engine     = deterministic prompt + optional enhancer
Workflow Router   = choose approved H3 workflow
ComfyUI           = AI workflow execution + built-in queue
MiniMax H3        = generate scene video/audio
FFmpeg            = deterministic final assembly
Trace IDs         = end-to-end troubleshooting
Evaluation        = prove quality/runtime/system acceptance
Redis             = NOT required in V1
```

Thiết kế V1 cố tình giữ boundary đơn giản: **không duplicate queue, không generic jobs table, không Redis khi chưa cần, không timeline editor, không template/batch trong core**. `scene_generations` và `final_videos` là durable async records; ComfyUI queue chỉ chịu trách nhiệm execution. Nhờ vậy backend agent có thể code theo domain rõ ràng và sau này thay model/workflow/storage transport mà không phải viết lại Product/Project/Video/Scene core.

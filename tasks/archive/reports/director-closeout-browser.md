# Director closeout: independent frontend review and browser evidence

**Verdict: FRONTEND_BROWSER_FAIL (pre-fix).** One confirmed P2 remains in the video list. This is a frontend sidecar verdict, not a backend/reliability/Director-runtime certification.

## Concrete blocker for implementation

**P2 — Video action group overflows with long, valid business names.**

Source: `frontend/app/videos/page.tsx:165` (action flex group), `:144` (metadata sibling), and `:169` / `:175` (Storyboard and Assembly links). The metadata sibling has `min-w-0` but no flex allocation; the actions have neither wrapping nor protected label widths. With a 230-character unbroken fixture title and long project/product names, metadata consumes the row while action labels shrink into vertical text. Assembly extends beyond the viewport. This is page-level overflow, not an intentional local scroll region.

| Viewport | document.scrollWidth | document.clientWidth | Result |
|---|---:|---:|---|
| 1440×900 | 1467 | 1440 | FAIL |
| 1280×800 | 1317 | 1280 | FAIL |
| 1024×768 | 1066 | 1024 | FAIL |
| 768×900 | 825 | 768 | FAIL |
| 480×900 | 480 | 480 | PASS |

At 768px, Storyboard ends at 774.70px and Assembly ends at 825.45px. At 1440px Assembly ends at 1467.19px. Reproduced again in fresh isolated contexts. Give metadata remaining flexible width; wrap/stack actions at insufficient usable width and prevent individual action labels from shrinking. Rebuild the production frontend and repeat the browser matrix after the implementation fix. The video-list file itself is not part of the tracked dirty diff, but this current-worktree defect violates spec sections 17/43.

## Isolation and scope

- Repository: `D:/project/ai-video-studio`, branch `codex/production-safety-fixes`, HEAD `01b740d`; shared dirty worktree reviewed without source or Git mutations.
- Skills used: browser-testing-with-devtools (profile isolation/runtime evidence) and code-review-and-quality (independent logic review). Authorized Playwright fallback because DevTools/CUA was unavailable.
- Executed installed `playwright-core` by absolute path from `frontend/.tmp/director-browser/node_modules`, headless Edge at the explicitly provided executable path. Fresh temporary launch profile and context per run, no personal profile, no cookies/storage-state read or export. Closed browsers after each run. Non-loopback requests blocked.
- App `http://127.0.0.1:18030`, isolated API `http://127.0.0.1:18080`. Logged in through normal UI labels using only supplied fixture credentials.
- Only fixture SQLite business data changed: long project/product/video names and three UI-only asset rows (READY image, VALIDATING video, FAILED audio). Database: `workspace/browser-fixture-4da934d604eb414eb7ca08b9a6fe84f0/browser.sqlite`. The fake asset rows have no stored objects; no original application DB was accessed or changed.
- Owned outputs are exactly `.artifacts/final-ui-media-closeout/director-closeout-browser.cjs`, `.json`, `.log`, `.png`, and `.md`.

## Actual browser matrix

97 geometry measurements, including supplemental reproductions. Tested all required viewport dimensions: 1440×900, 1280×800, 1024×768, 768×900, 480×900.

At each viewport: dashboard, project list, product list, populated project reference-assets tab, upload, video list, video detail, assembly, final versions, admin system/storage/users/workflows views. Also inspected project/product detail pages. `/assets` itself returns 404: no standalone source route exists. The functional assets library is the project reference-assets tab and product detail library; the populated project assets table rendered three rows at every required width.

All tested pages except `/videos` satisfied document.scrollWidth <= document.clientWidth. The sidebar measured 248px at 1440/1280 and 64px at 1024/768/480, positioned at x=0, with viewport-height rail. Main-column min-width was 0px and topbar content stayed within its box. Actual vertical scrolling on the long assembly page preserved a 900px left rail; y=-0.109375px at the document bottom is subpixel rounding. A strict zero-y assertion was corrected to a <=1px tolerance and rerun; the original assertion and raw measurement remain in JSON assertionCorrections.

Upload layout/field measurements:

| Viewport | Upload panels | Scope/select fields |
|---|---|---|
| 1440 | 562px + 562px | 254px + 254px |
| 1280 | 482px + 482px | 448px (stacked) |
| 1024 | 912px single panel | 429px + 429px; collapsed empty grid track |
| 768 | 656px single panel | 301px + 301px |
| 480 | 384px single panel | 350px (stacked) |

This follows usable panel width rather than viewport width. Long selected filenames wrapped; recent asset filenames truncated within their rows; status/download controls stayed contained. Native scope/role selects did not create page overflow.

Admin tabs at 480px owned local horizontal scrolling (384px client / 672px scroll width). Workflow table owned a focusable local region (350px client / 560px scroll width); keyboard scrolling changed its scrollLeft while document width stayed within 480px. Project populated asset tables likewise stayed locally contained.

Project-create dialogs were tested at all five viewports for viewport fit, forward/reverse Tab containment, Escape close, and focus restoration to their trigger. Product-create, scene-edit and generation-editor dialogs were measured at 480px and fit the viewport.

## Runtime behavior and independent logic review

**Actual fixture API:** normal UI authentication and page data loads; disabled/unqualified generation workflows remain unavailable. No real GPU job was launched.

**Explicitly stubbed behavior tests:**

- Upload-policy 503 rejects picker/drop selection, clears selected file, disables upload, shows configuration error and retry, and sends zero upload-intent requests. The native picker remains enabled, but cannot retain an eligible file; this is permitted by spec section 20. It is not a native-input-disabled claim.
- Retry with a 100-byte / image/png policy rejects 101 bytes and accepts 100 bytes. No fake 500MiB active fallback is used on failure. The normal fixture server legitimately returns 500MiB on success.
- Picker and synthetic browser drag/drop reject identical invalid-MIME and oversize files with identical messages. Multi-file drop is rejected. Drag feedback and valid selection work; keyboard Enter emits the real browser filechooser event.
- Local stub storage plus upload/complete/GET API responses verify HTTP202 VALIDATING does not show success, busy file/scope controls are disabled, FAILED exposes validation retry, and retry reaches success only after READY. Two completion requests and two status GETs observed per test sequence. This is UI behavior validation, not MinIO/worker execution.
- Logout 503 preserves the authenticated page, displays role=alert outside the 64px rail, fits at 480px even with a long unbroken error, and dismisses with keyboard Enter.

Source reviewed independently for upload policy and shared validator, asynchronous READY/FAILED transitions, retry AbortController and timers, bounded polling/unmount cleanup, idempotency identity, exact face-detector contract, generation qualification and prompt acceptance/history reuse, aggregate audio capability scope, authentication/logout failure, assembly freshness and error flows. No additional confirmed P1/P2 logic defect identified in these reviewed paths.

Evidence locations: `frontend/app/assets/upload/page.tsx:53` (policy guard), `:158` (shared selection validator), `:182` (submit validation), `:105` / `:111` (validation/retry); `frontend/lib/api/assets.ts:51` (server policy validation); `frontend/lib/api/types.ts:305` (exact detector literal); `frontend/lib/generation/capabilities.ts:57` (canonical detector default); `frontend/components/generation/generation-editor.tsx:60` / `:172` (settingsCap and scope-correct audio options); `frontend/components/studio-shell.tsx:180` (logout alert outside rail).

## Tests and console

- Targeted frontend tests: **73 passed, 0 failed** (responsive, upload, detector semantic TypeScript compilation, generation capabilities/editor/history, assembly/final errors).
- Entire existing frontend test suite: **158 passed, 0 failed**. Command: `node --test frontend/tests/*.test.cjs`. Full output appended to owned `.log`.
- Sidecar build/typecheck: NOT_RUN; used parent-provided production frontend. Source-only responsive tests passing does not override browser failures.
- Console recorded across three fresh contexts: twelve resource-error entries, corresponding to pre-login `/auth/me` 401, standalone `/assets` 404, injected policy 503, and injected logout 503. No page exceptions or React warnings observed. Raw console/network entries are in JSON; not represented as a clean zero-error console.
- ComfyUI/GPU generation, actual MinIO transfer/download, production application DB, five-editor UAT and backend reliability certification: NOT_RUN by this sidecar.

## Artifacts

- `.cjs`: runnable isolated browser harness. Normal run executes matrix and supplements; `--supplemental` retains prior matrix evidence and adds behavior/library checks.
- `.json`: raw geometry/checks, console/network entries, assertion correction, independent finding, and test counts.
- `.log`: browser measurements and frontend test output.
- `.png`: contact sheet, upload above and video-list reproduction below; 1440px capture on left, 480px capture on right, native captures combined without rescaling. Viewed and verified visually.

**Request changes:** resolve the confirmed video-list P2 and rerun against a rebuilt production frontend before claiming section 43 completion. No implementation edits made by this sidecar.

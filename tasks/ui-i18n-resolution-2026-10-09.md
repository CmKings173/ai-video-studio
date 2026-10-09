# UI language and generation settings followup

User scope: remove visible SSE connectivity and On-Premise Production copy; make ordinary UI consistently Vietnamese with an English switch; fix stretched label/select spacing; make AI clip resolution and qualified workflow selection understandable and discoverable.

Baseline: codex/production-safety-fixes, HEAD 01b740d8be4d58d3bd15bc46dd9842afb48af8bb. Preserve preexisting dirty work. No commit/push/deploy/database changes.

Implementation ownership:
- Main: locale persistence, document language, shell controls, shared fields/components, validation/error helpers, integration tests/browser verification/report.
- Dalton: dashboard/projects/products/admin/upload/login bilingual copy.
- Russell: video list/create/workspace/assembly/final versions bilingual copy and hide SSE text without removing live events.
- Hubble: generation components bilingual copy, discoverable clip dimensions and compatible workflow selection, resolver tests.

Rulings: VI default and EN alternate; persist only locale preference. UI translation must not rewrite user content, prompts, API enums/IDs or technical diagnostics. Workflow selection belongs to per-scene generation, not initial video metadata creation. Use only qualified capabilities; preserve input hashes/prompt acceptance/revision gates. Final MP4 dimensions remain separate from AI clip dimensions. Shared fields use content-start/self-start to prevent adjacent help copy stretching rows. Existing local workspace is retained for continuity; no new checkout or git mutation.

Progress: initial locale store/unit test and shell selector implemented; shared control alignment fixed. Sidebar hover/focus tooltips, search icon insets, VI/EN copy, removal of visible SSE/On-Premise text, and the generation/assembly resolution guidance are implemented. The create-video form no longer asks for an optional product; project is auto-selected when there is one active project and remains selectable when several exist. Per-scene generation has a direct qualified pixel-canvas choice, separate quality profile, automatic workflow default, and optional compatible workflow selection; custom dimensions are checked against qualified canvases before preview. Final MP4 resolution remains in Assembly. New-video actions stack at full width on narrow screens.

Prior verification before the last two small edits passed typecheck, lint, production build, UI-copy audit, focused workflow tests, and browser matrix at 360/480/768/1024/1280/1440 px. Full suite was 514/519: four stale assertions expected separate project and product retry controls on the create page, which now has one project query; one slow-client deadline test failed under parallel load. The retry regression now asserts project-only retry and absence of the product field/query; the mobile footer was made full-width and stacked. Post-edit verification is pending because command and browser runners repeatedly fail during environment setup (`helper_unknown_error: setup refresh had errors`) before launching; no results from those attempts are counted. Preserve this distinction when reporting.

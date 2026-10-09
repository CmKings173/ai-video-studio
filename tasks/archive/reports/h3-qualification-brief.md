# H3 technical verification sidecar

Read this first. Research current primary sources and probe the configured target runtime while Phase 0 fixes run. Do not implement app behavior or modify backend/frontend/infra/plan/ledger. Do not spawn agents.

Deliver `tasks/archive/reports/h3-qualification-report.md` with dated primary URLs and exact facts, runtime reachability, executable template evidence, and practical integration recommendations.

Questions to resolve:
- Official MiniMax H3 local release modes, duration/FPS/canvas constraints, reference counts/durations and audio-only restriction.
- Current official ComfyUI/native MiniMax H3 templates and /object_info node interfaces. Are the repository's custom ResolutionSelector and one-image graph authoritative/current? Identify exact native templates and model/custom-node dependencies; never invent node IDs.
- Turbo/Lightning availability per family and required artifact versions. H3-Regenerate-2K local/offline availability versus an external API.
- GPU/runtime availability on this machine and configured ComfyUI endpoint. Read only required COMFYUI_BASE_URL from environment/config without printing the entire .env or secrets; report reachability and inventory only. No GPU job, model download, software install or arbitrary remote command without controller coordination.
- Full Ref2VA graph/API availability: provide authoritative template files under tasks/h3-reference-artifacts only if small and useful. No fabricated executed PASS.

Use web primary sources (MiniMax official repository, ComfyUI docs/source/templates, vLLM official source) and source-linked evidence. Browser apps or third-party blog instructions are not authority. Pin observed repository commit/hash when practical. If endpoint unavailable, record NOT_RUN and exact commands/resources needed later.

Global constraints: current feature workspace; no commit/push/reset/clean; preserve all user changes; only write report and research artifacts under tasks.

# ADR 012: MiniMax H3 Director as the only H3 execution engine

Date: 2026-10-06\
Status: Accepted for implementation design; production enablement requires runtime qualification.

## Context

ai-video-studio already owns product/business state, assets, generation history, worker durability, MinIO, PostgreSQL, SSE and final delivery. Existing H3 integration has qualified graph/slot contracts but supports a narrower legacy generation-mode surface.

The inspected reference provider is `AIMixer/ComfyUI_MiniMaxH3_Director` pinned at commit `a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb`, Apache-2.0. Its source implements H3 task planning/execution for t2v/i2v/fl2v/r2v/v2v/rv2v/mixed, ordered reference groups, timeline/source-video modes, Motion Context, Refine, FaceRefine and segment-aware export.

## Decision

Use AIMixer MiniMax H3 Director as the only H3 execution provider inside ComfyUI. ai-video-studio remains the management/orchestration source of truth.

The integration uses a typed immutable `DirectorExecutionSpec` and a provider adapter/workflow builder. Product/business intent stays separate from Director task keys. Installed runtime capabilities are checked with source pin + `/object_info` + release qualification.

ai-video-studio retains ownership of:

- authentication and authorization;
- project/product/video/scene business state;
- asset lifecycle and MinIO;
- PostgreSQL generation history;
- immutable semantic snapshots and parent relationships;
- worker leases, retries and reconciliation;
- SSE/status;
- output probing and verification;
- final FFmpeg delivery/assembly.

Director owns its H3-specific planning/conditioning, Motion Context continuity, Refine, FaceRefine and provider export behavior.

## Consequences

Positive:

- source-supported H3 modes and reference handling can be integrated without reproducing provider algorithms;
- runtime qualification stays attached to a concrete provider release;
- existing production-hardening seams remain useful;
- frontend can become capability-driven instead of assuming one static workflow.

Costs:

- native Motion Context requires an aggregate durable run spanning ordered scene members;
- output collection must handle multiple/list/segment artifacts;
- prompt transformation requires accepted-prompt versus effective-provider metadata;
- model/Director/Comfy/dependency compatibility becomes part of release identity;
- source/UI maxima cannot be exposed as production guarantees without runtime evidence.

## Compatibility

Persisted legacy generation modes remain readable. `i2v_last` and `i2v_first_last` map to `fl2v` only during new planning. `v2v` and `rv2v` are additive modes. Historic snapshots/registry records are not rewritten.

## Qualification rule

No mode/resolution/reference/enhancement combination becomes production-runnable solely because it exists in source. It must have executed evidence for the exact release identity.

Claims for 1080p/2K/4K, VRAM, latency and throughput remain unqualified until measured. The inspected audio source also requires the architecture to distinguish internal/model audio rate from mux/export audio metadata.

## Rejected design directions

- hard-code example master-spec node IDs as the production workflow contract;
- replace the existing asset/job/database/delivery platform with Director-owned state;
- simulate native Motion Context with unrelated independent scene prompts;
- interpret 8192/16 MP source UI bounds as a guaranteed production envelope.

## Related docs

- `docs/architecture/h3-director-source-analysis.md`
- `docs/architecture/h3-director-target-architecture.md`
- `docs/architecture/h3-director-execution-contract.md`
- `docs/architecture/h3-director-capability-map.md`
- `docs/architecture/h3-director-gap-analysis.md`
- `docs/architecture/h3-director-migration-plan.md`
- `docs/architecture/h3-director-runtime-qualification.md`

## 2026-10-08 retirement decision

Director is the sole active H3 family. Legacy registry identities are historical records
and cannot authorize a new generation, retry, resume or derivative. An unavailable or
unqualified Director makes generation unavailable; no fallback provider is permitted.
The source pin remains unchanged. See the retirement section in the target architecture
for the dry-run management command and historical preservation policy.

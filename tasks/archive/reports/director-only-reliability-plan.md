# Director-only reliability closeout plan

Spec: user attachment `2d4fa56a-8e54-4b6f-934f-ee0a7801a762/Pasted text.txt`.
Task: director_only_reliability_closeout_v1. Started 2026-10-08.

1. Verify dirty workspace identity; audit original application DB and legacy history.
2. Stream Comfy outputs to bounded temporary paths with cancellation/limit checks.
3. Share canonical output persistence between compatibility bytes and production files; rewire dispatcher, Director dispatcher, assembler without weakening claims, immutability or validation.
4. Retire legacy H3 selection, registration and graphs after reference/history audit; preserve historical identities and all referenced Director graphs. Keep qualification fail closed.
5. Fix upload layout/policy, exact detector type and collapsed logout alert; add behavior/contract regressions.
6. Run fresh targeted/full backend, isolated PostgreSQL, frontend and runtime validations; browser geometry if usable, otherwise NOT_RUN. Synthetic media smoke without GPU.
7. Independently review all changes; fix confirmed P1/P2, rerun and write tasks/archive/reports/director-only-reliability-closeout.md with evidence and truthful verdict.

Constraints: no commit/push/reset/stash/clean/checkout; preserve existing dirty/untracked files; no queue redesign; no repin/qualification fabrication; original DB backup before schema/history mutation.

Interfaces: Comfy produces path/size/checksum; persistence computes actual file digest and uses existing immutable storage API; workers own staging lifetimes; retirement owns services/registry, parent owns collection/persistence workers. Frontend has a disjoint write scope.

Safety evidence: connector workspace_info/git_status attempted first, both returned Unknown tool. Direct local Git verified branch codex/production-safety-fixes and full HEAD 01b740d8be4d58d3bd15bc46dd9842afb48af8bb. Working tree is source of truth.

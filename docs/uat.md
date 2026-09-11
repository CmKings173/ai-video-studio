# UAT and release gate

This checklist is evidence-driven. `NOT_RUN` means the target ComfyUI/GPU,
PostgreSQL and MinIO environment was not available during implementation.

| ID | Acceptance check | Expected evidence | Status |
| --- | --- | --- | --- |
| UAT-01 | editor login, CSRF and logout revocation | HTTP transcript with no secret values | NOT_RUN |
| UAT-02 | create project/video, edit with stale `If-Match` | 412 structured error | NOT_RUN |
| UAT-03 | upload, interrupted completion, reconciliation | asset reaches READY and checksum matches | NOT_RUN |
| UAT-04 | H3 t2v/i2v/i2v-first-last/r2v execution | `docs/h3-poc/results.json` with executed PASS and model/custom-node hashes | NOT_RUN |
| UAT-05 | cancel during queue/running and worker restart | terminal state, no duplicate attempt | NOT_RUN |
| UAT-06 | storyboard replace before/after generation history | conflict/history preserved | NOT_RUN |
| UAT-07 | immutable assembly after scene edits | final manifest hash unchanged | NOT_RUN |
| UAT-08 | backup and isolated restore | measured RPO/RTO and checksum report | NOT_RUN |
| UAT-09 | five concurrent editors and two workers | no duplicate active assembly, no deadlock | NOT_RUN |
| UAT-10 | benchmark matrix | `docs/h3-poc/benchmark.json`, p50/p95 duration and peak VRAM | NOT_RUN |

The automated suite covers deterministic domain, contract and race behavior but
does not replace the external ComfyUI/GPU execution gate or the restore drill.

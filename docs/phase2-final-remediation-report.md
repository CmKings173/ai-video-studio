# Phase 2 authorization and consistency closeout

Date: 2026-10-12
Workspace: `ai-video-studio`
Branch: `codex/production-safety-fixes`
HEAD: `50071aeb6c2ae9b76d8ac0e1e61904ffc94a04b9`

## A. Repository state

The workspace was already dirty when this closeout resumed. Existing Phase 0.5,
Phase 1, and Phase 2 changes were preserved. This closeout also hardened
idempotent generation replay against producer-schema confusion, mismatched
GenerationAsset links, cross-scope references, and input Assets masquerading as
generated video outputs. HEAD did not move; no commit, push, reset, checkout,
or application-database operation was run.

Alembic has one current head: `c8f3b5d02e74`. The working tree remains dirty.

## B. Root causes and corrections

### P1-01 - object IDs were not consistently scoped to the resource owner

Several direct and indirect API/service paths authenticated the actor but
resolved resources by identifier without enforcing the owning Editor at the
database boundary. The shared `ownership.py` helpers now apply owner predicates
to list/search/count and direct resource queries; child resources inherit
authorization from their Video/Project graph. Cross-user references, output
downloads, SSE subscriptions, generation operations, assembly, and idempotency
replay are rejected before side effects. Admin remains able to act on behalf of
an Editor while ownership follows the target Video/Project, not the Admin actor.

### P2-01 — stale dependency values after lock

SQLAlchemy may return an already-loaded ORM object for a later `SELECT FOR
UPDATE`; the lock alone does not refresh its attributes. The shared
`video_dependency_guard.py` now validates persisted scalar projections during
preflight and re-locks/refreshes Project, Product, Brand, and Video state at the
authoritative write boundary. It suppresses autoflush, validates both Product
Brand and effective Brand ownership/state, and preserves pending edits through
`lock_revisioned_row`.

Review also reproduced the same identity-map class for Assembly Audio: a
READY Audio changed to DELETED after preflight remained READY in the identity
map and assembly creation proceeded. The locked Audio query and locked output
Asset query now use `populate_existing=True`. A deterministic integration test
failed before that change (`DID NOT RAISE AppError`) and passed after it.

### P2-02 — Storyboard omitted parent-resource consistency

Video authorization alone did not prove that its Project/Product/Brand graph
was still owned by the Video business owner or active. Preview and publish now
validate the shared dependency graph; publish does so before idempotency
handling. Scene writes now lock Video, validate/refresh its dependency graph,
then lock or mutate Scene. Archived parents return their existing 409 contract
before Scene/revision changes.

An additional adversarial review found that Admin visibility allowed a foreign
reference Asset to be stored in another owner's Scene. The shared reference
Asset guard now requires stable Asset owner and every non-null Project/Product
scope to match the target Video. It is used for Storyboard, Scene mutation, and
selected-generation input validation. Foreign scope returns 404 before
idempotency or writes; same-owner references remain usable.

### P2-04 - idempotency replay trusted ambiguous cached references

Replay previously recursively interpreted arbitrary persisted JSON as resource
references and did not prove that each cached input/output reference matched
the canonical database relationship. The replay validator now follows only
producer-defined paths, rejects malformed known schemas, validates input
GenerationAsset roles and Project/Product scope, checks parent-generation
ownership and lineage, and confirms output links against canonical output
pointers or Director artifact links. Output pointers must reference a
`GENERATED_VIDEO` Asset of kind `VIDEO`. Response DTOs are rebuilt from
authorized database rows rather than trusting cached response data.

Real HTTP regressions first reproduced false 409 replays for valid request
context, then exposed same-owner role/link and scope-confusion cases that were
incorrectly accepted. Those cases now fail closed without minting output URLs
or changing generation state.

### P2-03 — Admin actor was confused with Video owner

For new Videos, `created_by` is the Project/business owner. `created_actor_id`
records the initiating Admin separately. Generated/final Asset ownership comes
from the consistent Video/Project owner and records `VIDEO_PARENT`; the worker
actor remains audit attribution. Nullable migrations preserve unknown
historical attribution and do not bulk-transfer owners.

## C. Independent review

- **Faraday (E), read-only red-team:** found the archived-parent Scene-write
  gap and Admin foreign-reference Asset gap. Both were reproduced and fixed.
  Its final source review confirmed the lock/owner checks and no remaining
  relevant P1/P2 in those paths. The subagent did not execute tests.
- **Mendel (F), read-only integration review:** confirmed the migration chain
  and lock design. It identified a conditional legacy-output replay issue
  described below and the existing stateless-JWT role-revocation behavior. The
  subagent did not execute tests.
- **Bernoulli, final read-only review:** confirmed the reference-Asset,
  Scene-lock, Assembly refresh, and Admin-on-behalf changes. It agreed that
  PostgreSQL verification and production inventory of retryable legacy outputs
  are still required before rollout.
- **Hypatia, independent read-only replay review:** found that an input
  reference Asset could be presented as a canonical generated-output pointer.
  The case was reproduced at the API and fixed by validating output Asset role
  and kind. Its final review found no further P1/P2 in the reviewed replay
  paths.
- The lead ran the tests and checks listed below after integrating the fixes.

## D. Final ownership model

- Project, Brand, and Product `created_by` values define their business owner.
- Video `created_by` is the business owner and must match its Project,
  referenced Product, and effective/explicit Brand.
- Video `created_actor_id` preserves who initiated delegated creation.
- Child generation/assembly `created_by` remains actor/audit attribution;
  authorization inherits from the Video.
- Output Assets use `resource_owner_id = Video.created_by` and
  `ownership_provenance = VIDEO_PARENT`, with exact parent and Project checks.
- Legacy ownership/provenance is not inferred or bulk-backfilled. Inconsistent
  historical Video graphs fail closed.

## E. Lock order

```text
Video creation: preflight -> dependency graph guard -> Project -> Product ->
                sorted Brands -> create Video/Scene in one transaction

Existing Video write: authorize -> Video -> dependency fence and refreshed
                      Project/Product/sorted Brands -> child Scene/Generation
```

The dependency fence is non-blocking when a writer already holds Video, so an
existing Video writer does not wait for the graph guard while holding Video.
Slow planner/network work runs after preview validation and transaction release.
PostgreSQL concurrency was not run in this resumed environment because
`POSTGRES_TEST_DATABASE_URL` is unset. SQLite results below are not treated as
a substitute for PostgreSQL lock verification.

## F. Side-effect boundaries

Regression tests assert that rejected parent/Asset requests do not call the
planner, persist idempotency claims, insert/delete Scenes, change revisions,
enqueue jobs, or mint presigned URLs. The worker owner-mismatch tests assert
failure before Asset claim and object-store PUT. The legacy replay test verifies
that old null-provenance Asset metadata and bytes remain unchanged and no PUT is
attempted.

## G. Regression matrix T01–T25

| ID | Result | Evidence / limitation |
|---|---|---|
| T01 | SQLite PASS; PostgreSQL NOT_RUN | Preloaded archive refresh and lock/concurrency regressions; PostgreSQL URL unset. |
| T02 | SQLite PASS; PostgreSQL NOT_RUN | Brand archive and authoritative effective-Brand tests; PostgreSQL URL unset. |
| T03 | SQLite PASS; PostgreSQL NOT_RUN | Project archive and parent mutation tests; PostgreSQL URL unset. |
| T04 | SQLite PASS; PostgreSQL NOT_RUN | Reassignment and dependency-creation race tests; PostgreSQL URL unset. |
| T05 | PASS | Valid Editor dependency graph creation remains covered. |
| T06 | PASS | Editor A foreign Project rejected with 404/no rows. |
| T07 | PASS | Admin creates for B; Video owner and actor are persisted separately. |
| T08 | PASS | Admin B Project + A Product rejected before insert. |
| T09 | PASS | Admin B Project + A Brand rejected before insert. |
| T10 | PASS | Inconsistent parent preview rejected before planner invocation. |
| T11 | PASS | Inconsistent parent publish rejected before claims/Scene/revision changes. |
| T12 | PASS | Foreign Product graph rejected for preview and publish. |
| T13 | PASS | Foreign/effective Brand graph rejected for preview and publish. |
| T14 | PASS | All six reference fields reject foreign Assets for Editor and Admin; same-owner Admin Storyboard reference passes. |
| T15 | PASS | Replay rechecks parent graph and does not mutate existing claim/state. |
| T16 | PASS | Valid Storyboard replay preserves rows and revisions. |
| T17 | PASS | Admin-generated output is persisted for B; B reads/downloads/selects it; A is denied. |
| T18 | PASS | B assembles and downloads final output; parent ownership is verified. |
| T19 | PASS | Changing/disabling initiating Admin does not remove B's output access. |
| T20 | PASS | Foreign output read/download returns 404 before presigning. |
| T21 | PASS | Historical inconsistent Video graph fails closed and is not rewritten. |
| T22 | NOT_RUN | Disposable PostgreSQL Product mutation vs Video creation race; no test database URL. |
| T23 | NOT_RUN | Disposable PostgreSQL Brand mutation vs Video creation race; no test database URL. |
| T24 | NOT_RUN | Disposable PostgreSQL Project archive/mutation vs Video creation race; no test database URL. |
| T25 | PASS | Backend and frontend regression suites; see Gate results. |

## H. Verification gates

| Gate | Command/result | Status |
|---|---|---|
| Python syntax | In-memory `compile()` of 251 Python files | PASS |
| Collection | `.venv/Scripts/python.exe -B -m pytest --collect-only -q -p no:cacheprovider` | PASS, 2,013 collected, no collection failures |
| Focused replay regressions | Replay authorization/output suites and batch completion | PASS, 83 + 7 passed |
| Backend full suite | `.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider --tb=short` | PASS, 1,951 passed, 62 skipped, 0 failed, 443.16s |
| PostgreSQL suite | `tests/postgres` | NOT_RUN; PostgreSQL-dependent cases skipped because `POSTGRES_TEST_DATABASE_URL` is unset |
| PostgreSQL migration tests | Workflow execution-scope and workflow-version migration integration tests | NOT_RUN; requires a disposable PostgreSQL URL |
| Ruff | `ruff check --output-format concise apps workers tests migrations` | PASS, all checks passed |
| Alembic chain | `python -m alembic heads` | PASS, one head `c8f3b5d02e74` |
| Migration upgrade and constraints | Disposable PostgreSQL database | NOT_RUN; no disposable PostgreSQL URL was available; application DB untouched |
| PostgreSQL concurrency | `POSTGRES_TEST_DATABASE_URL` lane | NOT_RUN; 62 PostgreSQL-specific/migration cases skipped |
| Frontend tests | `npm test` | PASS, 529 passed, 0 failed |
| Frontend typecheck | `npm run typecheck` | PASS |
| Frontend lint | `npm run lint` | PASS |
| Frontend build | `npm run build` | PASS |
| Compose | `docker compose -f infra/compose.yaml config -q` with process-local validation-only placeholders | PASS, exit 0; Docker printed a local config ACL warning |
| JWT isolation | Backend Compose contract tests and source check | PASS in backend suite; signing secret is API-only |
| `uv lock --check` | `uv lock --check` | BLOCKED; installed `uv.exe` returned Windows Access Denied, and `uv` is not installed in the project venv |
| Diff integrity | `git diff --check` | PASS; only Git LF/CRLF warnings |
| Credential-prefix scan | `rg -l -i 'sk[-]or[-]v1[-]|apikey_[a-z0-9]{12,}' --glob '!phase2-final-remediation-report.md' backend frontend infra docs .env.example` | PASS; no files matched |

## I. Remaining findings and blockers

1. **Rollout prerequisite - production legacy-output inventory.** A worker retry that
   encounters an existing pre-migration deterministic output with null
   provenance raises `IMMUTABLE_OUTPUT_CONFLICT`, preserves the old Asset and
   bytes, and does not resume that job in place. The dispatcher now classifies
   this terminal case as `IMMUTABLE_OUTPUT_CONFLICT` rather than a generic input
   error. Before deployment, an operator must run the read-only pending-output
   inventory in `phase2-video-owner-inventory.sql` against the intended
   production database and drain or explicitly plan recovery for any listed
   jobs. This workspace had no safe production-database access, so that
   inventory was not run. No ownership backfill is authorized by this change.
2. **Known Phase 1 behavior, unchanged by scope:** a copied stateless Admin
   JWT retains its signed role until expiry after role change/disable. The
   authentication guide documents the 30-day default. Phase 2 explicitly
   prohibits changing JWT architecture; immediate role revocation remains a
   separate security decision.
3. **Worker-path coverage boundary:** the delegated integration journey uses
   the real database-backed `Dispatcher.claim`, `save_output_file`, and
   `_finish`, plus the real `Assembler` persistence path and deterministic
   storage. It does not run the ComfyUI `Dispatcher.process/_collect` loop;
   output ownership persistence is exercised, but the complete external-job
   collection wrapper is not. The batch replay retry fixture separately
   simulates a valid post-collection final manifest/link to isolate replay
   reauthorization; it does not call `_collect_director` itself.

Independent review and the new role/link/scope regressions found no confirmed
P1/P2 code defect in the reviewed Phase 2 paths. The current SQLite backend,
frontend, lint, and available static checks passed. PostgreSQL concurrency and
migration checks, the dependency-lock check, and the production legacy-output
inventory remain unverified; no production database was read or changed here.

## J. Verdict

`PHASE_2_BLOCKED - VERIFICATION_AND_ROLLOUT_PREREQUISITES`

Authorization, output ownership, replay consistency, Compose JWT isolation,
frontend, backend, lint, and Alembic-head gates passed. PostgreSQL migration and
concurrency verification were not run, the dependency-lock check was blocked by
Windows executable access, and the read-only production legacy-output inventory
remains a rollout prerequisite. No application database was accessed, no bulk
owner backfill is proposed, and no production deployment is authorized.

## K. Phase 3 integrated status

The Phase 3 static builder, routing, qualification, and frontend regression
tests are included in the successful backend/frontend suites above. The root
`execution_spec_hash` is now required to match the hash bound into runtime
evidence; regressions cover a different valid hash plus missing and malformed
root pins. An independent reviewer found no issue in this binding.

No ComfyUI/GPU execution evidence was collected in this environment. H3
workflows therefore remain unqualified/source-template-only; static checks do
not authorize runtime enablement.

`PHASE_3_STATIC_PASS_RUNTIME_NOT_RUN`

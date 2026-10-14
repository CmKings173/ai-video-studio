# Authorization matrix

## Ownership policy

`created_by` records the **actor** that created or started a row. It is audit
history, not a universal ownership field. The **resource owner** is the Editor
whose Project, Product, or Video owns the business resource. ADMIN may act on
another Editor's resources while the output remains owned by that Editor.

Project, Brand, Product, and Video use their existing `created_by` as root
ownership. Scene, SceneGeneration, GenerationAttempt, DirectorRun,
DirectorRunMember, FinalVideo, and assembly records inherit authorization from
their Video. Their own `created_by` remains the initiating actor.

Scoped Assets use `resource_owner_id` and immutable `ownership_provenance`:

- `OWNER`: an Editor uploaded to their own Project/Product.
- `ADMIN_ASSIGNMENT`: an Admin uploaded to a Project/Product and assigned its
  owner. Later role changes or disabling the actor do not remove that grant.
- `VIDEO_PARENT`: a worker output whose owner is the Project/Video parent.

Generated and final output access also requires a valid Generation or
FinalVideo parent, matching Video and Asset Project IDs, and a Project owner
that matches the Video owner. A matching `project_id` alone is insufficient.
Cross-parent or mismatched-owner rows fail closed for Editors. Direct Editor
uploads with inconsistent creator and parent ownership are not inherited by
either Editor. Scene generation references are also bound to their Video:
their stable Asset owner and every non-null Project/Product scope must match the
Video's business owner and exact associated Project/Product. ADMIN visibility
does not bypass this association check.

The provenance columns are nullable only to retain historical rows without
inventing ownership. Existing rows are not bulk-backfilled. Editors may still
access an older Project/Product Asset when its `created_by` is the parent
owner; historical Admin-created or worker output rows without verifiable
provenance fail closed for Editors and need separate, evidence-based handling.
ADMIN retains cross-owner access under the existing policy, subject to output
parent consistency.

### Historical Asset inventory and safe backfill

The ownership migration does not change `created_by` or infer a new owner for
existing rows. Until an explicit, reviewed backfill is approved, legacy scoped
assets are visible to an Editor only when their creator is also the verified
Project/Product owner. A historical upload or output created by an Admin can
therefore remain Admin-only; it returns 404 to Editors and does not issue a
presigned URL. New uploads and outputs use the stable ownership fields above.

Workers also reject an existing deterministic output row whose owner or
provenance differs from the new immutable output identity. A retry that reaches
a pre-migration output with null provenance therefore fails closed and leaves
that Asset unchanged. Before enabling workers after this migration, inspect
retryable Generation/FinalVideo rows that reference null-provenance outputs.
Complete them before migration where safe, or preserve them for an individually
reviewed recovery through a new generation/assembly. Do not rewrite the old
Asset's creator or infer its owner during restart.

Before any legacy backfill, run a read-only inventory over Assets with null
provenance and join their direct Project/Product scope plus every output edge
(`SceneGeneration.output_asset_id`, `GenerationAsset` roles beginning
`DIRECTOR_`, and `FinalVideo.output_asset_id`) through Video to Project. Report
candidate counts and Asset IDs by provenance path. A row is eligible only when
all verified parent paths resolve to exactly one existing resource owner and
one consistent Project, its stored scope agrees with those parents, and no
conflicting or orphaned parent edge exists. Ambiguous, inconsistent, orphaned,
or unsupported rows remain unchanged and Editor-inaccessible pending manual
review.

If backfill is later approved, first snapshot the database and candidate ID to
owner mapping, review it with the operator, and validate it in staging. Apply a
bounded transactional update to `resource_owner_id` and
`ownership_provenance` only; never rewrite `created_by`. Verify updated counts,
foreign keys, unique owner mappings, parent consistency, and Editor/Admin
download behavior before committing the transaction. This release intentionally
performs no historical backfill.

## Endpoint matrix

### Video business owner and creation actor

New Videos inherit `created_by` from their Project, including when an Admin
creates on behalf of an Editor. `created_actor_id` records the authenticated
creator separately and grants no access. Historical actor values remain null;
this release does not infer or backfill them. Generation, DirectorRun and
FinalVideo `created_by` continue to record the initiating actor.

Project, Video, referenced Product, the Product's Brand and any explicit Video
Brand must share a business owner. Existing same-owner explicit Brand overrides
remain supported. New creation preserves the Product/Brand pairing contract.
SQL ownership scopes hide inconsistent historical Video graphs and their
children from Editors and Admins, including list counts, SSE and output access.
Active operations additionally reject archived dependencies with 409. Foreign or
inconsistent owners return 404 without external calls or idempotency claims.

Creation locks the graph guard, then Project, Product and both resolved Brands
in sorted ID order. Existing Video writes lock Video, Project, Product and
both resolved Brands in sorted ID order before
children, without waiting on the graph guard after holding Video. Locked rows
are refreshed using the revision-preserving helper; unvalidated edits are never
implicitly flushed. Storyboard preview captures a validated read snapshot and
releases its transaction before invoking the planner.

Both the Product's actual Brand and an explicit same-owner override must be
active for an active operation. An override cannot conceal an archived parent.

Historical inconsistent Videos require a separate read-only inventory: compare
Video `created_by` with Project `created_by`, optional Product `created_by`, the
Product's Brand owner and explicit Brand owner. Report missing parents and
disagreements by Video ID, including linked Generation/DirectorRun/FinalVideo
and output Asset IDs. Do not repair them by bulk rewriting owners, actors or
immutable output provenance. Any repair needs an individually verified owner,
parent graph and audit record, followed by a separately approved migration.

| Capability | EDITOR | ADMIN | Required protection |
| --- | --- | --- | --- |
| Read owned resources and events | yes | yes | JWT access cookie; SQL ownership scope |
| Create/edit owned projects, brands, products, videos and scenes | yes | yes | JWT + CSRF + owner check + `If-Match` where mutable |
| Upload/complete/delete unreferenced scoped assets | own scope | cross-owner | Parent ownership checked before presign or mutation; asset provenance retained |
| Create/cancel generations and assemblies | own Video | cross-owner | JWT + CSRF + parent ownership; idempotency on creates |
| Download ready assets/final versions | own scope | cross-owner | Reauthorize parent and asset before presigning |
| Read dashboard/system status | own data | cross-owner | SQL-level owner filters |
| Manage users and roles | no | yes | JWT + CSRF; last-admin serialization |
| Manage workflow registry and enablement | no | yes | JWT + CSRF; H3 validation |
| Cleanup/reconcile storage | no | yes | JWT + CSRF; dry-run cleanup default |
| Scrape Prometheus metrics | no | no | separate `Authorization: Bearer METRICS_TOKEN` |

## Idempotency

An idempotency record is not authorization evidence. Generation replay checks
that the cached Generation still exists, belongs to the requested Scene and
Video, and remains in the caller's security scope before returning its DTO.
Generate All replay checks every cached Generation ID and every referenced
DirectorRun/execution group against the requested Video. Missing cached rows
return an unavailable/conflict response; foreign or mismatched parents return
404. Rejected replay creates no Generation/DirectorRun and enqueues no work.

Reference parsing is restricted to producer-defined locations: input
`assets[].id` in the snapshot, `generation_intent`, and `director_execution`;
root `lineage.parent_generation_id`; `prompt_provenance.inherited_from` in
those three containers; and output `artifacts[].asset_id`. Product/Brand
context, freshness dependencies, workflow profiles and media metadata remain
opaque business JSON even when their keys resemble resource references.
Inputs must have canonical input associations; outputs must have the canonical
output pointer or a Director output association. Cached output and immutable
identities cannot substitute other resources. Responses use database DTOs.
Storyboard replay likewise validates canonical Video/Scene identities and
Scene references, and rebuilds its response from database state.

The configured `studio_session` HttpOnly cookie carries an HS256 JWT with a
30-day default expiration. The CSRF value is signed into that JWT and compared
in constant time for cookie-authenticated writes. The API does not read or
write the `sessions` table during authentication. Logout clears the browser
cookie but does not revoke a copied JWT; password changes, role changes, and
account disable also do not revoke an already-issued JWT before expiry. See
[`authentication.md`](authentication.md) for configuration and cutover details.

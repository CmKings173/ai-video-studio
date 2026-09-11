# Authorization matrix

All authenticated editors operate in the shared on-prem workspace. Project,
brand, product, video, scene, asset, generation and final-version rows are not
owner-isolated between editors; the `created_by` field is audit provenance, not
an access boundary.

| Capability | EDITOR | ADMIN | Required protection |
| --- | --- | --- | --- |
| Read workspace resources and events | yes | yes | session cookie |
| Create/edit projects, brands, products, videos and scenes | yes | yes | session + CSRF + `If-Match` where mutable |
| Upload/complete/delete unreferenced assets | yes | yes | session + CSRF; reference check |
| Create/cancel generations and assemblies | yes | yes | session + CSRF + idempotency on creates |
| Download ready assets/final versions | yes | yes | session cookie |
| Read dashboard/system status | yes | yes | session cookie |
| Manage users and roles | no | yes | session + CSRF; last-admin serialization |
| Manage workflow registry and enablement | no | yes | session + CSRF; H3 validation |
| Cleanup/reconcile storage | no | yes | session + CSRF; dry-run cleanup default |
| Scrape Prometheus metrics | no | no | separate `Authorization: Bearer METRICS_TOKEN` |

Sessions are opaque, hashed at rest, revocable on logout/password or account
disable, and guarded by a same-site CSRF token for cookie-authenticated writes.

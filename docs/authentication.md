# Access-token authentication with immediate account revocation

The API authenticates protected requests with an HS256 JWT in the configured
HttpOnly cookie (`AUTH_COOKIE_NAME`, default `studio_session`). Tokens carry
the user ID, role, account authentication version, issuer, audience, issue time, expiration time, and a random
CSRF value. The default lifetime is 2,592,000 seconds (30 days); deployments
may configure a shorter lifetime, but not a longer one.

Generate `JWT_SECRET` once with a cryptographic random generator:
`python -c "import secrets; print(secrets.token_urlsafe(48))"`. Store the value
unchanged across restarts and share it among API replicas only. Do not use the
placeholder in `.env.example`. `JWT_ISSUER` and `JWT_AUDIENCE` must also be
consistent across API replicas. Production API startup fails if the signing
secret is missing or invalid. Rotating the secret invalidates every issued JWT.

JWTs are not stored in PostgreSQL or browser storage. The CSRF value is returned
by login and stored by the frontend separately; each mutation sends it in
`X-CSRF-Token`, and the API verifies it against the signed claim in the JWT.
The JWT itself remains inaccessible to frontend JavaScript.

Each protected request reads the current account role, active status and
authentication version from the database. Password, role and activation changes
increment the version, immediately rejecting previous tokens. SSE checks the
account before opening and on every snapshot poll, terminating revoked streams.
The migration protects raw SQL account updates as well as ORM mutations.

Logout clears the browser cookie and local authentication state and still
requires the signed CSRF value, including for a revoked token. Logout alone does
not revoke a copied token. Existing tokens without the authentication-version
claim require a fresh login after deployment of migration `e5a7c9b1d3f6`.

The `sessions` table and its migration history remain in place for rollback,
but the API authentication, logout, and SSE paths do not access that table. A
separate cleanup phase can remove the table after the JWT rollout is accepted.

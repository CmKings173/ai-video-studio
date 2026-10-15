# ASUS GX10 ARM64 — isolated UAT deployment

This recipe prepares Studio alongside the existing working ComfyUI/GB10 installation.
It does not install CUDA, replace PyTorch, download weights, enable workflows, or
prove GPU compatibility. Use only a separately approved UAT deployment window.
No container, migration, or GX10 operation was performed while preparing this source.

## Inputs and isolation

- Linux `aarch64`, Docker Engine and Compose **2.24.4 or later** (`!override` is required).
- Reviewed source revision and image inventory. `GX10_SOURCE_TAG` identifies that exact
  source; record the full Git SHA and any uncommitted diff separately.
- Existing ComfyUI root/Python environment and known-good rollback backup.
- Dedicated UAT credentials and storage; never reuse live `studio` data volumes.
- Two operator-managed HTTPS virtual hosts: app and S3. Example `.test` hostnames
  are placeholders and must resolve on the user's browser computer.

From the repository root, copy `infra/gx10.env.example` outside Git to a private
file, replace **every** `replace-with-*` and `.example.test` value, and `chmod 600`
that file. Generate independent random secrets, including two different proxy
credentials. Use a URL-safe PostgreSQL password (or percent-encode its URL component).
Never put the env file in an image/build context or commit it.

The project name is `ai-video-studio-gx10-uat`; named volumes inherit that prefix.
Do not pass a production `-p`, `COMPOSE_PROJECT_NAME`, external volume override,
or another Compose file. API, frontend, and PostgreSQL publish no ports.
Ingress is loopback `18080`; S3 is `19000`; console is `19001`. These defaults
avoid the usual Studio/ComfyUI ports; the operator must still check port availability.

## Network paths

| Caller | Destination |
| --- | --- |
| HTTPS app reverse proxy | `127.0.0.1:18080` on GX10 |
| Ingress → frontend → API | private Compose DNS, `frontend:3000` / `api:8000` |
| API/workers → storage | `http://minio:9000` |
| User browser → storage | `MINIO_PUBLIC_ENDPOINT`, e.g. `https://gx10-uat-storage.example.test` |
| API/workers → existing host ComfyUI | `http://host.docker.internal:8188` via `host-gateway` |

The S3 reverse proxy must preserve Host, URI, query parameters and request body;
do not prepend `/minio` or rewrite signatures. Configure upload limits/timeouts
there independently of API limits. Do not publish the MinIO console to the Internet.
Use the app's exact HTTPS origin in `ALLOWED_ORIGINS`; verify CORS and a real
browser upload/download during UAT. `localhost` on a remote browser means that
browser computer, not GX10. Container DNS `minio` is not browser DNS.

Host ComfyUI bound only to `127.0.0.1` cannot be reached from a bridge container.
An operator must expose it to the Docker bridge on a firewall-restricted interface,
or provide an explicitly reachable existing ComfyUI service URL. Never use host
networking as a shortcut or expose the unauthenticated ComfyUI API publicly.
Keep the working ComfyUI container/environment and its model mounts intact.

## Offline configuration check

The following command does not start containers or contact a database:

```sh
docker compose --env-file /secure/gx10-uat.env \
  -f infra/compose.yaml -f infra/compose.gx10.yaml config --quiet
```

Inspect the resolved model locally (it contains secrets; do not share it). Confirm
project/volumes, `linux/arm64`, only the three loopback ports, correct HTTPS URLs,
and `JWT_SECRET` only on API. Production startup rejects missing/weak auth secrets.
Do not interpret successful Compose interpolation of example secrets as readiness.

## Build and application startup — operator steps, not executed in this task

After separate approval, on GX10 build backend, frontend, and MinIO from the reviewed
revision using the same two Compose files and private env file. The backend consumes
`uv.lock`; ARM64 wheels/source builds still require an actual native build gate.
The MinIO image builds upstream security release `RELEASE.2025-10-15T17-29-55Z`
from commit `9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a`, checks the source archive
SHA256, uses `go.mod`/`go.sum`, and includes curl for `/minio/health/live`.
Record built image IDs/digests and base image digests for repeat deployments.
Versioned base tags and OS package repositories are not byte-for-byte immutable;
retain approved image digests rather than assuming a later rebuild is identical.

First start only UAT PostgreSQL and MinIO; verify their health and empty dedicated
volumes. Obtain explicit approval for Alembic against that new UAT database, then
run the `migrate` service. Never run migrations against an existing application
database under this recipe. Start API, frontend, and ingress; test login, CSRF,
ownership, upload/download, SSE, and health endpoints before starting workers.
The existing Compose dependency graph also runs `migrate` if `up` is issued for API;
therefore **do not run a blanket `up` before the migration approval**.

The image is non-root. Named Docker volumes initialize `/data` ownership from the
image; existing/bind-mounted data needs operator validation. Do not recursively
chown live storage. Read the bundled MinIO AGPL license and preserve source/revision
labels and corresponding-source obligations when distributing the image.

## Director/Studio node package and qualification

Follow [node packaging](gx10-node-packaging.md) with the existing ComfyUI Python.
The package pins Director to `a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb` and checks
source integrity, required node mappings and dependencies. It does not install or
upgrade torch/CUDA or model weights. Stop if dependency validation fails.

After operator-approved install/restart, inspect actual `/object_info` for required
Director/Refine/FaceRefine/Studio mappings. Node presence alone is insufficient:
preserve [production qualification](production-generation-qualification.md) and
the `docs/architecture/` contracts. Existing qualification evidence is tied to its
runtime/source/weights/task/canvas; do not reuse it blindly on GX10 or enable a
workflow by editing database flags. GPU/model execution requires separate approval.

## Rollback and remaining GX10 gates

Before changing ComfyUI custom nodes, retain the previous directories and dependency
inventory. Stop Studio workers before any operator-approved node rollback, restore
the prior directory set, restart the existing ComfyUI service, and recheck node
inventory. Requalification may be required; never forge evidence to restore service.

For app rollback use recorded previous images and matching configuration. An older
image may not support the newer schema: stop and follow a reviewed DB restore or
forward-fix plan instead of running Alembic downgrade. Keep UAT volumes/backups;
never use `down -v`, prune, reset, or delete live MinIO objects for rollback.

Unverified until run on GX10: native ARM64 image builds; healthchecks and non-root
volume permissions; S3/browser CORS and presigned operations; Docker-to-ComfyUI
connectivity; actual four-node load/dependency resolution; schema migration on
isolated UAT; production auth behavior; and approved GPU/quality qualification.

Sources: [MinIO security release](https://github.com/minio/minio/releases/tag/RELEASE.2025-10-15T17-29-55Z),
[Compose merge/override](https://docs.docker.com/reference/compose-file/merge/).

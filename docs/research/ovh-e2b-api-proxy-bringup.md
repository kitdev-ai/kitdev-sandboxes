# OVH E2B API and client-proxy bring-up from the pinned infra tree

Date: 2026-08-06

Status: repository research and proposed commands only; nothing was run on the
OVH host

## Scope and source identity

The inspected checkout is `/private/tmp/kitdev-upstream/infra`. It is clean,
detached at the candidate recorded in `versions.lock.yaml`:

```text
repository: https://github.com/e2b-dev/infra.git
commit: 882a3b4786755db9e94be3297de6827f9100ce5e
commit date: 2026-08-05T22:17:56Z
```

This note covers the fastest contained way to build and smoke-test the API and
client-proxy on the disposable OVH host. It does not authorize the
orchestrator, template manager, Firecracker, ingress, or production exposure.

## Decision

Build the API, database migrator, and client-proxy from the pinned source into
local `linux/amd64` container images. Run them with a reduced Compose file on
two Docker networks:

- an internal backend network for PostgreSQL, Redis, and Loki, with no host
  port publication;
- a runtime network for API/client-proxy and eventual access to the host
  orchestrator;
- publish only API `3000` and client-proxy `3002`/health `3003`, explicitly on
  `127.0.0.1`.

This is safer and faster than host binaries for the first bring-up. Both
programs hardcode wildcard listeners. Containers let the processes listen on
all container interfaces while Docker publishes only selected loopback ports.
The eventual systemd design needs a source change for configurable listen
addresses, or an equally reviewed network-namespace design.

Do not run `make local-infra` unchanged on OVH.

## Upstream build facts

The root `build/api` and `build/client-proxy` targets invoke the package
Makefiles. Direct source builds require Go `1.26.5`, also declared by the Go
workspace and component modules. The upstream Dockerfiles use:

```text
builder: golang:1.26.5-alpine3.24
runtime: alpine:3.24
```

Those are versioned tags, not immutable references. The following registry
objects were observed on 2026-08-06 and must be promoted to the project lock
before production use:

| Image | Multi-platform index digest | Linux/amd64 manifest digest |
|---|---|---|
| `golang:1.26.5-alpine3.24` | `sha256:0178a641fbb4858c5f1b48e34bdaabe0350a330a1b1149aabd498d0699ff5fb2` | `sha256:111d79159b2326f7e80c4a4706e1ba166acb0e2611df853955f3621828cd49e8` |
| `alpine:3.24` | `sha256:28bd5fe8b56d1bd048e5babf5b10710ebe0bae67db86916198a6eec434943f8b` | `sha256:79ff19e9084a00eece421b2523fb93e22d730e2c0e525905de047e848e56d95f` |

The API image must be compiled with the latest migration timestamp from the
same checkout. At this commit it is `20260728163016`. Startup checks the
database against that value. The API and migrator must therefore be built and
deployed as one unit.

The package constants identify API service version `1.0.0` and client-proxy
version `1.2.0`, but the selected commit is not a coordinated release tag.
Building the commit is more faithful than pulling an unrelated component tag.

The upstream build context is the `packages` directory, not the repository
root. Its Dockerfiles use paths such as `./shared`, `./db`, and `./api`.

## Minimum runtime graph

### Required for the initial control-plane smoke test

| Component | Why | Exposure and storage |
|---|---|---|
| PostgreSQL `17.4` | API startup migration check and durable control state | Backend network only; persistent named volume at `/var/lib/postgresql/17/docker` |
| Redis `7.4.6` | API and client-proxy startup; sandbox routing catalog | Backend network only; persistent named volume at `/data` with AOF enabled for the lab |
| Loki `3.4.1` | `LOKI_URL` is syntactically required and log query paths use it | Backend network only; lab volume at `/loki` |
| DB migrator | Applies the exact migrations paired with the API image | One-shot backend task, before API startup |

The datastore index digests already recorded as candidate locks are:

```text
postgres:17.4@sha256:304ab813518754228f9f792f79d6da36359b82d8ecf418096c636725f8c930ad
redis:7.4.6@sha256:a9cc41d6d01da2aa26c219e4f99ecbeead955a7b656c1c499cce8922311b2514
grafana/loki:3.4.1@sha256:1d0c5ddc7644b88956aa0bd775ad796d9635180258a225d6ab3552751d5e2a66
```

Their observed Linux/amd64 child manifests are respectively
`sha256:d4eceb7552a57997fff2e9ceb1a624210e61b6432a2a1f7934a418c27bfe1406`,
`sha256:6a11fed904cf317684ebb75bfe987d4f777c605d6f4e98d1bf3066db6c58f0c1`,
and `sha256:e3a897f58e38daaff7ff005681131bee715ee503a9004ca8ea04bd3ac10cdaff`.

### Deliberately omitted from the fastest path

- ClickHouse is optional in current API code: an empty connection string
  selects the no-op client. Metrics and ClickHouse log reads are unavailable.
  The locked candidate is
  `clickhouse:25.4.5.24@sha256:ad201eec325abb23e558e344d46d81bc9e2eba5a011fc02af440c124a27a1a61`.
- OpenTelemetry is explicitly no-op when
  `OTEL_COLLECTOR_GRPC_ENDPOINT` is unset, so OTel, Tempo, Mimir, Grafana, and
  memcached are not startup dependencies.
- Vector is not needed for API/client-proxy health. It becomes relevant when
  orchestrator/template logs are tested against Loki.
- The SOCKS5 container is a sandbox egress development aid, not an API/proxy
  startup dependency.

## Required environment

API minimum or safety-relevant values:

```text
NODE_ID=ovh-e2b-api
ENVIRONMENT=local
POSTGRES_CONNECTION_STRING=postgres://kitdev:<secret>@postgres:5432/kitdev?sslmode=disable
REDIS_URL=redis:6379
LOKI_URL=http://loki:3100
SERVICE_DISCOVERY_PROVIDER=local
LOCAL_ORCHESTRATOR_ADDRESS=host.docker.internal:5008
SANDBOX_ACCESS_TOKEN_HASH_SEED=<random secret>
ADMIN_TOKEN=<random secret>
AUTH_PROVIDER_CONFIG={"jwt":[]}
VOLUME_TOKEN_ENABLED=false
DOMAIN_NAME=localhost
```

`SANDBOX_ACCESS_TOKEN_HASH_SEED` cannot be empty. Volume token signing defaults
to enabled and otherwise requires issuer, method, key, and key name. It should
be disabled for this smoke test because the public volume-content service is
unresolved.

Client-proxy minimum values:

```text
NODE_ID=ovh-e2b-client-proxy
ENVIRONMENT=local
REDIS_URL=redis:6379
API_INTERNAL_GRPC_ADDRESS=api:5009
```

Leave `OTEL_COLLECTOR_GRPC_ENDPOINT` and `LOGS_COLLECTOR_ADDRESS` unset for the
minimal run. Leave `CLICKHOUSE_CONNECTION_STRING` unset.

The checked-in `.env.local` files are developer examples, not OVH secrets.
They contain public fixed tokens, a fixed EC private key, weak datastore
credentials, and legacy client-proxy variables that the current config model
does not consume. Do not deploy them.

## Proposed source verification and build commands

Run only after the checkout has been installed into a root-controlled path.
The path below is illustrative but the commit checks are exact.

```bash
set -Eeuo pipefail
umask 077

readonly INFRA=/opt/kitdev-sandboxes/src/e2b-infra
readonly PIN=882a3b4786755db9e94be3297de6827f9100ce5e
readonly MIGRATION=20260728163016
readonly GO_INDEX=sha256:0178a641fbb4858c5f1b48e34bdaabe0350a330a1b1149aabd498d0699ff5fb2
readonly ALPINE_INDEX=sha256:28bd5fe8b56d1bd048e5babf5b10710ebe0bae67db86916198a6eec434943f8b

cd "$INFRA"
test "$(git rev-parse HEAD)" = "$PIN"
test -z "$(git status --porcelain=v1)"
test "$(find packages/db/migrations -maxdepth 1 -type f -name '*.sql' \
  -exec basename {} \; | sed 's/_.*//' | sort | tail -n 1)" = "$MIGRATION"

readonly PIN_DIR="$(mktemp -d)"
trap 'find "$PIN_DIR" -type f -delete; rmdir "$PIN_DIR"' EXIT

for component in api db client-proxy; do
  sed \
    -e "s|^FROM golang:\${GOLANG_VERSION}-alpine\${ALPINE_VERSION} AS builder$|FROM docker.io/library/golang:1.26.5-alpine3.24@${GO_INDEX} AS builder|" \
    -e "s|^FROM alpine:\${ALPINE_VERSION}$|FROM docker.io/library/alpine:3.24@${ALPINE_INDEX}|" \
    "packages/${component}/Dockerfile" >"$PIN_DIR/${component}.Dockerfile"
done

grep -H '^FROM ' "$PIN_DIR"/*.Dockerfile

docker buildx build --pull --platform linux/amd64 --load \
  --file "$PIN_DIR/api.Dockerfile" \
  --build-arg "COMMIT_SHA=$PIN" \
  --build-arg "EXPECTED_MIGRATION_TIMESTAMP=$MIGRATION" \
  --tag "kitdev/e2b-api:$PIN" \
  "$INFRA/packages"

docker buildx build --pull --platform linux/amd64 --load \
  --file "$PIN_DIR/db.Dockerfile" \
  --tag "kitdev/e2b-db-migrator:$PIN" \
  "$INFRA/packages"

docker buildx build --pull --platform linux/amd64 --load \
  --file "$PIN_DIR/client-proxy.Dockerfile" \
  --build-arg "COMMIT_SHA=$PIN" \
  --tag "kitdev/e2b-client-proxy:$PIN" \
  "$INFRA/packages"

docker image inspect --format '{{.RepoTags}} {{.Id}}' \
  "kitdev/e2b-api:$PIN" \
  "kitdev/e2b-db-migrator:$PIN" \
  "kitdev/e2b-client-proxy:$PIN"
```

The generated Dockerfiles are required because the upstream `GOLANG_VERSION`
and `ALPINE_VERSION` arguments cannot independently pin both builder and
runtime images by digest. A project-owned build wrapper should eventually
replace this research-time transformation.

## Proposed contained Compose shape

The first committed lab Compose file should encode the following properties.
This is a proposed configuration, not a file created by this research task.

```yaml
name: kitdev-e2b-smoke

services:
  postgres:
    image: postgres:17.4@sha256:304ab813518754228f9f792f79d6da36359b82d8ecf418096c636725f8c930ad
    environment:
      POSTGRES_DB: kitdev
      POSTGRES_USER: kitdev
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?required}
      PGDATA: /var/lib/postgresql/17/docker
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U kitdev -d kitdev"]
      interval: 5s
      timeout: 3s
      retries: 24
    networks: [backend]
    volumes: [postgres-data:/var/lib/postgresql/17/docker]
    restart: unless-stopped

  redis:
    image: redis:7.4.6@sha256:a9cc41d6d01da2aa26c219e4f99ecbeead955a7b656c1c499cce8922311b2514
    command: ["redis-server", "--appendonly", "yes"]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 24
    networks: [backend]
    volumes: [redis-data:/data]
    restart: unless-stopped

  loki:
    image: grafana/loki:3.4.1@sha256:1d0c5ddc7644b88956aa0bd775ad796d9635180258a225d6ab3552751d5e2a66
    command: ["-config.file=/etc/loki/local-config.yaml"]
    healthcheck:
      test: ["CMD-SHELL", "wget --quiet --tries=1 --output-document=- http://localhost:3100/ready | grep -q -w ready"]
      interval: 5s
      timeout: 2s
      retries: 24
    networks: [backend]
    volumes: [loki-data:/loki]
    restart: unless-stopped

  db-migrator:
    image: kitdev/e2b-db-migrator:882a3b4786755db9e94be3297de6827f9100ce5e
    pull_policy: never
    environment:
      POSTGRES_CONNECTION_STRING: ${POSTGRES_CONNECTION_STRING:?required}
    depends_on:
      postgres: {condition: service_healthy}
    networks: [backend]
    read_only: true
    user: "65532:65532"
    cap_drop: [ALL]
    security_opt: [no-new-privileges:true]
    restart: "no"

  api:
    image: kitdev/e2b-api:882a3b4786755db9e94be3297de6827f9100ce5e
    pull_policy: never
    command: ["--port", "3000"]
    environment:
      NODE_ID: ovh-e2b-api
      ENVIRONMENT: local
      POSTGRES_CONNECTION_STRING: ${POSTGRES_CONNECTION_STRING:?required}
      REDIS_URL: redis:6379
      LOKI_URL: http://loki:3100
      SERVICE_DISCOVERY_PROVIDER: local
      LOCAL_ORCHESTRATOR_ADDRESS: host.docker.internal:5008
      SANDBOX_ACCESS_TOKEN_HASH_SEED: ${SANDBOX_ACCESS_TOKEN_HASH_SEED:?required}
      ADMIN_TOKEN: ${ADMIN_TOKEN:?required}
      AUTH_PROVIDER_CONFIG: '{"jwt":[]}'
      VOLUME_TOKEN_ENABLED: "false"
      DOMAIN_NAME: localhost
    extra_hosts: [host.docker.internal:host-gateway]
    depends_on:
      db-migrator: {condition: service_completed_successfully}
      redis: {condition: service_healthy}
      loki: {condition: service_healthy}
    networks: [backend, runtime]
    ports: ["127.0.0.1:3000:3000"]
    healthcheck:
      test: ["CMD-SHELL", "wget --quiet --tries=1 --output-document=- http://localhost:3000/health >/dev/null"]
      interval: 5s
      timeout: 2s
      retries: 24
    read_only: true
    tmpfs: [/tmp]
    user: "65532:65532"
    cap_drop: [ALL]
    security_opt: [no-new-privileges:true]
    restart: "no"

  client-proxy:
    image: kitdev/e2b-client-proxy:882a3b4786755db9e94be3297de6827f9100ce5e
    pull_policy: never
    environment:
      NODE_ID: ovh-e2b-client-proxy
      ENVIRONMENT: local
      REDIS_URL: redis:6379
      API_INTERNAL_GRPC_ADDRESS: api:5009
    depends_on:
      api: {condition: service_healthy}
      redis: {condition: service_healthy}
    networks: [backend, runtime]
    ports:
      - "127.0.0.1:3002:3002"
      - "127.0.0.1:3003:3003"
    read_only: true
    tmpfs: [/tmp]
    user: "65532:65532"
    cap_drop: [ALL]
    security_opt: [no-new-privileges:true]
    restart: "no"

networks:
  backend:
    name: kitdev-e2b-backend
    internal: true
  runtime:
    name: kitdev-e2b-runtime

volumes:
  postgres-data: {}
  redis-data: {}
  loki-data: {}
```

Create a private environment file without copying upstream's public values:

```bash
set -Eeuo pipefail
umask 077
readonly ENV_FILE=/etc/kitdev-sandboxes/e2b-smoke.env
install -d -m 0700 /etc/kitdev-sandboxes
postgres_password="$(openssl rand -hex 32)"
printf 'POSTGRES_PASSWORD=%s\n' "$postgres_password" >"$ENV_FILE"
printf 'POSTGRES_CONNECTION_STRING=postgres://kitdev:%s@postgres:5432/kitdev?sslmode=disable\n' "$postgres_password" >>"$ENV_FILE"
printf 'SANDBOX_ACCESS_TOKEN_HASH_SEED=%s\n' "$(openssl rand -hex 32)" >>"$ENV_FILE"
printf 'ADMIN_TOKEN=%s\n' "$(openssl rand -hex 32)" >>"$ENV_FILE"
chmod 0600 "$ENV_FILE"
unset postgres_password
```

Then validate before starting anything:

```bash
readonly COMPOSE=/opt/kitdev-sandboxes/compose/e2b-smoke.yaml
readonly ENV_FILE=/etc/kitdev-sandboxes/e2b-smoke.env
docker compose --env-file "$ENV_FILE" --file "$COMPOSE" config --quiet
docker compose --env-file "$ENV_FILE" --file "$COMPOSE" pull postgres redis loki
docker compose --env-file "$ENV_FILE" --file "$COMPOSE" up --no-start
docker compose --env-file "$ENV_FILE" --file "$COMPOSE" config --format json | \
  python3 -c '
import json
import sys
document = json.load(sys.stdin)
observed = {
    (name, port.get("host_ip"), int(port["published"]), port["target"], port["protocol"])
    for name, service in document["services"].items()
    for port in service.get("ports", [])
}
expected = {
    ("api", "127.0.0.1", 3000, 3000, "tcp"),
    ("client-proxy", "127.0.0.1", 3002, 3002, "tcp"),
    ("client-proxy", "127.0.0.1", 3003, 3003, "tcp"),
}
raise SystemExit(0 if observed == expected else 1)
'
```

The only published addresses must be `127.0.0.1` for `3000`, `3002`, and
`3003`. PostgreSQL, Redis, Loki, API gRPC `5009`/`5109`, and every other port
must be absent from the host binding list.

Start and verify:

```bash
docker compose --env-file "$ENV_FILE" --file "$COMPOSE" up -d
docker compose --env-file "$ENV_FILE" --file "$COMPOSE" ps
curl --fail --silent --show-error http://127.0.0.1:3000/health
curl --fail --silent --show-error http://127.0.0.1:3003/health
sudo ss -lntp | grep -E ':(3000|3002|3003|5009|5109|5432|6379|3100)([[:space:]]|$)'
```

Do not seed upstream's public test API keys for the health-only smoke test. If
SDK authentication testing is later required, make it a separate disposable
step, keep every public interface loopback-only, and replace the checked-in
seed identities before any ingress is enabled.

## Why upstream Compose must not run unchanged

`packages/local-dev/docker-compose.yaml` is a developer workstation stack, not
a portable single-host deployment definition:

1. It publishes PostgreSQL, Redis, ClickHouse, Loki, OTel, Vector, Grafana,
   memcached, and SOCKS5 on all host interfaces.
2. It uses `postgres/postgres`, `clickhouse/clickhouse`, unauthenticated Redis,
   anonymous Grafana admin, and fixed SOCKS5 credentials.
3. Only five image versions are candidate-locked in this project. `bash`,
   `python:3.12-alpine`, `timberio/vector:0.51.X-alpine`, and the Grafana plugin
   URL are floating; SOCKS5 installs the current `pproxy` from the network at
   container startup.
4. It includes eleven services when the initial API/proxy smoke test needs
   three datastores. The file does not define API or client-proxy.
5. It has few health dependencies, no restart policy, and an attached
   `up --abort-on-container-failure` Make target.
6. ClickHouse relies on a generated relative bind mount. `make local-infra`
   first runs `envsubst` against `packages/clickhouse/local/config.tpl.xml`.
7. Its public fixed `.env.local` values are intended only for local developer
   convenience.

It may technically start on an ordinary Linux Docker host after the generated
ClickHouse file exists, but it is neither isolated nor reproducible enough for
OVH. Upstream itself marks local bare-metal development as work in progress,
and the self-host support matrix leaves a general Linux machine unchecked.

## Concrete blockers and follow-up gates

1. **Wildcard listeners:** API HTTP, both API gRPC servers, client-proxy, and
   client-proxy health all bind wildcard addresses in source. Do not run host
   binaries until a reviewed bind-address option exists.
2. **Redis authentication:** standalone `REDIS_URL` is treated as a bare
   `host:port`; the code does not populate a Redis password. Keep Redis on a
   private container network or loopback. Do not publish it.
3. **Builder/runtime base pins:** Go and Alpine digests above are observed but
   not yet in `versions.lock.yaml`. Promote and verify them before treating
   image builds as reproducible.
4. **Local discovery is experimental:** `SERVICE_DISCOVERY_PROVIDER=local` is
   documented for a dummy macOS orchestrator. A real combined Linux
   orchestrator/template-manager at one static address needs integration tests.
5. **Health is not end to end:** API and client-proxy can become healthy
   without a working orchestrator, base template, kernel, Firecracker, envd,
   or sandbox route. This procedure proves only build, migration, dependency,
   and listener startup.
6. **Volumes API remains disabled:** the referenced public volume-content
   implementation is unresolved. `VOLUME_TOKEN_ENABLED=false` must remain in
   this smoke configuration.
7. **Secret delivery is environment-only:** API and proxy do not provide
   general `*_FILE` secret inputs. Container environment is visible to Docker
   administrators. Access to the Docker socket must remain privileged.
8. **Compose is a temporary bring-up vehicle:** project architecture still
   calls for systemd-managed API/proxy identities. Promotion requires
   configurable loopback listeners, systemd hardening, exact binary/image
   hashes, and clean-host tests.

## Verification performed during this research

- Confirmed the checkout is clean at the exact lock-file commit.
- Parsed the upstream Compose model and enumerated all images and services.
- Verified the API, migrator, and client-proxy Docker build contexts and
  migration coupling with `docker buildx bake --print`.
- Resolved the current image index and Linux/amd64 manifest digests listed
  above.
- Read the current environment parsers, listener construction, Redis factory,
  telemetry no-op path, API migration guard, and local discovery code.

No upstream image was built or run, no OVH SSH command was issued, and no host
state was changed by this research.

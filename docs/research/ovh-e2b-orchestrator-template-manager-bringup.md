# OVH E2B orchestrator and template-manager bring-up

Date: 2026-08-06

Status: pinned-source research plus a supervisor-reported live health milestone.
The author of this note did not execute commands on the OVH host. The supervisor
started the combined process there and reported the results recorded below.

## Scope and source identity

The inspected E2B checkout is `/private/tmp/kitdev-upstream/infra`, clean and
detached at:

```text
repository: https://github.com/e2b-dev/infra.git
commit: 882a3b4786755db9e94be3297de6827f9100ce5e
commit date: 2026-08-05T22:17:56Z
```

This note targets the live single-host layout under
`/var/lib/kitdev-sandboxes/data/runtime`. It covers a real, privileged host
orchestrator serving the containerized API on Docker network `kitdev-core`.
Command blocks remain runbook guidance rather than an authorization boundary.

The supported host operating systems for this project are Ubuntu 26.04 for
production and Ubuntu 25.04 for development/migration only. Ubuntu 24.04 is
not in the support matrix.

## Decision

Run one copy of the pinned orchestrator binary directly on the Ubuntu host as
root with both roles:

```text
ORCHESTRATOR_SERVICES=orchestrator,template-manager
```

The upstream code deliberately uses one binary for both roles. A second
template-manager process on the same host is invalid: both roles use the same
sandbox runtime and the non-development startup lock permits only one host
runtime instance.

Use local filesystem providers for the first lab run, local network-slot
allocation instead of Consul, and no Redis/ClickHouse/OTel dependency for the
first health smoke. The containerized API discovers the host process at
`host.docker.internal:5008`, with that name explicitly mapped to the gateway of
the Docker network that actually carries the API container.

Do not containerize the real orchestrator. It creates mount and network
namespaces, cgroups, TAP/veth devices, NBD attachments, iptables/nftables
rules, and Firecracker processes. Upstream runs it with Nomad `raw_exec`, not
the Docker driver.

## What startup actually does

The binary does more than open a health port. Before serving its shared
HTTP/gRPC listener it initializes local storage, optional telemetry and Redis,
template cache, cgroup v2, sandbox proxy, TCP firewall proxy, startup reclaim,
an NBD pool, a network-slot pool, the sandbox service, and the template
manager. The network and NBD pools populate asynchronously.

Starting the real binary immediately mutates host state. In particular, the
network pool starts creating named namespaces, veth pairs, TAP devices,
routes, and firewall rules even when no API sandbox request has been made.
The health smoke is therefore not a harmless listener-only test.

The process reports healthy by default once it reaches the server setup. That
does not prove a Firecracker VM can boot. It proves the process initialized
far enough to serve `InfoService` and `/health`.

## Exact live layout

The read-only, hash-locked runtime artifacts are:

```text
/var/lib/kitdev-sandboxes/data/runtime/firecrackers/v1.14.1_431f1fc/amd64/firecracker
/var/lib/kitdev-sandboxes/data/runtime/kernels/vmlinux-6.1.158/amd64/vmlinux.bin
/var/lib/kitdev-sandboxes/data/runtime/busybox/1.36.1/amd64/busybox
/var/lib/kitdev-sandboxes/data/runtime/envd/envd
```

The pinned hashes and sizes from `versions.lock.yaml` are:

| Artifact | SHA-256 | Bytes |
|---|---|---:|
| Kernel `vmlinux-6.1.158` | `1982f8d5f1bc1680a36b0cdf126f605834b1633bba200d3281bccd53b86ff9ee` | 43638104 |
| Firecracker `v1.14.1_431f1fc` | `d81fd733be7e027406b4d5241442c447a2b5878b06dfa63dc236e68f3536d689` | 3566832 |
| BusyBox `1.36.1` | `d7cce939adb09a41a22a5f846d22ba8d576b38dbb2b46a5c77a3a3e27ec52520` | 1210176 |
| envd `0.6.13` | `139b9edb6b7598c2bd2d2acff863846408c878cc038353e10f4c47a0276737f5` | 12927102 |

The code prefers `{version}/{arch}/firecracker` and
`{version}/{arch}/vmlinux.bin`. BusyBox is resolved as
`{HOST_BUSYBOX_DIR}/{BUSYBOX_VERSION}/{GOARCH}/busybox`; envd is an exact file
path.

Writable paths are:

```text
/var/lib/kitdev-sandboxes/data/runtime/orchestrator
/var/lib/kitdev-sandboxes/data/runtime/sandbox-vms
/var/lib/kitdev-sandboxes/data/runtime/snapshot-cache
/var/lib/kitdev-sandboxes/data/runtime/sandbox-cache
/var/lib/kitdev-sandboxes/data/runtime/template-cache
/var/lib/kitdev-sandboxes/data/runtime/build-templates
/var/lib/kitdev-sandboxes/data/runtime/shared-chunk-cache
```

The local template and build-cache stores should be nested under the existing
orchestrator directory:

```text
/var/lib/kitdev-sandboxes/data/runtime/orchestrator/template-storage
/var/lib/kitdev-sandboxes/data/runtime/orchestrator/build-cache
```

Local storage is single-host and non-HA. A host loss loses templates and build
cache unless the data directory is backed up or moved to an S3-compatible
provider later.

## Minimal exact environment

Proposed root-owned file `/etc/kitdev-sandboxes/orchestrator.env`:

```bash
NODE_ID=ovh-e2b-01
ENVIRONMENT=prod
ORCHESTRATOR_SERVICES=orchestrator,template-manager
USE_LOCAL_NAMESPACE_STORAGE=true

GRPC_PORT=5008
PROXY_PORT=5007
NBD_POOL_SIZE=16
ORCHESTRATOR_LOCK_PATH=/var/lib/kitdev-sandboxes/data/runtime/orchestrator/.lock

ORCHESTRATOR_BASE_PATH=/var/lib/kitdev-sandboxes/data/runtime/orchestrator
SANDBOX_DIR=/var/lib/kitdev-sandboxes/data/runtime/sandbox-vms
SANDBOX_CACHE_DIR=/var/lib/kitdev-sandboxes/data/runtime/sandbox-cache
SNAPSHOT_CACHE_DIR=/var/lib/kitdev-sandboxes/data/runtime/snapshot-cache
TEMPLATE_CACHE_DIR=/var/lib/kitdev-sandboxes/data/runtime/template-cache
TEMPLATES_DIR=/var/lib/kitdev-sandboxes/data/runtime/build-templates
SHARED_CHUNK_CACHE_PATH=/var/lib/kitdev-sandboxes/data/runtime/shared-chunk-cache

TEMPLATE_STORAGE_URL=file:///var/lib/kitdev-sandboxes/data/runtime/orchestrator/template-storage
BUILD_CACHE_STORAGE_URL=file:///var/lib/kitdev-sandboxes/data/runtime/orchestrator/build-cache
LOCAL_UPLOAD_BASE_URL=http://127.0.0.1:5008
PROVIDER=local
ARTIFACTS_REGISTRY_PROVIDER=Local

TARGET_ARCH=amd64
DEFAULT_KERNEL_VERSION=vmlinux-6.1.158
DEFAULT_FIRECRACKER_VERSION=v1.14.1_431f1fc
FIRECRACKER_VERSIONS_DIR=/var/lib/kitdev-sandboxes/data/runtime/firecrackers
HOST_KERNELS_DIR=/var/lib/kitdev-sandboxes/data/runtime/kernels
BUSYBOX_VERSION=1.36.1
HOST_BUSYBOX_DIR=/var/lib/kitdev-sandboxes/data/runtime/busybox
HOST_ENVD_PATH=/var/lib/kitdev-sandboxes/data/runtime/envd/envd

SANDBOX_ORCHESTRATOR_IP=192.0.2.1
SANDBOX_HYPERLOOP_PROXY_PORT=5010
SANDBOX_NFS_PROXY_PORT=5011
SANDBOX_PORTMAPPER_PORT=5012
SANDBOX_TCP_FIREWALL_HTTP_PORT=5016
SANDBOX_TCP_FIREWALL_TLS_PORT=5017
SANDBOX_TCP_FIREWALL_OTHER_PORT=5018
SANDBOXES_HOST_NETWORK_CIDR=10.11.0.0/16
SANDBOXES_VRT_NETWORK_CIDR=10.12.0.0/16
```

Why the non-obvious values matter:

- `ENVIRONMENT=prod` preserves the single-runtime flock. Local namespace
  allocation is selected independently with `USE_LOCAL_NAMESPACE_STORAGE`.
- The storage URL variables take precedence over legacy bucket variables and
  remove ambiguity about the provider and path.
- `ARTIFACTS_REGISTRY_PROVIDER=Local` prevents template-manager startup from
  attempting GCP Artifact Registry initialization. It uses the host Docker
  daemon only if a later build references a locally tagged custom image.
- Explicit kernel and Firecracker defaults keep offline feature-flag fallback
  aligned with the locked files.
- `LOCAL_UPLOAD_BASE_URL` is loopback because the signed local build-cache PUT
  is performed by this same host process. Its HMAC key is generated at every
  process start, so in-flight signed URLs do not survive a restart.

For the first health smoke, leave all of these unset:

```text
REDIS_URL
REDIS_CLUSTER_URL
CLICKHOUSE_CONNECTION_STRING
CLICKHOUSE_CONNECTION_STRINGS
OTEL_COLLECTOR_GRPC_ENDPOINT
LOGS_COLLECTOR_ADDRESS
LAUNCH_DARKLY_API_KEY
PERSISTENT_VOLUME_MOUNTS
DOCKERHUB_REMOTE_REPOSITORY_URL
CONSUL_TOKEN
```

Redis is optional in the orchestrator code. With it disabled, peer lookup and
Redis sandbox-event delivery are no-ops; that is sufficient for one-node API
health. For fuller behavior, publish the existing Redis container only on
`127.0.0.1:6379` and add `REDIS_URL=127.0.0.1:6379`. Do not expose Redis on the
public interface. The current standalone Redis client surface has no password
field, so loopback publication is the containment boundary.

Leaving `PERSISTENT_VOLUME_MOUNTS` unset also means the NFS and portmapper
listeners on 5011 and 5012 are not started.

The pinned telemetry package has no separate disable switch. It reads
`OTEL_COLLECTOR_GRPC_ENDPOINT` directly during package initialization and
returns no-op providers only when that value is empty. An API container that
still emits OTel connection timeouts therefore has a non-empty value in its
container configuration, even if the current shell or `.env` file does not.
Inspect the effective environment and recreate it after removing or emptying
the value:

```bash
docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' kitdev-api \
  | grep '^OTEL_COLLECTOR_GRPC_ENDPOINT=' || true

# Remove or empty the value in the Compose environment inputs first.
# Replace the service name if the Compose service is not named "api".
docker compose up --detach --force-recreate api
```

A restart alone retains the old environment. Standard `OTEL_EXPORTER_*`
variables do not disable this pinned implementation because its exporters use
the project-specific endpoint variable explicitly.

## Host prerequisites and stop conditions

The first real start must stop unless all of these are true:

- Bare-metal x86_64 with hardware virtualization and usable `/dev/kvm`.
- Cgroup v2 is mounted and `cpu` and `memory` controllers are available; the
  process can create `/sys/fs/cgroup/e2b` and enable both controllers.
- `nbd` is loaded with at least 16 devices for the proposed pool; the live
  host is prepared for `nbds_max=4096`, and `/dev/nbd0` exists.
- `/dev/net/tun` exists; named network namespaces and TAP/veth creation work.
- A default IPv4 route exists. The package resolves its default gateway during
  package initialization and aborts before `main` if none exists.
- IPv4 forwarding is enabled for sandbox egress.
- `10.11.0.0/16` and `10.12.0.0/16` overlap neither host routes nor Docker
  networks. If either overlaps, choose and lock two unused `/16` networks
  before the first start. Do not change them after templates are in use without
  a migration plan.
- The 2 MiB hugepage pool is ready before a template build or sandbox boot.
  API health alone does not allocate guest RAM.
- Required host commands are installed: `bash`, `unshare`, `mount`, `umount`,
  `ip`, `iptables`, `e2fsck`, `resize2fs`, `tune2fs`, `debugfs`, `rsync`, and
  `du`. On Ubuntu these principally come from `util-linux`, `iproute2`,
  `iptables`, `e2fsprogs`, `rsync`, and `coreutils`.

Upstream configures NBD as follows:

```bash
modprobe nbd nbds_max=4096

cat >/etc/udev/rules.d/97-nbd-device.rules <<'EOF'
ACTION=="add|change", KERNEL=="nbd*", OPTIONS:="nowatch"
EOF
udevadm control --reload-rules
udevadm trigger
```

The code's default networks must be checked structurally, not by a substring
grep. This read-only preflight detects overlap with existing non-default host
routes:

```bash
python3 - <<'PY'
import ipaddress
import json
import subprocess
import sys

reserved = [
    ipaddress.ip_network("10.11.0.0/16"),
    ipaddress.ip_network("10.12.0.0/16"),
]
routes = json.loads(subprocess.check_output(
    ["ip", "-j", "-4", "route", "show", "table", "all"], text=True
))
conflicts = []
for route in routes:
    dst = route.get("dst")
    if not dst or dst == "default":
        continue
    try:
        network = ipaddress.ip_network(dst, strict=False)
    except ValueError:
        continue
    for candidate in reserved:
        if candidate.overlaps(network):
            conflicts.append((str(candidate), str(network), route.get("dev", "")))

if conflicts:
    for candidate, network, device in conflicts:
        print(f"conflict: {candidate} overlaps {network} on {device}", file=sys.stderr)
    raise SystemExit(1)
PY
```

The initial mechanical preflight should also include:

```bash
set -Eeuo pipefail

test "$(uname -m)" = x86_64
. /etc/os-release
case "$VERSION_ID" in
  25.04|26.04) ;;
  *) printf 'unsupported Ubuntu version: %s\n' "$VERSION_ID" >&2; exit 1 ;;
esac

test "$(stat -fc %T /sys/fs/cgroup)" = cgroup2fs
grep -qw cpu /sys/fs/cgroup/cgroup.controllers
grep -qw memory /sys/fs/cgroup/cgroup.controllers
test -c /dev/kvm
test -r /dev/kvm
test -w /dev/kvm
test -c /dev/net/tun
test -r /sys/module/nbd/parameters/nbds_max
test "$(cat /sys/module/nbd/parameters/nbds_max)" -ge 16
test -b /dev/nbd0
ip -4 route show default | grep -q .
test "$(sysctl -n net.ipv4.ip_forward)" = 1
grep -q '^Hugepagesize:[[:space:]]*2048 kB$' /proc/meminfo

for command in bash unshare mount umount ip iptables e2fsck resize2fs tune2fs debugfs rsync du; do
  command -v "$command" >/dev/null
done

printf '%s  %s\n' \
  d81fd733be7e027406b4d5241442c447a2b5878b06dfa63dc236e68f3536d689 \
  /var/lib/kitdev-sandboxes/data/runtime/firecrackers/v1.14.1_431f1fc/amd64/firecracker \
  1982f8d5f1bc1680a36b0cdf126f605834b1633bba200d3281bccd53b86ff9ee \
  /var/lib/kitdev-sandboxes/data/runtime/kernels/vmlinux-6.1.158/amd64/vmlinux.bin \
  d7cce939adb09a41a22a5f846d22ba8d576b38dbb2b46a5c77a3a3e27ec52520 \
  /var/lib/kitdev-sandboxes/data/runtime/busybox/1.36.1/amd64/busybox \
  139b9edb6b7598c2bd2d2acff863846408c878cc038353e10f4c47a0276737f5 \
  /var/lib/kitdev-sandboxes/data/runtime/envd/envd \
  | sha256sum --check --strict

test -x /var/lib/kitdev-sandboxes/data/runtime/firecrackers/v1.14.1_431f1fc/amd64/firecracker
test -x /var/lib/kitdev-sandboxes/data/runtime/busybox/1.36.1/amd64/busybox
test -x /var/lib/kitdev-sandboxes/data/runtime/envd/envd
```

For 2 MiB hugepages, a VM needs at least `memory_MiB / 2` pages. A 512 MiB
single-build smoke therefore needs at least 256 available pages at the moment
Firecracker allocates memory. Production sizing must cover concurrent guest
RAM, not merely the first smoke. Upstream mounts `hugetlbfs` at
`/mnt/hugepages` and sets both `vm.nr_hugepages` and
`vm.nr_overcommit_hugepages`; the current Go config has no path to that mount,
so the mount itself is not an orchestrator environment value.

## Host listeners and firewall boundary

The pinned source does not support per-listener bind addresses.

| Port | Bind | Purpose | First smoke |
|---:|---|---|---|
| 5008/tcp | all host interfaces | cmux: gRPC services plus HTTP `/health` and local `/upload` | required from `kitdev-core` |
| 5007/tcp | all host interfaces | client-proxy to sandbox reverse proxy | not needed for API health; needed for sandbox traffic |
| 5010/tcp | `0.0.0.0` | guest Hyperloop/log path | runtime only |
| 5011/tcp | all interfaces when volumes configured | NFS proxy | disabled in first smoke |
| 5012/tcp | all interfaces when volumes configured | portmapper | disabled in first smoke |
| 5016-5018/tcp | `0.0.0.0` | sandbox egress TCP firewall proxies | runtime only |
| 6060/tcp | `127.0.0.1` | pprof | local only |

Port 5008 is plaintext HTTP/gRPC. It must not be public. UFW's default inbound
deny remains required, with an interface-scoped exception for the Docker
bridge behind `kitdev-core`.

Resolve and verify that bridge instead of assuming a generated name:

```bash
set -Eeuo pipefail

readonly CORE_NETWORK=kitdev-core
core_id="$(docker network inspect --format '{{.Id}}' "$CORE_NETWORK")"
core_bridge="$(docker network inspect --format '{{index .Options "com.docker.network.bridge.name"}}' "$CORE_NETWORK")"
core_gateway="$(docker network inspect --format '{{(index .IPAM.Config 0).Gateway}}' "$CORE_NETWORK")"
if test -z "$core_bridge"; then
  core_bridge="br-${core_id:0:12}"
fi
ip link show "$core_bridge" >/dev/null
test -n "$core_gateway"
printf 'kitdev-core bridge: %s; gateway: %s\n' "$core_bridge" "$core_gateway"

ufw allow in on "$core_bridge" to any port 5008 proto tcp comment 'kitdev API to E2B orchestrator'
```

Do not add a public-interface rule for 5008. Add the same bridge-scoped rule
for 5007 only when the client-proxy-to-sandbox path is tested.

Do not use Docker's generic `host-gateway` token on this multi-network host. In
the live test it resolved to `172.17.0.1`, the default `docker0` gateway, while
the API was attached only to `kitdev-core`, whose gateway was `172.18.0.1`.
That mapping timed out behind UFW. Mapping the name explicitly to `172.18.0.1`
and retaining the bridge-scoped UFW exception fixed discovery.

Derive the value each time the network is created and pass it to Compose:

```bash
set -Eeuo pipefail

export KITDEV_CORE_GATEWAY="$({
  docker network inspect \
    --format '{{(index .IPAM.Config 0).Gateway}}' \
    kitdev-core
})"
test -n "$KITDEV_CORE_GATEWAY"
docker compose up --detach --force-recreate api
```

The containerized API configuration must retain:

```yaml
environment:
  SERVICE_DISCOVERY_PROVIDER: local
  LOCAL_ORCHESTRATOR_ADDRESS: host.docker.internal:5008
extra_hosts:
  - "host.docker.internal:${KITDEV_CORE_GATEWAY:?set from kitdev-core gateway}"
networks:
  - kitdev-core
```

The wildcard host listener is currently necessary because a process bound to
host loopback is not reachable through Docker's host-gateway address. UFW is
the compensating control. A source change adding configurable bind addresses
would allow binding 5008 and 5007 to the Docker bridge gateway specifically.

There is a second firewall integration risk: sandbox egress depends on dynamic
iptables and nftables rules plus host INPUT access to 5010 and 5016-5018 from
the per-slot veth interfaces. UFW compatibility with those dynamic interfaces
has not been tested on this host. API health does not exercise this path. Do
not claim VM networking ready until a sandbox boot and outbound/inbound traffic
test pass without broadening public ingress.

## Build the pinned host binary

The upstream Dockerfile builds with CGO in `golang:1.26.5-bookworm`, then
exports the host binaries from a scratch stage. Bookworm glibc 2.36 was chosen
upstream so the binary runs on newer Ubuntu glibc.

The upstream tag is mutable. On 2026-08-06 the observed registry objects were:

```text
golang:1.26.5-bookworm index:
sha256:6c5605ab3a9a9fb3c4eafe5b3d63cdbf3881caf113262b67862547b54a9db599

linux/amd64 child manifest:
sha256:db25d241820546be7b96953eea8d3e6bd15d413d59d00a75b68b74dfb5e2ecd2
```

These digests are research observations, not yet entries in
`versions.lock.yaml`. Promotion to the project lock is required before calling
the build reproducibly locked.

Proposed build from the root-controlled pinned checkout:

```bash
set -Eeuo pipefail
umask 077

readonly INFRA=/opt/kitdev-sandboxes/src/e2b-infra
readonly PIN=882a3b4786755db9e94be3297de6827f9100ce5e
readonly GO_INDEX=sha256:6c5605ab3a9a9fb3c4eafe5b3d63cdbf3881caf113262b67862547b54a9db599
readonly DEST=/var/lib/kitdev-sandboxes/data/runtime/orchestrator

cd "$INFRA"
test "$(git rev-parse HEAD)" = "$PIN"
test -z "$(git status --porcelain=v1)"

stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT

sed \
  "s|^FROM golang:\${GOLANG_VERSION}-\${DEBIAN_VERSION} AS builder$|FROM docker.io/library/golang:1.26.5-bookworm@${GO_INDEX} AS builder|" \
  packages/orchestrator/Dockerfile >"$stage/orchestrator.Dockerfile"
grep -q "^FROM .*@${GO_INDEX} AS builder$" "$stage/orchestrator.Dockerfile"

docker buildx build --pull --platform linux/amd64 \
  --file "$stage/orchestrator.Dockerfile" \
  --build-arg "COMMIT_SHA=$PIN" \
  --output "type=local,dest=$stage/out" \
  "$INFRA/packages"

test -x "$stage/out/orchestrator"
file "$stage/out/orchestrator"
ldd "$stage/out/orchestrator"

install -o root -g root -m 0755 "$stage/out/orchestrator" "$DEST/orchestrator.new"
mv -T "$DEST/orchestrator.new" "$DEST/orchestrator"
sha256sum "$DEST/orchestrator"
```

The final binary hash must be recorded before deployment. `ldd` must resolve
every dynamic dependency on both supported Ubuntu versions. Do not replace a
running binary without the normal systemd stop/start sequence and rollback
copy.

## Fastest meaningful API health smoke

After all preflights and the bridge-scoped firewall rule pass, the fastest
foreground start is:

```bash
sudo env -i \
  PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  /bin/bash -c '
    set -Eeuo pipefail
    set -a
    source /etc/kitdev-sandboxes/orchestrator.env
    set +a
    exec /var/lib/kitdev-sandboxes/data/runtime/orchestrator/orchestrator
  '
```

In a second SSH session, first prove the host listener, then wait for the
containerized API's 5-second builder discovery and 20-second node sync loops:

```bash
set -Eeuo pipefail

curl --fail --silent --show-error http://127.0.0.1:5008/health \
  | jq -e '.status == "healthy"'

for attempt in $(seq 1 90); do
  if body="$(curl --fail --silent --show-error http://127.0.0.1:3000/health 2>/dev/null)"; then
    printf '%s\n' "$body"
    exit 0
  fi
  sleep 1
done

printf 'API did not become healthy within 90 seconds\n' >&2
docker compose --project-name kitdev-e2b-smoke logs --tail=200 api
exit 1
```

The supervisor subsequently ran this milestone using a transient root systemd
service. The combined-role process stayed active, created `/sys/fs/cgroup/e2b`
and 32 network namespaces, returned HTTP 200 from the host `/health`, and made
the API `/health` return HTTP 200 after the explicit `kitdev-core` gateway
mapping and bridge-scoped UFW rule were applied. Those observations validate
the combined process, listed local artifacts and paths, host initialization,
and API discovery at this checkpoint; they do not validate a guest boot.

The API's `/health` remains unhealthy until its in-memory orchestrator node
count is non-zero. In local discovery mode, the same address is also queried
as a template builder. A 200 response therefore demonstrates that the API
could reach the host-gateway listener and register the process after its Info
RPC reported the orchestrator role. The configured process also reports the
template-builder role.

This smoke does not prove artifact correctness, NBD attachment, hugepage
allocation, Firecracker boot, envd health, snapshot I/O, sandbox proxying, or
egress. Those require a subsequent smallest-template build and sandbox test.

## Proposed systemd service

The first service should remain deliberately close to upstream `raw_exec`.
Capability reduction is a later measured hardening task.

```ini
[Unit]
Description=Kitdev E2B orchestrator and template manager
Wants=network-online.target docker.service
After=network-online.target docker.service
ConditionPathExists=/dev/kvm
ConditionPathExists=/dev/net/tun
ConditionPathExists=/sys/module/nbd/parameters/nbds_max

[Service]
Type=simple
User=root
Group=root
EnvironmentFile=/etc/kitdev-sandboxes/orchestrator.env
ExecStart=/var/lib/kitdev-sandboxes/data/runtime/orchestrator/orchestrator
Restart=on-failure
RestartSec=5s
KillSignal=SIGTERM
TimeoutStopSec=75min
LimitNOFILE=1048576
TasksMax=infinity
UMask=0077
Delegate=yes

# Required by the current host-runtime architecture.
NoNewPrivileges=no
PrivateDevices=no
PrivateMounts=no
ProtectControlGroups=no
RestrictNamespaces=no

[Install]
WantedBy=multi-user.target
```

Why a dedicated unprivileged service account is not currently feasible:

- The architecture document explicitly says the orchestrator runs as root.
- Startup writes `/sys/fs/cgroup/e2b` and enables cgroup controllers.
- Network pooling creates named network namespaces, TAP/veth devices, routes,
  iptables NAT/filter rules, and nftables tables.
- Firecracker launch uses `unshare -m`, host mount commands, `ip netns exec`,
  `/dev/kvm`, and `/dev/net/tun`.
- NBD uses kernel netlink/ioctl operations and `/dev/nbd*`; template building
  also performs loop mounts and ext4 maintenance.
- Startup reclaim scans and removes leaked host Firecracker, NBD, netns,
  cgroup, and socket resources.

A non-root account with selected ambient capabilities is not a small config
change. It would also need device ACLs, a delegated cgroup subtree, a different
cgroup root in code, controlled namespace/mount helpers, and a reviewed
reclaim boundary. The first lab unit should use `User=root`, a root-owned env
file and binary, UFW containment, and a dedicated host.

## Remaining blockers before a VM smoke

1. The orchestrator build-base digest is observed but not yet promoted into
   `versions.lock.yaml`, and the produced binary has no recorded deployment
   hash yet.
2. The live process started and populated its network pool, but the structured
   host route/Docker-network overlap result for `10.11.0.0/16` and
   `10.12.0.0/16` still needs to be captured before a guest VM test.
3. UFW interaction with dynamic sandbox veth, iptables, and nftables rules is
   untested. API health exercises only TCP/5008 from `kitdev-core`.
4. The exact hugepage capacity policy must be derived from the purchased
   server RAM and intended concurrent guest RAM. "Hugepages ready" is not a
   capacity calculation.
5. Redis is deliberately omitted for first health. Before multi-node or
   production-like event/peer testing, decide whether to loopback-publish the
   existing container Redis or deploy a host-reachable authenticated design.
6. Local filesystem template/build storage and the per-start local-upload HMAC
   are lab choices, not a durable production storage design.
7. The minimum API health smoke does not establish that a base image can be
   built. That next gate needs `e2fsprogs`, `rsync`, hugepages, NBD, KVM,
   sandbox networking, the locked BusyBox/envd/kernel/Firecracker files, and a
   small public OCI base image.

## Pinned-source evidence

The main code points inspected were:

- `docs/ARCHITECTURE.md`: service topology, root execution, ports, storage,
  networking, NBD, UFFD, and role model.
- `packages/orchestrator/pkg/cfg/model.go`: env names, defaults, path
  normalization, ports, NBD pool, and persistent-volume validation.
- `packages/orchestrator/pkg/cfg/service.go`: combined-role parsing and sandbox
  runtime ownership.
- `packages/orchestrator/pkg/cfg/storage.go`: URL-first local/GCS/S3 storage
  resolution.
- `packages/orchestrator/pkg/factories/run.go`: startup order, optional Redis
  and ClickHouse, cgroup/NBD/network initialization, role registration, and
  shared cmux health listener.
- `packages/orchestrator/pkg/sandbox/network/{pool,slot,network,host}.go`:
  default CIDRs, TAP/veth/netns creation, default-route requirement, and
  iptables/nftables behavior.
- `packages/orchestrator/pkg/sandbox/nbd/pool.go`: module discovery and pool
  behavior.
- `packages/orchestrator/pkg/sandbox/cgroup/manager.go`: cgroup v2 root and
  controller writes.
- `packages/orchestrator/pkg/sandbox/fc/{config,process,script_builder}.go`:
  artifact layout and `unshare`/mount/`ip netns exec` launch path.
- `packages/orchestrator/pkg/template/server/main.go`: local artifact registry
  and template builder initialization.
- `packages/shared/pkg/artifacts-registry/registry_local.go`: host Docker-daemon
  access for locally tagged custom images.
- `packages/shared/pkg/telemetry/{config,main,logs,metrics,traces}.go`: the
  project-specific OTel endpoint and empty-value no-op behavior.
- `packages/api/internal/handlers/store.go` and local/static discovery code:
  `host.docker.internal:5008`, combined-role discovery, and the API health
  node-count gate.
- `iac/modules/job-{orchestrator,template-manager}`: upstream `raw_exec`
  deployment and long template-build shutdown allowance.
- `iac/provider-{gcp,aws}` client startup scripts: NBD, hugepage, cache, and
  host preparation precedent.

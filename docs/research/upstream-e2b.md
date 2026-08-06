# Upstream E2B discovery

Status: Milestone 0 research, not an installation approval

Retrieved: 2026-08-06

Primary lock: [`versions.lock.yaml`](../../versions.lock.yaml)

## Decision summary

Use `e2b-dev/infra` as the backend compatibility anchor, the coordinated
`e2b` JS/Python release as the client contract, and the E2B Desktop release
commit as the desktop template/SDK source. The candidate infrastructure SHA is
`882a3b4786755db9e94be3297de6827f9100ce5e`; it is immutable but not a stable
platform release because upstream only publishes weekly and per-component tags.
It must not be promoted until it builds and passes the Milestone 2 host tests.

The official self-host path is Terraform plus Nomad/Consul on GCP or AWS.
"General linux machine" remains unchecked in the upstream support table. A
single bare-metal systemd/Compose deployment is therefore a port of upstream,
not a supported upstream topology.

## Selected revisions

| Component | Selected revision | Why |
|---|---|---|
| Backend (`e2b-dev/infra`) | `882a3b4786755db9e94be3297de6827f9100ce5e` | Current main at retrieval and first main revision consistently using the public `e2b-artifact-binaries` bucket. No platform release exists. |
| Core JS/Python SDK source | `7a1fe4528cb29ccea0334adbee4dc86fadb7244d` | Coordinated release commit containing JS `2.38.0` and Python `2.37.0`. |
| Desktop source | `8bc61a0b6a716dd5e4714d7ab5882b43e261a591` | Release commit whose manifests actually contain desktop JS `2.3.1` and Python `2.4.2`. |
| Kernel recipe | `c9212424d29b6ef11a4a6998648cb6ae75abdada` | Current official E2B kernel recipe; builds `6.1.158` from Amazon Linux sources plus an E2B patch. |
| Firecracker recipe | `f249bfcf902efa0eb67f223cea48401c22917877` | Current official E2B Firecracker build/release pipeline. |
| E2B Firecracker fork | `431f1fc7d47f4cfe1dfe42437b9394f20972b65d` | Source encoded by infra's default `v1.14.1_431f1fc`. |
| Desktop noVNC fork | `461b7f1ccb20755037d8995612e5fb08ed16f9e4` | Exact head of the template's floating `e2b-desktop` branch at retrieval. |
| websockify | `99f83ca08390dc876b1b3580c210abea5b9f4edd` | Peeled commit for the template's `v0.12.0` tag. |

The desktop package tags point at the preceding change commits, before the
release automation updates package versions. Pinning those tag commits would
produce JS `2.3.0` or Python `2.4.1`, not the advertised releases. The selected
desktop commit is the later release commit and is the reproducible source pin.

The observed repository heads were `998e560a1abb85f0e5d2c6346b5c033f81f17736`
for the core SDK and `5cf64667128b01298f0b2c5a9e3fb4ea5c490824`
for Desktop. They are recorded but not selected: the SDK head follows the last
coordinated package release, while the Desktop head contains dependency churn
after its last published package versions.

## Contract compatibility

The core SDK release records infra spec revision
`24a054bca26ec50a6d59031d9360c1582612b3f8`. Upstream rewrote/cherry-picked
part of the infra history, so that SHA is no longer on current `main`; its
equivalent change appears there under a different SHA. This is a provenance
warning, but the actual generated contracts are verifiable:

| Contract | SDK release SHA-256 | Infra candidate SHA-256 | Result |
|---|---:|---:|---|
| Public API OpenAPI | `11a0e23ec7abffc81d5e004d60697e56f49344985742638f5d331cf056a13186` | same | Exact match |
| envd OpenAPI | `dab04a541720b813e837eb803074a8ae7e5611411b79a619bddf676e7e5fb324` | same | Exact match |

This establishes source-contract alignment, not runtime compatibility. The
release still needs JS and Python tests for create/connect/kill, commands and
PTYs, file operations/watch, timeout, port routing, pause/resume, snapshots,
and template builds.

Desktop JS `2.3.1` declares core `e2b ^2.27.1`; desktop Python `2.4.2` declares
`e2b ^2.25.1`. The selected core SDKs satisfy those ranges. However, the
desktop release lockfiles resolved JS `2.32.0` and Python `2.31.0`, so the
selected `2.38.0/2.37.0` combination is allowed but was not the combination
locked by that desktop release. Promote it only after desktop controls and
streaming pass with both official SDKs.

The SDK defaults are part of the public deployment contract:

- API: `E2B_API_URL`, otherwise `https://api.<E2B_DOMAIN>`.
- Sandbox traffic: `https://<port>-<sandbox-id>.<domain>` for self-hosted
  domains. `sandbox.<domain>` is treated as stable only for E2B-hosted domains.
- envd: guest port `49983` through the same sandbox proxy.
- Desktop stream: x11vnc on guest `5900`, websockify/noVNC on guest `6080`.
  The desktop SDK allows one stream at a time.

Wildcard DNS and a wildcard certificate for `*.<sandbox-domain>` are therefore
not optional for ordinary SDK `getHost`/`get_host` compatibility. Loopback-only
development still needs a resolver strategy that preserves the hostname.

## Runtime architecture

The backend separates control and data paths:

1. The API authenticates the request, resolves a template, selects an
   orchestrator, and records durable state in PostgreSQL and running/routing
   state in Redis.
2. The root-running orchestrator restores a pre-booted Firecracker snapshot.
   It uses cgroups, a network namespace, TAP/veth networking, NBD-backed
   copy-on-write disk state, userfaultfd lazy memory, kernel/Firecracker assets,
   and local/object-storage caches.
3. envd runs inside every guest and implements command, PTY, process and file
   operations. The orchestrator initializes envd before create returns.
4. Client proxy resolves sandbox ownership from Redis and forwards traffic to
   the owning orchestrator's proxy. A catalog miss can call API gRPC to resume a
   paused sandbox.
5. The same orchestrator binary runs as `template-manager` on build nodes. It
   imports an OCI image, injects envd, provisions through Firecracker phases,
   snapshots the VM, and stores memory, rootfs, state and metadata artifacts.

Upstream deploys the API, client proxy, dashboard API, registry proxy and
observability services as Nomad jobs. Orchestrator/template-manager use Nomad
`raw_exec` because they require root and host devices. For one host, the least
invasive adaptation is:

- systemd: API, client proxy, root orchestrator, and a separately configured
  template-manager instance if port/resource isolation requires it;
- Compose, loopback-only: PostgreSQL, Redis, ClickHouse, Loki and OTel;
- local file storage initially, using upstream's `file://` provider, with
  project-owned snapshot/build caches;
- API `SERVICE_DISCOVERY_PROVIDER=local` and an explicit
  `LOCAL_ORCHESTRATOR_ADDRESS`.

That local discovery mode is documented in source as a development path for a
dummy macOS orchestrator. Using it for a real production worker is plausible
but unsupported and must be tested. It also represents only one address, which
constrains separating the sandbox and template-manager roles.

## Ports and protocols

These are upstream defaults, not final kitdev bindings. All host listeners must
be explicitly rebound or firewalled; several upstream programs listen on all
interfaces by default.

| Port | Owner | Protocol/purpose | Exposure |
|---:|---|---|---|
| `80` (AWS default) / `50001` (GCP job default) | API | Public REST API | Behind local ingress only |
| `5009` | API | Internal gRPC, including resume | Private loopback/service network |
| `5109` | API | Edge gRPC resume endpoint | Private unless a remote edge is added |
| `3002` | client proxy | Sandbox HTTP/WebSocket traffic | Behind wildcard TLS ingress |
| `3003` | client proxy | Health | Loopback/private |
| `5008` | orchestrator/template manager | gRPC lifecycle/build/volume services | Private; never public |
| `5007` | orchestrator | Sandbox reverse proxy | Private; client proxy only |
| `49983` | envd in guest | Connect/HTTP command and filesystem API | Via sandbox proxy only |
| `5010` | orchestrator bridge | Hyperloop proxy | Guest-facing/private |
| `5011` | orchestrator bridge | NFS proxy | Guest-facing/private |
| `5012` | orchestrator bridge | Port mapper | Guest-facing/private |
| `5016-5018` | orchestrator bridge | HTTP/TLS/other egress firewall proxies | Guest-facing/private |
| `3010` | dashboard API | Dashboard REST | Optional, private |
| `5000` | Docker reverse proxy | Registry auth gateway | Optional ingress |
| `5432` | PostgreSQL | Durable control state | Compose network only |
| `6379` | Redis | Runtime/routing state | Compose network only |
| `9000`, `8123` | ClickHouse | Native and HTTP | Compose network only |
| `3100` | Loki | Logs API | Compose network only |
| `4317`, `4318` | OTel collector | OTLP gRPC/HTTP | Loopback/private |
| `5900`, `6080` | Desktop guest | VNC and noVNC | Per-sandbox proxy only |

There is an upstream inconsistency worth testing: client-proxy source and the
architecture document default health to `3003`, while the GCP Terraform
variable defaults to `3001`.

Public deployment ultimately needs `443/tcp` for `api.<domain>` and wildcard
sandbox traffic; `80/tcp` is optional for redirect/ACME. WebSockets and long
idle connections must survive the ingress proxy. No datastore, orchestrator,
guest bridge, VNC or noVNC port should bind publicly.

## Host and artifact requirements

The selected orchestrator is Linux-only and root-integrated. At minimum it
requires:

- x86-64 with `/dev/kvm` and hardware virtualization;
- cgroups v2 and permission to create per-sandbox cgroups;
- NBD loaded with enough devices; upstream suggests `nbds_max=4096` and a udev
  `nowatch` rule, while the process defaults `NBD_POOL_SIZE=64`;
- configurable 2 MiB HugeTLB pages; template memory can otherwise use 4 KiB
  pages, but that path needs explicit performance/correctness validation;
- userfaultfd features used by snapshot restore and dirty-page tracking;
- network namespace, TAP/veth, routing and firewall capabilities;
- local ext4-compatible storage with enough space and inode headroom for
  rootfs, memory snapshots and caches;
- Docker/OCI image access for template imports and a Go/Linux build toolchain
  if binaries are built from source.

Selected host artifacts are kernel `vmlinux-6.1.158`, Firecracker
`v1.14.1_431f1fc`, BusyBox `1.36.1`, and envd `0.6.13`. Exact URLs, sizes,
GCS generations and SHA-256 values are in the lock file. Only BusyBox publishes
an adjacent SHA-256 file. The other hashes were calculated from the retrieved
public objects; installer use must compare against the committed lock.

The orchestrator README still describes Firecracker `v1.14.1_458ca91`, but the
selected infra code's `DefaultFirecrackerVersion` and its integration fixtures
both use `v1.14.1_431f1fc`. The executable constant and tests take precedence;
this documentation drift remains a validation risk.

The kernel object has a content pin but incomplete provenance. The public path
does not encode the `fc-kernels` commit and has no checksum/source metadata.
The current recipe dynamically selects the newest matching Amazon Linux tag at
build time, so pinning only the recipe repository is insufficient. The lock
also records the observed Amazon Linux tag and commit, but a reproducible rebuild
still needs pinned container/toolchain packages and a binary comparison.

## Required supporting services and sources

Core lifecycle requires PostgreSQL and Redis. Current API startup also requires
a Loki URL. ClickHouse is used for metrics/events and queried by API/dashboard;
object or local storage holds template and paused-snapshot artifacts. OTel is
the expected telemetry path. The selected upstream local Compose versions are
locked by manifest digest as candidates, not yet accepted production images.

The public infra source supports GCS, S3-compatible and local file storage for
template/build artifacts. This means a single-host release can avoid inventing
an object-storage adapter, although backup, concurrency and signed local upload
behavior still require tests.

Persistent volume metadata and mounts exist in public infra, but SDK file
content is served by a separate "belt" volume-content API. The SDK records
`e2b-dev/belt` commit `13ca196c19eb2acbec6b2696ce2ff98fec1ceaf8`;
that repository was not publicly cloneable at retrieval. Volume content
compatibility is therefore blocked. Version 0.1 must either disable volume
tokens and state that SDK volumes are unsupported, obtain a public/upstream
implementation, or implement the bundled OpenAPI contract with independent
security review. It must not silently route volume content to the control API.

The desktop template also has reproducibility defects that kitdev must repair:

- Ubuntu base image is `ubuntu:22.04` without a digest.
- E2B noVNC is cloned from a floating branch.
- third-party apt keys and repositories are fetched during the build;
- Chrome, Firefox and VS Code packages are unversioned;
- deprecated `apt-key` commands are used;
- the template requests 8 vCPU and 8 GiB RAM by default.

The exact noVNC and websockify commits are locked, but all package repositories,
keys, package versions and the base-image digest must be resolved before the
desktop template is reproducible.

## Ubuntu 25.04 and 26.04 implications

The requested host matrix includes Ubuntu 25.04 and 26.04. As of this research
date, Ubuntu 25.04 has been end-of-life since 2026-01-15: it receives no package
or security updates and its archive is moved to old-releases. Running hostile
AI-generated code on an unpatched host is a security blocker. It should not be
represented as a production-supported target even if preflight can recognize
or migrate it.

Ubuntu 26.04 LTS was released on 2026-04-23 and has standard maintenance through
May 2031. Docker's official Ubuntu matrix lists 26.04 but not 25.04. Upstream
E2B CI and kernel build workflows currently use Ubuntu 24.04 runners, so 26.04
still needs host validation for KVM permissions, NBD/udev behavior, HugeTLB,
userfaultfd, cgroups v2, nftables/iptables interaction, Go/Rust build tooling,
and Docker packages. Docker also documents that its firewall integration uses
iptables-nft/iptables rules and the `DOCKER-USER` chain; a kitdev nftables
design cannot assume Docker honors standalone nft rules.

The guest templates using Ubuntu 22.04 are independent of the 26.04 host
userland because they boot the E2B-supplied kernel, but host kernel behavior is
still on the Firecracker/NBD/userfaultfd critical path.

## Risks and promotion gates

1. **Unsupported topology:** no official general-Linux installer; systemd plus
   Compose and static discovery are kitdev adaptations.
2. **25.04 security:** the requested host is EOL and cannot be a secure
   production target.
3. **Volume blocker:** the referenced content service is not public.
4. **No platform release:** the infra pin is a moving-main snapshot after a
   history rewrite and must be built/tested before use.
5. **Privileged blast radius:** orchestrator is root and manipulates KVM, NBD,
   namespaces, cgroups, routing and firewall state.
6. **Artifact provenance:** kernel reproduction is incomplete; most public
   artifacts lack upstream checksum sidecars.
7. **Desktop drift:** template inputs are floating and its lockfiles tested
   older core SDK versions than the selected pair.
8. **Resource pressure:** HugeTLB reservations, snapshot caches, four templates,
   ClickHouse and desktop VMs compete on one host.
9. **Networking complexity:** wildcard TLS, WebSockets, auto-resume, Docker's
   firewall chains and sandbox egress policy all intersect.
10. **Rollback/data migration:** Postgres and ClickHouse migrations are part of
    upstream deployment; binary rollback does not imply schema rollback.

Promotion requires all of the following:

- reproduce or independently verify every downloaded artifact;
- build all required Go binaries at the locked infra commit;
- start the data stores privately and run all migrations on disposable data;
- prove a real orchestrator works with local service discovery and local
  storage on Ubuntu 26.04;
- run official JS `2.38.0` and Python `2.37.0` SDK smoke/contract tests;
- test pause/resume more than once and snapshot integrity;
- build and test desktop with both desktop SDKs and authenticated noVNC;
- verify no backend or guest-facing port is publicly reachable;
- resolve or formally defer volume content compatibility.

## Primary sources

All technical conclusions above use official project sources:

- E2B infra repository and architecture:
  <https://github.com/e2b-dev/infra/tree/882a3b4786755db9e94be3297de6827f9100ce5e>
- E2B self-host guide:
  <https://github.com/e2b-dev/infra/blob/882a3b4786755db9e94be3297de6827f9100ce5e/self-host.md>
- E2B SDK release source:
  <https://github.com/e2b-dev/e2b/tree/7a1fe4528cb29ccea0334adbee4dc86fadb7244d>
- E2B Desktop release source:
  <https://github.com/e2b-dev/desktop/tree/8bc61a0b6a716dd5e4714d7ab5882b43e261a591>
- E2B kernel recipe:
  <https://github.com/e2b-dev/fc-kernels/tree/c9212424d29b6ef11a4a6998648cb6ae75abdada>
- E2B Firecracker build recipe:
  <https://github.com/e2b-dev/fc-versions/tree/f249bfcf902efa0eb67f223cea48401c22917877>
- E2B Firecracker source commit:
  <https://github.com/e2b-dev/firecracker/commit/431f1fc7d47f4cfe1dfe42437b9394f20972b65d>
- Public E2B artifact bucket: <https://storage.googleapis.com/e2b-artifact-binaries/>
- Ubuntu 25.04 EOL notice:
  <https://discourse.ubuntu.com/t/ubuntu-25-04-plucky-puffin-reached-end-of-life-on-15th-january-2026/75079>
- Ubuntu release list and 26.04 lifecycle:
  <https://ubuntu.com/project/docs/release-team/list-of-releases/>
- Docker Engine Ubuntu support/firewall notes:
  <https://docs.docker.com/engine/install/ubuntu/>

Repository SHAs, refs, release manifests, object hashes and HTTP metadata were
re-queried on 2026-08-06. Known uncertainty is preserved in the lock's `status`
and `unresolved` fields rather than hidden behind a version number.

# Architecture

## Status and scope

This is the proposed version 0.1 architecture. It is a Milestone 0 design, not
evidence that the platform is installable. The
[host discovery](research/host-discovery.md) and pinned
[upstream E2B research](research/upstream-e2b.md) may require revisions.

Version 0.1 is a single-node deployment on x86-64, systemd, cgroups v2, and KVM.
Ubuntu 26.04 LTS is the production target. Ubuntu 25.04 remains recognized and
tested only for explicit development/migration compatibility because it is
end-of-life; production preflight must reject it without mutation. Server and
desktop editions are accepted when their observed capabilities and coexistence
checks pass. macOS is a development environment only.

## Design principles

1. Treat guest code as malicious and keep it outside the control and state
   planes.
2. Grant privilege to the smallest component that needs it.
3. Own named paths and resources instead of taking over shared host facilities.
4. Make desired state, artifact identity, and host changes machine-readable.
5. Validate before mutation and make every mutation convergent and reversible.
6. Default to loopback-only interfaces and deny private network access from
   guests.

## Logical topology

```text
Operator / E2B SDK
        |
        v
  API + client proxy                unprivileged systemd services
        |
        +--------------------+
        |                    |
        v                    v
  PostgreSQL / Redis   orchestrator + template manager
  ClickHouse / object         |      privileged worker service
  storage (Compose)           |
                              v
                    Firecracker microVMs
                    per-sandbox TAP/netns
                              |
                              v
                    filtered internet egress
```

The public data path, when explicitly enabled, terminates TLS and wildcard
sandbox routing at a reverse proxy. It never exposes the orchestrator or a
datastore directly. The exact reverse proxy remains an upstream-compatibility
decision.

## Component boundaries

### Operator plane

The future `kitdev` CLI validates configuration, invokes local Ansible, records
an installation manifest, and presents status, logs, tests, backup, update, and
uninstall workflows. A minimal Bash bootstrap may create a pinned Python
environment, but orchestration logic belongs in typed Python and Ansible.

Dry-run and `doctor` must be useful before installation. No operation may infer
ownership from a broad name prefix alone; the installation manifest and exact
project paths/resource labels are authoritative.

### Control plane

The E2B API and client proxy run as dedicated unprivileged users under systemd.
They authenticate requests, map SDK operations to sandbox lifecycle actions,
and route authenticated sandbox port traffic. They cannot access KVM, NBD,
network administration, host mounts, Docker, or production application
credentials.

### Worker plane

The orchestrator/template manager is the only service expected to require
KVM, TAP, network namespace, cgroup, mount, and possibly NBD operations. Its
actual capabilities and device allowlist will be derived from a traced,
pinned-upstream execution rather than assumed. It receives only credentials
needed for sandbox orchestration and artifact access.

Each sandbox receives a unique runtime identity, explicit CPU/memory/PID/disk/
time/output limits, a copy-on-write root filesystem, and an isolated TAP and
network namespace. Destruction after TTL is mandatory. No host socket, project
configuration, host credential, or project state path is mounted into a guest.

### State plane

PostgreSQL, Redis, ClickHouse, and any required S3-compatible storage or local
registry run in the dedicated `kitdev-sandboxes` Compose project. They use a
private Compose network and bind no administration interface publicly. Compose
is not used to launch host-integrated microVM services.

The initial storage layout is:

```text
/opt/kitdev-sandboxes/       installed release and immutable tooling
/etc/kitdev-sandboxes/       operator configuration and root-only secrets
/var/lib/kitdev-sandboxes/   databases, artifacts, templates, snapshots, workspaces
/var/log/kitdev-sandboxes/   project logs not owned by journald/containers
/run/kitdev-sandboxes/       sockets, PIDs, locks, and other ephemeral state
```

Within `/var/lib`, data is separated by ownership and lifecycle: datastore
data, object storage, registry data, immutable template artifacts, snapshots,
build and sandbox caches, persistent volumes, and backups. Uninstall preserves
durable state unless a separately confirmed purge is requested.

### Artifact plane

Source revisions, release archives, Firecracker, kernels, root filesystems, and
guest toolchains are immutable inputs. Each artifact record must contain its
origin, exact revision/version, checksum, license/provenance metadata where
available, and compatibility set. Template outputs receive an immutable ID and
must never be updated in place while a sandbox references them.

## Deployment model

Ansible converges host packages, users, directories, project-specific kernel
configuration, and systemd units. Compose converges only private state
services. The installer writes managed files atomically and backs up any
shared-host file before a structural merge.

The desired order is:

1. Parse and validate configuration without privilege.
2. Run read-only preflight and calculate a change plan.
3. Acquire an installation lock and validate ownership boundaries.
4. Prepare project users, directories, packages, and host configuration.
5. Install verified immutable artifacts.
6. Start state services, then worker, API, and proxy in dependency order.
7. Build templates separately and promote only tested outputs.
8. Run health and acceptance checks and write the installation manifest.

Each phase records completion and input hashes so a failed run can resume. A
phase may be skipped only when its inputs and observed postconditions match.

## Configuration and secrets

`config/default.yaml` defines non-secret defaults. The installed operator file
is `/etc/kitdev-sandboxes/config.yaml`; unknown keys are rejected by
`config/schema.json`. Explicit CLI flags override installed configuration for
that invocation and are recorded without secrets.

Generated secrets live in `/etc/kitdev-sandboxes/secrets.env`, mode `0600`, and
are created only when absent. Logs and manifests record secret identifiers or
hashes, never values. A normal rerun cannot rotate secrets.

## Networking

The worker owns a dedicated bridge/address pool, per-sandbox network namespace
and TAP device, and the nftables table `inet kitdev_sandboxes`. Rules are
installed and removed by exact table/chain ownership and never flush unrelated
tables.

Guest egress is deny-first for host, Docker, control plane, datastore,
RFC1918/ULA, link-local, metadata, multicast, and other sandbox destinations.
DNS is limited to configured resolvers. HTTP/HTTPS egress is profile-controlled.
IPv6 is either explicitly filtered end to end or disabled on the sandbox path;
it is never left as an unfiltered fallback.

Network isolation and basic resource limits are prerequisites for the first
untrusted guest, not deferred security enhancements.

## Updates, rollback, and recovery

Releases install side by side under `/opt`; the active release changes only
after validation. Database migrations require declared forward and rollback
compatibility. A failed health gate returns binaries and configuration to the
previous release, but a migration is never described as reversible without a
tested reverse path or restore procedure.

Backups are installation-scoped, consistent, checksummed, and restorable into a
clean compatible deployment. Restore and destructive purge require stronger
confirmation than normal convergence.

## Observability

Services emit structured logs with credential redaction and expose local health
and metrics endpoints. Required signals include active VM count, sandbox create
latency/failure, template build time, CPU/memory/disk/huge-page/NBD pressure,
API traffic, and proxy failures. Operator dashboards bind locally by default.

## Future multi-node direction

The API-facing control plane, worker capabilities, and artifact metadata use
explicit interfaces so a later scheduler can address multiple workers. Version
0.1 does not add distributed coordination, remote worker credentials, shared
storage semantics, or multi-node availability claims.

## Open decisions

- Exact upstream E2B component graph and SDK compatibility surface.
- Whether object storage and a local registry are mandatory for the selected
  upstream revision.
- Firecracker, kernel, rootfs, and template build compatibility.
- Public reverse proxy and certificate workflow.
- Minimum host sizing and safe NBD/huge-page defaults based on discovery.
- AppArmor policy and tested systemd capability bounds for the worker.
- Per-release package, kernel, Python, Docker, and upstream E2B compatibility
  across production Ubuntu 26.04 LTS and development/migration Ubuntu 25.04.

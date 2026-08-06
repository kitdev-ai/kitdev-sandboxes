# Reproducible single-host control-plane slice

Date: 2026-08-06

Status: repository implementation derived from pinned-source research and a
successful disposable-host run; clean-host apply/apply and rollback
qualification remain pending

## Scope and evidence boundary

This slice converts the working single-host control plane into project-owned
Compose, build, layout, firewall, private-environment, and systemd assets. Its
source inputs are commits `3174907`, `682cd5e`, and `71403a2`, the normalized
live-run report, and E2B infra commit
`882a3b4786755db9e94be3297de6827f9100ce5e`.

No SSH or OVH mutation was performed while creating or testing these files.
The tracked assets contain no management endpoint or credential. Fixed
loopback addresses, container service names, sandbox CIDRs, and dynamically
derived project bridge topology are configuration contracts rather than host
identity.

This does not unblock or silently replace Stages 20/40/60/70/80. The reusable
assets are inputs to those eventual journaled stages; the scripts fail closed
on unsupported or foreign state.

## Locked container graph

The Compose project is `kitdev-control-plane`. It contains PostgreSQL, Redis,
ClickHouse with a single-node `cluster` definition, Loki, PostgreSQL and
ClickHouse migrators, API, and client proxy.

Registry services use exact manifest digest references. Locally built images
are accepted only through generated `sha256:` image IDs and `pull_policy:
never`; tags are build labels, not deployment identity. The API, both
migrators, and client proxy are built from the exact clean infra commit with
digest-rewritten Go/Alpine bases. The API build is coupled to PostgreSQL
migration maximum `20260728163016`.

Only this host port set is permitted, all on `127.0.0.1`:

| Service | Ports |
|---|---|
| PostgreSQL | `5432/tcp` |
| ClickHouse | `8123/tcp`, `9000/tcp` |
| API | `3000/tcp` |
| Client proxy | `3002/tcp`, `3003/tcp` |

Redis, Loki, API gRPC, and every datastore/container administration surface
have no non-loopback host publication. The API's
`host.docker.internal` mapping uses the verified `kitdev-core` gateway, never
Docker's generic `host-gateway` token.

The ClickHouse cluster file contains no password. Its user and password are
read from the container environment through ClickHouse `from_env` attributes.
Loki uses a project-owned single-node filesystem configuration without remote
telemetry dependencies.

## Secret and generated-state boundary

The private-environment bootstrap creates fresh hexadecimal PostgreSQL,
ClickHouse, sandbox-hash, and admin-token values under
`/etc/kitdev-sandboxes`. It never prints a value and never rotates an existing
valid file. Existing aliases, unsafe ownership/modes, malformed keys, or
foreign contents fail closed.

Built image IDs and derived bridge values are generated state, not source
constants. Build replay verifies the clean source commit, rewrites only exact
expected Dockerfile lines, builds for `linux/amd64`, captures local image IDs,
and validates every ID before Compose can use it.

## Runtime artifacts and host service

The host-runtime layout separates read-only kernel, Firecracker, BusyBox, and
envd artifacts from writable orchestrator, VM, snapshot, template, and cache
areas. Every downloaded artifact is size/hash checked before publication.

The envd used by the successful run is not silently substituted with the
separate released candidate in `versions.lock.yaml`. It is rebuilt from the
pinned infra commit inside
`docker.io/library/golang:1.26.5-bookworm@sha256:6c5605ab3a9a9fb3c4eafe5b3d63cdbf3881caf113262b67862547b54a9db599`
using:

```text
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath -buildvcs=false -a -o /out/envd -ldflags "-X=main.commitSHA=882a3b4 -s -w -buildid=" .
```

A clean `git archive` reconstruction reproduced 12,927,102 bytes and SHA-256
`530d84dfbfd82c05181e0dc61ca842f3caaa349b0cc2f3f52d2d8eb9478aa67e`
exactly. Runtime publication uses `root:kitdev` mode `0750`.

The persistent orchestrator unit retains root execution because the pinned
host-runtime architecture creates namespaces, veth/TAP devices, NBD mappings,
cgroups, mounts, and firewall rules. Its environment template uses local
storage, a single combined orchestrator/template-manager process, fixed
artifact paths, and no secret or machine endpoint.

## Guest-network firewall result

The first create-build booted Firecracker and reached Debian provisioning but
could not make outbound APT TCP connections. Host forwarding, per-slot
FORWARD/MASQUERADE rules, and TCP REDIRECT rules were present. The failure was
resolved by allowing the redirected host listeners through UFW at the actual
guest ingress boundary.

The persistent rule contract is:

```text
allow in on <derived-core-bridge> from <derived-core-subnet> to <derived-core-gateway> port 5007 proto tcp
allow in on <derived-core-bridge> from <derived-core-subnet> to <derived-core-gateway> port 5008 proto tcp
route allow in on veth+ out on <derived-default-interface> from 10.11.0.0/16 to any
allow in on veth+ from 10.11.0.0/16 to any port 5010:5012 proto tcp
allow in on veth+ from 10.11.0.0/16 to any port 5016:5018 proto tcp
```

The `veth+` wildcard and outbound interface constraint were confirmed in the
effective iptables rules. Direct create-build used temporary TCP ports
`5516:5518`; those belong to a scoped build wrapper and must be removed after
the build. They are not persistent service rules.

After the corrected rules, build
`6dfbb2b8-62a2-4a2b-a62a-cf94ffcdb5e5` completed in 46 seconds. It passed base
provisioning, snapshot create/load, envd `0.6.13` initialization, and final
resume smoke using base image
`e2bdev/base:latest@sha256:4a369f01a820fe5e65f53c2c5727a78899daf86f0541b721097f289559c8b73f`.
The normalized output sizes were:

| Output | Observed size |
|---|---:|
| Root filesystem diff | 6 MB |
| Root filesystem total | 3,722 MB |
| Memory diff | 166 MB |
| Memory total | 1,024 MB |
| Header | 44 KB |
| Memory header | 4 KB |
| Snapshot | 32 KB |
| Metadata | 4 KB |

An incremental build from that snapshot then performed a Python HTTP GET to a
Debian package endpoint and printed `KITDEV_EGRESS_OK`. Build
`2d9a8389-f5f5-4449-b0eb-e1d364ee98ae` completed its snapshot/resume cycle in
3 seconds. At that verification point, UFW counters recorded accepted traffic
on the temporary `5516:5518` rule, the interface-scoped routed rule, and the
persistent `5010:5012` listener rule. This confirms the interface-scoped rule
shape, not merely the earlier source-only diagnostic rule. The temporary
`5516:5518` rule was removed afterward and no listener remained on those
ports; only the scoped route, persistent guest listeners, SSH, and the scoped
project-bridge-to-orchestrator rule remained.

The persistent build log was written below `/var/log` with the build ID in its
name. The reusable `create-build` binary used by both the failed diagnostic run
and successful rerun had SHA-256
`a6e27848401401a56d9bf712a316ac28258aac925a73a3081dd1e60edb3f258e`.

At the final captured external probe, SSH `22/tcp` remained reachable while
ports `3000`, `3002`, `5007`, `5008`, `5010`, `5016`, `5017`, `5018`, `5432`,
`8123`, and `9000` were all blocked from the public path. No endpoint details
were retained.

## Lifecycle and remaining gates

Ubuntu 26.04 is the only production-eligible target. Ubuntu 25.04 is accepted
only when the operator explicitly selects `development` or `migration`; it is
never production-eligible. Ubuntu 24.04 and all other releases fail before
mutation.

The host worker identity must use both UID and primary GID from the reserved
`61000-61999` range and have exactly `kvm` as its supplementary group. It must
not reuse container identities `101`, `999`, or `10001`. The exact top-level
datastore post-state is PostgreSQL `999:root` mode `0700`, Redis `999:root`
mode `0750`, ClickHouse `101:101` mode `0750`, and Loki `10001:10001` mode
`0750`; the worker is not a member of any datastore container group.

The disposable live host allocated the worker UID `999` before this collision
was understood. Replay now rejects that state before creating or changing any
layout path. Because UID `999` is also the verified PostgreSQL/Redis container
identity and persistent files already exist, in-place renumbering is not a
qualified remediation. Reinstall the disposable host, create the reserved
worker identity first, and replay from the clean image.

This repository pass verifies syntax, rendering, pins, paths, secret absence,
and policy behavior on the PC. It does not claim a clean-server replay,
rollback, service restart, data restore, or concurrent sandbox result. The
generated local image IDs and orchestrator binary hashes must be captured on
the target build host. Root-service hardening, dynamic firewall reversal,
state migration, and apply/apply/reinstall qualification remain required for
production promotion.

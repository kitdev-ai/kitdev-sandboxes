# OVH disposable lab services and host tuning first run

Date: 2026-08-06
Status: manually applied under explicit user approval; normalized evidence only;
stage-owned automation and rollback qualification pending

## Scope and redaction

This report preserves the later mutation-first work performed by the project
lead after Docker Engine bootstrap. It records only public-safe versions,
digests, counts, loopback ports, and fixed project paths. Lab database
credentials, public/management endpoints, unrelated device names, and private
bind-root paths are intentionally omitted. Normalized project-network topology
needed to explain the lab result is retained. No SSH was used while preparing
this tracked report.

These observations do not enable blocked stages. Host identity and KVM access
belong to Stage 20, hugepages to Stage 40, firewall policy to Stage 60, source
artifacts to Stage 70, and persistent services/migrations to Stage 80.

## Firewall and SSH boundary

UFW was installed but inactive. The approved operation applied:

```bash
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp comment 'SSH management'
ufw --force enable
```

The project lead successfully reconnected afterward. UFW was active/enabled
and Docker remained active. All later database host ports were bound only to
`127.0.0.1`.

The effective SSH policy was already:

```text
passwordauthentication=no
kbdinteractiveauthentication=no
pubkeyauthentication=yes
permitrootlogin=prohibit-password
```

No SSH configuration file was changed. Production automation must preserve a
verified management path while applying firewall changes and must not infer
that these effective values identify which configuration fragment owns them.

## Hugepages

The upstream development profile requests 2,048 2-MiB hugepages. The manual
operation created `/etc/sysctl.d/90-kitdev-sandboxes-hugepages.conf` with:

```text
vm.nr_hugepages = 2048
```

It applied the sysctl, created `/mnt/hugepages`, mounted `hugetlbfs` with
`mode=1770` and the current `kitdev` group, added this fstab record, and reloaded
systemd:

```text
none /mnt/hugepages hugetlbfs mode=1770,gid=982 0 0
```

Verification reported 2,048 total/free hugepages at 2,048 KiB and an exact
`findmnt` result. GID `982` is only this run's observed allocation. Automation
must resolve the `kitdev` group at apply time and render its actual numeric GID;
it must never pin `982`.

## Pinned source build

The E2B checkout was pinned to commit
`882a3b4786755db9e94be3297de6827f9100ce5e`; build metadata used
`COMMIT_SHA=882a3b4`.

The orchestrator build completed with these root-owned outputs:

| Artifact | Exact size |
| --- | ---: |
| `orchestrator` | 129,597,232 bytes |
| `clean-nfs-cache` | 50,355,939 bytes |

Root ownership is the observed lab result, not the desired production service
ownership. At the last captured point, the API source-image build was running
from `packages/api/Dockerfile` for `linux/amd64`, with the same short commit and
a migration timestamp derived from `packages/db/migrations`. Its completion,
digest, and reproducibility evidence were not yet captured.

The `envd` guest artifact was then built from the same pinned source with a
digest-pinned Go `1.26.5` Alpine builder, explicit static production flags, and
`main.commitSHA=882a3b4`. The installed artifact is:

| Property | Exact result |
| --- | --- |
| Path | `/var/lib/kitdev-sandboxes/data/artifacts/bin/envd-882a3b4` |
| Owner/mode | `root:kitdev`, `0750` |
| Size | 12,927,102 bytes |
| SHA256 | `530d84dfbfd82c05181e0dc61ca842f3caaa349b0cc2f3f52d2d8eb9478aa67e` |
| Runtime version | `0.6.13` |

Two diagnostic attempts stopped before compilation: host Git rejected the
bind-mounted checkout as dubious ownership, then the pinned builder image was
found not to contain `make`. The successful attempt invoked the pinned Go tool
directly. Automation must not depend on host Git trusting container ownership
or assume a language builder image contains Make.

## Persistent database containers

The project lead started these Docker containers with
`restart=unless-stopped`, persistent bind directories on the data disk, and
loopback-only published ports:

| Container | Image and exact digest | Loopback ports | Persistent area |
| --- | --- | --- | --- |
| `kitdev-postgres` | `postgres:17.4@sha256:304ab813518754228f9f792f79d6da36359b82d8ecf418096c636725f8c930ad` | `5432` | `data/postgres` |
| `kitdev-redis` | `redis:7.4.6@sha256:a9cc41d6d01da2aa26c219e4f99ecbeead955a7b656c1c499cce8922311b2514` | `6379` | `data/redis` |
| `kitdev-clickhouse` | `clickhouse:25.4.5.24@sha256:ad201eec325abb23e558e344d46d81bc9e2eba5a011fc02af440c124a27a1a61` | `8123`, `9000` | `data/clickhouse` |

PostgreSQL used `PGDATA=/var/lib/postgresql/17/docker`. Redis ran
`redis-server --appendonly yes`. PostgreSQL accepted connections, Redis
returned `PONG`, ClickHouse reported `25.4.5.24`, and socket inspection confirmed
all published ports were loopback-only. Dedicated weak lab credentials were
used for PostgreSQL and ClickHouse but are not tracked here.

## Database migration and seed results

A migrator image, `kitdev/e2b-db-migrator:882a3b4`, was built from the pinned
`packages/db/Dockerfile`. A one-shot host-network run reached loopback
PostgreSQL and applied 129 migrations through `20260728163016`; the normalized
query result was `129|20260728163016`.

The first ClickHouse migration applied through `20250521131545`, then failed at
a distributed table because the default image did not define cluster
`cluster`. This was a correct fail-closed dependency error, not a completed
migration. The project lead generated
`/etc/kitdev-sandboxes/clickhouse/config.xml` from pinned
`packages/clickhouse/local/config.tpl.xml`, mounted it read-only under the
container's `config.d`, and restarted ClickHouse. The bind source required mode
`0644`; mode `0640` owned by root and `kitdev` was unreadable to the image's
internal ClickHouse user. The tracked report omits the rendered lab credential.

`system.clusters` then reported cluster `cluster`, local endpoint port `9000`,
and `is_local=1`. The migration rerun completed with 28 applied migrations,
maximum `20260702181515`, and 20 tables.

The first pinned Go `1.26.5` Alpine seed attempt mounted the canonical E2B
checkout read-only. It made no seed mutation because `go run` attempted to
update `/src/go.work.sum` and failed. The retry used an isolated writable copy
at `data/build-cache/seed-src-882a3b4`, retaining the pinned checkout unchanged,
and persistent Go caches under `data/build-cache`. The idempotent local-dev seed
then completed; normalized verification found the local development team and
base template records.

Future automation should build a dedicated pinned seed image or stage a
disposable writable source copy. It must not assume `go run` is source-read-only.

## Lab control plane

Redis was recreated without a published host port on Docker network
`kitdev-core`. Loki `3.4.1` was started digest-pinned on that network with no
host port; its persistent directory was owned by container UID `10001` during
final verification, and its ready check passed. The exact Loki digest was not
included in the normalized facts supplied for this report and remains a
required evidence item.

The project lead created `/etc/kitdev-sandboxes/e2b-lab.env` as root-owned mode
`0600`. At creation it contained freshly generated lab-only sandbox-hash and
admin-token values with 32 random bytes represented as hex. Their values were
never output and are not tracked.

The orchestrator and `clean-nfs-cache` outputs were installed into the managed
`runtime/orchestrator` area. Docker network `kitdev-core` used subnet
`172.18.0.0/16` and bridge `br-10f4c6294b40`; this did not overlap the default
E2B `10.11` and `10.12` address ranges. UFW received one scoped rule:
allow source `172.18.0.0/16` on that bridge to destination `172.18.0.1` at
TCP port `5008`. The rule granted no access from a public interface.

A transient systemd unit named `kitdev-orchestrator-lab` started the combined
orchestrator/template-manager with the reviewed explicit environment,
`ENVIRONMENT=prod`, local namespace/storage settings, 16 NBD devices, and the
locked artifacts. At the final captured point it ran as root, which is accepted
only for this disposable exercise and remains a Stage 70/80 production blocker.
The unit was active, had created `/sys/fs/cgroup/e2b` and 32 network namespaces,
and its host `/health` endpoint returned `200` during final verification.

The API ran pinned commit `882a3b4` on `kitdev-core`, publishing only
`127.0.0.1:3000`. It connected to the verified PostgreSQL migration state,
Redis, Loki, and ClickHouse, used explicit fresh lab secrets, and set
`VOLUME_TOKEN_ENABLED=false`. Docker's `host-gateway` unexpectedly resolved
`host.docker.internal` to `172.17.0.1` even though the API was attached to
`kitdev-core`, so the first orchestrator request timed out. Recreating the API
with the explicit mapping `host.docker.internal:172.18.0.1` restored
container-to-orchestrator health. The API `/health` then returned `200` during
final verification.

The client proxy ran the same pinned commit and published only
`127.0.0.1:3002` and `127.0.0.1:3003`; its `/health` returned `200` during final
verification. At that point PostgreSQL and ClickHouse were loopback-published
for lab diagnostics, while Redis and Loki were internal-only. Final
API/client-proxy image digests were not supplied with the normalized
observations and must be captured before qualification.

Automation must discover and verify the selected Docker network, bridge,
gateway, and non-overlap state instead of assuming `host-gateway` selects the
gateway of the attached user-defined network. Its firewall rule must remain
bound to the verified bridge/source/destination tuple and be journaled with an
exact reverse operation.

## Rollback and automation gates

These mutations are not covered by a shared transaction journal. Container
removal alone would not restore database files, migration state, build caches,
firewall rules, hugepage allocation, sysctl/fstab changes, generated
ClickHouse configuration, or source/build artifacts. No automated reverse plan
is authorized by this report; the disposable-host reset remains an OVH
operating-system reinstall.

Each owning stage needs exact pre-state inventory, component-bundle approval,
atomic writes, durable journaling, idempotent apply, tested reverse operations,
and apply/apply/rollback/rollback qualification. Credential delivery must move
to a private secret boundary, the ClickHouse config visibility issue must be
resolved without tracking a password in a world-readable host file, and all
image builds must record final content digests before production promotion.

# Host discovery: `kit@pc`

Status: Milestone 0, read-only inspection

Observed: 2026-08-06 07:27 UTC (12:57 IST)

## Executive verdict

**GO for development/migration work; NO-GO as a production host because the
installed Ubuntu release is end-of-life.**

The hardware and kernel have the core virtualization capabilities: x86-64,
bare metal, AMD-V, loaded KVM modules, `/dev/kvm`, systemd, cgroups v2, and a
working Docker Engine/Compose installation. Ubuntu 25.04 is now an explicitly
supported OS version, alongside Ubuntu 26.04. The observed OS version therefore
passes the revised version requirement.

This is an **Ubuntu Desktop** installation. The `ubuntu-desktop` and
`ubuntu-desktop-minimal` metapackages and GDM are installed, the default
boot target is graphical, and GDM is active. Under the capability-based support
policy, Desktop passes because the required virtualization, init, cgroup,
container, and networking capabilities are present. GDM is a coexistence and
capacity warning that must be covered by preflight and tests, not an edition
blocker.

The SSH account `kit` also cannot currently read or write `/dev/kvm` and is not
a member of the `kvm` group. This is a host-preparation requirement for the
eventual worker identity, not an OS-version failure.

Official lifecycle research supplied to this audit reports that Ubuntu 25.04
reached end-of-life on 2026-01-15. The host can be used for development and
migration qualification, but it must not be accepted as a production-security
baseline. No host changes are permitted until Milestone 0 is reviewed.

## Discovery constraints

This inspection made no host changes. It did not install packages, edit files,
start or stop services, alter Docker resources, change firewall rules, reboot,
or inspect secrets. Privilege escalation was used only for the two read-only
commands expressly allowed by `PROMPT.md`: `nft list ruleset` and `ss -tulpn`.

The report deliberately omits process IDs, machine IDs, boot IDs, hardware
serials, container internals, environment variables, and credentials.

## Compatibility matrix

| Requirement | Finding | Result |
| --- | --- | --- |
| Ubuntu 25.04 or 26.04 | Ubuntu 25.04 | Pass |
| Capability-based edition support | Ubuntu Desktop metapackages and active GDM; required capabilities present | Pass; coexistence/capacity warning |
| x86-64 | `x86_64` | Pass |
| Bare metal | `systemd-detect-virt` reports `none` | Pass |
| systemd boot | systemd 257; default target is `graphical.target` | Pass for init system; coexistence warning |
| Intel VT-x or AMD-V | AMD Ryzen 9 7950X; AMD-V and `svm` CPU flag | Pass |
| KVM | `kvm_amd` and `kvm` loaded; `/dev/kvm` exists | Pass at host level |
| Runtime KVM access | `/dev/kvm` is `root:kvm` mode `0660`; `kit` has neither read nor write access | **Fail for current account** |
| cgroups v2 | `/sys/fs/cgroup` filesystem is `cgroup2fs`; Docker reports cgroup v2/systemd | Pass |
| Docker | Engine 29.2.1, linux/amd64, overlayfs, six running containers | Pass; coexistence required |
| Docker Compose | v5.0.2 | Pass; version compatibility still needs preflight bounds |
| TUN/TAP prerequisite | `/dev/net/tun` exists and is mode `0666` | Pass |
| NBD prerequisite | `nbd` is not loaded and no `/dev/nbd*` devices exist | Conditional host preparation required |
| Huge pages | zero configured; base huge-page size is 2 MiB | Conditional host preparation required |
| Firewall framework | nftables 1.1.1; UFW active/enabled; Docker and custom rules present | Pass with high coexistence risk |
| Storage | ext4 root volume, about 777 GiB free of 1.8 TiB | Adequate for prototype; capacity policy required |
| Memory | 60 GiB total, about 50 GiB available at observation time; no swap | Adequate for prototype; exhaustion risk |

### Release lifecycle qualification

Official Ubuntu lifecycle validation supplied by upstream research reports that
Ubuntu 25.04 reached end-of-life on 2026-01-15. A supporting citation is pending
in `docs/research/upstream-e2b.md`; this host audit did not browse independently. The
version remains accepted by the product requirement, but this particular host is
development/migration-only. It cannot provide a production security baseline
through normal maintenance, and matching an allowed version string is not
sufficient to make it production-safe. Preflight must report the lifecycle state
separately from version compatibility and prevent production deployment on an
end-of-life release.

Ubuntu 26.04 is allowed by the revised product requirement, but nothing collected
from this 25.04 host validates 26.04 behavior. Its release status, support tier,
kernel/KVM behavior, package names, Docker/Compose availability, and pinned E2B
compatibility remain qualification assumptions. Validate them on a clean 26.04
image matching the supported edition and confirm the authoritative lifecycle
before claiming support.

## Host findings

### Compute and memory

- CPU: AMD Ryzen 9 7950X, 16 physical cores / 32 logical CPUs, one NUMA node.
- Virtualization: AMD-V is enabled and KVM is loaded.
- RAM: 60 GiB total, 10 GiB used and 50 GiB available when observed.
- Swap: none.
- Kernel: `6.14.0-37-generic` on x86-64.
- AppArmor is enabled and active. Docker reports AppArmor, its built-in seccomp
  profile, and cgroup namespaces as security options.

### Storage

- Root: `/dev/nvme0n1p2`, ext4, 1.8 TiB total, 962 GiB used, 777 GiB free (56%).
- EFI: `/dev/nvme0n1p1`, approximately 1 GiB.
- A 110.7 GiB ext4 partition exists on `sdb` but is not mounted.
- A separate 1.8 TiB `sda` contains NTFS and FAT partitions and is not mounted.
- Docker reports approximately 36.33 GiB of images, 19.83 GiB of build cache,
  and 713.6 MiB of local volumes. These are existing resources and must not be
  pruned or repurposed.

No unmounted disk is assumed available to this project. Storage allocation must
be an explicit operator decision with a free-space reserve and snapshot/template
quota.

### Existing Docker estate

Docker has six running containers:

| Container | Purpose/image | Published port |
| --- | --- | --- |
| `litellm-proxy` | LiteLLM proxy | none reported by Docker |
| `litellm-postgres` | PostgreSQL 16 | `127.0.0.1:15432 -> 5432` |
| `litellm-redis` | Redis 7 | `127.0.0.1:16379 -> 6379` |
| `vllm-grafana` | Grafana | none reported by Docker |
| `vllm-prometheus` | Prometheus | none reported by Docker |
| `gbrain-postgres` | pgvector/PostgreSQL 17 | `127.0.0.1:5432 -> 5432` |

Existing Docker networks and subnets:

| Network | Subnet |
| --- | --- |
| default `bridge` | `172.17.0.0/16` |
| `monitoring_default` | `172.23.0.0/16` |
| `litellm-proxy_default` | `172.24.0.0/16` |

There are six existing local volumes, including data for GBrain PostgreSQL,
LiteLLM PostgreSQL/Redis, and monitoring. All containers, networks, volumes,
images, and build cache are pre-existing protected resources.

### Existing host services and project-name collision

Relevant active services include Docker, containerd, GDM, GNOME Remote Desktop,
Ollama, GBrain HTTP/worker services, and a custom vLLM stack. Grafana,
Prometheus, LiteLLM, vLLM, and PostgreSQL/Redis workloads are also listening or
running in containers.

The host already uses the proposed project name in resources that do not belong
to this repository:

- network interface `kitdev` on `10.77.0.0/24`;
- `kitdev-vllm-firewall.service`;
- `kitdev-vllm-backup.service`;
- nftables chain `KITDEV_VLLM`.

Milestone 1 must not infer ownership from a `kitdev` name prefix. It needs an
installation ID and an ownership manifest; it may mutate only resources created
and recorded by this installer. New unit, interface, nftables, Compose, user,
group, and directory names must be checked against existing names before use.

## Listening ports and conflicts

Stable or attributable TCP listeners observed:

| Bind | Owner/purpose | Conflict assessment |
| --- | --- | --- |
| `0.0.0.0:69`, `[::]:69` | SSH | Preserve; SSH is on a nonstandard port |
| `*:3000` | Grafana | **Direct conflict** with a common E2B API default |
| `0.0.0.0:4000` | LiteLLM | Occupied; do not select |
| `127.0.0.1:5432` | GBrain PostgreSQL container | **Direct conflict** if E2B PostgreSQL is published on the default host port |
| `0.0.0.0:8000` | vLLM | Occupied and governed by custom nftables policy |
| `*:9090` | Prometheus | Occupied; do not select for project observability |
| `*:11434` | Ollama | Occupied; preserve |
| `<host-lan-address>:3131` | Bun service | Occupied on the host's LAN address; preserve |
| `127.0.0.1:15432` | LiteLLM PostgreSQL | Occupied; preserve |
| `127.0.0.1:16379` | LiteLLM Redis | Occupied; preserve |
| `127.0.0.1:631`, `[::1]:631` | CUPS | Occupied locally; desktop service |
| `127.0.0.1:61385` | local query engine | Occupied, likely dynamic |
| several `127.0.1.1` and high ports | vLLM engine | Treat as dynamic and do not hard-code assumptions |

UDP listeners include DNS on port 53, mDNS on 5353, DHCPv6 client traffic, and
unattributed port 26679. High ports used by mDNS/vLLM are dynamic and should not
be treated as reservations.

The pinned upstream E2B port matrix must be completed before final assignments.
At minimum, the Compose state plane should remain on a private network with no
host publication. API/proxy ports must be configurable and preflight must fail
before making changes if any selected bind is occupied. Binding only a new
address does not remove the `*:3000`, `0.0.0.0:4000`, `0.0.0.0:8000`, `*:9090`,
or `*:11434` conflicts.

## Firewall and network coexistence

UFW is active and enabled. nftables contains:

- Docker-managed IPv4 filter, NAT, and raw tables and IPv6 filter/NAT tables;
- a host `INPUT` chain with accept policy;
- a host `FORWARD` chain with drop policy that traverses `DOCKER-USER` and
  Docker forwarding chains;
- custom `DOCKER-USER` allow rules for the existing LAN, Docker bridges, and
  selected interfaces followed by a final drop;
- `KITDEV_VLLM`, which filters TCP/8000 and allows loopback, a specific peer on
  the `kitdev` interface, and the LAN before dropping other sources;
- warnings that Docker's nftables compatibility tables are managed by
  `iptables-nft` and must not be edited directly.

Existing routed networks are `10.77.0.0/24`, `172.17.0.0/16`, `172.23.0.0/16`,
`172.24.0.0/16`, and LAN `192.168.29.0/24`. Sandbox and control-plane networks
must avoid them and must also be checked against all private routes at install
time. The existing final drop in `DOCKER-USER` may block newly created bridges
until narrowly scoped rules are added.

Future firewall work must use project-owned chains with deterministic jump
points, must coexist with UFW and Docker, and must never flush or replace a
built-in or foreign chain/table. IPv4 and IPv6 behavior must both be tested.

## Required changes before installation

These changes are recommendations only; none were applied.

### Hard prerequisites

1. Use a release receiving security maintenance for production. This Ubuntu
   25.04 host is development/migration-only because it reached EOL on 2026-01-15;
   plan an upgrade or reprovisioning path to a maintained supported version.
2. Retain GDM and the other desktop services. Include their resource use,
   listeners, and network behavior in capacity and coexistence tests; their mere
   presence is not a reason to reinstall the host.
3. Define the orchestrator service account and grant only that account the
   required KVM access, normally through controlled `kvm` group membership and
   device policy. Do not grant broad KVM access to unrelated API/proxy services.
4. Select non-conflicting API/proxy/observability binds after the pinned upstream
   E2B port inventory is complete. Keep PostgreSQL, Redis, ClickHouse, object
   storage, and orchestrator ports private.
5. Select network CIDRs that do not overlap existing host routes or Docker
   networks and validate them again during preflight.
6. Adopt resource ownership metadata that distinguishes this project from the
   existing `kitdev` interface, units, and firewall chain.

### Conditional on the pinned E2B implementation

1. Load and persist `nbd` with the required device/partition count if template
   building or snapshot operations use network block devices.
2. Configure huge pages only if measurements or the pinned Firecracker/E2B path
   require them; none are configured now.
3. Add narrowly scoped nftables rules for sandbox TAP interfaces and egress only
   after the network isolation ADR defines ownership, order, IPv6 handling, and
   rollback.
4. Establish storage quotas/reserves for images, templates, snapshots, logs, and
   persistent workspaces. Do not claim the unmounted disks without approval.
5. Define memory admission control for a no-swap host so sandboxes cannot starve
   existing vLLM, Ollama, databases, monitoring, or desktop workloads.

## Risks and blockers

1. **Ubuntu 25.04 EOL (production blocker):** official lifecycle research reports
   EOL on 2026-01-15. Version compatibility does not make this host suitable for
   production security. Restrict it to development/migration work.
2. **Ubuntu 26.04 unqualified on this host (high):** 26.04 is allowed by policy,
   but its package, kernel, Docker, and E2B behavior has not been exercised by
   this 25.04 audit. Test a clean 26.04 target separately.
3. **KVM authorization (blocker on this account):** KVM exists, but `kit` cannot
   use it. The eventual privileged worker identity is not yet defined.
4. **Foreign `kitdev` resources (high):** prefix-based cleanup or idempotency
   logic could damage an unrelated vLLM/WireGuard setup.
5. **Firewall composition (high):** UFW, Docker, iptables-nft compatibility rules,
   and custom final-drop logic all coexist. Incorrect rule order could either
   break current workloads or expose sandbox/control-plane traffic.
6. **Port contention (high):** ports 3000 and 5432 already collide with likely
   defaults; several other common service ports are occupied.
7. **Shared-host exhaustion (high):** no swap and substantial AI workloads make
   CPU, memory, and disk admission control mandatory before untrusted execution.
8. **NBD not prepared (medium):** no module/devices are currently available.
9. **Desktop/network coexistence (medium):** Wi-Fi is the active uplink and the
   host runs NetworkManager, GDM, GNOME Remote Desktop, Avahi, CUPS, and other
   desktop services that expand coexistence and capacity testing.
10. **Moving dependencies (medium):** several existing containers use unpinned
   `latest` images. The installer must not rely on their stable behavior and must
   not modify them.

## Exact commands executed

The required baseline commands were executed over `ssh kit@pc`:

```bash
cat /etc/os-release
uname -a
lscpu
free -h
lsblk
df -h
docker version
docker compose version
systemctl --version
stat -fc %T /sys/fs/cgroup
ls -l /dev/kvm
sudo -n nft list ruleset
sudo -n ss -tulpn
```

The following supplemental read-only commands were also executed:

```bash
hostname
id -un
date -u +%Y-%m-%dT%H:%M:%SZ
id
test -r /dev/kvm
test -w /dev/kvm
hostnamectl
systemctl get-default
cat /proc/cmdline
dpkg-query -W ubuntu-desktop ubuntu-desktop-minimal gdm3
systemctl is-enabled display-manager.service
systemctl is-active display-manager.service
lsblk -o NAME,SIZE,TYPE,FSTYPE,FSVER,MOUNTPOINTS
findmnt -no SOURCE,FSTYPE,OPTIONS /
grep -E '^(kvm|kvm_amd|nbd) ' /proc/modules
ls -l /dev/nbd*
grep -E '^(HugePages|Hugepagesize|Hugetlb)' /proc/meminfo
docker info --format 'ServerVersion={{.ServerVersion}} StorageDriver={{.Driver}} CgroupDriver={{.CgroupDriver}} CgroupVersion={{.CgroupVersion}} RootDir={{.DockerRootDir}} Containers={{.Containers}} Running={{.ContainersRunning}} Images={{.Images}} LiveRestore={{.LiveRestoreEnabled}}'
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
docker network ls --format 'table {{.Name}}\t{{.Driver}}\t{{.Scope}}'
docker volume ls --format 'table {{.Name}}\t{{.Driver}}'
docker network inspect bridge litellm-proxy_default monitoring_default --format '{{.Name}} subnets={{range .IPAM.Config}}{{.Subnet}} gateway={{.Gateway}} {{end}}'
docker system df
systemctl list-units --type=service --state=running --no-pager --no-legend
systemctl list-unit-files --type=service --no-pager --no-legend
command -v nft
nft --version
command -v ufw
systemctl is-active ufw.service
systemctl is-enabled ufw.service
ip -brief address
ip route show
ip link show type bridge
ip link show kitdev
ls -l /dev/net/tun
systemd-detect-virt
cat /sys/module/apparmor/parameters/enabled
docker info --format 'SecurityOptions={{json .SecurityOptions}}'
systemctl is-active apparmor.service
systemctl is-enabled apparmor.service
systemctl status --no-pager --lines=0 docker-lan-only-firewall.service kitdev-vllm-firewall.service kitdev-vllm-backup.service
systemctl show docker-lan-only-firewall.service kitdev-vllm-firewall.service -p Id -p FragmentPath -p ActiveState -p SubState -p ExecStart --no-pager
sudo -n ss -H -lntup4
sudo -n ss -H -lntup6
```

Some commands included `2>/dev/null` and/or `|| true` in the SSH wrapper so an
absent optional package, unit, or device would be recorded without aborting the
rest of the read-only probe. `sudo -n` prevented an interactive password prompt.

## Milestone 0 conclusion

Host discovery is complete. Ubuntu 25.04 and the Desktop edition both pass the
revised capability-based compatibility policy. GDM and the other foreign
services are coexistence constraints, not blockers. The host is nevertheless
development/migration-only because Ubuntu 25.04 reached EOL on 2026-01-15; a
maintained supported release is required for production. The exact next
host-facing step remains Milestone 1 preflight after review, including separate
version, lifecycle, capability, KVM-access, port, route, and coexistence checks.
No changes were applied to `kit@pc`.

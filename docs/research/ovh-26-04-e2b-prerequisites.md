# Ubuntu 26.04 prerequisites for the disposable E2B lab

Date: 2026-08-06

## Scope and method

This report defines the package and host-configuration sequence to qualify the
disposable OVH Ubuntu 26.04 x86-64 bare-metal server for the pinned E2B
infrastructure. It uses official Ubuntu, Docker, Linux, Firecracker and E2B
sources plus the repository's pinned revisions. No server was contacted or
changed during this work. No endpoint, address, credential, host key or private
inventory value is recorded here.

The recommendation is deliberately a qualification plan, not an installation
approval. E2B does not publish a supported general-Linux or single-host
installer. The proposed systemd/Compose deployment remains a kitdev port that
must earn support through reproducible tests on Ubuntu 26.04.

## Conclusions

1. Direct Ubuntu 26.04 on x86-64 bare metal is the simplest valid target.
   Nested virtualization is unnecessary and would introduce another KVM and
   networking boundary.
2. Docker Engine now officially lists Ubuntu Resolute 26.04 LTS and amd64. The
   official apt repository and exact-version packages are the supported path.
3. Firecracker needs KVM and read/write access to `/dev/kvm`; it does not need
   QEMU or libvirt. Installing Ubuntu's QEMU/HWE or libvirt stacks would add
   services, networking and package state without satisfying an E2B runtime
   dependency.
4. E2B additionally depends on host NBD devices, cgroups v2, 2 MiB HugeTLB
   pages, userfaultfd behavior, TUN/TAP, network namespaces, routing/firewall
   control and local storage with substantial byte and inode headroom.
5. Ubuntu 26.04 support is not established by Docker support alone. Current E2B
   CI/kernel recipes and Firecracker's documented Ubuntu example use 24.04.
   Every kernel-facing function must therefore pass on the exact installed
   26.04 kernel before the host can be called compatible.

## Support boundary

The pinned E2B self-host guide describes Terraform deployment and lists Packer,
Terraform 1.7.5, Go, Docker, Docker Buildx and npm as deployment/build tools. Its
supported provider matrix marks GCP supported and AWS beta; General Linux is
unchecked. Packer and Terraform are therefore requirements for reproducing the
official cloud topology, not requirements for running the proposed single-host
port.

Firecracker supports x86-64 Linux and requires the KVM kernel module. Its
production guidance also requires the jailer or equally restrictive process
constraints, a dedicated unprivileged identity, cgroup/resource limits,
bounded serial output, current kernel/firmware/microcode and a deliberate swap
policy. E2B's orchestrator is root-integrated, so a successful unjailed
Firecracker smoke test is only a functional gate, not a production isolation
claim.

Ubuntu 26.04 offers both its base virtualization stack and a separately updated
HWE virtualization stack. They are mutually exclusive. Neither QEMU stack is a
Firecracker prerequisite; defer both unless a separately approved diagnostic
needs QEMU. Similarly, `cpu-checker` can provide `kvm-ok`, but the project can
test CPU flags, modules, `/dev/kvm` permissions and a real pinned Firecracker
boot directly.

## Prerequisite matrix

| Area | Required state | Package consequence | Qualification evidence |
| --- | --- | --- | --- |
| Platform | Ubuntu 26.04 LTS, `x86_64`, bare metal | None | Exact OS, architecture and kernel recorded; no VM/hypervisor layer detected |
| KVM | Vendor KVM module loaded and `/dev/kvm` usable by the worker | Kernel-provided; do not add QEMU/libvirt | Pinned Firecracker boots a disposable microVM; negative permission test fails closed |
| NBD | Kernel `nbd` module supports the approved device pool | Kernel-provided; `nbd-client` and `qemu-utils` are not runtime requirements | Module parameters, device count, attach/detach, udev behavior and cleanup verified |
| Memory | 2 MiB HugeTLB pages and required userfaultfd operations work | Kernel-provided | Reservation survives reboot; snapshot create/resume loop succeeds without leaks |
| Isolation | Unified cgroups v2 plus mount, PID, network and user namespaces needed by the worker | Normally base OS | Limits are observable inside and outside a sandbox; teardown removes all state |
| Network | TUN/TAP, veth, netns, forwarding, NAT and egress policy | Normally `iproute2`, `iptables`, `kmod`; inventory before adding | Sandbox reachability/denial matrix and clean rollback pass |
| Containers | Docker Engine, containerd, Buildx and Compose v2 from Docker's apt repository | `docker-ce`, `docker-ce-cli`, `containerd.io`, `docker-buildx-plugin`, `docker-compose-plugin` | Exact apt versions and repository key fingerprint recorded; digest-pinned containers pass |
| Storage | Project-owned, ext4-compatible local filesystem with byte and inode headroom | No formatting package implied | Mount source/type/options, free bytes/inodes, discard and crash-recovery tests pass |
| Build | Pinned Go and Node/npm only when building upstream assets | Resolve from locked toolchain policy, not distro `latest` | Rebuilt binaries match locked inputs or independently recorded hashes |
| Data plane | Private Postgres, Redis, ClickHouse, Loki and OTel services | Digest-pinned OCI images; no public host ports | Migrations, health, persistence and backup/restore pass on disposable data |

`nbd-client` is a user-space TCP NBD client and `qemu-utils` supplies QEMU disk
tools. E2B's dependency is the kernel NBD interface used by its Go worker. Do
not install either package unless a traced, pinned build or diagnostic command
actually requires it.

## Stage-by-stage recommendation

### Stage 00: immutable baseline

Install nothing. Capture the exact apt sources and pins, installed packages,
kernel and module availability, CPU virtualization flags, cgroup mount, KVM and
TUN device ownership, NBD parameters, supported huge-page sizes, userfaultfd
sysctls, swap, storage type/capacity/inodes, routes, forwarding sysctls,
listeners and firewall fingerprints. Record only normalized redacted evidence.

Stop if the server is not Ubuntu 26.04 x86-64 bare metal, KVM is unavailable,
the root filesystem is unsuitable for project state, an unexpected workload is
present, or the package/repository baseline cannot be reproduced.

### Stage 10: repository and bootstrap tools

After review, install only the bootstrap dependencies required by Docker's
official repository flow: `ca-certificates` and `curl`. Reuse base `kmod`,
`iproute2`, `iptables`, `util-linux` and `procps` only after inventory confirms
their versions and commands; add a missing package explicitly rather than
assuming it exists. `git`, `make` and `jq` belong to a later build/operator-tool
set, not the runtime minimum.

Configure Docker's official apt key and `docker.sources` exactly as documented.
Resolve the complete version strings available for Resolute/amd64, write them
to the project lock before installation and retain the previous apt state for
recovery. Do not use Docker's convenience script.

Stop if the repository does not publish a coherent Resolute/amd64 package set,
the signing key cannot be verified, or any selected package is unpinned.

### Stage 20: identities and privilege boundary

Create project-owned system identities only through the reviewed, journaled
automation. Keep API, proxy, datastore and telemetry processes unprivileged.
Give only the worker boundary the capabilities or privileged helper needed for
KVM, NBD, mounts, cgroups, TAP/netns and firewall operations. Do not make human
accounts permanent members of `docker`, `libvirt` or broadly privileged
groups. Plan a dedicated Firecracker jailer UID/GID strategy before hostile or
multi-tenant code is accepted.

Stop until exact systemd units, writable paths, device access and rollback of
every identity/group change have independent review.

### Stage 30: project storage

Use a dedicated project-owned filesystem or mount for root filesystems, memory
snapshots, template artifacts, caches and local object storage. Prefer ext4 for
the first qualification because upstream expects ordinary Linux block/file
semantics and ext4-compatible local storage; do not format or repartition a
disk based only on device order or size.

Upstream publishes no credible single-host capacity minimum. Its GCP example's
quota request of 2,500 GB persistent SSD and 24 CPUs describes a cloud
deployment envelope, not a bare-metal minimum. Size this host from measured
template/rootfs/snapshot amplification, concurrent sandbox memory, image cache,
database retention and at least one local recovery copy. Define explicit
minimum free-byte and free-inode gates before any build or sandbox start.

### Stage 40: kernel facilities

Load the vendor KVM module and `nbd` only after the baseline confirms that the
installed Ubuntu kernel provides them. Persist the reviewed NBD device count;
the pinned E2B source suggests `nbds_max=4096`, while its application pool
defaults to 64. Treat 4,096 as an address-space ceiling, not a concurrency
target. Set the worker pool from an approved concurrency budget and prove that
all devices detach after crashes. The pinned upstream rule is
`ACTION=="add|change", KERNEL=="nbd*", OPTIONS:="nowatch"` in
`/etc/udev/rules.d/97-nbd-device.rules`; reproduce it through managed
configuration only after testing reload, trigger and rollback behavior on
Ubuntu 26.04.

Reserve 2 MiB huge pages from a measured formula based on concurrent template
memory, with host/datastore headroom; do not copy a fixed count from a cloud
example. Verify allocation and release across reboot and repeated snapshot
cycles. Test the actual userfaultfd operations used by snapshot restore; a
readable sysctl alone is insufficient. Confirm unified cgroups v2 and run
resource-limit tests.

Do not weaken Firecracker seccomp to make a test pass. A seccomp failure on the
26.04 host is a compatibility defect to resolve or pin around, not a reason to
use `--no-seccomp`.

### Stage 50: Docker Engine and Compose

Before install, detect and resolve Docker's documented conflicts:
`docker.io`, `docker-compose`, `docker-compose-v2`, `docker-doc`,
`docker-buildx`, `podman-docker`, `containerd` and `runc`. Removal is a separate
reviewed mutation because another workload may own them.

Install one exact coherent version of `docker-ce`, `docker-ce-cli`,
`containerd.io`, `docker-buildx-plugin` and `docker-compose-plugin`. Record apt
versions, package hashes and daemon/containerd versions. Configure a
project-owned data root only after Stage 30, set bounded logging, and verify
restart behavior. Pull application and datastore images by manifest digest.

Docker may start automatically after apt installation, so the before/after,
listener and firewall evidence must surround the package transaction. Do not
publish any service until the network stage is approved.

### Stage 60: network and firewall

Keep the host's management path separate from project bridges. Allocate
non-overlapping private ranges for Docker and per-sandbox TAP/netns networks.
Build forwarding, NAT, DNS and egress-denial rules from an explicit ownership
model shared by Docker and the E2B worker.

For the conservative first qualification, retain Docker's established
iptables backend and place operator filtering in `DOCKER-USER`. Docker warns
that published ports can bypass ufw rules. Do not disable Docker firewall rule
management because that commonly breaks bridge networking. Docker 29's native
nftables backend is currently experimental and has no `DOCKER-USER` chain; do
not adopt it in the first 26.04 qualification profile.

Only ingress `443/tcp` should ultimately be required; `80/tcp` is optional for
redirect or certificate issuance. Keep API gRPC `5009`, orchestrator `5008`,
reverse proxy `5007`, client proxy/health `3002`/`3003`, guest envd `49983`,
bridge services `5010-5012` and `5016-5018`, datastores, telemetry, VNC and
noVNC on loopback, project bridges or private Compose networks. Test the known
upstream `3001` versus `3003` health-port inconsistency instead of opening both.

Stop if a backend listener is reachable from the public interface, sandbox
egress bypasses policy, Docker restart changes the effective policy, or
rollback cannot restore the exact original routes/sysctls/rules.

### Stage 70: pinned build and artifact qualification

Install pinned Go and Node/npm toolchains only if the selected release process
builds E2B components locally. Add `git`, `make` and other tools only when a
locked build manifest names them. Packer and Terraform remain excluded from
the single-host runtime.

Verify the locked kernel, Firecracker, BusyBox and envd hashes before use. The
kernel has content integrity but incomplete reproducible provenance, so do not
represent hash verification as a source-reproduction guarantee. Run
Firecracker's environment check and a minimal boot first, then E2B's NBD,
snapshot, pause/resume and teardown paths repeatedly.

### Stage 80: private control/data plane

Start digest-pinned Postgres, Redis, ClickHouse, Loki and OTel containers on a
private Compose network with no public host publishing. Run migrations against
disposable data and exercise restart plus backup/restore before starting API,
proxy and worker services. Bind local artifact storage to the Stage 30 mount.

Use systemd for project binary supervision and Compose only for the selected
support services. Explicit dependencies and health checks must prevent the
privileged worker or public ingress from starting against an incomplete data
plane.

### Stage 90: compatibility acceptance

Accept Ubuntu 26.04 only after the pinned Firecracker boot, NBD attach/detach,
snapshot create/resume, cgroup limits, network isolation, Docker restart,
datastore recovery, SDK contracts and full cleanup all pass repeatedly. Reboot
and rerun the suite. Verify there are no leaked mounts, NBD attachments,
namespaces, TAP devices, cgroups, routes, firewall rules, processes or public
listeners.

Because this is a disposable learning host, manual experiments can inform the
automation but cannot become production provenance. Promote the learned state
into journaled kitdev/Ansible actions, reinstall Ubuntu 26.04, and qualify the
clean automated path before making a production-support claim.

## Ubuntu 26.04 gaps and decisions still required

- No current E2B primary source claims support for General Linux, Ubuntu 26.04
  or a single-host systemd/Compose topology.
- E2B workflows and Firecracker's Ubuntu example remain on 24.04, so the exact
  26.04 kernel, KVM ioctls, seccomp policy, NBD/udev, userfaultfd and snapshot
  behavior are unqualified.
- Docker supports 26.04, but the exact apt package versions must be resolved and
  locked at execution time; `latest` is not a reproducible selection.
- Docker's experimental nftables backend is not the initial target. The
  interaction among Ubuntu's ufw, Docker iptables rules and worker-created
  sandbox rules needs destructive-lab testing and rollback evidence.
- Upstream does not publish a defensible single-host CPU, RAM, huge-page,
  storage or inode minimum. Capacity must be derived from measured concurrency
  and template/snapshot behavior.
- The pinned E2B Firecracker version differs from a version still named in its
  orchestrator README, and the locked kernel's build provenance is incomplete.
- Persistent SDK volume content remains blocked by the unavailable referenced
  `belt` implementation; host readiness does not resolve that product gap.

## Primary sources

- E2B pinned self-host guide:
  <https://github.com/e2b-dev/infra/blob/882a3b4786755db9e94be3297de6827f9100ce5e/self-host.md>
- E2B pinned infrastructure source:
  <https://github.com/e2b-dev/infra/tree/882a3b4786755db9e94be3297de6827f9100ce5e>
- E2B pinned orchestrator prerequisites:
  <https://github.com/e2b-dev/infra/blob/882a3b4786755db9e94be3297de6827f9100ce5e/packages/orchestrator/README.md>
- Docker Engine installation on Ubuntu:
  <https://docs.docker.com/engine/install/ubuntu/>
- Docker packet filtering and firewalls:
  <https://docs.docker.com/engine/network/packet-filtering-firewalls/>
- Docker's experimental nftables backend:
  <https://docs.docker.com/engine/network/firewall-nftables/>
- Firecracker getting started and host prerequisites:
  <https://github.com/firecracker-microvm/firecracker/blob/main/docs/getting-started.md>
- Firecracker production host recommendations:
  <https://github.com/firecracker-microvm/firecracker/blob/main/docs/prod-host-setup.md>
- Firecracker seccomp policy:
  <https://github.com/firecracker-microvm/firecracker/blob/main/docs/seccomp.md>
- Linux kernel NBD documentation:
  <https://kernel.org/doc/html/latest/admin-guide/blockdev/nbd.html>
- Ubuntu 26.04 LTS release summary:
  <https://documentation.ubuntu.com/release-notes/26.04/summary-for-lts-users/>
- Ubuntu QEMU/KVM guidance:
  <https://documentation.ubuntu.com/server/how-to/virtualisation/qemu/>
- Ubuntu libvirt guidance:
  <https://documentation.ubuntu.com/server/how-to/virtualisation/libvirt/>
- Ubuntu nested-virtualization guidance:
  <https://documentation.ubuntu.com/server/how-to/virtualisation/enable-nested-virtualisation/>
- Ubuntu firewall guidance:
  <https://documentation.ubuntu.com/server/how-to/security/firewalls/>

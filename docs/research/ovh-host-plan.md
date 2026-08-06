# OVHcloud dedicated-host plan

Status: pre-purchase research; no server ordered or modified

Retrieved: 2026-08-06

## Scope and method

This report compares Ubuntu Server 26.04 LTS directly on a newly purchased
OVHcloud dedicated server with Proxmox VE plus an Ubuntu 26.04 guest. It uses
only current first-party OVHcloud, Canonical, Firecracker, and Proxmox sources.
No OVHcloud account, order, server, or local host was accessed.

OVHcloud exposes the operating systems compatible with a particular delivered
server through the authenticated
`GET /dedicated/server/{serviceName}/install/compatibleTemplates` API. The
server model, product range, region, and service name are not known yet.
Consequently, this report cannot prove that Ubuntu 26.04 is present in the
native OVHcloud installation catalog for the eventual machine. Provider stock,
bandwidth, IP options, IPMI implementation, and prices are likewise
model/region/account dependent and must be confirmed in the actual order and
Control Panel.

## Recommendation

Install **Ubuntu Server 26.04 LTS directly on the bare metal**. Do not put
Proxmox under kitdev for the primary development and qualification host.

This is the smallest host contract for the project:

```text
OVHcloud dedicated x86_64 server
`-- Ubuntu Server 26.04 LTS
    |-- systemd and Compose control/data services
    |-- KVM, NBD, cgroups, namespaces, TAP and host firewall
    `-- Firecracker microVM sandboxes
```

Ubuntu 26.04 was released in April 2026 and receives standard security
maintenance through May 2031. Canonical recommends LTS releases for long-lived
and production deployments. Ubuntu 25.04 remains a project compatibility target
but is already outside its nine-month interim-release maintenance window; it
should not be installed on this new internet-facing server.

Firecracker requires a Linux host, the KVM kernel module, and read/write access
to `/dev/kvm`. Direct installation exposes those primitives without nesting.
It also makes CPU, kernel, cgroup, NBD, TAP, nftables, and performance failures
attributable to the platform being tested rather than an outer hypervisor.

### Why not Proxmox here

Proxmox VE 9.2 is a capable Debian-based KVM/LXC platform. It is useful when a
server must host several unrelated machines, needs a general VM management
plane, or will join a Proxmox cluster. None of those is the current requirement.
To preserve kitdev's Ubuntu host contract, Proxmox would produce:

```text
OVHcloud bare metal
`-- Proxmox VE host (Debian and Proxmox kernel)
    `-- Ubuntu 26.04 QEMU/KVM guest
        `-- Firecracker/KVM microVMs
```

That design adds all of the following to the test result:

- nested KVM enablement and `host` CPU-feature passthrough;
- a virtual disk and an extra filesystem/storage layer for Firecracker images,
  snapshots, and NBD operations;
- Proxmox bridge/routing/NAT plus the Ubuntu guest's Firecracker TAP, routing,
  NAT, MTU, and firewall rules;
- resource accounting at both Proxmox and Ubuntu cgroup layers;
- another public management service and upgrade lifecycle;
- recovery that depends on both the Proxmox host and the Ubuntu guest.

Firecracker documents nested virtualization as a development option, while the
official `firecracker-containerd` guide warns that Firecracker is not well
tested under nested virtualization. Proxmox requires nested virtualization on
the host and the guest CPU type set to `host` to expose the needed features.
These are avoidable qualification variables on actual bare metal.

Proxmox snapshots would be convenient, but they are not a substitute for source
control, ownership manifests, backups, or reinstall tests. OVHcloud already
provides remote console, rescue boot, and reinstall mechanisms. Reconsider
Proxmox only if this physical machine later becomes a shared virtualization
host; in that case, nested Firecracker must be treated as a separate,
non-production test profile.

## Ubuntu 26.04 provisioning path

Use this order of preference after the server is delivered:

1. **Native OVHcloud OS catalog:** query the compatible templates for the exact
   service. If Ubuntu Server 26.04 is listed, use that installer and inject the
   operator SSH public key. This is the preferred path.
2. **BYOLinux:** if 26.04 is absent, build and checksum a compatible 26.04
   QCOW2, then deploy it through OVHcloud's supported BYOLinux reinstall path.
   BYOLinux requires a cloud-ready image smaller than server RAM minus 3 GiB,
   one partition, ext4/XFS/Btrfs without Btrfs subvolumes, and an executable
   `/root/.ovh/make_image_bootable.sh`. For UEFI Ubuntu the documented EFI path
   is `\efi\ubuntu\grubx64.efi`. This is image-engineering work; do not assume
   the stock Canonical cloud image already satisfies every OVHcloud constraint.
3. **Remote ISO through IPMI:** OVHcloud documents virtual-media installation
   for operating systems outside its catalog, but explicitly does not guarantee
   manually installed operating systems. Reserve this for recovery or for a
   controlled experiment if BYOLinux is blocked.

OVHcloud's BYOLinux and native reinstall operations erase the server. Record
the image URL, SHA-256, installer payload, partition layout, and resulting
package/kernel versions so the host can be reproduced.

Do not change PXE/iPXE from first place in BIOS/UEFI. OVHcloud's normal disk,
rescue, microcode-update, and custom boot modes depend on its network boot
service. A manual bootloader must not rewrite NVRAM boot order. Also avoid a
fully private OLA configuration for this first server: OVHcloud documents that
its standard netboot does not work on vRack-only interfaces.

## What to choose during purchase

These choices are tied to hardware, datacenter inventory, contract, or product
range and should be decided before checkout:

| Purchase choice | Initial requirement | Reason |
|---|---|---|
| Product range | Regular OVHcloud dedicated range with confirmed IPMI/KVM, rescue, reinstall, and vRack/Additional IP eligibility | IPMI and networking features can be unavailable or limited on Eco products |
| Architecture | `x86_64` Intel VT-x or AMD-V | Matches the current AMD64 host/template plan and provides KVM |
| CPU | At least 16 physical cores; prefer a current server CPU | Leaves capacity for the control plane, builds, and concurrent microVMs |
| Memory | At least 128 GiB, preferably ECC | Desktop sandboxes, builds, databases, and page cache compete for RAM |
| Storage | At least 2 x 2 TB enterprise NVMe; prefer software RAID1 for the initial host | NVMe for image/snapshot churn; mirroring tolerates one device failure |
| RAID controller | Direct/JBOD disks for Linux software RAID unless a documented hardware-RAID design is chosen | Keeps storage behavior visible and reproducible; do not accidentally stack RAID layers |
| Region/datacenter | Choose from actual stock based on operator latency, target-user latency, and data-location needs | OVHcloud model and feature availability varies by location |
| Network | Confirm committed public bandwidth, traffic policy, primary IPv4, IPv6 allocation, and upgrade ceiling | Builds, desktop streams, artifacts, and sandbox egress can be bandwidth-heavy |
| Recovery | Confirm the exact model exposes browser KVM or SoL, virtual media if needed, rescue mode, hard reboot, and reinstall | SSH is the normal path, but an out-of-band path is mandatory before firewall work |
| Contract | Prefer a commitment that still permits reconfiguration or replacement during qualification | The first hardware/model may expose compatibility problems |

The exact CPU generation, RAM, disk count, bandwidth, price, and datacenter
remain open until the user provides the OVHcloud product configuration. Do not
infer that a marketing range implies a specific NIC, RAID controller, or IPMI
method.

### Safe to configure after delivery

The following do not need to drive the initial purchase, provided the selected
range is eligible:

- native OS installation or BYOLinux reinstall, hostname, SSH key, and
  partition layout;
- host firewall and OVHcloud Edge Network Firewall rules;
- DNS, wildcard DNS, and TLS;
- Additional IP ordering/attachment and vRack attachment;
- private Firecracker CIDRs, Docker CIDRs, bridges, routing, and NAT;
- monitoring, backup activation, and application installation.

If checkout asks for an OS, select Ubuntu 26.04 only if it is explicitly listed.
The OS choice is not permanent because OVHcloud supports reinstall after
delivery. Do not select Proxmox merely because 26.04 is absent from that screen.

## Public networking and firewall plan

One primary public IPv4 is sufficient for the first kitdev deployment:
`api.<domain>` and the wildcard sandbox hostname can resolve to the same
address, and the host ingress can route by TLS SNI/HTTP Host. Firecracker guests
should initially use private RFC1918 addressing behind host-controlled routing
and NAT. Additional public IPs and vRack are not prerequisites for Milestone 1
or the first end-to-end sandbox.

Initial exposure should be:

- TCP 22 from known operator source addresses while bringing up the host;
- later TCP 80/443 for public API, certificate issuance, and sandbox ingress;
- no public PostgreSQL, Redis, ClickHouse, MinIO, Docker, Firecracker API,
  VNC/noVNC, application admin, or Proxmox ports.

The OVHcloud Edge Network Firewall is stateless, IPv4-only, handles traffic
from outside the OVHcloud network, and permits at most 20 rules per IP. It is a
DDoS-edge control, not the host firewall; OVHcloud explicitly requires a local
firewall as well. As of the research date, OVHcloud drops QUIC at its edge, so
plan on TCP HTTP/2 rather than HTTP/3. Configure both edge and local rules only
after IPMI/SoL access is proven. Preserve an established SSH session and test a
second session before closing the default policy.

Record OVHcloud's assigned public interface MAC, IPv4/IPv6 values, route,
gateway behavior, MTU, and DNS before changing networking. Allocate Docker and
Firecracker networks only after checking all host routes; do not overlap them
with OVHcloud/vRack, VPN, operator, or commonly used Docker ranges. Firecracker
does not filter guest traffic, so guest egress policy belongs on the Ubuntu
host and is a platform security requirement, not optional hardening.

## Initial non-destructive bring-up

The first SSH session should only inventory and verify. It must not install
kitdev, change BIOS, repartition disks, enable nested virtualization, or alter
network/firewall state.

1. Save the OVHcloud order summary, model, datacenter, public IP allocations,
   interface/MAC information, rescue instructions, and support identifiers in
   the private operator record. Do not commit secrets or identifying values.
2. Open IPMI/KVM or SoL from the Control Panel and confirm console output is
   visible. Do not enter BIOS or change boot order. Keep the console available
   during later network changes.
3. Verify the SSH host fingerprint through the console, then connect with an
   operator key. Confirm the created account has `sudo`; do not enable remote
   root or password login.
4. Capture OS and kernel facts with `cat /etc/os-release`, `uname -a`,
   `systemd --version`, and `systemctl is-system-running`.
5. Capture CPU/KVM facts with `lscpu`, `grep -Eo 'vmx|svm' /proc/cpuinfo | sort
   -u`, `lsmod | grep kvm`, `ls -l /dev/kvm`, and `test -r /dev/kvm -a -w
   /dev/kvm`. Do not change BIOS until a missing capability is confirmed from
   both the OS and remote console.
6. Capture cgroup and kernel controls with `stat -fc %T /sys/fs/cgroup`,
   `mount | grep cgroup`, and read-only `sysctl` queries for user namespaces,
   userfaultfd, forwarding, bridge netfilter, and unprivileged BPF.
7. Inventory storage without mounting or writing: `lsblk -e7 -o
   NAME,PATH,SIZE,TYPE,FSTYPE,MOUNTPOINTS,MODEL,SERIAL`, `findmnt`, `cat
   /proc/mdstat`, `mdadm --detail --scan` if available, `nvme list` if
   available, and `df -hT`.
8. Inventory networking without changes: `ip -br link`, `ip -br address`, `ip
   route show table all`, `ip -6 route show table all`, `ss -lntup`, `nft list
   ruleset`, and `docker network ls` if Docker was preinstalled.
9. Record firmware and virtualization-related messages with `dmesg`/`journalctl
   using redaction. Check for KVM disabled-by-BIOS errors, IOMMU faults, NVMe
   errors, machine-check events, and NIC link resets.
10. Run provider hardware diagnostics in rescue mode only after the inventory
    is stored and a maintenance reboot is explicitly scheduled. Rescue boot is
    disk-nondestructive but service-disruptive; verify disk visibility and SSH
    recovery, then restore `Boot from the hard disk` before rebooting.
11. Compare the redacted results with the kitdev preflight contract. Stop on a
    missing `/dev/kvm`, inaccessible IPMI/rescue path, unexpected disk layout,
    hardware errors, or public networking discrepancy. Do not compensate with
    invasive changes before documenting the cause.

After this gate passes, the next change should be host hardening and a pinned
Firecracker minimal-boot proof, not the complete platform installer.

## Sources

### OVHcloud

- [Install an OS through the dedicated-server API](https://docs.ovhcloud.com/en/guides/bare-metal-cloud/dedicated-servers/api-os-installation)
- [BYOI and BYOLinux comparison](https://help.ovhcloud.com/csm/en-ca-dedicated-servers-bring-your-own-image-versus-bring-your-own-linux?id=kb_article_view&sysparm_article=KB0061593)
- [Deploy a custom Linux image with BYOLinux](https://help.ovhcloud.com/csm/es-dedicated-servers-bring-your-own-linux?id=kb_article_view&sysparm_article=KB0061614)
- [Dedicated-server boot process](https://help.ovhcloud.com/csm/en-dedicated-servers-boot-process?id=kb_article_view&sysparm_article=KB0074824)
- [Dedicated-server IPMI console](https://docs.ovhcloud.com/en/guides/bare-metal-cloud/dedicated-servers/ipmi)
- [Dedicated-server rescue mode](https://docs.ovhcloud.com/en/guides/bare-metal-cloud/dedicated-servers/rescue-mode)
- [Dedicated-server first steps](https://docs.ovhcloud.com/en/guides/bare-metal-cloud/dedicated-servers/getting-started-with-dedicated-server)
- [Edge Network Firewall](https://docs.ovhcloud.com/en/guides/bare-metal-cloud/dedicated-servers/firewall-network)
- [Proxmox networking on dedicated servers](https://help.ovhcloud.com/csm/en-sg-dedicated-servers-proxmox-network-hg-scale?id=kb_article_view&sysparm_article=KB0043911)
- [OS install storage and partitioning](https://help.ovhcloud.com/csm/en-au-dedicated-servers-api-partitioning?id=kb_article_view&sysparm_article=KB0043879)

### Ubuntu, Firecracker, and Proxmox

- [Ubuntu 26.04 release images](https://releases.ubuntu.com/26.04/)
- [Ubuntu lifecycle and support dates](https://ubuntu.com/about/release-cycle)
- [Firecracker getting started and KVM requirements](https://github.com/firecracker-microvm/firecracker/blob/main/docs/getting-started.md)
- [Firecracker design and host-level network filtering](https://github.com/firecracker-microvm/firecracker/blob/main/docs/design.md)
- [Firecracker development-machine setup](https://github.com/firecracker-microvm/firecracker/blob/main/docs/dev-machine-setup.md)
- [firecracker-containerd nested-virtualization warning](https://github.com/firecracker-microvm/firecracker-containerd/blob/main/docs/getting-started.md)
- [Proxmox VE 9.2 release](https://www.proxmox.com/en/about/company-details/press-releases/proxmox-virtual-environment-9-2)
- [Proxmox network models](https://pve.proxmox.com/wiki/Network_Configuration)
- [Proxmox nested-virtualization documentation](https://pve.proxmox.com/wiki/Nested_Virtualization)

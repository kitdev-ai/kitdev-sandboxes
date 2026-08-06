# OVH Ubuntu 26.04 first read-only intake

Date: 2026-08-06

## Result

SSH reachability and non-interactive key authentication passed. The delivered
host matches the ordered Ubuntu 26.04 bare-metal, CPU, memory, and disk profile.
The host is suitable for continued read-only qualification but is **not ready
for install or apply**, and no change plan is authorized. Its first-seen SSH
host key remains `UNVERIFIED`, unprivileged firewall-rule collection was denied,
and the required production collector has not run with an independent baseline.
Required unknown evidence remains blocking under the intake runbook.

No endpoint, hostname, address, MAC, account, SSH fingerprint, hardware serial,
or provider identifier is retained in this report. Exact observations are in
the ignored local-only operator inventory.

## Method

The intake used an isolated temporary `known_hosts` file and fixed SSH options:
batch authentication, a ten-second connect timeout, one connection attempt,
connection sharing disabled, error-only logging, and accept-new host-key
capture. A whole-command timeout bounded each invocation. The first command was
only `/usr/bin/true`; it succeeded before inventory began. Approved runbook
commands then ran individually without `sudo`, uploads, remote writes, pipes,
redirections, package operations, service operations, or configuration changes.

After that inventory, the user explicitly approved a narrow read-only KVM
privilege-boundary check. `sudo -n` authentication succeeded, root observed the
same character device, and root read/write access tests succeeded. No command
changed identity, group membership, mode, ownership, or device state.

One initial KVM-stat command mistakenly contained a shell metacharacter in its
format string. It produced only stat/command-not-found errors. Four fixed
read-only stat calls immediately replaced that invalid observation. One KVM
read-access test had a transient SSH failure and was repeated once; the bounded
repeat returned the expected negative access result. Neither event changed
remote state.

The first-seen host key was retained privately as `UNVERIFIED`; it was not
promoted to trusted identity. Temporary host-key files were removed after the
run, and the normal local SSH host-key file was not used or changed.

## Normalized observations

| Area | Redacted observation | Gate |
| --- | --- | --- |
| Platform | Ubuntu Server 26.04 LTS Resolute, x86-64, kernel `7.0.0-28-generic` | Pass |
| Virtualization | Direct bare metal; Intel VT-x is exposed | Pass |
| Init and cgroups | PID 1 is systemd, system state is running, cgroup v2 is active | Pass |
| CPU | One socket, four physical cores, eight logical CPUs | Matches order |
| Memory | 67,193,135,104 bytes RAM and about 1 GiB swap | Matches order; policy threshold still pending |
| KVM | KVM modules loaded; `/dev/kvm` is a root:kvm character device; root can read/write it | Device pass; login identity has no read/write access |
| TUN/NBD/huge pages | TUN is a valid character device; NBD is unloaded; zero huge pages are reserved | Known; future plan may propose NBD/huge-page changes |
| Storage | Two 450,098,159,616-byte NVMe disks back healthy two-member RAID1 EFI (`md1`), boot (`md2`), and root (`md3`) volumes; all arrays report 2/2 `[UU]` | Matches order |
| Data disk | One 4,000,787,030,016-byte rotational SATA disk is present, raw, and unmounted | Matches order; storage/adoption plan required |
| Capacity | Root has about 414 GB available and about 27.2 million free inodes | Observed; profile threshold still pending |
| Network | Dual-stack primary NIC is up; second physical NIC is down; assigned routes match the private order record | Pass, exact values redacted |
| Route overlap | No observed host route overlaps the configured sandbox range | Pass for current configuration |
| Listeners | Public wildcard SSH only; DNS and time listeners are loopback-local; DHCP is bound to the primary NIC | No unexpected public listener observed |
| Services | AppArmor, UFW, and time synchronization are active; Docker is inactive or absent | Known |
| Firewall rules | Unprivileged nftables inspection was permission-denied | Blocking unknown; no sudo attempted |
| Security | CPU reports mitigations plus SMT-sensitive residual statuses | Security review required before hostile workloads |
| Host identity | First-seen ED25519 host key captured privately | Blocking `UNVERIFIED` pending console comparison |

The current SSH account has existing administrative/root-equivalent exposure
through the `sudo` and `lxd` groups, but is not a member of the KVM group and
cannot read or write `/dev/kvm`. This privilege shape is not the intended worker
access model. Group and identity redesign is reviewed host-preparation work, not
a reason to change account membership or device ownership during intake.
The future plan should use a dedicated least-privilege service account in the
KVM group. It must not run the stack as root or broaden access with `chmod`.

## Decision and next gate

The hardware and delivered operating system are suitable for continued
read-only qualification, but the intake result is **NO-GO for installation or
apply**. Resolve the following through separate reviewed read-only or planning
steps:

1. Compare the private first-seen SSH fingerprint with the OVH console and mark
   it verified only on an exact match.
2. Establish the effective UFW/nftables rules without granting broad privilege
   or mutating firewall state.
3. Run the full bounded production collector with an independent before/after
   baseline.
4. Approve the project port/bind/ownership policy and capacity thresholds.
5. Approve a storage plan for the raw 4 TB data disk before formatting,
   mounting, or adopting it.
6. Review KVM access and the existing administrative/root-equivalent group
   exposure as an identity design. Plan a dedicated least-privilege service
   account in the KVM group; do not run the stack as root or change device mode.
7. Approve the security disposition for the observed CPU/SMT vulnerability
   statuses and the future NBD/huge-page settings.

## Mutation and rollback

No intentional remote configuration or project-state mutation occurred. SSH
authentication can create normal provider and operating-system audit/session
records, as documented by the runbook. There is no kitdev rollback action. If
later evidence shows that the delivered image or disk layout is wrong, an OVH
reinstall is the clean recovery path, but it is destructive and requires its
own reviewed plan and explicit approval.

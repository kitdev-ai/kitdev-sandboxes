# OVH Ubuntu 26.04 bare-metal intake

Status: first-login qualification runbook; zero intentional host mutation

## Scope and gate

The qualification target is **Ubuntu Server 26.04 LTS installed directly on
the OVH bare metal**. Proxmox, Ubuntu 25.04, nested virtualization, and desktop
images are not acceptable substitutes for this production target. This runbook
qualifies an untouched installation only; it does not prepare the host.

Passing this intake permits a reviewed change plan to be written. It does not
authorize installation or any host change. Every mutation requires a specific
reviewed plan and explicit approval first.

Related context:

- [OVHcloud dedicated-host plan](ovh-host-plan.md)
- [`kit@pc` read-only host discovery](host-discovery.md)
- [Preflight design](../preflight-design.md)
- [Supported-host ADR](../adr/0005-supported-host-matrix.md)

## Installer record

Before first login, create the ignored local-only operator record at
`docs/private/ovh-26-04-server-inventory.md`. The file is inside the worktree
but excluded locally from Git and must never be committed. Record the choices
actually submitted to OVH and the completed installer job; do not infer them
from the running host later.

| Area | Exact values to retain privately |
| --- | --- |
| Image | Installation path (native catalog or approved alternative), OVH template identifier, Ubuntu image/build serial, source URL and SHA-256 when supplied, installer job identifier and completion time, boot mode |
| Storage | Physical disk count/model/capacity, software or hardware RAID implementation, RAID level and member count, partition sizes, filesystems, mount points, encryption choice, swap choice |
| Identity | Requested hostname and resulting hostname; retain only `<redacted-host>` in project evidence |
| SSH | Injected public-key algorithm and fingerprint, target account, console-verified host-key fingerprints; never record private-key material |
| Network | Assigned IPv4/IPv6 addresses and prefixes, gateways, interface/MAC mapping, MTU, reverse DNS and datacenter; use redacted placeholders in project evidence |

Select Ubuntu Server 26.04 LTS and the disk layout approved in the private
installer record. Do not
choose Proxmox or Ubuntu 25.04 as a fallback. During intake, do not compensate
for an installer discrepancy by changing packages, partitions, RAID, hostname,
SSH, DNS, routes, interfaces, the OVH edge firewall, or the host firewall.

## Zero-mutation rules

1. Verify the server identity and SSH host-key fingerprint through the OVH
   console before connecting. Confirm that console, rescue, and reinstall
   controls exist, but do not change boot mode or reboot.
2. Start one SSH session with the injected operator key. Disable shell-history
   persistence for that session (`HISTFILE=/dev/null`) and do not inspect or
   change SSH credentials or configuration.
3. Run the fixed commands below individually. Do not add pipes, redirects,
   command substitutions, globs, loops, scripts, or host-derived arguments.
   An automated collector must use argv execution with no shell.
4. Do not run `sudo`. A permission failure is an explicit unknown observation,
   not a reason to escalate privileges during this gate.
5. Do not install packages, run package updates, write files, create temporary
   files, start/stop/reload/enable services, change users/groups, load modules,
   mount filesystems, alter RAID, change sysctls, networking or firewall state,
   invoke Docker mutations, reboot, or enter rescue mode.

SSH authentication and ordinary reads can produce provider/OS audit records,
session accounting, and filesystem access-time changes. Those are unavoidable
platform telemetry, not authorized configuration changes. The commands below
create no project state and intentionally change no host setting.

## First-login inventory

Use the absolute argv shown. A missing executable, timeout, permission error,
or unexplained nonzero result must be recorded as `unknown`; do not install a
replacement. Expected semantic nonzero results remain observations:
`systemd-detect-virt` reports bare metal as `none`, `test` reports false, and
`systemctl` can report a known inactive/degraded state.
Budget each command to five seconds and 256 KiB per output stream. Abort on a
limit and record `unknown`; an automated run must use the bounded project
runner rather than adding a shell wrapper.
Do not use `uname -a`, process listings, environment dumps, `dmidecode`, disk
serial output, machine/boot IDs, cloud-init user data, or full journal export.

### Platform and virtualization

```text
/usr/bin/cat /etc/os-release
/usr/bin/hostname
/usr/bin/uname -m
/usr/bin/uname -r
/usr/bin/cat /proc/1/comm
/usr/bin/systemd-detect-virt
/usr/bin/systemctl is-system-running
/usr/bin/systemctl get-default
/usr/bin/stat --file-system --format=%T /sys/fs/cgroup
/usr/bin/lscpu --json
/usr/sbin/lsmod
/usr/bin/stat --format=%F:%a:%U:%G /dev/kvm
/usr/bin/test -r /dev/kvm
/usr/bin/test -w /dev/kvm
/usr/bin/stat --format=%F:%a:%U:%G /dev/net/tun
/usr/bin/id -u
/usr/bin/id -G
```

Retain only the OS identity/version, architecture, kernel release, PID 1,
virtualization result, cgroup filesystem type, CPU count/model and `vmx`/`svm`
capability, KVM module names, device types/modes, and boolean access results.
Do not retain the complete `lscpu`, `lsmod`, user, or group output.

### Storage and capacity

```text
/usr/bin/lsblk --json --output NAME,SIZE,TYPE,FSTYPE,MOUNTPOINTS,ROTA,MODEL
/usr/bin/cat /proc/mdstat
/usr/bin/findmnt --json --output TARGET,SOURCE,FSTYPE,OPTIONS
/usr/bin/df --block-size=1 --output=source,fstype,size,used,avail,pcent,target
/usr/bin/df --output=source,itotal,iused,iavail,ipcent,target
/usr/bin/cat /proc/meminfo
```

Do not request disk serials. Normalize memory evidence to total/available RAM,
swap totals, and huge-page counters. Normalize storage evidence to disk counts,
capacity, RAID health, filesystems, mount points, bytes and inodes needed by the
preflight policy. Redact a username or home path if one appears in a mount.

### Network, listeners, services, and security

```text
/usr/sbin/ip -json -details link show
/usr/sbin/ip -json address show
/usr/sbin/ip -json route show table all
/usr/sbin/ip -6 -json route show table all
/usr/bin/ss -H -lntu
/usr/bin/systemctl list-units --type=service --state=running --no-pager --no-legend
/usr/bin/systemctl is-active docker.service
/usr/bin/systemctl is-active ufw.service
/usr/bin/systemctl is-active apparmor.service
/usr/bin/timedatectl show --property=NTPSynchronized --value
/usr/sbin/nft list ruleset
/usr/sbin/sysctl -n kernel.unprivileged_userns_clone
/usr/sbin/sysctl -n vm.unprivileged_userfaultfd
/usr/sbin/sysctl -n kernel.unprivileged_bpf_disabled
/usr/sbin/sysctl -n net.ipv4.ip_forward
/usr/sbin/sysctl -n net.ipv6.conf.all.forwarding
/usr/bin/journalctl --dmesg --boot=0 --priority=warning..alert --no-pager --output=short-monotonic
```

The unprivileged `nft` and journal reads may be denied; record `unknown` and
stop rather than using `sudo`. Retain normalized interface state, address
families/prefix lengths, route overlap results, listener protocol/bind
scope/port, allowlisted service states, boolean sysctl values, time-sync state,
and hardware-error categories only. Omit PIDs and command lines.

Never place raw network output in the repository. Replace every IPv4/IPv6
address, gateway and route endpoint with `<redacted-ip>`, MAC address with
`<redacted-mac>`, hostname with `<redacted-host>`, and account/home value with
`<redacted-account>` or `<redacted-path>`. Do not capture credentials, cookies,
authorization headers, tokens, SSH material, or signed URLs at all.
Compare exact addresses on screen with the private OVH record; do not save a raw
command transcript and then rely on later redaction.

## Decision matrix

| Observation | GO to change-plan review | NO-GO / stop condition |
| --- | --- | --- |
| Installed target | `ID=ubuntu`, `VERSION_ID=26.04`, Server image record, direct bare metal, `x86_64` | Any other OS/release/image, Proxmox or other virtualization, wrong architecture |
| Core host contract | PID 1 is systemd, cgroup filesystem is `cgroup2fs`, system state is running or explicitly degraded with a bounded explanation | Non-systemd boot, cgroups v1, unexplained failed/degraded system state |
| CPU and KVM | `vmx` or `svm`; KVM modules and character `/dev/kvm` exist | Hardware virtualization absent/disabled, missing or wrong-type `/dev/kvm` |
| Worker access | Existing access is recorded; absent access becomes a future proposed identity/group change | Never change access during intake; unknown device ownership blocks planning evidence |
| TUN, NBD, huge pages | TUN device is valid; NBD/huge-page state is known and may yield proposed changes | Wrong-type TUN device or unexplained devices/resources already in use |
| Storage | Disk count/capacity, partitions and RAID match the private approved installer record; arrays healthy; capacity meets the selected profile | Layout mismatch, degraded RAID, missing disk, unexpected mount, insufficient capacity |
| Network | Assigned families/default routes match the private OVH record; no CIDR conflicts; only expected fresh-host listeners | Address/gateway discrepancy, missing management path, conflicting route, unexpected public listener |
| Recovery | Console is readable and rescue/reinstall controls are available without exercising them | No verified out-of-band console or reinstall/recovery path |
| Security/health | Time sync and AppArmor state known; no KVM, machine-check, NVMe/filesystem or persistent NIC errors | Relevant hardware/kernel errors or unexplained security-service failure |
| Evidence quality | Required observations are bounded, normalized and redacted | Required fact is missing, truncated, permission-denied or cannot be safely redacted |

A `NO-GO` result means preserve the evidence and stop. An `unknown` required
fact also means the host is not qualified; resolve it with a separate reviewed
read-only plan. Package absence, worker KVM access, unloaded NBD, zero huge
pages, and default forwarding state may become proposed changes after the gate,
but they are not permission to change the machine now.

## Evidence and rollback

Store only a redacted summary containing the installer-record comparison,
timestamp, lifecycle mode `production`, ordered observations, result, and
limitations. Keep exact addresses, hostnames, hardware identifiers, OVH service
identifiers, and SSH fingerprints in the private operator record.

Qualification creates no project state and has no project rollback procedure.
If the delivered image or layout is wrong, the clean rollback is an OVH
reinstall from the approved Ubuntu Server 26.04 LTS image. Because intake is
read-only, there is no kitdev data to preserve or restore. Reinstall itself is
destructive and requires its own reviewed plan and explicit approval.

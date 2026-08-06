# Preflight and doctor design

## Purpose

Preflight protects a shared host from unsupported or conflicting installation.
It gathers facts without mutation, evaluates them against explicit requirements,
and produces both a human report and stable JSON. The same fact/evaluation
engine powers `kitdev doctor`, install planning, CI host qualification, and
post-install diagnostics.

This document is the design contract for Milestone 1. The current foundation
implements only typed configuration and an initial strictly read-only collector
subset. Required checks that are not implemented remain blocking `unknown`
results; the complete preflight and host-preparation exit gate has not passed.

## Safety contract

The default preflight is read-only. It may execute unprivileged commands and
specifically approved `sudo -n` reads, but it must not load modules, change
sysctls, start services, create users or directories, pull images, alter
firewall rules, or start a microVM.

The deeper Firecracker probe is a separately named post-install check because
starting a VM is not read-only. `--dry-run` calculates changes from observations
and Ansible check/diff results; it makes no repair attempt.

Fact collection should use `/proc`, `/sys`, structured command output, and
system APIs where practical. Locale is fixed to `C`. Missing optional commands
produce an `unknown` result rather than an exception.

## Processing stages

1. Parse defaults, installed configuration if present, and CLI overrides,
   including the explicit lifecycle mode.
2. Validate the merged non-secret configuration against its schema.
3. Collect facts with bounded command timeouts.
4. Evaluate facts independently of collection.
5. Discover conflicts and classify ownership.
6. Build a proposed host-change plan.
7. Render text or versioned JSON.
8. Exit before any mutation if a blocking check fails.

Collectors return raw value, normalized value, source, command status, elapsed
time, and redacted evidence. Evaluators return a stable check ID, status,
severity, explanation, remediation, and whether the installer can safely
remediate it.

## Result model

Statuses are `pass`, `warn`, `fail`, `unknown`, and `skipped`. Required platform
incompatibility is `fail`; operator policy or capacity concerns may be `warn`.
An unknown required fact is blocking unless an explicit, documented override is
safe. Overrides are recorded in the installation manifest. Release lifecycle
eligibility cannot be bypassed by a generic warning override.

Proposed exit codes:

| Code | Meaning |
| --- | --- |
| 0 | All required checks pass; warnings may exist |
| 2 | Invalid invocation or configuration |
| 3 | Unsupported platform or missing hard requirement |
| 4 | Resource, port, service, network, or ownership conflict |
| 5 | Required fact could not be collected |
| 6 | Installed deployment is unhealthy |
| 10 | Unexpected internal error |

JSON uses a top-level `schema_version`, timestamp, project release,
`lifecycle_mode`, command mode, redacted host fingerprint, summary counts,
`checks` array, and proposed `changes` array. Check IDs and meanings are API
contracts; prose is not.

## Required checks

### Platform

- `/etc/os-release` identifies Ubuntu 25.04 or Ubuntu 26.04 LTS.
- Machine architecture is `x86_64`.
- PID 1/system service manager is systemd.
- `/sys/fs/cgroup` is cgroup v2.
- Kernel and required interfaces meet the pinned upstream baseline.

Edition detection uses observable capabilities rather than branding. A desktop
installation is supported when GDM/display services, NetworkManager, listening
ports, GPU/input devices, routes, suspend policy, and resource use can coexist
with the project. A conflict is rejected with exact evidence and remediation;
the presence of `ubuntu-desktop` or GDM alone is not a failure.

### Release lifecycle gate

The merged configuration contains `deployment.lifecycle_mode`, one of
`production`, `development`, or `migration`. Preflight evaluates the tuple of
release and lifecycle mode before calculating or applying host changes:

| Host release | Production | Development | Migration |
| --- | --- | --- | --- |
| Ubuntu 26.04 LTS | eligible | eligible | eligible |
| Ubuntu 25.04 (EOL) | fail | eligible with EOL warning | eligible with EOL warning |
| Any other release | fail | fail | fail |

The Ubuntu 25.04 production failure is non-overridable in version 0.1 and exits
before privilege acquisition or mutation. Development/migration reports and the
installation manifest prominently record that the host has no supported
distribution security maintenance and is not production-eligible. Lifecycle
mode is included in versioned JSON output and cannot be inferred from profile,
TTY presence, environment name, or an existing installation.

### Virtualization and kernel

- CPU exposes Intel VMX or AMD SVM.
- `/dev/kvm` exists and the intended worker identity can eventually access it.
- KVM modules and vendor module state are reported.
- Nested virtualization is identified when applicable, without claiming it is
  supported.
- NBD module state, configured maximum devices/partitions, and current use are
  reported.
- Huge-page size, configured/reserved/free counts, and mount state are reported.
- Required network namespace, TUN/TAP, overlay/copy-on-write, and mount
  facilities are available.

Preflight reports repairable configuration separately from immutable
incompatibility. It never loads a module in read-only mode.

### Capacity

- Total and available RAM and swap.
- CPU count and virtualization topology.
- Filesystems, types, mount options, free bytes, and inodes for every intended
  project path.
- Estimated install, datastore, artifact, snapshot, workspace, and backup
  capacity for the selected profile.

Thresholds are configuration/versioned policy, not magic values embedded in a
collector. A full profile may fail where minimal would pass.

### Host services and packages

- Docker Engine, Compose plugin, Ansible bootstrap prerequisites, nftables,
  AppArmor, time synchronization, and required build tools.
- Existing containers, Compose projects, networks, volumes, and address pools.
- Conflicting unit names, users/groups, paths, package repositories, mounts,
  and project installation markers.
- GDM and other display managers, GNOME Remote Desktop, NetworkManager,
  system sleep/suspend targets, and desktop-session resource use on desktop
  installations.
- Existing service health when kitdev-sandboxes is installed.

Absence of a repairable package is a planned change, not a platform failure.
Existing Docker is inventoried so uninstall never assumes project ownership.

### Ports, DNS, and firewall

- Listeners are collected with protocol, address, port, process/unit where
  permitted, and ownership confidence.
- Required internal/public ports are calculated from the selected pinned E2B
  version and configuration, then compared with listeners.
- Existing routes, interfaces, bridges, network namespaces, Docker subnets,
  nftables tables/chains, and configured address ranges are checked for overlap.
- IPv4 and IPv6 forwarding/filtering state is recorded.
- Resolver behavior, requested domain records, wildcard DNS, and certificate
  prerequisites are checked only when public exposure is requested.

No port list should be hard-coded before upstream discovery establishes the
actual component topology.

### Security posture

- AppArmor and relevant profiles.
- World/group readability of installed secrets and state.
- Docker socket exposure and worker/control-plane group membership.
- Cloud metadata reachability risk and private/management route inventory.
- Unexpected public datastore, orchestrator, metrics, or management listeners.
- Project nftables rule presence, exact ownership, and duplicate detection after
  installation.

## Change plan

Dry-run output groups every proposed action as package, account, directory,
managed file, shared-file merge, kernel/module, sysctl, network/firewall,
service, Compose resource, artifact, template, or validation action. Each item
includes reason, desired state, current state, privilege, restart/reboot impact,
rollback, and confidence.

A required reboot is reported and installation pauses; the tool never reboots
automatically. Any change outside the declared project paths or dedicated
drop-in files is highlighted and requires an implementation-specific backup and
restore path.

## Idempotency and testability

Collectors and evaluators are separate so saved, redacted fact fixtures can be
unit tested on macOS. Integration tests run on disposable Ubuntu 25.04
development/migration and Ubuntu 26.04 LTS production hosts, including server
and desktop variants, plus a shared-host fixture containing unrelated Docker,
nftables, systemd, and listener resources.

Required tests cover Ubuntu 26.04 production, Ubuntu 25.04
development/migration with EOL reporting, non-mutating rejection of Ubuntu
25.04 production, desktop/GDM coexistence, actual desktop conflicts, unsupported
OS/architecture, missing KVM, cgroups v1, command absence/timeouts, malformed
config, port and subnet conflicts, occupied NBD devices, insufficient capacity,
partial prior install, JSON schema stability, redaction, `sudo -n` failure,
dry-run zero mutation, and repeat-run identical results.

Mutation absence is verified by hashing relevant host facts before and after
read-only preflight, excluding explicitly volatile counters and timestamps.

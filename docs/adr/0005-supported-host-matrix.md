# ADR 0005: Qualify Ubuntu 26.04 for production and recognize Ubuntu 25.04

- Status: proposed
- Date: 2026-08-06
- Supersedes: the Ubuntu Server 24.04-only and desktop-exclusion statements in
  the original project brief

## Context

The project owner changed the version 0.1 host matrix after the initial brief.
Ubuntu 26.04 LTS is the production target. Ubuntu 25.04 remains requested for
development/migration compatibility with the existing host, but its end-of-life
status prevents a production support claim. Desktop editions are no longer
rejected merely because desktop packages or a display manager are installed.

Release branding is not a sufficient predictor of KVM, cgroups, kernel modules,
network behavior, available resources, or conflicts. Conversely, a desktop can
introduce real coexistence concerns through GDM, GNOME Remote Desktop,
NetworkManager, sleep policy, display/input devices, ports, and resource use.
Both Ubuntu releases also require separate validation of their kernels, package
versions, Python interpreters, Docker support, and pinned E2B artifacts.

The [host discovery](../research/host-discovery.md) and
[upstream research](../research/upstream-e2b.md) confirm Ubuntu 25.04's EOL
status and package lifecycle. This decision records that as a production
blocker; requested compatibility does not waive lifecycle validation.

## Decision

Version 0.1 will qualify Ubuntu 26.04 LTS on x86-64 for production. It will
recognize and test Ubuntu 25.04 only in explicit `development` or `migration`
lifecycle mode. Preflight rejects `production` mode on Ubuntu 25.04 before any
mutation. Within those release/mode boundaries, both server and desktop
installations are eligible when observed capabilities pass and no unresolved
coexistence conflict exists.

Desktop qualification explicitly inventories:

- GDM or other display manager units and their restart/reboot behavior;
- GNOME Remote Desktop, VNC/RDP, X11, Wayland, and conflicting listeners;
- NetworkManager ownership of interfaces, bridges, routes, DNS, and forwarding;
- GPU, input, KVM, TUN/TAP, NBD, and cgroup device/resource access;
- suspend, hibernate, lid, and idle policies that could interrupt sandboxes;
- memory, CPU, huge-page, disk, and port headroom after desktop consumption;
- existing Docker, firewall, mDNS, printing, and unrelated desktop services.

Installation must preserve and keep GDM and unrelated desktop services running.
It may not replace NetworkManager configuration or claim an existing interface.
A concrete conflict is reported with evidence; remediation is never performed
silently.

CI and host acceptance tests cover Ubuntu 26.04 LTS production and Ubuntu 25.04
development/migration, including at least one desktop/GDM coexistence fixture.
They also prove Ubuntu 25.04 production is rejected without mutation.
Release-specific dependency and artifact locks are allowed when one immutable
set cannot safely serve both releases.

## Consequences

- The project carries a production Ubuntu 26.04 LTS matrix plus a constrained
  Ubuntu 25.04 development/migration compatibility matrix.
- A host may pass despite desktop packages, while a server install may fail due
  to an actual missing capability or conflict.
- Preflight and networking code must integrate with, not take ownership of,
  NetworkManager-managed host interfaces.
- Ubuntu 25.04's EOL status is a hard production blocker, not merely a warning;
  its development/migration use prominently reports the security-maintenance
  limitation.
- `PROMPT.md` was updated to make the new release and lifecycle requirements
  canonical; this ADR preserves the rationale and history of that change.

## Validation

- Run the read-only discovery set on Ubuntu 25.04 development/migration and
  Ubuntu 26.04 LTS production server and desktop fixtures.
- Prove Ubuntu 25.04 production mode fails before privilege or mutation.
- Demonstrate that GDM and NetworkManager remain active and unchanged across
  install, rerun, reboot, update simulation, and uninstall.
- Exercise port, route, bridge, DNS, sleep-policy, device, and capacity conflict
  fixtures and verify evidence-driven failures.
- Run the core Firecracker and SDK acceptance suite on each qualified
  release/lifecycle-mode tuple.

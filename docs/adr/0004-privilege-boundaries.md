# ADR 0004: Isolate privileged worker operations from API-facing services

- Status: proposed
- Date: 2026-08-06

## Context

MicroVM creation may require KVM, NBD, TAP/network namespace, cgroup, mount, and
filesystem operations. The API and client proxy process attacker-controlled
requests and do not need those privileges. A shared root service would turn an
API/proxy compromise into immediate host control.

## Decision

Use separate Unix users and systemd units:

- `kitdev-e2b` for the API, without privileged devices or host administration;
- `kitdev-proxy` for sandbox-port routing, without worker or datastore admin
  access;
- a dedicated worker identity for the orchestrator/template manager; and
- `kitdev-observe` for optional read-only observability access.

The worker is the only component permitted to operate KVM, TAP/netns, cgroups,
mounts, and NBD. Its boundary is derived by tracing the pinned upstream version.
Prefer narrow device policy, capability bounds, and purpose-built privileged
helpers over a permanently unrestricted root process where upstream interfaces
permit it.

Control/worker communication uses a local authenticated interface with narrow
method semantics and peer identity. It does not expose arbitrary command,
mount, path, or network configuration primitives. The worker never inherits
production application credentials, Docker socket access, SSH keys, or broad
third-party credentials.

Systemd hardening is applied per component and tested. `NoNewPrivileges`,
filesystem protections, private temporary space, writable path allowlists,
device policy, capability bounds, syscall filters, resource accounting, UMask,
and namespace settings are enabled only where compatible with demonstrated
behavior.

## Consequences

- An API or proxy compromise does not automatically grant microVM host control.
- The worker remains a high-value boundary requiring small inputs, careful
  logging, AppArmor consideration, and aggressive testing.
- Some upstream assumptions may resist separation and require patches or a
  documented blocker.
- Datastore credentials must be component-specific and least-privileged.
- Operators need aggregate status/log tooling across identities and units.

## Alternatives considered

- Run all E2B services as root: rejected because it needlessly expands attack
  impact.
- Put services in one privileged container: rejected because a container is not
  a meaningful boundary once broad devices/capabilities/host mounts are granted.
- Apply the strictest systemd flags blindly: rejected because untested flags can
  cause unreliable worker behavior and unsafe operational workarounds.

## Validation

Milestone 2 records the worker's actual device, syscall, capability, file, and
network use. Negative tests prove API/proxy identities cannot open `/dev/kvm`,
manage namespaces/cgroups/mounts, read worker secrets, or access state-service
administration. Milestone 6 iteratively tightens policies and reruns the full
sandbox lifecycle and escape-oriented test suite after every hardening change.

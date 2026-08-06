# ADR 0003: Use project-owned per-sandbox networking with deny-first policy

- Status: proposed
- Date: 2026-08-06

## Context

Firecracker guests need controlled internet and exposed-port connectivity while
remaining unable to reach the host, other sandboxes, Docker networks, state
services, management networks, or cloud metadata. The host may already use
Docker, NetworkManager, GDM/desktop networking, bridges, VPNs, and nftables.
Replacing its firewall or claiming existing interfaces is unacceptable.

## Decision

Allocate a configurable, conflict-checked project address pool. Each sandbox
gets a unique network namespace, TAP device, runtime identity, and policy. The
worker connects them through a dedicated project bridge or equivalent construct
validated against upstream.

Own only the nftables table `inet kitdev_sandboxes` and exact project-created
links/namespaces. Never flush another table or replace `/etc/nftables.conf`.
Integrate through a dedicated include or service transaction selected after
host discovery. Install rules atomically and verify their loaded form.

Default guest policy denies:

- host and loopback-reachable host services;
- Docker/Podman and private/management networks unless explicitly allowlisted;
- control plane and datastore addresses/ports;
- link-local and cloud metadata addresses;
- multicast and sandbox-to-sandbox traffic.

DNS is allowed only to configured resolvers. HTTP/HTTPS internet egress is
profile-controlled. IPv6 is filtered with equivalent policy when enabled and
otherwise disabled on guest interfaces. Port exposure traverses the
authenticated proxy; no guest interface is publicly bridged.

## Consequences

- Isolation policy has one auditable project ownership boundary.
- Address selection must account for host routes, VPNs, Docker pools, desktop
  networking, and future route changes.
- NetworkManager must ignore project-owned transient links without modifying
  unrelated connection profiles.
- DNS and proxy workflows must not become routes around denied destinations.
- Rule ordering and host forwarding policy vary, requiring tests on both
  supported Ubuntu releases and server/desktop hosts.

## Alternatives considered

- Docker networking for microVMs: rejected because it couples the worker trust
  boundary to Docker and makes private-network denial harder to reason about.
- Global host firewall replacement: rejected because it violates coexistence
  and makes rollback unsafe.
- Shared guest bridge without namespaces: rejected because per-sandbox identity
  and cross-sandbox denial are weaker.
- Unrestricted NAT egress: rejected under the hostile-workload threat model.

## Validation

Before first untrusted execution, tests must prove denial of loopback-host
services, host bridge addresses, Docker gateways, state services, worker ports,
`169.254.169.254`, IPv6 equivalents, management/private routes, and another
sandbox. Rerun must not duplicate rules. Install/reboot/uninstall must preserve
unrelated nftables, NetworkManager, GDM, Docker, VPN, and network resources.

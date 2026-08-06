# ADR 0001: Split deployment between systemd and Docker Compose

- Status: proposed
- Date: 2026-08-06

## Context

State services benefit from Compose lifecycle and private networks. E2B API,
proxy, worker, and template operations interact with host devices, cgroups,
network namespaces, and service ordering. Putting every component in one
privileged container would expand the Docker socket/device boundary; putting
every datastore directly on the host would increase installation ownership and
uninstall risk.

## Decision

Run PostgreSQL, Redis, ClickHouse, and any required object storage or registry
in the dedicated `kitdev-sandboxes` Compose project. Use explicitly named
project networks, labels, and data directories.

Run the E2B API, client proxy, and orchestrator/template manager as systemd
services. API and proxy use dedicated unprivileged identities. The worker gets
only the host devices, writable paths, and capabilities demonstrated necessary
for the pinned upstream revision.

Ansible converges packages, accounts, directories, managed host files, Compose
definitions, and systemd units. The CLI coordinates phases and reports results;
it does not replace either lifecycle manager.

## Consequences

- Datastores remain private and can be backed up by a consistent project
  workflow.
- Host-integrated components have clear boot ordering, journald logs, and
  systemd hardening controls.
- Operators must diagnose two lifecycle systems, so `kitdev status` and logs
  must aggregate them.
- Compose resource identity and systemd unit ownership must be recorded exactly
  for narrow uninstall.

## Alternatives considered

- All services in Compose: rejected because privileged host integration would
  broaden container device/capability access and complicate nested networking.
- All services on the host: rejected because it increases package/configuration
  conflicts and makes state-service isolation less explicit.
- Kubernetes: rejected as disproportionate for a version 0.1 single host.

## Validation

Upstream discovery must confirm component ports, datastore requirements, and
whether any component fundamentally assumes container orchestration. Milestone
2 must prove dependency ordering, reboot recovery, health aggregation, and
uninstall without disturbing an unrelated Compose project or systemd unit.

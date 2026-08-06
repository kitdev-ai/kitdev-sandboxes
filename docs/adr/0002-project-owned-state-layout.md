# ADR 0002: Isolate project-owned files and durable state

- Status: proposed
- Date: 2026-08-06

## Context

The deployment shares a host with unrelated applications. Installation,
updates, backup, rollback, and uninstall need a reliable ownership boundary.
Keeping mutable data in a Git checkout or generic Docker volumes makes that
boundary ambiguous and can couple data lifetime to a release.

## Decision

Use these top-level paths exclusively:

| Path | Contents | Default uninstall |
| --- | --- | --- |
| `/opt/kitdev-sandboxes` | Versioned releases, verified tools, source/build outputs | Remove inactive/owned binaries after validation |
| `/etc/kitdev-sandboxes` | Non-secret config, root-only secrets, ownership metadata | Preserve unless explicitly requested |
| `/var/lib/kitdev-sandboxes` | Datastores, artifacts, templates, snapshots, workspaces, backups | Preserve |
| `/var/log/kitdev-sandboxes` | Project-owned file logs | Preserve/rotate by policy |
| `/run/kitdev-sandboxes` | Locks, sockets, PIDs, transient metadata | Remove when stopped |

Shared host changes use dedicated drop-ins such as modules-load, modprobe,
sysctl, and an owned nftables table/include. When a shared file truly must be
merged, parse it structurally, write atomically, save the prior state, and
record the exact mutation in the installation manifest.

The manifest gives every managed resource a type, stable ID, ownership marker,
desired-state hash, prior-state reference where applicable, creation/update
time, phase, and release. Destructive purge requires the installation ID and an
explicit confirmation independent of normal uninstall.

## Consequences

- Runtime state survives release checkout replacement and default uninstall.
- Backup and restore can enumerate data by declared lifecycle.
- Operators can inspect disk use and ownership without reverse-engineering
  anonymous volumes.
- File permissions and service identities need explicit design for every
  subdirectory.
- Paths alone are insufficient proof of ownership; purge also checks the
  manifest, canonical path, mount boundaries, and installation ID.

## Alternatives considered

- State in the repository: rejected because it mixes code and mutable runtime
  data and makes upgrades unsafe.
- Only Docker-managed named volumes: rejected because backup/ownership and
  host-capacity inspection become less explicit.
- Distribution-wide generic paths: rejected because collision and uninstall
  risk are higher on a shared host.

## Validation

Milestones 1 and 2 must test permissions, symlink/mount escape prevention,
atomic writes, manifest reconciliation, rerun preservation, default uninstall,
and confirmed purge. Coexistence tests seed similarly named unrelated resources
and verify they remain untouched.

# OVH Ubuntu 26.04 LXD read-only inventory

Date: 2026-08-06

## Purpose

Determine whether the operator's root-equivalent `lxd` group membership is in
active use before any automated membership change is planned.

## Method

A LUNA agent used bounded, non-interactive SSH with isolated temporary host-key
state. It ran only fixed read-only binary, package, unit, socket, process, and
resource-availability observations. It used no `sudo`, upload, file write,
package operation, service action, network change, or reboot. Temporary local
state was removed, and exact infrastructure identifiers remain only in the
ignored private inventory.

## Result

- No `lxc` or `lxd` executable was installed in the standard system or snap
  paths.
- No LXD snap was installed.
- No LXD daemon unit, standard daemon socket, or LXD process was present.
- No LXD resource API was available, so there were no instances, projects,
  storage pools, networks, or profiles available to enumerate.
- Ubuntu's `lxd-installer` package version `14ubuntu0` was installed.
- `lxd-installer.socket` was enabled, active, and listening. This is an
  installer activation shim, not a running LXD daemon.

These observations support making removal of the operator's `lxd` membership
eligible for deterministic planning. They do not authorize a manual group
change. The installer package and socket remain foreign operating-system state
and are excluded from the identity phase; their disposition requires a
separate reviewed package/service plan.

## Gate

The reproducible identity automation must re-run the complete normalized LXD
absence/non-use check immediately before apply. Unknown, changed, or active
state blocks the entire phase. Rollback may restore the prior membership only
from the exact write-ahead record. No server state changed during this
inventory.

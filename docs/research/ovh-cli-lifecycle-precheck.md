# OVH CLI lifecycle precheck

Date: 2026-08-07

Status: stopped before mutation because the manually assembled lab does not
have the installed deployment identity required by the new CLI

## Intent

The approved exercise was to stage exact repository commit
`15fcbac2e83e4cfebadd196480f9dd129629437d`, run structured status and dry-runs,
then prove `down`, `up`, `restart`, status, and the combined SDK smoke test on
the disposable Ubuntu 26.04 lab.

The exercise was coordinated with both active sandbox agents. They reported
terminal cleanup, zero Firecracker processes, no API sandboxes, and released
SDK locks before this precheck began.

## Read-only result

The first SSH operation used the existing verified transport and ran only
read-only process, service, and Docker inventory commands. It observed:

- zero Firecracker processes;
- no active service named `kitdev-e2b-orchestrator.service`;
- no containers carrying the new `kitdev-control-plane` Compose project label;
- no holder counted for the checked lifecycle/test lock paths.

The first lock diagnostic used an unsupported `fuser -s -- <path>` form. It
printed usage and returned nonzero. It did not create, remove, lock, unlock, or
write a file. The overall safety predicate remained blocked. A second
read-only query confirmed that the new orchestrator unit was not found and no
container used the new project label.

An independent supervisor check established the reason: the working lab still
runs the manually assembled legacy unit `kitdev-orchestrator-lab` and six
legacy control-plane containers. That legacy runtime was healthy, and
Firecracker remained at zero. The apparent zero-container result was an
ownership-label mismatch, not a service outage.

## Decision

No release checkout was staged on the server. No lifecycle command, service
start/stop/restart, Compose operation, SDK test, package operation, identity
change, filesystem layout change, firewall change, or configuration write was
performed.

The new CLI deliberately verifies its installed service bytes, unit name,
Compose project, network, environment, and ownership contract before day-two
operations. Installing the new unit beside the legacy unit or teaching the CLI
to adopt unlabeled/manual resources would create two owners for privileged
Firecracker, network, database, and API state. The exercise therefore stopped
without attempting `down` or `up`.

## Next gate

Reinstall the disposable host with clean Ubuntu 26.04, converge the corrected
reserved worker identity and prepared-host prerequisites, then run the new
installer so `/opt` assets, `kitdev-e2b-orchestrator.service`, and the
`kitdev-control-plane` Compose project share one manifest and lifecycle lock.
Only that deployment can qualify apply/apply, reboot, down/up/restart, and
combined official SDK smoke behavior.

Ubuntu 25.04 remains explicit development/migration-only. Ubuntu 24.04 is
unsupported.

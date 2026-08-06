# OVH disposable lab Stage 10 first-run diagnosis

Date: 2026-08-06
Run: `run-10-H2Ynnbd9`
Status: failed closed during the first read-only precheck; zero mutation

## Scope and evidence

The first approved Stage 10 invocation stopped before its `before` snapshot
with the stable reason `package_inventory_broken`. Stage 10 was plan-only and
the failure occurred before any resolution artifact was accepted. No package,
repository, keyring, APT metadata, service, account, storage, network, or
project-owned host state was changed.

The project lead then ran bounded read-only diagnostics against the same host.
They returned:

- `dpkg --audit`: exit zero, empty standard output, and empty standard error;
- `ca-certificates`, `curl`, and `ubuntu-keyring`: selected `install`, error
  state `ok`, and status `installed`;
- all eight Docker conflict package queries: exit one and absent.

These observations do not retroactively turn the failed run into a pass. They
show that the original public reason combined two distinct checks and was not
actionable enough to distinguish a transient audit result from a package
status error. Private package versions and host identifiers are intentionally
omitted.

## Reason-code correction

The Stage 10 resolver now reports bounded, non-sensitive reasons:

- `dpkg_audit_unavailable_pre` or `dpkg_audit_unavailable_post` when the audit
  probe cannot be invoked through the bounded runner;
- `dpkg_audit_failed_pre` or `dpkg_audit_failed_post` when `dpkg --audit`
  completes unsuccessfully;
- `dpkg_audit_dirty_pre` or `dpkg_audit_dirty_post` when the command succeeds
  but emits output;
- `package_status_error_probe_NN` when a fixed package probe reports a dpkg
  error state other than `ok`.

Probe indices are deterministic and reveal no discovered host data: `01-02`
are the two bootstrap prerequisites, `03-10` are the eight published Docker
conflicts in contract order, and `11` is the Ubuntu archive trust package.
Package names, versions, command output, and paths are not included in failure
evidence.

## Safety conclusion

The correction changes diagnosis only. It does not relax dpkg checks, retry a
failed probe, mutate the host, or authorize package apply. A future remote retry
requires a new approval bound to the corrected bundle. Stage 10 remains a
read-only cached inventory and simulation stage, and Stage 50 remains blocked.

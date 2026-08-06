# OVH disposable lab stages 00 and 30 first run

Date: 2026-08-06

## Scope and method

The approved disposable-lab runner executed stages `00` and `30` against the
Ubuntu 26.04 bare-metal lab. Both stages were read-only. The runner captured its
normal before, execute, after, and postcondition evidence locally and off-host.
No endpoint, address, account, host key, device name, serial, or private path is
retained here. Artifact run identifiers and paths are omitted because they were
not needed for the normalized finding and were not supplied to this report.

The execution used the private SSH-config boundary committed in `02d2af1`.
That commit is the completed implementation corresponding to the earlier
activity entry whose commit field said it was pending.

## Normalized results

| Stage | Runner result | Normalized observation | Evidence disposition |
| --- | --- | --- | --- |
| `00` | Passed | Supported Ubuntu 26.04/x86-64/systemd/cgroup-v2 baseline remained eligible. The Docker unit is absent, but the collector rendered `error`. | Baseline remained read-only; Docker service evidence is invalid pending the collector correction. |
| `30` | Passed | Three physical parent disks were counted. The collector incorrectly classified all three as raw because it inspected only each parent row's blank filesystem and mount fields. | Raw-candidate output is invalid and must not authorize disk selection. |

The earlier intake established that two NVMe parents contain the active system
RAID topology and only the separate approximately 4 TB SATA leaf is untouched.
The first stage-30 implementation did not inspect children, holders, slaves, or
MD membership, so its three-candidate result contradicted that topology without
detecting the contradiction.

## Corrections

Stage `30` now requests bounded `lsblk` JSON and parses it structurally in
Python. A candidate must be a physical `disk` leaf with exactly one supported
transport, positive byte size, no children or partitions, no filesystem, no
mount, no partition-table type, empty sysfs holders and slaves, and no MD path.
The parser emits only total disk count, candidate count, and candidate size. It
never emits a device name or serial and fails closed unless exactly one
candidate is proven.

The shared service collector now reads systemd `LoadState` first. `not-found`
maps to `absent`; loaded units are then classified through a bounded recognized
active-state set. Manager errors, malformed load states, and unrecognized
active states remain `error` and return nonzero.

Independent review found exact dot-component traversal in the first correction
and a lost executable-mode bit. The final parser rejects dot components, bounds
topology to 64 levels and 4,096 nodes, normalizes recursion failure, and remains
executable. Re-review approved the correction after 21 focused and 170 complete
unit tests passed.

## Mutation and next gate

The remote run made no project or host configuration change. It did not format,
partition, mount, adopt, wipe, or write any disk; it made no package, identity,
kernel, network, firewall, container, service, or reboot change. Ordinary SSH
and OS audit/session telemetry may have changed as already documented by the
lab runner contract.

**All storage mutation remains blocked.** The corrected stage must pass local
fixture and safety review, be committed, receive a new bundle-bound approval,
and be rerun read-only. Its anonymous single-candidate size must reconcile with
the private inventory before a separate crash-consistent storage action can be
designed or reviewed.

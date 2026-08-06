# OVH disposable lab stages 00 and 30 first run

Date: 2026-08-06

## Scope and method

The approved disposable-lab runner executed stages `00` and `30` against the
Ubuntu 26.04 bare-metal lab. Both stages were read-only. The runner captured its
normal before, execute, after, and postcondition evidence locally and off-host.
After the collector and approval-bound `known_hosts` corrections, both stages
were approved and rerun read-only. No endpoint, address, account, host key,
device name, serial, or private path is retained here.

The execution used the private SSH-config boundary committed in `02d2af1`.
That commit is the completed implementation corresponding to the earlier
activity entry whose commit field said it was pending.

## Normalized results

| Stage/run | Runner result | Normalized observation | Evidence disposition |
| --- | --- | --- | --- |
| Initial `00` | Runner completed | Supported Ubuntu 26.04/x86-64/systemd/cgroup-v2 baseline; the absent Docker unit was incorrectly rendered as `error`. | Superseded by the corrected approval-bound rerun. |
| Initial `30` | Runner completed | Three physical parent disks were incorrectly classified as raw from parent-row fields alone. | Invalid for disk selection and superseded by the corrected rerun. |
| Corrected `00` | Passed; operation, after, and postconditions all returned `0` | 8 logical CPUs; 67,193,135,104 memory bytes; root can read/write KVM; TUN present; NBD not loaded; zero huge pages; three block devices and three MD arrays; Docker absent; UFW active; firewall rules readable; one default route. | Valid read-only evidence, bound to the approved SSH config, verified `known_hosts`, and exact bundle. |
| Corrected `30` | Passed; operation, after, and postconditions all returned `0` | Three disks and exactly one anonymous raw unmounted candidate of 4,000,787,030,016 bytes; discovery only, format and mount forbidden. | Valid read-only evidence. The anonymous size reconciles with the private approximately 4 TB inventory. |

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

The corrected reruns used approvals bound to the exact source/config/bundle and
verified `known_hosts` snapshot. Their local summaries and redacted evidence
record all three return codes as zero. This report update reads those existing
off-host artifacts only; the Stage 05 implementation work did not open SSH or
run another remote command.

## Mutation and next gate

The remote run made no project or host configuration change. It did not format,
partition, mount, adopt, wipe, or write any disk; it made no package, identity,
kernel, network, firewall, container, service, or reboot change. Ordinary SSH
and OS audit/session telemetry may have changed as already documented by the
lab runner contract.

**All storage mutation remains blocked.** The corrected read-only stage and
private inventory now agree on one approximately 4 TB candidate, but that is
discovery evidence only. A separate exact, crash-consistent storage plan,
journal, rollback design, implementation, review, and bundle-bound approval are
required before any format, partition, mount, adopt, wipe, or write operation.

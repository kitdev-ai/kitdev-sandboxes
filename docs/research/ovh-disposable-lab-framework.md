# OVH disposable lab framework

Date: 2026-08-06

## Purpose and boundary

`experiments/ovh-lab/` records fixed experiments for the user-approved strategy:
learn on the disposable OVH Ubuntu 26.04 host, convert observations into
reusable repository automation, then reinstall the host and qualify only the
clean `kitdev`/Ansible path. The experiment harness is not production
automation and cannot waive that reinstall gate.

No server command was run while implementing or validating this framework. No
endpoint, address, account, host key, credential, or secret was added to the
repository. Runtime evidence defaults to the ignored local
`artifacts/ovh-lab/` directory, which is off the remote host.

## Implemented controls

- A machine-readable manifest fixes the eleven stages and distinguishes
  read-only, plan-only, mutation, executable, and blocked status.
- The local runner accepts only an operator-managed SSH alias. Both the SSH
  config and verified `known_hosts` input must be absolute, bounded to one MiB,
  current-user-owned regular non-symlink files read through stable
  `O_NOFOLLOW` descriptors. The SSH config permits no group/other bits and
  rejects `Include`; verified public host keys may be group/other readable but
  may not have execute, special, or group/other write bits. The inputs cannot
  share an inode. Execution creates distinct exclusive mode-0600 snapshots in
  the guarded local run directory and passes only those snapshots to every SSH
  phase. Command-line policy disables global known-host files,
  `KnownHostsCommand`, SSH host-key updates, DNS host-key verification, and
  connection sharing while requiring strict host-key checking. It streams the
  selected script to `sudo bash` without copying it to the host. Both snapshots
  are removed on ordinary exit and are never written to tracked evidence.
- The manifest selects the stage, and the local approval binds its operation,
  SSH alias, private SSH-config hash, verified-known-hosts hash, and exact
  streamed bundle hash. Every
  stage uses Bash strict mode, verifies Ubuntu
  26.04/x86-64/systemd/cgroup-v2, refuses known production/install markers,
  emits bounded normalized observations, and implements before, after,
  postcondition, and rollback modes.
- The runner applies defense-in-depth redaction before evidence reaches disk.
  Stage authors must still avoid emitting sensitive data because pattern-based
  redaction cannot establish secrecy. Each invocation is bounded to 90 seconds
  and one MiB of redacted output.
- Stage 05 is the only executable mutation. Its typed Python reconciler uses
  the existing `JournalStore` state machine through one operation-wide locked
  session, fixed no-follow paths, exact canonical marker/plan bytes,
  write-ahead transitions, bounded observations, and idempotent reconciliation
  and rollback. The approved bundle contains the exact reviewed journal and
  reconciler bytes and streams them through an anonymous pipe to isolated
  `/usr/bin/python3`; it installs no remote source.

## Executable versus blocked

| Stage | Current behavior | Reason |
| --- | --- | --- |
| `00` | Read-only baseline | Fixed normalized platform, KVM, capacity, storage, service, firewall-fingerprint, and route-count evidence. |
| `05` | Executable mutation | Journaled marker/workspace apply and exact rollback; local independent review approved, while separately approved disposable-host qualification remains required. |
| `10` | Blocked | Pinned Ubuntu package bootstrap and checksums are not approved. |
| `20` | Blocked | The identity plan was rejected pending write-ahead journaling and Ansible convergence. |
| `30` | Discovery/plan only | Reports anonymous raw-disk counts and sizes; formatting and mounting are forbidden. |
| `40` | Blocked | NBD parameters and huge-page values are not approved. |
| `50` | Blocked | Docker repository, package versions, daemon policy, and Compose ownership are not approved. |
| `60` | Blocked | Required ports, bind addresses, bridges, forwarding, and firewall ownership remain unresolved. |
| `70` | Blocked | The pinned upstream build graph and artifact verification flow are not implemented. |
| `80` | Blocked | Reviewed units, identities, dependencies, hardening, and health contracts do not exist. |
| `90` | Blocked | Acceptance requires completed reinstallable automation. |

## Recovery and promotion

Stage 05 can roll back only the exact marker and empty workspace directory
prefix it created, while retaining its journal. It removes the marker first
and refuses foreign or downstream content. Once a future approved experiment
changes packages, accounts, kernel state, storage, network, firewall,
containers, or services, that stage needs its own journal and rollback; the
authoritative lab recovery remains an OVH OS reinstall. Manual success is
useful discovery evidence only.

Promotion requires typed collectors, deterministic dry-run actions, dependency
pins and hashes, a crash-consistent manifest/journal, Ansible convergence and
rollback, security review, and clean Ubuntu 26.04 apply/apply tests after
reinstall. Those requirements prevent the experiment harness from becoming a
second, undocumented installer.

## Verification

The final Stage 05, journal, and lab-framework focused suite passes 99/99
with bytecode disabled. It verifies canonical bytes, deterministic embedded
sources, strict loader isolation, approval integrity, production refusal,
pristine and idempotent apply/rollback, all enumerated forward and reverse
crash points, suspicious residue, mount/link/symlink/ownership/mode/ACL/xattr
conflicts, durability-barrier replay, terminal-residue preservation, legal
cross-operation recovery, and same-process and multi-process lock behavior.
The complete suite passes 225/225. Independent LUNA review initially found
seven recovery defects plus one terminal-residue ambiguity during re-review;
all were corrected, independently reproduced, and approved with no blockers.
ShellCheck is unavailable in the local environment and is not a locked project
dependency. No SSH or remote mutation occurred during implementation and local
qualification.

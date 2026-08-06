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
- The local runner accepts only an operator-managed SSH alias, requires a
  regular non-symlink verified `known_hosts` file, and requires an absolute,
  regular, non-symlink SSH config owned by the invoking user with no group or
  other permissions. The config is limited to one MiB and its path is bounded
  and control-free. `Include` directives are rejected. The runner creates one
  transient mode-0600 snapshot in the guarded local run directory and passes
  only that immutable snapshot through every `ssh -F` call. It enables strict
  host-key checking, disables connection sharing, and streams the selected
  script to `sudo bash` without copying it to the host. The snapshot is removed
  on ordinary exit and is never written to tracked evidence.
- The manifest selects the stage, and the local approval binds its operation,
  SSH alias, private SSH-config hash, and exact streamed bundle hash. Every
  stage uses Bash strict mode, verifies Ubuntu
  26.04/x86-64/systemd/cgroup-v2, refuses known production/install markers,
  emits bounded normalized observations, and implements before, after,
  postcondition, and rollback modes.
- The runner applies defense-in-depth redaction before evidence reaches disk.
  Stage authors must still avoid emitting sensitive data because pattern-based
  redaction cannot establish secrecy. Each invocation is bounded to 90 seconds
  and one MiB of redacted output.
- No mutation is executable. Independent review rejected the marker/workspace
  transition because it lacked durable provenance, crash recovery, no-follow
  ancestry enforcement, and retry-safe rollback. It now fails closed.

## Executable versus blocked

| Stage | Current behavior | Reason |
| --- | --- | --- |
| `00` | Read-only baseline | Fixed normalized platform, KVM, capacity, storage, service, firewall-fingerprint, and route-count evidence. |
| `05` | Blocked | Crash-consistent provenance and retry-safe rollback are not implemented. |
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

No lab rollback runs in this revision because no mutation runs. Once a future
approved experiment changes packages, accounts, kernel state, storage, network,
firewall, containers, or services, the authoritative lab recovery is an OVH OS
reinstall. Manual success is useful discovery evidence only.

Promotion requires typed collectors, deterministic dry-run actions, dependency
pins and hashes, a crash-consistent manifest/journal, Ansible convergence and
rollback, security review, and clean Ubuntu 26.04 apply/apply tests after
reinstall. Those requirements prevent the experiment harness from becoming a
second, undocumented installer.

## Verification

Fifteen focused repository tests passed. The complete unit suite passed 164/164
with bytecode disabled. The static coverage verifies the full manifest sequence,
Bash syntax, strict mode, acknowledgement and production gates, postcondition/rollback
surfaces, blocked-stage absence of guessed mutation commands, verified-host-key
runner options, local evidence location, and absence of literal endpoints or
private keys. ShellCheck was unavailable in the local environment and is not a
locked project dependency yet. Independent review rejected the initial mutable
marker stage; this revision removed that mutation. External termination can
still interrupt after/postcondition collection, which is acceptable only while
all executable stages remain read-only.

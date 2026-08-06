# OVH Ubuntu 26.04 ephemeral remote test

Date: 2026-08-06

## Result

The current nonignored worktree passed its first reproducible ephemeral test on
the Ubuntu 26.04 bare-metal development server. Python 3.14.4 ran 145 unit tests
with exit 0. Read-only JSON smoke tests for `doctor`, general `install
--dry-run`, and identity-access `install --dry-run` each returned the expected
blocking exit 5. Their output parsed as JSON and their standard error streams
were empty.

The fixed project-owned host roots were absent before and after the run:

- `/etc/kitdev-sandboxes`
- `/opt/kitdev-sandboxes`
- `/var/lib/kitdev-sandboxes`
- `/var/log/kitdev-sandboxes`

The before and after snapshots were identical. The extracted repository tree
was also identical before and after testing, no `__pycache__`, `.pyc`, or `.pyo`
entry appeared, and an independent follow-up SSH command confirmed that the
unique remote temporary directory had been deleted.

This is evidence for the read-only test path only. It does not authorize an
install or prove that a mutating bootstrap/apply/rollback workflow is ready.

## Reproducible harness

[`scripts/dev/ovh-remote-test.sh`](../../scripts/dev/ovh-remote-test.sh) is the
repository-owned procedure. It accepts a constrained `user@host` only as an
argument or `KITDEV_TEST_SSH_TARGET`; it never hardcodes the server. The
expected ED25519 SHA-256 fingerprint is required separately in
`KITDEV_TEST_SSH_FINGERPRINT`. The endpoint and fingerprint remain only in the
ignored private operator inventory and are not emitted by the successful run.

The local archive is built from sorted `git ls-files --cached --others
--exclude-standard` output. Archive ownership, names, and modification times
are normalized; ignored private inventory and Git metadata are excluded. The
harness refuses empty or traversal-bearing repository entries and non-file,
non-symlink entries.

SSH uses batch authentication, one connection attempt, a ten-second connection
timeout, no connection sharing, no global or normal user `known_hosts`, strict
host-key checking, and an isolated mode-0600 `known_hosts`. Before login, an
ED25519 key scan must exactly match the privately supplied expected
fingerprint. This pins the run to the prior observation for consistency. The
fingerprint remains **independently unverified** until it is compared with the
OVH control plane or recovery console over a separate trust path.

The remote directory must match `/tmp/kitdev-test-[A-Za-z0-9_-]+`. It is created
mode 0700, and both the remote command and local caller arm guarded cleanup. The
test invocation is bounded to 300 seconds; creation, upload, verification, and
cleanup operations have shorter bounds. Cleanup refuses an empty, root, linked,
or nonmatching path and deletes only within the guarded temporary filesystem
tree. The remote EXIT trap removed the directory in this run, and the local
fallback was not needed.

## Mutation boundary

The only intentional remote writes were the unique mode-0700 temporary
directory, its uploaded deterministic archive, extracted worktree, and test
result files. All were removed before success was reported. Normal SSH and OS
audit/session records may have changed as a consequence of logging in.

The harness contains no `sudo`, package, service, account/group, firewall,
network, kernel, disk, mount, or reboot operation. It neither read nor modified
the raw data disk. The CLI smoke tests ran with `PYTHONDONTWRITEBYTECODE=1` and
only exercised commands whose public contract is read-only.

## Verification and limitations

- `bash -n scripts/dev/ovh-remote-test.sh`: passed locally.
- `git diff --check`: passed locally.
- ShellCheck: unavailable on the development workstation, so this optional
  static lint remains pending; no package was installed to obtain it.
- Local unit fallback through the workstation's default Apple Python 3.9 was
  inapplicable because the project uses `enum.StrEnum`. The authoritative
  remote run used Python 3.14.4 and passed all 145 tests.
- The surface snapshot covers existence and root-object stat metadata for the
  four exact project roots. Because every root was absent, unchanged absence
  proves this run created no persistent project subtree. It is not a general
  whole-host filesystem attestation.
- No privileged collector or KVM workload ran. KVM access, identity setup,
  firewall evidence, storage adoption, host bootstrap, apply, and rollback
  remain separate blocked/reviewed work.

## Rollback

No persistent project state was created, so no project rollback is required.
If a future interrupted run leaves a matching temporary directory, rerunning
the harness cleanup with the same privately pinned SSH identity removes only
that guarded path. Any nonmatching or linked path is deliberately refused and
requires manual inspection before action.

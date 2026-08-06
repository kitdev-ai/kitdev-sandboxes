# Maintenance scripts

Small, independently testable scripts may support artifact builds, migration,
backup, and diagnostics in later milestones. Installation orchestration does not
belong in one large shell script. Every shell entrypoint uses strict mode,
bounded inputs, explicit paths, and ShellCheck.

Development-only scripts live under `dev/`. `dev/ovh-remote-test.sh` packages
the current nonignored worktree deterministically and runs unit tests plus
read-only CLI smoke tests in a guarded, self-cleaning remote `/tmp` directory.
It requires the SSH target and expected host-key fingerprint at invocation time;
neither value belongs in the repository. The script does not use `sudo` or
change packages, services, identities, firewall rules, kernel settings, or
disks.

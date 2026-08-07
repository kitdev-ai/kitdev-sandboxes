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

`control-plane/verify-typescript-sdk-e2e.sh` is a development/migration-only
live gate for the official TypeScript SDK. It requires root-owned mode-0600 API
key and template-ID files, installs the exact lockfile with a digest-pinned
Node.js 22.18.0 image, and runs through the loopback API and client proxy. The
runner rejects a pre-existing Firecracker process and cleans its sandbox on
every exit path.

```sh
sudo KITDEV_LIFECYCLE=development \
  scripts/control-plane/verify-typescript-sdk-e2e.sh \
  --api-key-file /run/kitdev-sandboxes/e2e-api-key \
  --template-id-file /run/kitdev-sandboxes/e2e-template-id
```

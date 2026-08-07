# Control-plane lifecycle

The repository-local `kitdev` command exposes the first reviewed lifecycle
slice for the minimal control-plane profile. Ubuntu 26.04 is production
eligible. Ubuntu 25.04 requires an explicit `development` or `migration`
lifecycle. Ubuntu 24.04 is unsupported.

## Installation boundary

`sudo ./kitdev install` currently converges the pinned control-plane assets
only in explicit `development` or `migration` mode and only after a
strict gate proves that packages, the reserved worker identity, KVM, TUN, NBD,
IPv4 forwarding, hugepages, Docker, and UFW are already prepared. The command
does not create those prerequisites. It refuses `standard` and `full` apply,
foreign state, and incomplete prerequisites before the control-plane sequence.
Production install fails before mutation because its production-qualified
template publication path is not implemented yet. Production `up`, `down`,
`restart`, and `status` remain valid for an already installed control plane.

The existing `./kitdev install --dry-run` remains the non-mutating host plan.
It does not imply that prerequisite apply is implemented.

Install publishes day-two shell assets under
`/opt/kitdev-sandboxes/libexec/control-plane`. Subsequent repository-local
`up`, `down`, `restart`, `status`, and `test` dispatches re-execute that
installed copy. Installing a standalone `/usr/local/bin/kitdev` launcher and
Python package is still pending.

## Service lifecycle

```console
sudo ./kitdev up
sudo ./kitdev status
sudo ./kitdev status --json
sudo ./kitdev restart
sudo ./kitdev down
```

`down` preserves databases, templates, images, networks, configuration, and
containers. It refuses a running Firecracker process. It first quiesces API
and client-proxy admission, checks again for a racing sandbox, stops the host
orchestrator, and then stops Compose services. A failure after quiescing
attempts to restore the prior running service set.

`status` is non-mutating and does not take the lifecycle lock. Its structured
result reports orchestrator, Compose, API, proxy, and Firecracker state.

## Post-install tests

Tests mutate runtime state and are accepted only in `development` or
`migration` mode. Credential and template values must be supplied through
absolute root-owned, mode-0600, single-link files; the CLI passes only their
paths through a clean environment.

`core` exercises the API, Firecracker, client proxy, and envd using the pinned
Go client. `sdk` exercises the official pinned TypeScript SDK. `smoke` runs
both gates and requires both files.

```console
sudo ./kitdev test smoke --lifecycle-mode development \
  --api-key-file /run/kitdev-sandboxes/e2e-api-key \
  --template-id-file /run/kitdev-sandboxes/e2e-template-id
```

No stable credential or template-ID pathname is provisioned yet. Operators
must explicitly create the two transient files; tracked documentation never
contains their values.

## Remaining gaps

Fresh-host package, identity, kernel, Docker, and firewall prerequisite apply;
the standalone installed CLI; complete manifest/journal integration; update,
uninstall, backup/restore, and non-minimal profiles remain unimplemented. The
slice requires clean Ubuntu 26.04 apply/apply, reboot, restart, failure
recovery, and reinstall qualification before production use.

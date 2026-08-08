# kitdev-sandboxes

`kitdev-sandboxes` is an open-source deployment system for running an
E2B-compatible sandbox platform on one bare-metal Ubuntu server. The target
runtime uses Firecracker microVMs and assumes sandbox workloads are hostile.

## Project status

A running deployment on an Ubuntu 26.04 bare-metal lab **serves the official
E2B TypeScript SDK to external clients over trusted public HTTPS.** The full
`e2b@2.38.0` feature matrix passes from a separate host: 42 of 42 checks
covering lifecycle, commands, PTY, files and watch, wildcard guest HTTP,
chunked streaming, WebSocket upgrades, pause/resume, snapshots, and a Chromium
sandbox driven by Playwright. Only TCP 443 is reachable from the Internet.

Measured capacity on that host: **12** concurrent 2 GiB sandboxes or **3**
concurrent 8 GiB browser sandboxes, both exactly `hugepage pool / per-sandbox
RAM`. Beyond the pool, individual creates fail cleanly and running sandboxes
are unaffected.

That deployment was **assembled by hand and then progressively brought under
reviewed automation** — it is not yet the output of a one-command install. The
fresh-host installer remains in **Milestone 1: preflight and host preparation
(in progress)**: `doctor` and `install --dry-run` are strictly read-only, and a
prepared-host minimal-profile lifecycle wires the pinned control-plane assets
into `install`, `up`, `down`, `restart`, `status`, and post-install tests.

The prepared-host lifecycle has not passed clean-host prerequisite apply,
apply/apply, rollback, reboot, or reinstall qualification. It supports only the
minimal profile and does not yet install a standalone CLI. **Do not treat the
current tree as production-qualified.** In particular the live lab's
control-plane firewall is operator-managed, so its public exposure runs under
an explicit development-only acknowledgement.

Version 0.1 recognizes this host matrix:

- Ubuntu 26.04 LTS on x86-64 for production
- Ubuntu 25.04 on x86-64 only for explicit development/migration compatibility,
  because that release is end-of-life
- systemd, cgroups v2, and working KVM virtualization
- one bare-metal host

Server and desktop editions are capability-qualified. Desktop services such as
GDM must coexist without port, network-manager, device, or resource conflicts;
the edition label alone is not a rejection criterion.

Run the current read-only checks from the repository root. Ubuntu 25.04 must use
an explicit development or migration lifecycle mode:

```console
./kitdev doctor --lifecycle-mode development
```

The repository-local `./kitdev` launcher is the only supported entrypoint in
this slice. Day-two shell assets are published under `/opt`, but an installed
package console script is still pending.

Use `--json` for the versioned machine-readable report and `--verbose` to include
bounded evidence. `--dry-run` is accepted for CLI consistency, but `doctor` is
always read-only and always proposes zero changes:

```console
./kitdev doctor --lifecycle-mode development --json --verbose --dry-run
```

Complete normalized fact groups now replace their initial scope sentinels, but
required-port policy remains blocking `unknown`; the command must not be treated
as full host qualification. A deterministic `install --dry-run` is available:

```console
./kitdev install --dry-run --json
```

The dry-run remains blocked while required-port policy is unapproved. Bare
`kitdev install` now applies only the minimal development/migration control
plane after strict prepared-host gates. Production install refuses before
mutation until production template publication is implemented. It does not
prepare a fresh host or replace the pending
journaled prerequisite installer. See [control-plane lifecycle](docs/operations.md).

## Design priorities

- Preserve unrelated host services, containers, networks, firewall rules, and
  configuration.
- Keep project state under dedicated `/opt`, `/etc`, `/var/lib`, `/var/log`,
  and `/run` paths.
- Separate unprivileged API/proxy services from the privileged microVM worker.
- Pin and verify every external artifact before it reaches a host.
- Make installation convergent, resumable, auditable, and reversible.
- Bind interfaces to loopback unless public exposure is explicitly configured.

## Repository map

| Path | Responsibility |
| --- | --- |
| `config/` | Versioned defaults and JSON Schema |
| `ansible/` | Local convergence playbooks and roles, starting in Milestone 1 |
| `compose/` | Digest-pinned project-private control-plane services |
| `systemd/` | Host-integrated service definitions |
| `templates/` | Versioned guest template build inputs |
| `networking/` | Project-owned network policy and nftables inputs |
| `tests/` | Unit, smoke, integration, and security acceptance tests |
| `scripts/external-sdk-matrix/` | Off-host official-SDK qualification and capacity probes |
| `docs/` | Architecture, decisions, discovery, and milestone contracts |

Start with [the architecture](docs/architecture.md), [the preflight design](docs/preflight-design.md),
and [the milestone plan](docs/milestone-plan.md). Collected evidence and
compatibility findings are kept separately in [research](docs/research/README.md).
Operators should use the [bare-metal operator guide](docs/bare-metal-operator-guide.md).
External product agents should use the
[official TypeScript SDK integration guide](docs/typescript-sdk-integration-guide.md).
Browser-template qualification has a separate
[operator gate guide](docs/browser-sandbox-guide.md).
The complete product brief is kept in `PROMPT.md`.

## Development

See `CONTRIBUTING.md` before contributing. Both `kitdev doctor` and
`kitdev install --dry-run` retain read-only safety contracts. Prepared-host
control-plane apply and day-two operations now have a top-level CLI, while
fresh-host preparation remains unavailable. The lifecycle remains pending
clean-host qualification. The
dependency-free unit-test command is documented in
[`tests/README.md`](tests/README.md).

## License

Licensed under Apache License 2.0. See `LICENSE`.

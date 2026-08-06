# kitdev-sandboxes

`kitdev-sandboxes` is an open-source deployment system for running an
E2B-compatible sandbox platform on one bare-metal Ubuntu server. The target
runtime uses Firecracker microVMs and assumes sandbox workloads are hostile.

## Project status

The project is in **Milestone 1: preflight and host preparation (in progress)**.
The repository provides dependency-free, strictly read-only `doctor` and
`install --dry-run` foundations, but it does not yet install, prepare, or run
services. Do not use it to qualify or operate a production host yet.

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
this slice. An installed package console script will not be provided until its
configuration assets and installation layout have a complete contract.

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

It remains blocked while required-port policy is unapproved. Bare `install`,
apply, bootstrap, and all host mutation remain absent.

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
| `compose/` | Project-private state services, starting in Milestone 2 |
| `systemd/` | Host-integrated service definitions |
| `templates/` | Versioned guest template build inputs |
| `networking/` | Project-owned network policy and nftables inputs |
| `tests/` | Unit, smoke, integration, and security acceptance tests |
| `docs/` | Architecture, decisions, discovery, and milestone contracts |

Start with [the architecture](docs/architecture.md), [the preflight design](docs/preflight-design.md),
and [the milestone plan](docs/milestone-plan.md). Collected evidence and
compatibility findings are kept separately in [research](docs/research/README.md).
The complete product brief is kept in `PROMPT.md`.

## Development

See `CONTRIBUTING.md` before contributing. Both `kitdev doctor` and
`kitdev install --dry-run` have read-only safety contracts in the current
slice; no apply or host-preparation command is available. The dependency-free
unit-test command is documented in
[`tests/README.md`](tests/README.md).

## License

Licensed under Apache License 2.0. See `LICENSE`.

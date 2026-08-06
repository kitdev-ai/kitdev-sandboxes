# kitdev-sandboxes

`kitdev-sandboxes` is an open-source deployment system for running an
E2B-compatible sandbox platform on one bare-metal Ubuntu server. The target
runtime uses Firecracker microVMs and assumes sandbox workloads are hostile.

## Project status

The project is in **Milestone 0: discovery and architecture**. This repository
does not yet install or run services. Do not use it on a production host.

Version 0.1 recognizes this host matrix:

- Ubuntu 26.04 LTS on x86-64 for production
- Ubuntu 25.04 on x86-64 only for explicit development/migration compatibility,
  because that release is end-of-life
- systemd, cgroups v2, and working KVM virtualization
- one bare-metal host

Server and desktop editions are capability-qualified. Desktop services such as
GDM must coexist without port, network-manager, device, or resource conflicts;
the edition label alone is not a rejection criterion.

The intended operator entrypoint is eventually:

```console
sudo ./kitdev install
```

That entrypoint is intentionally absent until Milestone 1 defines preflight,
dry-run, and host-change behavior.

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

Milestone 0 changes are documentation and scaffold only. See
`CONTRIBUTING.md` before contributing. No command in the repository should be
assumed safe to run against a host until Milestone 1 is reviewed.

## License

Licensed under Apache License 2.0. See `LICENSE`.

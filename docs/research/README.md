# Research reports

This directory contains collected evidence and compatibility analysis produced
during discovery or later investigations. Examples include host inventories,
upstream repository/API analysis, dependency lifecycle findings, benchmarks,
and experiment results.

Research reports are dated, identify their sources and collection method, and
separate observed facts from inference. They may contain unresolved findings
and do not establish project policy by themselves. Durable decisions belong in
`../adr/`; normative system design, preflight behavior, dependency policy, and
milestone gates remain in their existing documents directly under `docs/`.

Host reports must be redacted before commit. Never record credentials, tokens,
private keys, secret values, or unnecessary personal/host identifiers.

## Reports

- [`cloud-options.md`](cloud-options.md): GCP, AWS, hosted bare-metal, and local
  nested-virtualization deployment comparison.
- [`host-discovery.md`](host-discovery.md): read-only `kit@pc` capability and
  coexistence inventory.
- [`milestone-1-pc-doctor.md`](milestone-1-pc-doctor.md): temporary read-only
  execution of the first Milestone 1 doctor slice on the shared development PC.
- [`milestone-1-safety-review.md`](milestone-1-safety-review.md): release-gate
  checklist for non-mutating configuration, doctor, process execution,
  redaction, lifecycle policy, JSON contracts, and shared-host testing.
- [`ovh-host-plan.md`](ovh-host-plan.md): Ubuntu-versus-Proxmox decision,
  OVHcloud purchase choices, provisioning paths, and read-only bring-up plan.
- [`upstream-e2b.md`](upstream-e2b.md): pinned upstream revisions, contracts,
  architecture, and host requirements.

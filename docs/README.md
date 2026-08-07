# Design documentation

Milestone 0 establishes contracts before implementation:

- `architecture.md` describes the proposed system and trust boundaries.
- `preflight-design.md` specifies read-only discovery, validation, and dry-run.
- `milestone-plan.md` defines incremental delivery and review gates.
- `dependency-management.md` defines pinning and lock generation policy.
- `adr/` contains decisions whose consequences span multiple components.
- `research/` contains dated host, upstream, and compatibility evidence.
- `operations.md` documents the implemented prepared-host control-plane
  lifecycle and its remaining fresh-install boundary.
- `typescript-sdk-integration-guide.md` gives external product agents the
  exact official SDK configuration, proven examples, and ingress limitations.
- `bare-metal-operator-guide.md` is the practical operator runbook, including
  exact implemented commands and clearly labeled unimplemented workflows.
- `../experiments/ovh-lab/` contains the gated disposable-host experiment
  harness; it is not production installation automation.

Host discovery and upstream compatibility reports are produced independently
under `research/` and must be reviewed before these proposed decisions become
final. Operational installation, configuration, security, template, upgrade,
and recovery guides will be added with the milestones that implement and test
those behaviors.

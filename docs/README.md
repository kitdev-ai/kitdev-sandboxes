# Design documentation

Milestone 0 establishes contracts before implementation:

- `HANDOVER.md` is the clean-resume checkpoint for a new project lead: current
  live state, capacity model, security boundaries, evidence, the
  dependency-ordered backlog, rollback, and hard-won lessons. It supersedes the
  former `PROJECT-HANDOVER.md` and `open-tasks.md`.
- `vision.md` explains how templates, images and snapshots actually work, what
  the mechanism makes possible that is unexploited, how the architecture scales
  to multiple worker nodes, and the product directions under consideration with
  the infrastructure each demands. Forward-looking, not a status claim.
- `fresh-host-remediation-plan.md` is the ordered plan to make a fresh install
  actually work end to end, with the decisions, gates, and unresolved risks.
- `fresh-server-installation.md` is the stage-by-stage runbook for standing the
  platform up on a newly installed bare-metal Ubuntu server, marking which
  stages are automated and which remain manual.
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
  exact implemented lifecycle and API-key commands plus clearly labeled
  unimplemented workflows.
- `browser-sandbox-guide.md` is the development-only Chromium/Playwright
  template qualification procedure and its public-ingress limitations.
- `disaster-recovery.md` defines the implemented offline backup format, secret
  boundary, restore gates, and remaining live qualification.
- `../experiments/ovh-lab/` contains the gated disposable-host experiment
  harness; it is not production installation automation.

Host discovery and upstream compatibility reports are produced independently
under `research/` and must be reviewed before these proposed decisions become
final. Operational installation, configuration, security, template, upgrade,
and recovery guides will be added with the milestones that implement and test
those behaviors.

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

- [`backup-restore-contract.md`](backup-restore-contract.md): first offline
  physical backup format, durable-state inventory, integrity and compatibility
  gates, secret exclusion, interrupted-run recovery, and live test plan.
- [`host-prerequisite-ansible-contract.md`](host-prerequisite-ansible-contract.md):
  pinned Ansible controller, fresh-host role boundaries, kernel persistence,
  rollback ownership and local verification for Ubuntu 26.04/25.04.
- [`hugepage-capacity-model.md`](hugepage-capacity-model.md): derived 24 GiB
  HugeTLB profile for 8 GiB heavy sandboxes, transient build/snapshot mapping
  semantics, normal-memory guards, upstream evidence, and remaining load gates.
- [`activity-log.md`](activity-log.md): append-only, redacted project activity
  ledger updated before each commit and review or deployment gate.
- [`cloud-options.md`](cloud-options.md): GCP, AWS, hosted bare-metal, and local
  nested-virtualization deployment comparison.
- [`cli-lifecycle-slice.md`](cli-lifecycle-slice.md): prepared-host install,
  service lifecycle, structured status, safe shutdown, and E2E CLI integration.
- [`control-plane-replay-slice.md`](control-plane-replay-slice.md): pinned
  Compose, host-runtime, firewall, identity, and persistent orchestrator replay
  derived from the successful disposable-host control-plane run.
- [`e2b-typescript-sdk-self-host-contract.md`](e2b-typescript-sdk-self-host-contract.md):
  exact `e2b@2.38.0` artifact/runtime pin, self-host authentication, API-key,
  `sandbox.kitdev.ai` ingress/TLS, public feature surface, and live
  compatibility gates.
- [`host-discovery.md`](host-discovery.md): read-only `kit@pc` capability and
  coexistence inventory.
- [`milestone-1-pc-doctor.md`](milestone-1-pc-doctor.md): temporary read-only
  execution of the first Milestone 1 doctor slice on the shared development PC.
- [`milestone-1-read-only-integration.md`](milestone-1-read-only-integration.md):
  implementation, limitations, safety properties, verification, and rollback
  for composed doctor and `install --dry-run`.
- [`milestone-1-safety-review.md`](milestone-1-safety-review.md): release-gate
  checklist for non-mutating configuration, doctor, process execution,
  redaction, lifecycle policy, JSON contracts, and shared-host testing.
- [`ovh-26-04-intake.md`](ovh-26-04-intake.md): zero-intentional-mutation
  first-login qualification and redacted evidence procedure for the direct
  Ubuntu 26.04 OVH host.
- [`ovh-26-04-e2b-prerequisites.md`](ovh-26-04-e2b-prerequisites.md): official
  Ubuntu 26.04, Docker, KVM/Firecracker, NBD, storage and networking
  prerequisites with a fail-closed staged qualification sequence.
- [`ovh-26-04-first-intake.md`](ovh-26-04-first-intake.md): redacted results,
  blockers, and next gates from the first command-by-command OVH inventory.
- [`ovh-26-04-identity-access-plan.md`](ovh-26-04-identity-access-plan.md):
  rejected first review of the automation-only operator recovery and
  least-privilege identity plan; it authorizes no host change.
- [`ovh-26-04-lxd-inventory.md`](ovh-26-04-lxd-inventory.md): bounded read-only
  evidence for LXD non-use and the remaining installer-shim boundary.
- [`ovh-lab-stages-00-30-first-run.md`](ovh-lab-stages-00-30-first-run.md):
  normalized first disposable-lab baseline/storage run, discovered collector
  defects, zero-mutation result, and continued storage block.
- [`ovh-stage05-first-run.md`](ovh-stage05-first-run.md): normalized Stage 05
  initial apply and idempotent reapply evidence, exact immutable hashes,
  mutation boundary, and remaining rollback/reinstall gates.
- [`ovh-stage10-official-prerequisites.md`](ovh-stage10-official-prerequisites.md):
  official Ubuntu and Docker package/repository contract for the two-phase,
  reproducible Stage 10 plan.
- [`ovh-stage10-first-run.md`](ovh-stage10-first-run.md): first Stage 10
  fail-closed result, zero-mutation diagnosis, and actionable reason-code
  correction.
- [`ovh-26-04-remote-test.md`](ovh-26-04-remote-test.md): reproducible ephemeral
  Ubuntu 26.04 unit and read-only CLI verification with before/after evidence.
- [`ovh-host-plan.md`](ovh-host-plan.md): Ubuntu-versus-Proxmox decision,
  OVHcloud purchase choices, provisioning paths, and read-only bring-up plan.
- [`ovh-disposable-lab-framework.md`](ovh-disposable-lab-framework.md): staged,
  gated experiment harness, executable/blocked boundary, evidence controls, and
  reinstall-to-production promotion rule.
- [`ovh-docker-bootstrap-manual-run.md`](ovh-docker-bootstrap-manual-run.md):
  approved disposable-host package/repository/Docker mutation, exact observed
  versions and trust pins, interrupted-attempt diagnosis, and rollback caveats.
- [`ovh-live-lab-services-first-run.md`](ovh-live-lab-services-first-run.md):
  normalized firewall, hugepage, pinned build, persistent database, migration,
  and seed results with stage ownership and rollback blockers.
- [`ovh-api-client-proxy-e2e.md`](ovh-api-client-proxy-e2e.md): live pinned
  snapshot-to-API-to-client-proxy command assertion, discovered storage,
  discovery, Docker gateway, and firewall requirements, plus verified cleanup.
- [`ovh-cli-lifecycle-precheck.md`](ovh-cli-lifecycle-precheck.md): zero-mutation
  stop when the new CLI found the healthy lab still owned by legacy manual
  service/container identities rather than its installed lifecycle contract.
- [`pinned-api-e2e-readiness-contract.md`](pinned-api-e2e-readiness-contract.md):
  pinned OpenAPI shapes and credential-safe live predicates for node, template,
  sandbox-create, and terminal-state verification.
- [`upstream-e2b.md`](upstream-e2b.md): pinned upstream revisions, contracts,
  architecture, and host requirements.

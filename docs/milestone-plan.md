# Milestone plan

## Delivery rules

Each milestone is independently reviewable, documents its assumptions, and has
an explicit entry and exit gate. Work does not advance merely because code is
present: required tests and rollback behavior must pass. Host changes happen
only after Milestone 0 review and explicit operator approval.

Security is incremental. The minimum network isolation and resource controls
needed to execute hostile code must land with the first runnable microVM;
Milestone 6 verifies and hardens the complete platform rather than introducing
those controls for the first time.

## Milestone 0: discovery and architecture

Deliver:

- Read-only host inventory and compatibility/conflict report.
- Ubuntu 26.04 LTS production and Ubuntu 25.04 development/migration matrix,
  plus capability-based server/desktop coexistence findings including GDM and
  NetworkManager.
- Pinned upstream repositories and artifact compatibility matrix.
- Repository scaffold, configuration contract, architecture, ADRs, preflight
  design, and milestone plan.
- Recorded risks, unresolved decisions, and exact Milestone 1 boundary.

Exit gate: reviewers accept the host as a viable test target, approve upstream
pins and architecture, and authorize only the documented Milestone 1 changes.
No runtime component is installed.

## Milestone 1: preflight and host preparation

Status: in progress. The current foundation slice includes typed configuration,
the root CLI, and an initial read-only `doctor`. `install --dry-run`, full host
qualification, Ansible bootstrap, apply, rollback, and host preparation remain
required by this milestone and are not yet implemented.

Deliver:

- Typed CLI foundation with `doctor`, `install --dry-run`, and versioned JSON.
- Configuration merge/validation and redacted reporting.
- Local Ansible bootstrap with exact Python dependency hashes.
- Idempotent project users/directories and only approved KVM/NBD/huge-page host
  preparation.
- Installation manifest, phase journal, backup of managed shared state, and
  uninstall of Milestone 1-owned resources.

Exit gate: Ubuntu 26.04 LTS production and Ubuntu 25.04 development/migration
server/desktop fixture tests pass; Ubuntu 25.04 production fails before
mutation; supported/unsupported capability cases are distinguished; two
dry-runs produce zero mutation; two applies converge; reboot persistence is
tested with approval; unrelated host resources including GDM remain unchanged;
rollback restores the baseline.

## Milestone 2: minimal E2B core

Deliver:

- Verified pinned sources and Firecracker/kernel/rootfs artifacts.
- Private PostgreSQL, Redis, and ClickHouse services required by upstream.
- systemd API, proxy, and worker services with initial privilege separation.
- Base template and one SDK-driven command/file sandbox workflow.
- Minimum safe per-VM CPU, memory, PID, disk, output, timeout, TTL, TAP/netns,
  deny-private-network, and metadata isolation controls.

Exit gate: official supported SDK creates, uses, and destroys a sandbox; core
security reachability tests pass; repeat install preserves state and secrets;
reboot recovery and narrow uninstall are tested. No public exposure.

## Milestone 3: coding workflow

Deliver:

- Immutable coding template with checksummed toolchain inputs.
- TypeScript and Python examples using the supported SDK contract.
- Command streaming/PTY, file transfer/watch, port exposure, reconnect, TTL,
  pause/resume/snapshot capabilities that upstream research confirms.
- Persistent workspace semantics if accepted for this release boundary.

Exit gate: compatibility matrix tests pass for both SDKs and each claimed
operation; template rebuilds are reproducible or differences are explained.

## Milestone 4: browser workflow

Deliver:

- Chromium/Firefox and Playwright/CDP browser template.
- Navigation, screenshot, download/artifact, and teardown behavior.
- Browser profile persistence only with defined isolation and cleanup semantics.

Exit gate: browser acceptance tests pass repeatedly without leaked browser or
VM processes and without network policy regression.

## Milestone 5: desktop workflow

Deliver:

- Pinned E2B Desktop-compatible template and lightweight desktop stack.
- Authenticated streaming, screenshot, pointer, keyboard, scrolling, window,
  app-launch, resolution, and DPI operations supported by upstream.
- TypeScript and Python end-to-end examples.

Exit gate: both examples pass from sandbox creation through destruction;
stream URLs expire and never appear in logs; desktop processes are cleaned up.

## Milestone 6: complete security validation

Deliver:

- Traced least-privilege systemd hardening and AppArmor decisions.
- Complete IPv4/IPv6 nftables policy and bandwidth controls where practical.
- Exhaustion, cross-sandbox, host/control/state-plane, credential, persistence,
  browser, template provenance, and coexistence tests.
- Threat model and residual-risk documentation.

Exit gate: all security and coexistence acceptance tests pass after fresh
install, rerun, reboot, update simulation, and uninstall. Exceptions require a
documented risk owner and cannot contradict the hostile-workload assumption.

## Milestone 7: operations and release

Deliver:

- Consistent local backup and clean-host restore.
- Side-by-side update, migration compatibility checks, health-gated activation,
  and tested rollback/restore paths.
- Non-destructive uninstall and strongly confirmed purge.
- Health, structured logs, metrics, local observability profile, and operator
  installation/configuration/security/recovery documentation.
- Release provenance, checksums, software bill of materials, and acceptance
  report.

Exit gate: full selected-profile acceptance suite passes on a clean supported
host and the shared-host coexistence fixture; backup restoration and failed
update recovery are demonstrated; remaining limitations are release notes.

## Scope control

Multi-node scheduling, broad credential brokering, automatic updates, public
datastore/observability exposure, and support outside the declared host matrix
are excluded from version 0.1. An upstream capability is not promised until a
pinned SDK/API integration test proves it.

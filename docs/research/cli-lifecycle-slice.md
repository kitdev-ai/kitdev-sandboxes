# CLI lifecycle integration slice

Date: 2026-08-07

Status: implemented and locally verified; disposable-host and clean-reinstall
qualification pending

## Scope

This change connects the public repository-local CLI to the pinned
control-plane replay assets. It adds convergent prepared-host installation,
start, quiesced stop, restart, structured status, and explicit Go/official
TypeScript SDK test selection.

No web research, SSH connection, or server mutation was performed for this
implementation. No endpoint, credential, host identifier, or secret is stored
in the change.

## Safety properties

- The dispatcher uses a fixed absolute Bash executable, an allowlisted
  operation, no shell interpolation, and a minimal environment.
- Lifecycle operations do not invoke doctor fact collectors. Doctor remains
  strictly read-only, and every lifecycle `--dry-run` performs zero changes.
- Install is development/migration-only until production template publication
  exists, and gates the existing host prerequisites before layout, secret,
  network, source, build, firewall, Compose, or systemd convergence.
- Day-two operations re-execute project-owned assets published below `/opt`,
  rather than continuing from the mutable development checkout.
- Down refuses active Firecracker processes, quiesces new API/proxy admission,
  checks again for a race, and restores the running services if a later stop
  step fails.
- Status takes no mutation lock and returns only bounded allowlisted component
  health fields.
- E2E tests are forbidden in production and accept secret material only by an
  explicit root-owned file path. The smoke suite runs both the pinned Go core
  verifier and official `e2b@2.38.0` TypeScript SDK verifier.

## Verification

Focused CLI and lifecycle unit tests cover dispatch, dry-run suppression,
clean environment construction, JSON normalization, bounded status parsing,
prepared-host install ordering, installed-asset publication, and shutdown
ordering/recovery invariants. Shell syntax and repository diff checks are part
of the local gate. Full-suite, lint, type, and credential-pattern checks are
recorded in the commit activity entry.

## Evidence boundary and gaps

The install command deliberately does not claim fresh-host preparation or
production installation. Only the minimal development/migration profile is
implemented for apply. A standalone installed Python
CLI, manifest/journal integration, updates, uninstall, backup/restore, and
clean Ubuntu 26.04 replay remain required. Ubuntu 25.04 remains explicit
development/migration only; Ubuntu 24.04 is unsupported.

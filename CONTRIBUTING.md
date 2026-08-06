# Contributing

## Current phase

Milestone 0 accepts discovery notes, architecture decisions, compatibility
research, and scaffold improvements. Host-mutating code belongs in later
milestones and must not be introduced without its dry-run behavior, rollback
contract, and focused tests.

## Workflow

1. Read `PROMPT.md`, `docs/architecture.md`, and the accepted ADRs.
2. Keep changes within one milestone and one ownership boundary.
3. Record assumptions explicitly; do not invent upstream versions or checksums.
4. Never commit credentials, host identifiers, collected secrets, or runtime
   state.
5. Run the relevant local checks and document tests, limitations, and rollback
   steps in the change description.

All host experiments must begin read-only. Never reboot, change SSH, flush a
firewall, stop unrelated services, prune Docker, or delete host data as part of
development.

## Code quality

Python will target the compatible system interpreter range shared by Ubuntu
25.04 and 26.04. Ubuntu 25.04 exists in this matrix for development and
migration compatibility only; production behavior is qualified on Ubuntu
26.04 LTS. New Python code must be typed and pass Ruff, mypy, and pytest on the
declared interpreter matrix. Shell entrypoints must use
`set -Eeuo pipefail` and pass ShellCheck. YAML and JSON must remain
machine-parseable and should be validated in CI.

Manual file mutation should be atomic. Shared configuration must be parsed and
merged structurally. Every external artifact needs an immutable version and a
verified checksum.

## Commits

Milestone commits should state:

- what changed;
- how it was tested;
- known limitations; and
- how the change is rolled back.

# Ansible ownership

Local Ansible will converge host state beginning in Milestone 1. No playbook is
present in Milestone 0, which prevents the scaffold from appearing actionable
before preflight and dry-run are reviewed.

Roles are separated by ownership and rollback behavior. Each future role must
declare inputs, preconditions, exact managed resources, handlers, check-mode
support, idempotency tests, and removal behavior. Roles must not use broad shell
commands when an idempotent module or structured parser is available.

`site.yaml` and a localhost-only inventory will be introduced together with the
Milestone 1 CLI and test harness.

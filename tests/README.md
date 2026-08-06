# Test layout

- `unit`: pure configuration, collector/evaluator, planning, and manifest logic;
- `smoke`: fast installed-service and minimal sandbox checks;
- `integration`: SDK workflows, lifecycle, templates, reboot, and restore;
- `security`: hostile workload, reachability, resource, redaction, and
  coexistence tests.

Unit tests run locally with saved redacted fixtures. Firecracker and host
integration tests run only on explicitly selected Ubuntu 26.04 LTS production
or Ubuntu 25.04 development/migration x86-64 hosts. Tests also verify Ubuntu
25.04 production mode fails without mutation. Setup records unrelated baseline
resources and verifies they remain unchanged after every lifecycle operation.

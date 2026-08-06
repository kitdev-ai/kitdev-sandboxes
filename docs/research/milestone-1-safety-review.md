# Milestone 1 doctor/config safety review

Status: implementation review checklist

Reviewed: 2026-08-06

## Scope and evidence

This review covers the first typed configuration and `kitdev doctor`
implementation. It is intentionally stricter than a feature checklist: a doctor
that reports plausible-looking facts while mutating the host, leaking evidence,
or silently accepting an unknown hard requirement is unsafe.

The checklist was derived from `PROMPT.md`, `docs/preflight-design.md`,
`docs/architecture.md`, ADRs 0001 through 0005, `docs/milestone-plan.md`, the
configuration schema/defaults, and the redacted `kit@pc` discovery report. No
commands were run on `kit@pc` for this review. Statements below are project
requirements or review recommendations; they are not new host observations.

## Release-blocking checklist

### Zero mutation and privilege

- [ ] `doctor` opens no file for writing, creates no cache/temp/runtime
  directory, changes no permissions, and performs no dependency installation or
  environment bootstrap on the inspected host.
- [ ] Every collector command has been classified as read-only. The allowlist
  contains commands/arguments, not only executable names; commands capable of
  repair or mutation are absent.
- [ ] Default doctor never runs `sudo`, even to determine whether it is
  available. Any approved privileged read is separately identifiable, uses
  `sudo -n`, and cannot prompt or refresh a credential timestamp.
- [ ] Ubuntu 25.04 plus `production`, an unsupported OS/architecture, and an
  invalid configuration fail before privileged collection, change planning that
  has side effects, or any mutation entrypoint.
- [ ] `install --dry-run` reuses the read-only fact/evaluation engine. It does
  not create the installation lock, users, directories, backups, manifests,
  virtual environments, Ansible facts/cache, package metadata refreshes, Docker
  resources, or systemd state.
- [ ] Tests compare a declared host baseline before and after doctor/dry-run.
  The baseline includes project paths, users/groups, units, loaded modules,
  sysctls, mounts, routes/links/namespaces, nftables, Docker resources and
  listeners; volatile fields are narrowly enumerated rather than broadly
  ignored.
- [ ] macOS execution is local-fixture/config testing only. It never attempts
  Firecracker, Linux host preparation, or SSH fallback implicitly.
- [ ] SSH is never initiated merely because the local platform is unsupported.
  A remote target must be explicit, and the implementation must not alter SSH
  configuration, known-host state, agents, keys, tunnels, or ControlMaster
  sockets.

### Command execution, timeouts and hostile output

- [ ] Every subprocess has a finite per-command timeout from versioned policy;
  there is no unbounded default and no collector can replace the timeout with a
  larger value from command output.
- [ ] Timeout terminates the entire spawned process group, escalates from
  graceful termination to kill after a bounded grace period, and always reaps
  children. A command cannot leave a pipe writer or privileged descendant
  running after doctor exits.
- [ ] Stdin is closed or `/dev/null`; locale is `C`; pager, color and interactive
  behavior are disabled; a minimal explicit environment is used. No shell is
  invoked and host-derived values are passed as argument-vector elements.
- [ ] stdout and stderr are read without deadlock and independently bounded by
  byte limits. Limits apply while streaming, before full buffering or decoding,
  so infinite output cannot exhaust memory or disk.
- [ ] Output decoding is deterministic and survives invalid UTF-8, NUL bytes,
  terminal escape sequences, carriage returns, very long lines and binary
  content. Human rendering neutralizes control characters and terminal escape
  sequences.
- [ ] Truncation, timeout, signal termination and nonzero exit are represented
  explicitly in the fact model. Partial/truncated output is never evaluated as
  a complete successful fact unless that collector defines and tests a safe
  partial-result rule.
- [ ] A timeout or absent command maps to `unknown` for that fact, not an
  internal exception. If the fact is required, the evaluator blocks with exit
  code 5 unless a specific safe override exists.
- [ ] Tests include a sleeping child process, a child ignoring `SIGTERM`, an
  inherited pipe held open by a grandchild, output larger than both caps,
  simultaneous stdout/stderr floods, invalid bytes, ANSI/OSC sequences and
  adversarial strings resembling JSON, log prefixes, tokens and remediation
  instructions.

### Configuration and lifecycle enforcement

- [ ] Merge precedence is explicit and tested: versioned defaults, installed
  config when present, requested config, then CLI overrides. The implementation
  never silently searches the current directory or user home for config.
- [ ] Inputs are parsed with a safe YAML loader, size/depth/alias limits, and
  duplicate-key rejection. Unknown keys and wrong scalar types fail rather than
  being coerced.
- [ ] Validation uses the repository's exact JSON Schema revision. It checks
  post-merge data before fact collection and reports source location/path
  without echoing a whole potentially secret document.
- [ ] `deployment.lifecycle_mode` is always explicit in the merged result and is
  never inferred from profile, TTY, environment variables, uid, hostname,
  desktop/server edition, prior installation or command mode.
- [ ] The tuple gate is exact: Ubuntu 26.04 permits all three modes; Ubuntu
  25.04 permits only `development` and `migration` with a prominent EOL warning;
  every other release fails every mode.
- [ ] Ubuntu 25.04 production rejection is non-overridable. Generic
  ignore-warning/force options cannot downgrade it, and the failure wins over
  secondary port/capacity conflicts.
- [ ] OS identity is based on parsed `/etc/os-release` fields, not substring or
  lexicographic version comparisons. Missing, duplicate, malformed or spoofed
  fixture values become a blocking unknown/failure.
- [ ] Configuration paths are canonicalized and constrained before later
  mutation code consumes them. Tests cover `..`, symlinks, mount boundaries,
  newline/control characters, root `/`, overlapping project roots and a path
  nested inside the source checkout.
- [ ] Cross-field policy is evaluated beyond JSON Schema syntax, including
  domain/public-exposure consistency, valid IP/CIDR/resolver syntax,
  non-overlapping project paths, and feature/profile combinations.

### Redaction and evidence minimization

- [ ] Redaction happens before a value enters a result, exception, log,
  diagnostic event or JSON object. Renderer-only redaction is insufficient.
- [ ] Collectors return allowlisted normalized evidence, not arbitrary command
  dumps. Environment variables, process command lines, container inspect data,
  full systemd environments, config files and secret files are not collected.
- [ ] At minimum, case-insensitive credential names, bearer/basic authorization,
  cookies, API/token/key/password/secret assignments, signed URL query values,
  private keys and common cloud credentials are redacted in both stdout and
  stderr. Tests include split lines, mixed case, quoting and URL encoding.
- [ ] Host fingerprinting is stable enough for comparison but does not expose
  machine ID, boot ID, serial numbers, MAC addresses, public/private IPs,
  username, home path or hostname. A keyed or installation-scoped identifier is
  preferable to publishing a reversible/raw identifier.
- [ ] Listener and service evidence omits PIDs and full command lines by
  default. File evidence does not include contents unless a collector has a
  documented allowlist and redaction rule.
- [ ] Exceptions are mapped to bounded public messages. Tracebacks, local paths
  and raw subprocess output appear only in an explicitly local debug channel
  that still applies secret redaction; `--verbose` does not disable redaction.
- [ ] Redaction is idempotent, does not mutate check semantics, and is verified
  recursively for nested mappings/lists and exception fields.

### Stable result contract and exits

- [ ] JSON has one documented top-level object containing `schema_version`,
  project version, timestamp, lifecycle mode, command mode, redacted host
  fingerprint, summary, ordered checks and ordered proposed changes.
- [ ] In `--json` mode stdout contains JSON only, even on invalid config,
  unsupported platform, timeout and internal error. Diagnostics/progress never
  contaminate stdout.
- [ ] Check IDs are constants with one meaning each. Status values are exactly
  `pass`, `warn`, `fail`, `unknown`, `skipped`; severity/remediation fields have
  stable types, and absent data uses JSON `null` or an omitted documented
  optional field consistently.
- [ ] Checks and evidence are deterministically ordered. Sets, filesystem
  iteration, route/listener order and concurrent completion order cannot change
  output. Repeatability comparison excludes only documented timestamp/elapsed
  fields.
- [ ] Summary counts are derived from the emitted checks and are validated
  against them. A failing/unknown required check cannot coexist with a success
  exit.
- [ ] Exit codes match the contract: 0 success with warnings; 2 invocation or
  configuration; 3 unsupported platform/hard requirement; 4 resource/service/
  network/ownership conflict; 5 unavailable required fact; 6 unhealthy existing
  deployment; 10 unexpected internal error.
- [ ] Multiple-failure precedence is defined and tested. Recommended order is
  2, 3, 4, 5, 6, 10 for known classified outcomes, with 10 reserved for an
  actual uncaught internal failure; check ordering must not choose the exit.
- [ ] `--help` and `--version` are side-effect-free and stable. Broken-pipe and
  keyboard-interrupt behavior is deliberate and does not print a traceback or
  misreport success.
- [ ] A checked-in JSON Schema or golden contract fixtures protect public output
  shape. Schema-version changes are deliberate; prose changes do not require a
  version bump, but check-ID meaning or field-shape changes do.

### Capability evaluation

- [ ] Platform checks independently establish Ubuntu release, x86-64, systemd
  as PID 1/service manager and cgroups v2. Passing one does not imply another.
- [ ] Virtualization checks distinguish CPU VMX/SVM availability, vendor KVM
  module state, `/dev/kvm` existence/type, and access by the intended future
  worker identity. Current login-user access is not treated as the target state.
- [ ] Nested virtualization is reported but never presented as supported for the
  bare-metal production profile.
- [ ] NBD reports module state, parameters and current device use without
  loading it. Missing NBD/huge pages can be a repairable planned change only
  when pinned runtime requirements establish that they are needed.
- [ ] Huge-page checks distinguish configured, reserved, free and mounted
  state. Zero huge pages is not automatically a platform failure.
- [ ] Capacity policy is versioned by profile and evaluates bytes and inodes on
  the actual containing filesystems for intended paths. It preserves a host
  reserve and treats unavailable filesystems/paths without creating them.
- [ ] Listener checks retain protocol, address family and bind scope. Wildcard
  IPv4/IPv6 conflicts are handled correctly; a process name or missing PID is
  not used as sole ownership evidence.
- [ ] Routes, Docker address pools/networks, NetworkManager links and requested
  sandbox CIDRs are parsed structurally. IPv4 and IPv6 overlap checks do not
  rely on textual prefix matching.
- [ ] Existing Docker, nftables, UFW, systemd, users/groups, paths and similarly
  named resources are foreign unless an exact installation manifest and
  ownership marker prove otherwise. The existing `kitdev` interface,
  `kitdev-vllm-*` units and `KITDEV_VLLM` chain are explicit collision fixtures.
- [ ] GDM, NetworkManager and desktop packages are inventory/warning inputs, not
  automatic rejection. Concrete port, route, device, sleep-policy or capacity
  conflicts carry exact redacted evidence.
- [ ] Package absence that host preparation can safely repair is a proposed
  change, not an immutable platform failure. Installed project health checks are
  skipped, not passed, when no owned installation is present.
- [ ] The default read-only doctor does not claim it can start a minimal
  Firecracker VM. That probe is a separately named, explicit post-install
  operation because VM startup is mutating.

### Shared PC and remote-host guardrails

- [ ] Tests never default to `kit@pc`, a hostname from config, or the current SSH
  target. Remote/integration tests require an explicit target plus a positive
  opt-in environment variable or command flag.
- [ ] The test harness refuses mutation-oriented suites on the shared PC unless
  the operator separately authorizes that exact phase. Unit tests use saved,
  redacted fixtures and cannot invoke real collectors by accidental fixture
  fallback.
- [ ] Remote commands use fixed argument vectors and no interpolation into a
  remote shell. Hostname, port and host-key policy are operator supplied; secrets
  are neither command-line arguments nor captured evidence.
- [ ] SSH connection failure, host-key mismatch, sudo prompt/failure and command
  timeout become explicit failed/unknown observations. They never trigger local
  execution as fallback.
- [ ] Baseline coexistence fixtures include the discovered occupied ports,
  Docker networks/resources, UFW/nftables state, active GDM and NetworkManager,
  nonstandard SSH listener, no swap, absent NBD devices, zero huge pages and a
  login user without KVM access.
- [ ] No test invokes reboot, package removal, firewall disable/flush, Docker
  prune, disk formatting/partitioning, SSH edits, public binding or broad
  cleanup. Reboot persistence and apply/convergence work remain separately
  approval-gated Milestone 1 phases.

## Minimum test matrix before approval

| Area | Required cases |
| --- | --- |
| Config | defaults; each lifecycle mode; precedence; unknown/duplicate keys; wrong type; malformed/oversized YAML; missing file; unreadable file; unsafe paths; cross-field failures |
| Lifecycle | 26.04 in all modes; 25.04 development/migration warning; non-overridable 25.04 production failure; unsupported Ubuntu and non-Ubuntu; malformed os-release |
| Process runner | success; nonzero; missing executable; permission denied; timeout/process tree; stdout/stderr flood; invalid bytes; signals; output truncation; closed stdin; deterministic environment |
| JSON/exit | success with warning; codes 2/3/4/5/6/10; simultaneous failures; stdout purity; schema validation; deterministic check ordering; broken pipe; internal exception |
| Redaction | every credential class in stdout, stderr, config validation, exception and nested evidence; URL/cookie/auth formats; ANSI/control characters; `--verbose` |
| Capabilities | unsupported arch; non-systemd; cgroups v1; VMX/SVM absent; KVM absent/inaccessible; nested VM; NBD absent/in-use; huge pages zero/configured; command unavailable |
| Coexistence | wildcard/listener conflicts; IPv4/IPv6 CIDR overlap; foreign similarly named resources; active GDM/NetworkManager; partial owned install; unhealthy owned install |
| Non-mutation | doctor and two dry-runs against instrumented fake collectors plus disposable Ubuntu hosts; before/after baseline comparison; no implicit SSH or sudo |

Property/fuzz tests are warranted for configuration merge/validation, redaction,
terminal-safe rendering, CIDR/listener parsing and result serialization. Unit
tests should monkeypatch the command runner so an unexpected real subprocess is
a hard failure. Integration tests need disposable Ubuntu 26.04 and 25.04
fixtures; the shared PC is not a substitute for a clean-host fixture.

## Review findings and open decisions

1. **Doctor scope conflict:** `PROMPT.md` lists the ability to start a minimal
   Firecracker sandbox under `doctor`, while the newer preflight contract says
   startup is a separately named post-install check because it mutates runtime
   state. The implementation should follow the narrower preflight contract and
   expose the VM probe only through an explicit post-install operation.
2. **Exit precedence was unspecified:** the design lists exit codes but does not
   say which wins when checks fail together. The implementation now chooses the
   lowest classified code and tests platform/conflict/health coexistence;
   promote that behavior into the normative design before external automation
   depends on it.
3. **JSON began as a prose contract:** the implementation now provides separate
   checked-in success and error schemas. Add emitted-object schema validation
   and define compatibility policy before treating version 1 as externally
   stable.
4. **Collector resource bounds are incomplete:** the design requires timeouts
   but does not set output-byte limits, process-tree termination semantics or
   terminal-control handling. These must be runner invariants before invoking
   general host commands.
5. **Configuration paths required semantic policy:** the original
   `absolutePath` accepted `/`, controls, traversal and overlapping roots. The
   implementation and updated schema now constrain paths to project ownership
   roots and canonical components; retain those tests as mutation code lands.
6. **Release qualification remains prospective:** the policy accepts Ubuntu
   26.04, but current host evidence is from Ubuntu 25.04. Unit fixtures can test
   logic; they cannot substantiate kernel, package or service compatibility on
   the ordered OVH host.
7. **Dry-run boundary needs a bootstrap decision:** installing a Python virtual
   environment or dependencies before `install --dry-run` would mutate the
   target despite the user-visible mode. Run the source checkout's already
   provisioned tooling for dry-run, or explicitly separate bootstrap planning
   from host dry-run and document the limitation.

## Concurrent implementation review

Final snapshot reviewed: uncommitted configuration, preflight, CLI, launcher,
JSON schemas and unit tests visible on 2026-08-06.

The implementation corrected the issues found during live review: configuration
input size/depth and paths are bounded; addresses and CIDRs are structurally
validated; duplicate OS identity is rejected; CPU info has a collector-specific
bound; KVM is stat-tested as a character device; incomplete required scope is
blocking; exit categories 3 through 6 have deterministic precedence; platform
fingerprinting is accurately named; JSON errors are structured; the repository
launcher suppresses bytecode writes; human/JSON rendering applies credential,
path and terminal-control redaction; and a broken pipe does not turn a blocked
doctor result into success. Configuration opens now use nonblocking, no-follow
flags and regular-file `fstat` enforcement, while cookie redaction covers both
header and assignment forms. Private deployments require a loopback listen
address. The supported entrypoint for this slice is only the repository-local
`./kitdev`; the package console script was removed until installed assets and
layout have a complete contract.

Independent final review also corrected three fact/evidence interpretation
risks. VMX/SVM is now derived only from named CPU `flags`/`Features` fields, so
model names and unrelated text cannot produce a false pass. The KVM module's
`nested` parameter is represented as `nested_guest_support`, not mistaken for
evidence that the host itself is virtualized. Redaction now covers C1 controls,
quoted multiword values, and bounded double percent-decoding for both strings
and sensitive dictionary keys.

### Residual findings

1. **Schema test gap:** the checked-in configuration, success-report and error
   schemas parse, and targeted parity assertions exist, but emitted objects are
   not validated against those schemas in tests. Add schema-validation tests
   before consumers depend on the JSON contract.
2. **Collector fixture gap:** KVM stat/access and text reads are injectable, but
   cgroup path existence still comes from the executing machine. A fully
   hermetic saved-fact collector test needs an injected path/stat boundary.
3. **Non-mutation evidence gap:** source audit finds no sudo, SSH, subprocess or
   explicit write path, and the launcher creates no bytecode, but the required
   before/after host-baseline integration test has not run. The saved shared-PC
   fixture is not yet consumed by evaluator tests.
4. **Intentional incomplete scope:** host virtualization-environment detection,
   KVM modules, NBD, huge pages, capacity, Docker/Compose,
   ports/routes/firewall, security posture, ownership and
   installed health remain unimplemented. The current report correctly emits
   blocking `unknown` checks and exits 5 rather than claiming qualification.
   `install --dry-run`, host preparation and any subprocess runner/timeout logic
   also remain future Milestone 1 work.

Final local verification passed 33 unit tests on Python 3.14.6, JSON parsing for
all three schemas, structured JSON CLI probes, bytecode-absence inspection,
focused FIFO, symlink, redaction, CPU-flag and nested-parameter probes, and
`git diff --check`. FIFO rejection returned without blocking. On macOS, local
`doctor --json` rejected the unsupported platform without stderr contamination.
This final local safety-review rerun used no SSH or sudo.

The supervisor ran the final revision from a temporary unprivileged copy on
`kit@pc` using Python 3.13.3. All 33 tests passed. Final doctor output exited 5
with 5 pass, 2 warn, 0 fail, 5 unknown and 1 skipped result; `changes` was
empty. No bytecode was created and temporary cleanup was verified. This covers
the final revision on the minimum supported Python line, but does not replace
the missing independent before/after host baseline.

## Approval gate

Approve the first doctor/config slice only when all release-blocking items that
apply to its implemented surface have automated tests, JSON/exit behavior is
contracted, adversarial runner/redaction cases pass, and a source audit finds no
write-capable or implicitly privileged path. Capability checks that are not yet
implemented must emit `unknown`/`skipped` honestly and must not permit an
overall success when the missing fact is a hard requirement.

Verdict for the reviewed snapshot: acceptable as a non-qualifying local
development slice because omitted hard requirements block success and no host
mutation path is present. It does not satisfy the Milestone 1 exit gate. Close
the remaining integration and schema-validation gaps before any approved
host-preparation work.

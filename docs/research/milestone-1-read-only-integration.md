# Milestone 1 read-only integration

Date: 2026-08-06

## Scope

This slice connects the bounded Linux collectors and pure directory planner to
the production CLI. It adds no apply path or host-writing implementation. No web
research was needed; the implementation follows the checked-in preflight,
upstream-port, configuration, and lifecycle contracts.

`kitdev doctor` now composes the original platform observations with normalized
Linux fact groups. A scope sentinel is replaced only when every required probe
in that group has a successful, structurally valid result. Incomplete,
permission-denied, timed-out, truncated, or malformed groups remain blocking
`unknown`. Raw `LinuxFacts`, command output, process IDs, addresses, mount
credentials, and caller environment are not serialized.

`kitdev install --dry-run` is a timestamp-free, deterministic planning command.
It performs the platform and lifecycle gate before extended Linux or directory
collection, observes configured directories through strict no-follow lstat,
and passes only normalized resource facts to the pure planner. Bare `install`
and apply-like flags are invocation errors with exit code 2.

## Safety properties

- Commands use the bounded no-shell runner with a fixed environment and fixed
  argv.
- Directory observations do not create paths and reject symlinks in every
  parent component.
- Existing directories have `unknown` ownership unless an authenticated
  installation manifest and installation ID explicitly prove the exact target.
- Non-directories, symlinks, collection errors, foreign resources, and unknown
  ownership cannot authorize a root action.
- Planning revalidates the exact project-owned path roots even when callers
  directly construct configuration dataclasses.
- Outputs are recursively redacted and bounded. Plans contain no timestamp or
  raw host inventory.
- Human CLI errors are redacted and capped at 4 KiB including the newline;
  fixed JSON error envelopes and early invocation/configuration failures handle
  closed output pipes without a traceback or exit-code drift.
- A concrete platform or lifecycle failure takes precedence over unknown peer
  platform facts and returns exit code 3. An unknown-only platform gate returns
  exit code 5.
- A dependency-free recursive test validator checks actual and fixture plan
  output against JSON Schema types, constants, enums, required and additional
  properties, array items, integer ranges, and string patterns.
- The isolated CLI test compares current-working-directory state before and
  after a dry-run with injected collectors and bytecode generation disabled.
  This proves that tested CLI path creates no artifacts in that directory; it
  does not constitute a live-host non-mutation test.

## Deliberate limitations

The required E2B port/bind/ownership policy discovered in
`upstream-e2b.md` is not yet approved as install policy. Consequently,
`scope.network_conflicts` remains blocking `unknown`, and every otherwise
eligible install dry-run includes `network.required_ports.policy`. The final
plan suppresses all candidate actions while that blocker exists and returns
exit code 5. This slice therefore does not claim host readiness.

Profile capacity thresholds are also not approved. A complete capacity
inventory is reported as a warning rather than a readiness pass. Manifest
authentication is modeled as an explicit trust-boundary input but is not yet
wired to the CLI, so existing directories are never inferred as project-owned.

No Ansible bootstrap, package installation, account creation, directory
creation, module loading, sysctl change, network/firewall change, service
operation, reboot, or apply path exists in this slice.

## Rollback

There is no runtime or host-state rollback because this slice has no mutation
path. Code rollback consists of removing the CLI composition module, the
install-plan schema and fixture, and the `install --dry-run` parser branch, then
restoring the prior doctor-only test expectations. No project or foreign host
resource should be removed or changed.

## Verification boundary

Hermetic tests cover lifecycle modes, mixed platform failures and unknowns,
complete and malformed fact groups, directory types and ownership, path
authorization, port-policy blocking, deterministic permutations, recursive
JSON Schema contracts, broken pipes, and no-artifact behavior under injected
collectors. A production-collector `install --dry-run` with an independent
before/after host baseline remains pending until the supervisor runs it on the
Ubuntu 25.04 PC. The ordered Ubuntu 26.04 OVH server is available and its active
read-only intake must complete before qualification can be claimed. The shared
PC remains a development/migration target only.

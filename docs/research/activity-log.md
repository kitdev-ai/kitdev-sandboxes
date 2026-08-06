# Project activity log

This is the append-only project activity ledger. Add an entry before every
commit and every review or deployment gate. Do not rewrite or delete prior
entries except to correct a factual error; append a dated correction that names
the superseded statement instead. Dates use `YYYY-MM-DD` as a calendar date
without implying a timezone unless an entry explicitly records one.

Each entry records intent, the delegated LUNA role, a safe summary of actions,
affected evidence, result, mutation status, the next gate, and a commit hash
when one exists. Agent identifiers not established by repository history are
reported as unknown rather than reconstructed.

This tracked ledger contains redacted project evidence only. Credentials,
private keys, raw command transcripts, endpoints, hostnames, addresses, account
names, hardware identifiers, and private filesystem paths do not belong here.
The designated operator inventory is
`docs/private/ovh-26-04-server-inventory.md`; it is local-only, must remain
untracked, and must never be quoted into tracked documentation.

## 2026-08-06 - Architecture and discovery baseline

- **Intent:** Establish the single-host architecture, upstream-port boundary,
  project-owned state rules, privilege model, network model, supported host
  matrix, and a read-only development-PC discovery baseline.
- **Delegated LUNA agent:** LUNA architecture and research agents; individual
  task identifiers are not established by repository history.
- **Safe activity summary:** Reviewed the project brief and upstream evidence,
  performed bounded read-only host discovery, documented assumptions and
  blockers, and created ADRs. The product support correction was made explicit:
  Ubuntu 26.04 LTS on x86-64 is the production target; Ubuntu 25.04 is accepted
  only for development or migration and is rejected for production; Ubuntu
  24.04 is not a supported project target.
- **Files and evidence:** [architecture](../architecture.md),
  [supported-host ADR](../adr/0005-supported-host-matrix.md),
  [host discovery](host-discovery.md), and
  [upstream E2B research](upstream-e2b.md).
- **Result:** Milestone 0 contracts and the development-host risk inventory were
  established. The shared Ubuntu 25.04 PC remained development/migration-only,
  and Ubuntu 26.04 behavior remained unqualified.
- **Mutation status:** No project installation or host configuration mutation;
  discovery was read-only. The historical report records narrowly bounded
  read-only privilege use for two observations.
- **Limitations / next gate:** Implement a strictly read-only preflight slice,
  preserve coexistence with unrelated PC resources, and qualify production only
  on a clean Ubuntu 26.04 target.
- **Commit:** `e4f1f5a99ed306adb72db3ada05d35a727f1e913`

## 2026-08-06 - Cloud and local-VM deployment comparison

- **Intent:** Determine the easiest deployment substrate for upstream E2B, the
  best fit for this project's one-host architecture, and whether development
  could proceed inside a VM on the existing PC.
- **Delegated LUNA agent:** LUNA cloud research agent; the individual task
  identifier is not established by repository history.
- **Safe activity summary:** Compared GCP, AWS, cloud bare metal, dedicated
  hosts, and a nested Ubuntu 26.04 KVM/libvirt VM. Distinguished the easiest
  upstream topology from the project's direct single-host qualification path.
- **Files and evidence:** [cloud options](cloud-options.md).
- **Result:** GCP was the easiest match for the upstream multi-service topology;
  dedicated bare metal was the cleaner fit for kitdev's single-host design. A
  disposable Ubuntu 26.04 VM on the PC was considered useful for narrow
  development, but not production evidence because the Ubuntu 25.04 outer host
  and nested virtualization remain part of the trust and failure boundary.
- **Mutation status:** Documentation and research only; no cloud resources or
  local VMs were created.
- **Limitations / next gate:** Validate nested KVM only in a disposable proof if
  needed, and perform authoritative runtime qualification on Ubuntu 26.04 bare
  metal.
- **Commit:** `43204f09d6bbc80c9ed15aa39261ece330559041`

## 2026-08-06 - OVH bare-metal operating-system decision

- **Intent:** Choose between Ubuntu installed directly on the ordered OVH
  server and Proxmox with an Ubuntu guest.
- **Delegated LUNA agent:** LUNA OVH platform research agent; the individual
  task identifier is not established by repository history.
- **Safe activity summary:** Compared direct Ubuntu and Proxmox boundaries,
  including nested virtualization, network layers, resource accounting,
  recovery, and image-install paths.
- **Files and evidence:** [OVH host plan](ovh-host-plan.md).
- **Result:** Selected Ubuntu Server 26.04 LTS directly on OVH bare metal.
  Proxmox was rejected for the primary development and qualification host
  because it would add an outer Debian/Proxmox control plane and nested KVM
  boundary that the product does not need.
- **Mutation status:** Research and decision record only; no OVH resource was
  accessed or changed during this work.
- **Limitations / next gate:** Confirm the exact installed image, storage,
  management access, network, CPU virtualization, and KVM state through a
  zero-intentional-mutation first-login intake.
- **Commit:** `b253f60df95485116e1d6622340f459270df59a0`

## 2026-08-06 - Read-only doctor foundation and PC validation

- **Intent:** Implement the first typed, dependency-free `kitdev doctor` slice
  and verify it on the shared PC without installing the package or changing host
  configuration.
- **Delegated LUNA agent:** LUNA implementation, safety-review, and PC-validation
  agents; individual task identifiers are not established by repository
  history.
- **Safe activity summary:** Added configuration and report contracts, lifecycle
  gates, redaction, a repository-local launcher, and unit coverage. Ran the
  final revision from a temporary unprivileged copy with bytecode disabled and
  executed the read-only doctor on Ubuntu 25.04 in development mode.
- **Files and evidence:** [PC doctor run](milestone-1-pc-doctor.md) and
  [Milestone 1 safety review](milestone-1-safety-review.md).
- **Result:** All 33/33 unit tests passed on the PC's Python 3.13.3. Doctor exited
  `5` with 5 pass, 2 warn, 0 fail, 5 unknown, and 1 skipped result; the changes
  list was empty. The result correctly did not claim host readiness.
- **Mutation status:** No `sudo`, package install, host preparation, service
  action, or persistent project state. The temporary execution directory and
  bytecode-absence check were cleaned and verified. A full independent
  before/after host baseline was not captured.
- **Limitations / next gate:** Add bounded collectors and deterministic planning,
  close schema and fixture gaps, and run stronger non-mutation evidence before
  approving host preparation.
- **Commit:** `40bcd30d7e65c4a2aaf1d3edd0067afec6702b07`

## 2026-08-06 - Bounded collectors, runner, and planner implementation

- **Intent:** Extend the doctor with composed Linux facts and add a
  deterministic, deliberately blocking `install --dry-run` plan without adding
  an apply or host-writing path.
- **Delegated LUNA agent:** LUNA collector/planner implementation agent and LUNA
  safety-review agent; their individual task identifiers are not recorded in
  tracked project evidence.
- **Safe activity summary:** Implemented a fixed-argv, no-shell bounded runner;
  normalized collectors; fact composition; no-follow directory observation;
  pure planning; CLI integration; a plan schema and fixture; and hermetic tests.
  The first review snapshot passed 64 unit tests.
- **Files and evidence:** Current uncommitted `runner.py`, `collectors.py`,
  `composition.py`, `planning.py`, CLI/test/schema changes, and
  [read-only integration notes](milestone-1-read-only-integration.md).
- **Result:** Complete, valid fact groups could replace scope sentinels, while
  incomplete groups remained blocking `unknown`. Planning remained
  timestamp-free and suppressed actions while required-port policy was unknown.
- **Mutation status:** Source edits and hermetic local tests only. No SSH,
  privilege escalation, package, account, directory, service, firewall, module,
  sysctl, reboot, or apply operation was introduced or run.
- **Limitations / next gate:** Safety review identified unsafe config special-file
  handling, ambiguous runner outcomes, ownership inference, action-enum drift,
  partial malformed fact acceptance, and duplicated identifiers in diagnostics;
  correction was required before integration approval.
- **Commit:** Not committed.

## 2026-08-06 - Milestone 1 commit ledger update

- **Commit:** `88355fa` - Added bounded host collection and deterministic
  planning primitives. This commit introduced the fixed-argv runner, typed
  Linux collectors, pure planning model, and their focused tests; it did not
  add or run a host mutation path.
- **Commit:** `c31683c` - Integrated read-only doctor collection and blocking
  `install --dry-run` planning into the CLI with stable schema/fixture output.
  Required unknowns continued to suppress actions; no host mutation occurred.
- **Commit:** `00b5958` - Added identity-access dry-run planning across eight
  files. The verified suite passed 126 tests, and the CLI smoke check returned
  exit `3` with zero actions. No host mutation occurred.

## 2026-08-06 - Safety correction cycle 1

- **Intent:** Correct the first collector/planner safety-review findings and
  repeat adversarial verification.
- **Delegated LUNA agent:** LUNA integration implementation agent, reviewed by a
  separate LUNA safety-review agent; individual task identifiers are not
  recorded in tracked project evidence.
- **Safe activity summary:** Hardened configuration opens against symlinks and
  special files, made timeout/permission/truncation interpretation explicit,
  removed name-only ownership authorization, constrained action values, made
  malformed partial fact groups blocking, and bounded/deduplicated diagnostics.
- **Files and evidence:** The uncommitted collector, runner, planning,
  composition, CLI, and unit-test set described in
  [read-only integration notes](milestone-1-read-only-integration.md).
- **Result:** The corrected snapshot passed 87 unit tests. Re-review then found
  five remaining blockers: arbitrary or relative planner config paths, mixed
  valid/malformed socket rows, partially malformed address data, mount-source
  secret/null retention, and whitespace-only NBD input handling.
- **Mutation status:** Code and hermetic test changes only; no host or remote
  mutation.
- **Limitations / next gate:** Correct all five parsing and authorization issues,
  then repeat full CLI/schema/non-mutation review.
- **Commit:** Not committed.

## 2026-08-06 - Safety correction cycle 2 and CLI integration review

- **Intent:** Close the second review blockers and assess the integrated CLI,
  output contracts, and non-mutation claims as a release-gate candidate.
- **Delegated LUNA agent:** LUNA integration implementation agent, reviewed by a
  separate LUNA release-gate agent; individual task identifiers are not recorded
  in tracked project evidence.
- **Safe activity summary:** Revalidated planner paths against project-owned
  roots, rejected partial socket/address groups, sanitized mount evidence, made
  NBD parsing total, and exercised deterministic CLI, schema, redaction,
  lifecycle, parser, and temporary-directory checks.
- **Files and evidence:** The uncommitted integrated implementation and
  [read-only integration notes](milestone-1-read-only-integration.md).
- **Result:** The integrated snapshot passed 107 unit tests. Final review still
  found incorrect platform/lifecycle exit precedence, incomplete early
  broken-pipe handling, unbounded human CLI errors, a shallow plan-schema test,
  and an overbroad non-mutation evidence claim.
- **Mutation status:** Code review and local hermetic tests only; no host or
  remote mutation.
- **Limitations / next gate:** Fix the release-gate findings, narrow the evidence
  claim to what the isolated test proves, and rerun the complete unit suite.
- **Commit:** Not committed.

## 2026-08-06 - Safety correction cycle 3 and current CLI state

- **Intent:** Resolve the final integrated-review findings and establish the
  current pre-commit verification state.
- **Delegated LUNA agent:** LUNA integration implementation agent, supervised by
  the project lead and independently reviewed by a LUNA safety-review agent;
  individual task identifiers are not recorded in tracked project evidence.
- **Safe activity summary:** Corrected concrete platform/lifecycle precedence,
  bounded and redacted human errors, handled output closure before report
  rendering, expanded dependency-free recursive schema validation, and narrowed
  the no-artifact test claim to its injected isolated working directory. A
  first local test command omitted `PYTHONPATH=src` and stopped at import
  discovery without running project tests; the documented command was then run.
- **Files and evidence:** Current uncommitted CLI, schema, collector, composition,
  planner, runner, test, changelog, README, milestone-plan, and
  [read-only integration notes](milestone-1-read-only-integration.md).
- **Result:** The current documented unit command passed exactly 114/114 tests
  on Python 3.14.6. The implementation remains intentionally non-qualifying:
  required-port policy is unresolved, so plans contain no authorized actions
  and return blocking `unknown` rather than readiness.
- **Mutation status:** Source edits and hermetic local tests only. Bytecode was
  disabled. The failed discovery command and successful suite made no host or
  remote configuration changes.
- **Limitations / next gate:** The current worktree is uncommitted. Review the
  final diff and tracked links, run read-only PC validation with an independent
  baseline, then qualify the OVH Ubuntu 26.04 host before any mutation phase.
- **Commit:** Not committed.

## 2026-08-06 - Private versus tracked evidence policy

- **Intent:** Separate operator-only OVH data from shareable, reviewable project
  evidence before connecting to the ordered server.
- **Delegated LUNA agent:** LUNA OVH intake research agent; the individual task
  identifier is not recorded in tracked project evidence.
- **Safe activity summary:** Defined a private inventory boundary and a tracked
  redaction policy. Tracked reports retain only normalized capability outcomes
  and placeholders; exact installer records, endpoints, SSH fingerprints,
  accounts, network assignments, hardware identifiers, and recovery details
  remain private.
- **Files and evidence:** [OVH intake runbook](ovh-26-04-intake.md). The designated
  local-only inventory is `docs/private/ovh-26-04-server-inventory.md` and is
  neither linked nor quoted here.
- **Result:** A reviewable evidence path exists without committing identifying
  or access data.
- **Mutation status:** Documentation policy only; no server access or mutation.
- **Limitations / next gate:** Populate and compare exact values only in the
  local operator record, redact at collection time, and commit only normalized
  intake conclusions.
- **Commit:** Not committed.

## 2026-08-06 - OVH read-only intake initiated

- **Intent:** Begin first-login qualification of the ordered direct Ubuntu
  Server 26.04 LTS bare-metal target under the zero-intentional-mutation runbook.
- **Delegated LUNA agent:** LUNA OVH intake agent under project-lead supervision;
  the internal task identifier is not included in tracked project evidence.
- **Safe activity summary:** Started fixed, bounded read-only SSH observations
  with an isolated host-key file and temporary working directory. The first
  orchestration attempt encountered a local closed-file-descriptor bookkeeping
  bug after at most the first fixed read-only hostname observation. The agent
  identified the local orchestration defect, cleaned temporary state, and began
  a safe rerun. No endpoint or raw observation is retained here.
- **Files and evidence:** [OVH intake runbook](ovh-26-04-intake.md); a redacted
  qualification result is still pending.
- **Result:** Intake is in progress. The first attempt did not produce a valid
  qualification record and is not treated as host evidence.
- **Mutation status:** No remote mutation and no raw endpoint exposure. The
  isolated host-key file and temporary directory from the failed local attempt
  were cleaned.
- **Limitations / next gate:** Complete the bounded rerun, compare observations
  against the private operator inventory, publish only a normalized redacted
  result, and stop before any installation or host-preparation action.
- **Commit:** Not committed.

## 2026-08-06 - OVH read-only intake completed with blocking unknowns

- **Intent:** Replace the stalled automation attempts with bounded, individual
  read-only SSH observations and publish a redacted first-intake decision.
- **Delegated LUNA agent:** LUNA OVH intake agent under project-lead supervision;
  individual task identifiers and infrastructure identifiers are omitted.
- **Safe activity summary:** A second custom orchestration attempt stalled in
  local bookkeeping and was abandoned without treating its partial output as a
  qualification record. Temporary state was cleaned. Two subsequent bounded
  `/usr/bin/true` authentication gates used separate isolated host-key state and
  succeeded. The approved inventory commands then ran individually. One KVM
  stat format accidentally included a shell metacharacter and produced only
  read/command errors; fixed read-only stat commands replaced it. One transient
  SSH failure on a KVM read test was repeated once with the same bound.
- **Files and evidence:** [OVH intake runbook](ovh-26-04-intake.md) and
  [redacted first-intake report](ovh-26-04-first-intake.md). Exact identifying
  evidence is retained only in the ignored local operator record.
- **Result:** Reachability and key authentication passed. The delivered direct
  Ubuntu 26.04 bare-metal host, CPU, memory, disks, RAID health, and assigned
  dual-stack network match the private order record. Installation remains
  blocked: the first-seen host key is unverified and unprivileged firewall-rule
  collection was denied. The login identity also lacks KVM access and requires
  a future reviewed host-preparation change.
- **Mutation status:** No `sudo`, upload, remote write, package operation,
  service operation, firewall/network/kernel change, or reboot occurred. SSH
  authentication may have produced ordinary provider/OS audit and session
  records. Temporary host-key files were removed; normal local SSH host-key
  state was not used or changed.
- **Limitations / next gate:** Verify the private host fingerprint through the
  OVH console, obtain bounded effective-firewall evidence through a separately
  reviewed read-only method, complete the missing private provider record, and
  review KVM identity plus CPU/SMT security posture before any host change.
- **Commit:** Not committed.

## 2026-08-06 - OVH first-intake review gate

- **Intent:** Reconcile the completed command-by-command intake with the private
  order record and determine whether installation or apply can be considered.
- **Delegated LUNA agent:** LUNA OVH intake and documentation agents under
  project-lead supervision; infrastructure identifiers are omitted.
- **Safe activity summary:** Confirmed the two stalled LUNA orchestration
  attempts were local bookkeeping failures and were not accepted as evidence.
  The supervisor's two authentication-only `/usr/bin/true` checks used batch
  mode, isolated temporary host-key files, disabled connection sharing, and
  bounded connection/whole-command timeouts. The subsequent approved commands
  ran individually. One malformed KVM stat format produced harmless read and
  command errors; corrected fixed read-only stat calls replaced it. Temporary
  state was cleaned and the normal local host-key file remained untouched.
- **Files and evidence:** [OVH intake runbook](ovh-26-04-intake.md) and
  [redacted first-intake report](ovh-26-04-first-intake.md). Exact endpoint,
  address, account, hardware, and fingerprint values remain only in the ignored
  local operator inventory.
- **Result:** The delivered Ubuntu 26.04 LTS Resolute bare-metal platform and
  ordered CPU, memory, dual-stack network, two-disk healthy RAID1 system layout,
  and raw rotational data disk were observed. The login account has existing
  administrative/root-equivalent group exposure but lacks KVM access. The host
  is suitable for continued read-only qualification and is not ready for
  installation or apply.
- **Mutation status:** No remote write, `sudo`, upload, package/service action,
  account/group change, disk/filesystem action, firewall/network/kernel change,
  or reboot occurred. Only ordinary SSH/provider audit and session telemetry may
  have changed. All isolated temporary host-key and working files were removed.
- **Limitations / next gate:** Verify the private first-seen host key, obtain
  bounded firewall evidence, approve KVM/identity and raw-data-disk plans, settle
  the port policy and CPU/SMT security disposition, and run the full production
  collector with an independent baseline before any host mutation.
- **Commit:** Not committed.

## 2026-08-06 - Approved read-only KVM privilege check

- **Intent:** Distinguish device/host capability from the login account's access
  before designing any identity or group change.
- **Delegated LUNA agent:** LUNA OVH intake agent under explicit user approval
  and project-lead supervision; account and infrastructure identifiers are
  omitted.
- **Safe activity summary:** Ran only the approved non-interactive root identity
  check, KVM stat, and KVM read/write access tests. Root identity was confirmed,
  the device remained a character device, and both root access tests succeeded.
  The direct login account tests remained negative because it is not in the KVM
  group.
- **Files and evidence:** [Redacted first-intake report](ovh-26-04-first-intake.md)
  and the ignored local operator inventory.
- **Result:** The kernel/device boundary is usable by root, while the current
  login identity lacks the required least-privilege KVM access. The future plan
  should create or use a dedicated service account in the KVM group; it must not
  run the stack as root or broaden the device mode.
- **Mutation status:** No identity, group, mode, ownership, device, service, or
  other host state was changed. The approved `sudo -n` operations were read-only.
- **Limitations / next gate:** Review the dedicated service-account and group
  design together with the remaining host-key, firewall, storage, port-policy,
  production-collector, and CPU/SMT security gates before any mutation.
- **Commit:** Not committed.

## 2026-08-06 - Reproducible OVH identity and recovery-access plan

- **Intent:** Design the first Ubuntu 26.04 identity/access change without
  turning the new server into a hand-built snowflake, and resolve the observed
  operator/LXD and worker/KVM privilege questions before any mutation.
- **Delegated LUNA agent:** LUNA identity/access planning and official-source
  research agent under project-lead supervision; infrastructure identifiers are
  omitted.
- **Safe activity summary:** Reviewed project architecture, ADRs, preflight and
  milestone contracts, the redacted OVH intake, and the private local inventory.
  Researched Ubuntu account/SSH behavior, Linux KVM and TUN permissions,
  systemd service controls, LXD, Docker, and libvirt group/socket privilege,
  Firecracker/E2B boundaries, and OVH recovery using primary or official
  sources. Defined an
  Ansible-only, checkable, manifest-owned identity phase with rollback and
  lockout guards. Manual privileged account/group commands are explicitly not
  the execution path.
- **Files and evidence:** [OVH identity and recovery-access plan](ovh-26-04-identity-access-plan.md).
- **Result:** Proposed retaining the operator's SSH and `sudo` access, removing
  `lxd` only after read-only evidence proves it unused, and granting only the
  non-login worker the existing `kvm` group. No TUN group, Docker/LXD socket,
  sudo, capability, NBD, mount, namespace, or firewall authority is granted.
  The plan is blocked until the pinned Ansible bootstrap, typed phase CLI,
  manifest, tests, host-key verification, recovery check, and LXD discovery are
  implemented and reviewed.
- **Mutation status:** Documentation and web research only. No server command,
  account/group change, upload, package/service action, SSH change, or other
  remote mutation occurred.
- **Limitations / next gate:** Implement the reproducible bootstrap and identity
  role in the repository with dry-run/check mode, pass Ubuntu 26.04
  apply/apply/rollback/rollback tests, complete host-key/recovery/LXD read-only
  gates, and obtain approval for the exact plan hash before invocation.
- **Commit:** Not committed.

## 2026-08-06 - Identity-access read-only planner foundation

- **Intent:** Convert the reviewed identity policy into reusable typed discovery
  and a phase-specific dry-run contract without enabling host mutation.
- **Delegated LUNA agent:** LUNA identity dry-run implementation agent under
  project-lead supervision.
- **Safe activity summary:** Added explicit operator configuration, bounded
  fixed-argv local/NSS account and group discovery, system-range UID/GID
  allocation, LXD absence qualification, and deterministic identity planning.
  Added `install --phase identity-access --dry-run` with stable redacted JSON
  and text output. Apply remains rejected.
- **Result:** The plan names only `kitdev-e2b`, `kitdev-proxy`, and
  `kitdev-worker`; only the worker receives `kvm`. Unresolved bootstrap,
  write-ahead journal, host-key, recovery, second-session, sudo-policy,
  operator-key, LXD, collision, or path evidence blocks the whole phase and
  suppresses every action. Numeric allocation is deterministic and included in
  the plan hash with an immediate pre-apply vacancy recheck contract.
- **Mutation status:** Repository edits and local unit tests only. No server
  access, privilege acquisition, account/group operation, package operation,
  service operation, or other host mutation occurred.
- **Verification:** 124 unit tests passed locally, including malicious runtime
  types, duplicate/permuted facts, Ubuntu 25.04 lifecycle rejection, stable
  schema/fixture output, and working-directory no-mutation checks.
- **Limitations / next gate:** Bootstrap, journal, authenticated host-key and
  recovery evidence, apply, rollback, and Ansible execution remain deliberately
  unimplemented and blocking.
- **Commit:** Not committed.

## 2026-08-06 - Identity-plan rejection and LXD non-use evidence

- **Intent:** Close the first identity-plan review and establish whether the
  operator's existing root-equivalent LXD membership is actively required.
- **Delegated LUNA agents:** Independent identity-plan reviewer and bounded LXD
  inventory agent under project-lead supervision.
- **Safe activity summary:** The reviewer rejected the prose apply plan because
  it lacked a crash-consistent write-ahead journal, treated bootstrap as an
  undeclared exception, and did not bind deterministic numeric IDs and several
  path/group/recovery invariants precisely. Separately, fixed read-only SSH
  observations found no LXD client, daemon, snap, standard socket, process, or
  resource API. Ubuntu's enabled `lxd-installer.socket` remains an installer
  shim and is not a running LXD daemon.
- **Files and evidence:** [Rejected identity plan](ovh-26-04-identity-access-plan.md)
  and [LXD inventory](ovh-26-04-lxd-inventory.md).
- **Result:** No apply plan is approved. LXD membership removal is eligible for
  future deterministic planning only; the installer package/socket is excluded
  and remains unchanged. Production identity collection still fails closed
  until complete authenticated LXD non-use evidence is wired.
- **Mutation status:** Read-only SSH and documentation only. No package,
  service, account, group, socket, or host state changed.
- **Limitations / next gate:** Implement and test the separate bootstrap plan,
  write-ahead journal, exact UID/GID plan, and complete LXD prerequisite before
  any identity apply can be reviewed.
- **Commit:** Not committed.

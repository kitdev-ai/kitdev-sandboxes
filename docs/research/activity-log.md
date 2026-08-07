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

## 2026-08-06 - Disposable OVH lab framework implementation

- **Intent:** Support fast, user-approved learning on the disposable OVH host
  without creating undocumented manual state or confusing lab success with a
  reusable production installation.
- **Delegated LUNA agent:** LUNA lab-framework implementation agent under
  project-lead supervision.
- **Safe activity summary:** Added a fixed eleven-stage experiment manifest,
  local SSH runner, shared acknowledgement/production guards, normalized
  evidence and redaction rules, stage scripts, static tests, and a promotion
  rule requiring reinstall followed by `kitdev`/Ansible. Independent review
  rejected the initial marker mutation; all mutations now fail closed.
- **Files and evidence:** [Disposable lab framework report](ovh-disposable-lab-framework.md)
  and `experiments/ovh-lab/`.
- **Result:** Only the baseline and raw-storage discovery stages are executable,
  and both are read-only. Marker, package, identity, kernel, Docker,
  network/firewall, upstream, service, and acceptance stages are blocked. Eleven focused
  framework tests and the complete 160-test unit suite passed locally;
  ShellCheck was unavailable.
- **Mutation status:** Repository edits and local static tests only. No SSH,
  upload, remote command, privilege operation, package/account/storage/kernel,
  network/firewall, container/service action, or server mutation was run.
- **Limitations / next gate:** Complete independent review, authorize exact
  stage hashes before remote use, promote learned behavior into typed
  `kitdev`/Ansible automation, reinstall, and qualify the clean reusable path.
- **Commit:** Not committed.

## 2026-08-06 - Ephemeral Ubuntu 26.04 remote verification

- **Intent:** Stop treating the new bare-metal host as an inferred target and
  execute the current repository through a reproducible, tightly bounded
  development harness without enabling persistent host configuration.
- **Delegated LUNA agent:** LUNA remote-harness implementation and execution
  agent under project-lead supervision.
- **Safe activity summary:** Added a deterministic worktree archive and
  isolated SSH test harness. It pinned the privately supplied prior host-key
  observation, created one guarded mode-0700 `/tmp` directory, uploaded and
  extracted the archive, ran the full unit suite and three read-only CLI smoke
  paths, compared worktree and project-root snapshots, checked for bytecode
  cache, and removed the temporary tree through a remote EXIT trap. A separate
  SSH command verified cleanup.
- **Result:** Ubuntu Python 3.14.4 passed 145 unit tests. Doctor, general install
  dry-run, and identity-access dry-run returned expected blocking exit 5 with
  valid JSON. The four fixed project roots were absent before and after; the
  extracted worktree was unchanged; bytecode cache was absent; cleanup passed.
- **Mutation status:** Temporary upload/extraction/result files under one unique
  guarded remote `/tmp` path only, followed by verified removal. Normal SSH/OS
  session audit records may change. No sudo, packages, services, identities,
  firewall, network, kernel, disk, mount, reboot, or persistent project path
  was changed.
- **Files and evidence:** [Ephemeral remote test report](ovh-26-04-remote-test.md)
  and `scripts/dev/ovh-remote-test.sh`. Endpoint and fingerprint remain only in
  ignored private inventory; the fingerprint is still independently unverified.
- **Verification:** Harness `bash -n` and repository whitespace checks passed.
  ShellCheck was unavailable locally and was not installed. The workstation's
  Apple Python 3.9 is below the project language requirement; the authoritative
  remote Python 3.14.4 suite passed all 145 tests.
- **Limitations / next gate:** This validates the nonmutating code path, not
  bootstrap/apply/rollback. Independently verify the host key, then implement
  the journaled reproducible bootstrap and identity phase before seeking a
  privileged mutation approval.
- **Commit:** Not committed.

## 2026-08-06 - Crash-consistent installation journal foundation

- **Intent:** Implement the durable write-ahead state boundary required before
  any resumable or rollback-capable host mutation can be considered.
- **Delegated LUNA agents:** LUNA journal/bootstrap implementation agent and an
  independent LUNA journal review agent under project-lead supervision.
- **Safe activity summary:** Added the crash-consistent installation journal
  implementation and focused tests across two files with about 1,100 inserted
  lines. The final cached diff was clean and the independent review approved
  the journal foundation.
- **Result:** The final pre-commit full suite passed 157 tests. The foundation
  was committed as `7ccf7ea` (`Add crash-consistent installation journal`).
- **Mutation status:** Repository implementation and hermetic tests only. No
  server access or host mutation occurred.
- **Limitations / next gate:** The journal is a prerequisite, not an approved
  bootstrap or identity apply path. Bind it to reviewed typed actions and
  Ansible convergence before any persistent server change.
- **Commit:** `7ccf7ea`

## 2026-08-06 - Ephemeral OVH validation harness commit

- **Intent:** Preserve the bounded remote validation procedure as reusable
  repository code rather than an operator-only command transcript.
- **Delegated LUNA agent:** LUNA remote-harness implementation and execution
  agent under project-lead supervision.
- **Safe activity summary:** Added the ephemeral OVH validation harness and its
  redacted research report across two files with about 410 inserted lines. It
  was executed twice; the final run used OVH Python 3.14.4.
- **Result:** The final remote run passed 145/145 tests at that revision. All
  three dry-run paths returned the expected blocking exit `5`; persistent paths,
  bytecode caches, and worktree content were unchanged; temporary cleanup was
  verified. The work was committed as `7fcc48a` (`Add ephemeral OVH validation
  harness`).
- **Mutation status:** One guarded temporary remote worktree was created and
  removed by the harness. No persistent project path or host configuration was
  changed.
- **Limitations / next gate:** This is nonmutating qualification only. It does
  not approve bootstrap, identity, package, storage, kernel, network, service,
  or other persistent apply work.
- **Commit:** `7fcc48a`

## 2026-08-06 - Private SSH-config boundary for OVH lab runner

- **Intent:** Let tracked lab invocations use a non-identifying SSH alias while
  keeping the endpoint and access configuration entirely outside the
  repository.
- **Delegated LUNA agents:** LUNA lab-framework implementation agent and an
  independent LUNA safety-review agent under project-lead supervision.
- **Safe activity summary:** Added an explicit private SSH-config input to the
  disposable lab runner. Validation requires an absolute regular non-symlink
  file owned by the invoking user with no group or other permission bits. The
  runner passes it through `ssh -F` and binds its SHA-256 into the exact stage
  approval without logging its path or content.
- **Result:** The first review found pathname TOCTOU, unbound `Include` files,
  and an incompletely bounded read. The corrected runner uses a stable bounded
  descriptor, rejects `Include`, writes one exclusive mode-0600 snapshot from
  the hashed bytes, and passes only that snapshot to all SSH phases. Independent
  re-review approved the correction. Fifteen focused tests and the complete
  164-test unit suite passed; Bash syntax and whitespace checks passed.
- **Mutation status:** Repository edits and hermetic local tests only. No SSH,
  endpoint discovery, upload, remote command, or server mutation.
- **Limitations / next gate:** This is deliberately a single-file SSH config;
  `Include` is unsupported. EXIT cleanup removes the private snapshot during
  ordinary completion; an uncatchable process kill can leave a mode-0600 copy
  only inside the ignored, mode-0700 local run directory. No remote invocation
  is authorized by this implementation commit.
- **Commit:** Pending in this change set.

## 2026-08-06 - Ubuntu 26.04 E2B prerequisite qualification plan

- **Intent:** Replace guessed package installation with a primary-source-backed
  sequence for qualifying the disposable Ubuntu 26.04 bare-metal lab for the
  pinned E2B/Firecracker stack.
- **Delegated LUNA agent:** LUNA Ubuntu/E2B prerequisite research agent under
  project-lead supervision.
- **Safe activity summary:** Rechecked current official E2B, Docker, Ubuntu,
  Linux-kernel and Firecracker sources. Distinguished required kernel/runtime
  facilities from optional QEMU/libvirt and cloud deployment tools, then
  documented package, storage, firewall, port and acceptance gates stage by
  stage.
- **Files and evidence:** [Ubuntu 26.04 E2B prerequisites](ovh-26-04-e2b-prerequisites.md).
- **Result:** Docker officially supports Ubuntu 26.04 amd64, but upstream E2B
  does not support General Linux or this single-host topology and its current
  kernel-facing evidence remains based on Ubuntu 24.04. Ubuntu 26.04 therefore
  requires explicit KVM, NBD, HugeTLB, userfaultfd, cgroup, snapshot, network
  and cleanup qualification before compatibility can be claimed.
- **Mutation status:** Research and tracked documentation only. No SSH, server
  access, package operation, service action or host mutation occurred.
- **Limitations / next gate:** Independently review the staged recommendations,
  resolve exact Docker/toolchain versions into locks, then implement only the
  next approved journaled stage with before/after and rollback evidence.
- **Commit:** Not committed.

## 2026-08-06 - OVH lab stages 00/30 run and collector corrections

- **Intent:** Record the first approved read-only disposable-lab baseline and
  storage-discovery run, then correct two conservative interpretation defects
  without authorizing storage mutation.
- **Delegated LUNA agents:** LUNA lab-framework implementation agent and an
  independent LUNA safety-review agent under project-lead supervision.
- **Safe activity summary:** Stages `00` and `30` completed remotely through the
  guarded runner with no intentional host mutation. Review found that the
  missing Docker unit was rendered as `error` and both partitioned NVMe RAID
  parents were misclassified as raw disks. Locally, stage `30` was changed to
  parse bounded structured `lsblk` JSON plus holders/slaves/MD topology, and
  systemd service-state interpretation was made outcome-aware.
- **Files and evidence:** [Stages 00/30 first run](ovh-lab-stages-00-30-first-run.md),
  a generic RAID-parent/raw-leaf fixture, and focused lab-framework tests.
- **Result:** Twenty-one focused tests and the complete 170-test suite pass.
  Review found and correction closed exact dot-component traversal and a lost
  executable-mode bit; topology depth/node bounds and normalized recursion
  failure were added. Independent re-review approved the result and reproduced
  both suites, Bash syntax, and whitespace checks.
- **Mutation status:** The recorded remote stages were read-only; current work
  is repository code, fixtures, tests, and redacted documentation only. No
  storage or other host mutation occurred.
- **Limitations / next gate:** All storage mutation is blocked until the
  corrected read-only stage is reviewed, committed, rerun, and reconciled with
  private inventory.
- **Commit:** Pending in this change set.

## 2026-08-06 - Approval-bound OVH known-hosts snapshot

- **Intent:** Close the gap where an approved lab run still read a live
  operator `known_hosts` file during SSH execution.
- **Delegated LUNA agents:** LUNA lab-framework implementation agent and an
  independent LUNA safety-review agent under project-lead supervision.
- **Safe activity summary:** Required `OVH_LAB_KNOWN_HOSTS` during approval and
  execution, added stable bounded `O_NOFOLLOW` validation, bound its SHA-256
  into the approval, and made every SSH phase consume one distinct exclusive
  mode-0600 run-local snapshot. Source/snapshot inode aliasing is rejected and
  both private snapshots are removed on exit. Global known-host files,
  `KnownHostsCommand`, DNS host-key verification, and host-key updates are
  disabled on the SSH command line so no unbound trust source can authorize or
  rewrite the approved snapshot.
- **Result:** Twenty-seven focused tests and the complete 185-test suite passed,
  including a hermetic fake-SSH four-phase execution test. Bash syntax and
  whitespace checks passed. Independent LUNA review found an initial unbound
  OpenSSH trust-source/update gap; after the command-line isolation fix, it
  reproduced the focused suite and approved the gate with no remaining
  findings.
- **Mutation status:** Repository edits and hermetic local tests only. No SSH,
  endpoint discovery, upload, remote command, or server mutation.
- **Limitations / next gate:** Group/other read access is accepted for public
  host-key verification material; execute, special, and group/other write bits
  are rejected. An uncatchable process kill can leave mode-0600 copies only in
  the ignored mode-0700 run directory. No server action is authorized here.
- **Commit:** Pending in this change set.

## 2026-08-06 - Stage 05 journaled lab authorization and workspace

- **Intent:** Enable only the minimum reversible mutation needed to mark the
  disposable lab and allocate its empty experiment workspace, without creating
  a second journal algorithm or installing remote implementation source.
- **Delegated LUNA agents:** LUNA Stage 05 contract-mapping and bundle-design
  agents, followed by an independent LUNA code and safety reviewer under
  project-lead supervision.
- **Safe activity summary:** Extended the committed `JournalStore` foundation
  with one locked-session transaction boundary and read-only in-flight record
  inspection. Added a typed Stage 05 reconciler for canonical plan/marker
  bytes, fixed descriptor-relative paths, production refusal, exact
  root/mode/ownership/mount/link/xattr policy, write-ahead apply, crash resume,
  strict rollback, and bounded evidence. The runner embeds exact component
  bytes and digests and streams them through an anonymous pipe to isolated
  Python; it does not install or leave source on the server.
- **Contract amendment:** A retry may adopt only the exact root-owned,
  link-count-two, empty `0700` provisional directory at the legal crash prefix
  before its required final `0755` mode. It verifies descriptor identity,
  mount, ACL, xattrs and capabilities, performs one `fchmod` plus `fsync`, and
  revalidates. Read-only observation never repairs it. Every other existing
  wrong-mode directory remains a conflict.
- **Files and evidence:** [Stage 05 contract](ovh-stage05-marker-contract.md),
  [lab framework](ovh-disposable-lab-framework.md), the experiment README,
  typed journal/reconciler modules, and hermetic unit tests. Existing corrected
  Stage 00/30 off-host artifacts were summarized without opening SSH.
- **Result:** The focused journal, Stage 05, and lab-framework suite passes
  99/99 with bytecode disabled, and the complete suite passes 225/225. Coverage
  includes exact byte fixtures, apply/apply,
  rollback/rollback, all enumerated forward and reverse crash points,
  provisional and linked residue, symlink/mount/ownership/mode/ACL/xattr/
  capability conflicts, plan mismatch, durability replay through a second
  recovery crash, terminal-residue preservation, legal cross-operation journal
  recovery, and process-lock tests. Independent review found seven initial
  recovery defects and one later terminal-residue ambiguity; all were corrected
  and independently reproduced. Re-review approved the local gate with no
  remaining blockers.
- **Mutation status:** Repository edits and hermetic temporary-directory tests
  only. No SSH, endpoint discovery, upload, remote command, or server mutation
  occurred in this implementation task. Stage 05 is the sole manifest-enabled
  mutation, but this change does not itself authorize a remote invocation.
- **Limitations / next gate:** A separately approved disposable-host
  apply/apply/rollback/rollback exercise. Later mutations remain blocked, and
  final acceptance still requires reinstalling Ubuntu 26.04 and qualifying
  clean automation. `shellcheck` was unavailable locally; Bash parsing passed.
- **Commit:** Pending in this change set.

## 2026-08-06 - Stage 05 Ubuntu usr-merge precheck correction

- **Intent:** Diagnose the first remotely approved Stage 05 precheck failure
  and correct only the redundant lexical production-unit scan that made the
  standard Ubuntu 26.04 usr-merge layout unclassifiable.
- **Delegated LUNA agent:** LUNA lab-framework implementation agent followed by
  independent LUNA safety re-review under project-lead supervision.
- **Safe activity summary:** Off-host run `run-05-GRF4C5bu` contains only the
  Stage 05 run-start record and fixed reason `production_state_unknown`. A
  project-lead read-only diagnosis established that all three bounded systemd
  `LoadState` queries returned `not-found`, `/usr/lib/systemd/system` is the
  real unit directory, and `/lib` is the standard usr-merge symlink. The
  redundant `/lib/systemd/system` lexical scan correctly rejected that symlink
  under its no-follow policy, so the operation stopped before Stage 05 execute.
- **Correction:** Removed only `/lib/systemd/system` from direct path scans.
  Direct scans of `/etc/systemd/system`, `/usr/lib/systemd/system`, and the
  multi-user wants directory remain, as do `LoadState` queries for every known
  production unit. A standard `/lib -> usr/lib` fixture now passes absence;
  a symlink in any retained scanned ancestry still fails closed.
- **Mutation status:** The failed remote precheck made zero Stage 05 mutation:
  no journal/root allocation, marker, or workspace operation began. This
  correction and its tests used no SSH, network access, or server mutation.
- **Result:** The 100-test focused journal/Stage 05/framework suite and complete
  226-test suite pass. Independent re-review additionally exercised all three
  retained ancestries, present unit files, and exact service queries, then
  approved the local gate with no findings. Bash, AST, JSON, whitespace, and
  bytecode-artifact checks passed; `shellcheck` was unavailable.
- **Limitations / next gate:** Generate a new exact bundle-bound approval before
  any remote retry; the source correction invalidates the failed run's prior
  approval digest.
- **Commit:** Pending in this change set.

## 2026-08-06 - Stage 05 remote apply/apply qualification

- **Intent:** Record the first successful journaled disposable-lab mutation and
  its idempotent second execution from bounded off-host evidence.
- **Safe activity summary:** Run `run-05-ZgYEf0Rm` began with absent Stage 05
  state, applied the exact plan, reached `validated` through four transitions,
  and passed independent after/postcondition collection with an empty workspace
  and retained provenance. Run `run-05-JTmISBLY` began, executed, and ended in
  the same exact `validated` four-transition state with identical plan, bundle,
  and marker hashes. All operation, after, and postcondition return codes were
  zero for both runs.
- **Files and evidence:** [Stage 05 apply/apply report](ovh-stage05-first-run.md)
  summarizes the ignored redacted artifacts and exact public-safe hashes. The
  private server inventory records the current project-owned host state.
- **Result:** Remote Stage 05 apply/apply qualification passed. The second run
  published no new journal transition and preserved the exact empty workspace,
  marker, and retained journal provenance.
- **Mutation status:** The first successful run created only the fixed
  project-owned state/journal roots, Stage 05 journal, config/experiments/
  workspace directories, and canonical disposable-lab marker. The idempotent
  second run performed validation only. Neither run contained package,
  account/group, storage, kernel, Docker/container, network/firewall, systemd
  service, mount, or reboot actions.
- **Limitations / next gate:** Rollback/rollback host qualification remains
  pending under separate exact approvals. Later mutation stages remain blocked,
  and final acceptance still requires a clean Ubuntu reinstall and reusable
  automation qualification.
- **Commit:** Pending in this documentation change set.

## 2026-08-06 - Stage 10 first-run fail-closed diagnosis

- **Intent:** Record the first Stage 10 host result and make future precheck
  failures actionable without exposing package names, versions, or command
  output.
- **Safe activity summary:** Off-host run `run-10-H2Ynnbd9` stopped before its
  first snapshot with `package_inventory_broken`. The plan-only stage made zero
  mutation. A bounded read-only follow-up found `dpkg --audit` clean, all three
  prerequisite/trust packages in `install/ok/installed` state, and all eight
  Docker conflict packages absent.
- **Correction:** Split audit invocation, nonzero, and dirty-output failures by
  pre/post phase. Dpkg error states now report only a deterministic package
  probe index. The fixed index ranges identify the contract category without
  publishing host-derived package or version data.
- **Files and evidence:** [Stage 10 first-run diagnosis](ovh-stage10-first-run.md),
  typed resolver tests, and the existing ignored redacted run artifact.
- **Mutation status:** The remote run and its follow-up diagnostics were
  read-only. This local change updates code, tests, and documentation only. No
  SSH or server operation was performed while preparing the correction.
- **Limitations / next gate:** Independent LUNA re-review and local focused/full
  tests are required before generating a new exact bundle-bound approval.
  Stage 10 still cannot apply, and Stage 50 remains blocked.
- **Commit:** Pending in this change set.

## 2026-08-06 - Manual disposable-lab Docker bootstrap and tracked replay

- **Intent:** Preserve the explicitly approved mutation-first package,
  repository, and Docker Engine exercise as a reviewable disposable-host replay
  without enabling the staged Docker mutation.
- **Safe activity summary:** The project lead ran `apt-get update`, explicitly
  requested the fixed baseline tools, verified and published the pinned Docker
  key and canonical Resolute source, refreshed metadata, and installed five
  exact Docker package versions. Only `make` was newly installed by the
  baseline request; eight already-installed packages were changed to manual.
  Docker and containerd ended active/enabled with overlayfs, systemd cgroups,
  cgroup v2, zero containers, and zero images.
- **Interrupted attempt:** The first Docker script stopped after repository
  update and before Docker installation because an early-exit `awk` consumer
  caused `apt-cache` to receive `SIGPIPE` under `pipefail`. The safely resumed
  parser consumes the full input and decides in `END`; the tracked replay keeps
  that invariant.
- **Tracked artifact:** `experiments/ovh-lab/bootstrap-docker-engine.sh` pins
  all explicitly requested observed versions, the key digest/fingerprints, and
  exact source bytes; rejects conflicts/foreign state and clears caller
  `APT_CONFIG`;
  requires a framed component-digest acknowledgement from a root-owned
  immutable checkout and operation-wide Stage 05 authorization; publishes with
  no-clobber semantics; and provides an exact read-only verify mode.
- **Mutation status:** The approved host work changed APT lists and manual
  marks, installed `make`, wrote repository trust/source files, installed
  Docker packages and solver dependencies, and activated package-managed
  services. This implementation task itself used no SSH and made repository
  edits/tests only.
- **Rollback / promotion:** The original dependency closure and complete mark
  pre-state were not captured, so no automated rollback is claimed. Reinstall
  remains authoritative. Stage 50 stays blocked, and production still requires
  isolated artifacts, journaling, reverse-plan qualification, and clean-image
  automation tests.
- **Result:** The dedicated bootstrap suite ran 12 tests with one expected
  Linux-only skip. The focused bootstrap/Stage 10/framework suite ran 60 tests
  with the same skip, and the complete suite ran 257 tests with the same skip.
  `bash -n` and `git diff --check` passed. Independent LUNA re-review approved
  the normalized invocation and complete component-ancestry trust boundary
  with no remaining blocker.
- **Commit:** `67fc006` (`Document reproducible OVH Docker bootstrap`).

## 2026-08-06 - Live-lab firewall, hugepage, build, and database mutations

- **Intent:** Preserve public-safe facts from the explicitly approved
  mutation-first lab work without treating manual state as qualified stage
  automation.
- **Safe activity summary:** UFW was enabled with deny-in/allow-out and SSH-only
  ingress; 2,048 2-MiB hugepages and hugetlbfs were configured; pinned E2B
  orchestrator binaries were built; digest-pinned PostgreSQL, Redis, and
  ClickHouse containers were bound to loopback with persistent data; and the
  pinned PostgreSQL/ClickHouse migrations and local development seed completed.
- **Control plane:** Redis and digest-pinned Loki were moved to the internal
  Docker network without host ports. Root-only fresh lab secrets were created
  without output. The built orchestrator/template-manager was installed into
  the managed runtime area and started as a transient root systemd service. It
  created its cgroup and 32 network namespaces and reported healthy. A scoped
  UFW rule allowed only the verified `kitdev-core` bridge subnet to the bridge
  gateway's TCP/5008 listener. During final verification, the host orchestrator
  and loopback-only API and client proxy health endpoints each returned `200`.
  Root execution, transient service ownership, and missing final application
  image digests remain production blockers.
- **Network finding:** `kitdev-core` used non-overlapping subnet
  `172.18.0.0/16` on bridge `br-10f4c6294b40`, but Docker's `host-gateway`
  mapped the API hostname to the default bridge gateway `172.17.0.1`. The
  request timed out until the API was recreated with explicit mapping to the
  verified `kitdev-core` gateway `172.18.0.1`. Automation must discover,
  verify, and journal this topology rather than assuming `host-gateway` follows
  a container's attached network.
- **Guest artifact:** A direct Go build produced the root-owned, group-readable
  `envd` 0.6.13 artifact at pinned commit `882a3b4`: 12,927,102 bytes with
  SHA-256 `530d84dfbfd82c05181e0dc61ca842f3caaa349b0cc2f3f52d2d8eb9478aa67e`.
  Earlier precompile attempts exposed dubious-checkout ownership and a missing
  `make` assumption in the pinned builder image; neither altered the checkout.
- **Failure/resume evidence:** The ClickHouse migrator first stopped on its
  missing `cluster` dependency, and the seed first stopped because Go attempted
  to update a read-only `go.work.sum`. The corrected ClickHouse config and an
  isolated writable seed-source copy allowed safe reruns without modifying the
  canonical checkout.
- **Security boundary:** Lab credentials are omitted. A generated ClickHouse
  bind config required host mode `0644`, which is a production blocker for
  embedded credentials. All database host ports were loopback-only, and SSH
  effective policy was observed but not mutated.
- **Mutation status:** These host changes were manual and explicitly approved.
  Preparing this report used no SSH and made documentation changes only.
- **Automation / rollback:** Stages 20/40/60/70/80 remain blocked and must own
  their respective identity, hugepage, firewall, build, and service changes.
  No complete reverse plan exists; reinstall remains the authoritative reset.
- **Commit:** Initial record in `67fc006`; control-plane follow-up pending in
  this change set.

## 2026-08-06 - Reproducible control-plane replay slice

- **Intent:** Convert the successful disposable-host control plane into
  reviewable, replayable repository assets without claiming production
  qualification or mutating the remote host.
- **Implementation:** Added digest-pinned Compose services, exact source-build
  wrappers, nonrotating root-only private state, verified network adoption,
  conflict-audited UFW rules, exact runtime layout, and a persistent host
  orchestrator unit with fail-closed preflight and byte-for-byte install
  verification.
- **Identity correction:** Reserved UID/GID `61000-61999` for project service
  identities and persisted deterministic mappings in the Stage 20 plan. The
  replay rejects the disposable host's colliding UID `999` worker before
  layout mutation. Clean-image probes established PostgreSQL `999:root` mode
  `0700`, Redis `999:root` mode `0750`, ClickHouse `101:101` mode `0750`, and
  Loki `10001:10001` mode `0750` as the convergent top-level datastore state.
- **Evidence boundary:** The implementation and tests ran locally and made no
  SSH or OVH mutation. Credentials and management endpoints are absent. The
  successful live build/resume and public-port observations are normalized in
  [`control-plane-replay-slice.md`](control-plane-replay-slice.md).
- **Remaining gate:** Fresh Ubuntu 26.04 apply/apply, restart, rollback,
  reinstall, restore, and concurrent-sandbox qualification remain required.
  Ubuntu 25.04 remains development/migration-only; Ubuntu 24.04 is unsupported.
- **Commit:** Pending in this change set.

## 2026-08-06 - Live snapshot-to-API-to-envd milestone

- **Intent:** Prove the pinned local template can traverse the complete API,
  orchestrator, Firecracker, client-proxy, and envd command path, then retain a
  normalized record and reproducible helper-build/network contracts.
- **Result:** Direct command-bearing resumes passed for both the base and
  incremental snapshots. The copied incremental template was transactionally
  seeded, API create returned `201`, the pinned ConnectRPC client received the
  exact command sentinel with exit code zero, active delete returned `204`,
  and final API, Firecracker, and Redis cleanup assertions passed.
- **Corrections:** Local template storage requires the appended `templates/`
  directory. Both API and client-proxy require the exact derived project-bridge
  gateway mapping; Docker's generic host gateway resolved to the wrong bridge.
  UFW must separately permit only project-bridge traffic to host TCP 5007 and
  5008.
- **Artifacts:** `copy-build` and `resume-build` are rebuilt from pinned commit
  `882a3b4` with the digest-pinned Go builder, exact sizes/hashes, online module
  prefetch, and a network-disabled final build. No credential or management
  endpoint is retained in the repository.
- **Chain evidence:** Both version-3 binary headers were decoded with the
  pinned upstream package. Their mappings validated full virtual-size coverage
  and selected the exact 11-file, suffixless snapshot tree: six direct files
  and five ancestor rootfs layers, all now recorded with byte sizes, hashes,
  ownership, and modes.
- **Evidence:** See
  [`ovh-api-client-proxy-e2e.md`](ovh-api-client-proxy-e2e.md). Fresh Ubuntu
  26.04 replay and reinstall qualification remain open; Ubuntu 25.04 is
  development/migration-only and Ubuntu 24.04 is unsupported.
- **Commit:** Pending in this change set.

## 2026-08-06 - Reproducible API-to-proxy E2E verifier

- **Intent:** Turn the successful live API, Firecracker, client-proxy, and envd
  command path into a bounded, credential-safe replay gate for development and
  migration hosts.
- **Implementation:** Added transactional curl credential files, an exact
  pinned API readiness predicate, an offline-built ConnectRPC command client,
  serialized sandbox creation, and cleanup that converges API, Firecracker,
  and Redis state on success or failure.
- **Evidence boundary:** The API schema predicates were checked against pinned
  source and a credential-safe live query. Local unit, shell, lint, and static
  security gates passed. The pinned ConnectRPC client also compiled as an
  exact-locked Linux amd64 artifact with the final build network disabled and
  source mounted read-only. The complete wrapper still requires execution on
  the Ubuntu x86_64 development host with a caller-supplied API-key file.
- **Support:** Ubuntu 25.04 remains development/migration-only and Ubuntu 26.04
  is the production target. Ubuntu 24.04 is unsupported.
- **Commit:** Pending in this change set.

## 2026-08-07 - TypeScript SDK and external ingress contract

- **Intent:** Select the exact official TypeScript client compatible with the
  pinned backend and define the credential-safe, externally reachable test
  contract for `sandbox.kitdev.ai`.
- **Result:** Selected `e2b@2.38.0`, recorded immutable npm and Node runtime
  integrity pins, mapped public SDK operations, and documented official
  project API-key creation/revocation semantics.
- **Ingress:** Defined `api.sandbox.kitdev.ai` as the API route and
  `*.sandbox.kitdev.ai` as the client-proxy fallback, including the official
  host parser, shared-host headers, WebSocket/streaming requirements, and a
  Let's Encrypt DNS-01 wildcard-certificate contract. The DNS provider remains
  a required input.
- **Evidence boundary:** Primary-source research and local documentation only;
  this activity did not connect to or mutate the server. Live SDK execution,
  ingress publication, certificate issuance, pause/snapshot storage, and
  template-manager coverage remain explicit gates.
- **Storage finding:** Docker's active containerd content store is on the small
  NVMe root despite the reported Docker data-root. Image pulls/builds require a
  relocation or capacity gate and must not race active sandbox tests.
- **Support:** Ubuntu 25.04 remains development/migration-only and Ubuntu 26.04
  is the production target. Ubuntu 24.04 is unsupported.
- **Commit:** Pending in this change set.

## 2026-08-07 - Prepared-host CLI lifecycle integration

- **Intent:** Connect the reviewed control-plane replay assets to the public
  repository-local CLI without claiming that fresh-host prerequisite apply is
  complete.
- **Implementation:** Added minimal-profile prepared-host `install`, `up`,
  quiesced `down`, `restart`, structured `status`, and explicit `test core`,
  `test sdk`, and combined `test smoke` dispatch. Install publishes day-two
  assets below `/opt`; later operations re-execute those installed copies.
- **Production gate:** Install refuses production before mutation because the
  only current template seed is explicitly development/migration-only.
  Production day-two lifecycle remains supported for a previously installed
  control plane; production template publication remains a release blocker.
- **Shutdown safety:** Down refuses active Firecracker processes, stops new
  API/proxy admission, checks again for a racing sandbox, stops the host
  orchestrator before Compose, and attempts to restore the running service set
  when a later stop step fails.
- **Evidence boundary:** This implementation and its tests were local only. It
  made no SSH connection or server mutation and contains no endpoint,
  credential, host identifier, or secret.
- **Verification:** The complete local suite passed 312 tests with two expected
  platform/tool skips. Python compilation, Bash syntax for every tracked shell
  entrypoint, diff whitespace, and lifecycle credential/endpoint pattern scans
  passed. Ruff, mypy, and ShellCheck were not available locally; the repository
  also has no selected/pinned development-tool versions yet, so those gates
  were not installed ad hoc and remain pending.
- **Remaining gates:** Fresh-host package/identity/kernel/Docker preparation,
  standalone installed Python CLI, complete manifest/journal ownership,
  non-minimal profiles, update/uninstall, restore, and clean Ubuntu 26.04
  replay remain pending. Ubuntu 25.04 is development/migration-only; Ubuntu
  24.04 is unsupported.
- **Commit:** Pending in this change set.

## 2026-08-07 - OVH CLI lifecycle exercise stopped before mutation

- **Intent:** Stage the exact reviewed lifecycle release and prove status,
  dry-run, down/up/restart, and combined SDK smoke on the disposable lab after
  all sandbox agents reported terminal cleanup.
- **Read-only result:** Firecracker was zero, but the new expected orchestrator
  unit was not installed and no container carried the new Compose project
  identity. An independent check confirmed the healthy lab still used the
  manually assembled legacy orchestrator unit and six legacy containers.
- **Safety decision:** The new lifecycle refuses unowned/manual state. No
  release checkout was staged and no service, container, sandbox, package,
  identity, filesystem, firewall, or configuration mutation occurred. Running
  the new unit beside the legacy runtime was rejected as a dual-ownership risk.
- **Diagnostic correction:** One read-only `fuser` invocation used an
  unsupported separator, printed usage, and returned nonzero. It changed
  nothing and the overall predicate remained blocked.
- **Next gate:** Clean Ubuntu 26.04 reinstall and one-owner replay are required
  before lifecycle qualification. See
  [`ovh-cli-lifecycle-precheck.md`](ovh-cli-lifecycle-precheck.md).
- **Commit:** Pending in this change set.

## 2026-08-07 - Fresh-host prerequisite Ansible slice

- **Intent:** Replace the blocked disposable-lab package/identity/kernel stages
  with reusable fresh-host convergence while preserving strict lifecycle and
  ownership boundaries.
- **Implementation:** Added a localhost-only pinned Ansible controller and
  narrow preflight, package, identity, kernel, manifest, and removal roles.
  Ubuntu 26.04 is production eligible; Ubuntu 25.04 requires explicit
  development/migration; Ubuntu 24.04 is unsupported.
- **Safety:** All platform, APT trust, NSS/name/numeric collision, KVM/TUN,
  loaded-NBD, hugepage total-RAM and current-MemAvailable gates execute before
  the first project host mutation. Only the worker receives KVM membership.
  No role edits SSH, unattended updates, Docker, firewall, storage, or
  unrelated services.
- **Rollback:** Root-only prior state records packages, identities, files,
  sysctls, and loaded modules. Removal refuses managed-file or live-sysctl
  drift and active service identities, restores only recorded prior state, and
  emits an ephemeral controlled-reboot marker instead of forcibly unloading
  KVM/TUN/NBD modules.
- **Evidence boundary:** Implementation and validation were local only. The
  intentionally incompatible legacy OVH lab was not contacted or mutated.
- **Verification:** An empty virtual environment installed the complete hashed
  lock and reported `ansible-playbook [core 2.21.2]`; both playbooks passed
  syntax check. The final full Python unit suite passed 328 tests with two
  expected platform/tool skips, including the expanded focused prerequisite
  suite. Bash syntax and Git whitespace checks passed.
- **Remaining gates:** Apply/apply, check-mode zero-mutation evidence, reboot
  persistence, remove/remove and post-removal reboot evidence on clean Ubuntu
  26.04 production and Ubuntu 25.04 development/migration hosts remain pending.
- **Commit:** Pending in this change set.

## 2026-08-07 - Offline backup and clean-target restore slice

- **Intent:** Implement the first reproducible backup/restore format for the
  project-owned minimal control-plane state without claiming an untested live
  restore.
- **Implementation:** Added a lifecycle-lock-serialized coordinator for a
  quiesced physical backup of PostgreSQL, Redis, ClickHouse, Loki, and local
  template/snapshot storage. Canonical manifests bind archive hashes and sizes
  to the installed Compose/image locks, architecture, backup schema, and pinned
  upstream commit. Restore validates every input, secret compatibility, stopped
  service state, free space, and empty targets before journaled publication.
- **Safety:** Active Firecracker processes, partial service state, symlinks,
  nested mounts, special files, path traversal, foreign backup entries,
  archive tampering, incompatible releases, and non-clean targets fail closed.
  Backup restores the prior running/stopped state on success and failure.
  Pre-publication restore interruptions remove staging; publication resumes
  from a root-only journal.
- **Secret boundary:** `/etc`, DNS API tokens, ACME/TLS material, and SDK keys
  are excluded. Operators must use separate encrypted storage or reissue them;
  the root-only manifest records an exact high-entropy private-environment
  digest for restore compatibility, never the secret values.
- **Evidence boundary:** Implementation and validation were local only. No SSH
  connection or OVH mutation occurred and no destructive restore is claimed.
- **Verification:** Ten focused unit tests and the complete 336-test suite
  passed, with two expected platform/tool skips. Ruff, Python compilation,
  Bash syntax, and Git whitespace checks passed. ShellCheck was unavailable.
- **Remaining gates:** Seeded live backup, off-host round trip, clean-release
  restore, fault injection at every publication step, service health, and
  official TypeScript SDK/template/snapshot verification must pass before the
  public `kitdev backup` and `kitdev restore` commands are exposed.
- **Commit:** Pending in this change set.

## 2026-08-07 - Heavy-sandbox hugepage capacity correction

- **Intent:** Replace the 1 GiB prerequisite floor with a derived capacity
  profile suitable for 8 GiB browser/heavy sandboxes without hard-coding 8 GiB
  as the reusable platform minimum.
- **Implementation:** The default now derives a 24 GiB persistent HugeTLB pool
  (`12288` 2 MiB pages) from two 8 GiB live-sandbox slots and one 8 GiB
  transient-mapping allowance. Validation caps the pool at 50% of physical RAM,
  requires 16 GiB of normal memory after any additional page allocation, and
  records every input and derived result in the ownership manifest. Explicit
  profiles remain valid down to 512 MiB per sandbox.
- **Capacity boundary:** The default covers either two live guests plus one
  snapshot mapping, or one live guest plus a build requiring two guest-sized
  mappings. It does not cover two live guests and that build simultaneously;
  that workload needs a 32 GiB pool. The reservation is not runtime admission
  control.
- **Research:** Pinned upstream E2B source at commit
  `882a3b4786755db9e94be3297de6827f9100ce5e` was inspected over read-only SSH
  to confirm host allocation, Firecracker hugepage, template-build, and empty
  memory-file behavior. No server state was changed. Evidence and remaining
  qualification are in
  [`hugepage-capacity-model.md`](hugepage-capacity-model.md).
- **Verification:** Eleven focused prerequisite tests and the complete
  330-test local suite passed with two expected platform/tool skips. Both
  Ansible playbooks passed syntax check under pinned `ansible-core==2.21.2`;
  Python compilation and Git whitespace checks passed.
- **Remaining gates:** Clean-host apply/reboot/idempotency/removal, 24 GiB live
  allocation, the two supported workload combinations, failure cleanup, and
  runtime admission control remain unqualified. Ubuntu 26.04 is the production
  target; Ubuntu 25.04 remains development/migration-only; Ubuntu 24.04 is
  unsupported.
- **Commit:** Pending in this change set.

## 2026-08-07 - Disposable-lab legacy capacity migration path

- **Intent:** Apply the reviewed 24 GiB hugepage profile to the live disposable
  lab without weakening fresh-host ownership guards or manually editing legacy
  configuration.
- **Read-only audit:** The SDK lock was exact and free; the lifecycle lock was
  absent; Firecracker and template-build process counts were zero; PostgreSQL
  contained only `ready` and `failed` build groups; six expected containers,
  Docker, the legacy orchestrator, API, and proxy were healthy. All 2,048
  existing pages were free and ordinary `MemAvailable` was about 56 GiB.
- **Ownership blocker:** The normal prerequisite role correctly cannot adopt
  the legacy worker/group identities or separately named kernel files without
  its manifest. No mutation was attempted through that path.
- **Implementation:** Added an Ubuntu 26.04 development-only migration wrapper
  and Ansible role. Apply holds the exact SDK lock, atomically creates and holds
  the initially absent lifecycle lock, repeats idle checks, queries PostgreSQL
  for nonterminal builds, proves the exact service/container set and the single
  root-owned legacy sysctl file, then records rollback state before mutation.
  Removal requires the apply-created lock and authenticated manifest.
- **Scope:** The migration adopts only the exact legacy hugepage file and live
  pool. It does not adopt identities, modules, services, containers, storage,
  Docker, firewall state, or the broader prerequisite contract.
- **Failure recovery:** The file/sysctl/verification/manifest sequence is an
  Ansible transaction with explicit post-file and post-sysctl injection points.
  A failed incomplete first apply restores and verifies the exact prior file
  and live pool, removes an incomplete manifest, and retains root-only prior
  state for audit and safe retry.
- **Verification:** Nine focused local tests and the complete 344-test workspace
  suite passed with two expected platform/tool skips. Bash and Python parse
  checks, both migration action syntax paths, and Git whitespace checks passed.
  Live apply and post-apply evidence remain pending.
- **First staging result:** Exact commit `75c6450` was archived to a root-only
  temporary release directory. Controller bootstrap stopped before package or
  capacity mutation because `python3 -m venv --help` succeeded although the
  distro `ensurepip` payload was absent. The reusable bootstrap was corrected
  to query the exact `python3-venv` dpkg install state before venv creation; no
  manual package-install bypass was used.
- **First check result:** The corrected bootstrap installed only four Ubuntu
  venv support packages and the hash-locked repository controller. Migration
  check then stopped before mutation because Ansible skips command modules by
  default in check mode, leaving no container JSON to validate. The role now
  explicitly executes its read-only service, process, database, capacity,
  assignment, and runtime probes during check mode.
- **Successful check:** The next exact committed preview passed every gate and
  predicted only the migration state directory, rollback record, adopted
  sysctl file, and 12,288-page live setting. Before apply, container proof was
  narrowed from verbose inspection JSON to only exact name/running fields so
  Ansible cannot echo container environment into migration logs.
- **Commit:** Pending in this change set.

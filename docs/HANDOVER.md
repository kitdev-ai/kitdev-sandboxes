# Project handover

Checkpoint date: 2026-08-08
Revision at handover: `039cdb8`
Remote: `git@github.com:kitdev-ai/kitdev-sandboxes.git`

This is the clean-resume document for a new project lead or implementation
agent. It supersedes the previous `PROJECT-HANDOVER.md` and `open-tasks.md`,
which are removed; the backlog now lives here so there is one place to look.

It separates three things that are easy to conflate: what is committed, what
has recorded live evidence, and what remains unqualified. Do not infer
production readiness from a successful development-lab test.

## Where this is

**A client on a separate host drives this deployment through the official
`e2b@2.38.0` TypeScript SDK over trusted public HTTPS.** The complete feature
matrix passes — 42 of 42 checks across 10 stages — covering lifecycle,
commands, PTY, files and watch, wildcard guest HTTP, chunked streaming,
WebSocket upgrades, pause/resume, snapshots, and a Chromium sandbox driven by
Playwright. Only TCP 443 is reachable from the Internet.

**The host that does this was assembled by hand and then progressively brought
under reviewed automation. It is not the output of a one-command install.** If
the box died today it would be rebuilt manually. Closing that gap is the
project's largest remaining body of work.

Evidence: [external HTTPS enablement](research/external-https-enablement-2026-08-08.md),
[capacity qualification](research/host-capacity-qualification-2026-08-08.md),
[stable template publication](research/stable-template-publication-contract.md).

## Mission and nonnegotiable requirements

The end goal is a reusable, single-bare-metal E2B-compatible sandbox platform.
Software on another server must be able to use the official TypeScript E2B SDK
to create, reconnect to, inspect, operate, pause, snapshot, and destroy
sandboxes, including command, PTY, file, streaming, and wildcard HTTP traffic.

- Every manual server change must become reviewed repository automation that
  can reproduce the system on a newly installed server.
- Document server work, design decisions, evidence, rollback, and limitations.
- Research output goes below `docs/research/`.
- Pin dependencies. The SDK is `e2b@2.38.0` on Node `22.18.0`.
- Commit and push coherent checkpoints.
- Eventually reinstall the bare-metal host and qualify a replay using only the
  reviewed automation.
- Ubuntu 26.04 LTS is the production target; Ubuntu 25.04 is accepted only for
  explicit development or migration work; **Ubuntu 24.04 is not a target and
  must never be described as one**.
- Never expose databases, Redis, Loki, orchestrator ports, admin credentials,
  or Docker-published loopback services to the Internet.
- External SDK support is proved from a *development* client. Do not extend
  that claim to the product server until the same matrix runs there.

## Current live state

Last verified 2026-08-08 on the Ubuntu 26.04 OVH host. These are recorded
observations, not a standing health assertion — recheck before any mutation.

| Area | State |
| --- | --- |
| Public API | `https://api.sandbox.kitdev.ai` returns 200 with a trusted chain |
| Certificate | Let's Encrypt wildcard `*.sandbox.kitdev.ai`, valid to 2026-11-06 |
| Ingress | Nginx container healthy; service and daily renewal timer enabled |
| Firewall | `public` mode: TCP 443 open to all sources, 80 closed |
| Externally reachable | 443 only; 80, 3000, 3002, 3003, 3100, 5432, 6379, 8123, 9000, 5007, 5008, 5010, 5016-5018 all refused |
| DNS | `A` records for `api.` and `*.`, DNS-only through Cloudflare |
| Containers | 7 healthy: PostgreSQL, Redis, ClickHouse, Loki, API, client proxy, ingress |
| Orchestrator | transient `kitdev-orchestrator-lab`, running as root |
| HugeTLB | 12,288 of 12,288 2 MiB pages free (24 GiB pool) |
| Firecracker | 0 processes |
| Lifecycle locks | both free |

Published templates, both consumer-verified from an off-host client:

```text
kitdev-coding:stable         / kitdev-coding:v1          2 vCPU, 2,048 MiB
kitdev-browser-heavy:stable  / kitdev-browser-heavy:v1   2 vCPU, 8,192 MiB
```

The external product credential is `d63b17ec-07cb-4577-b33d-e576b01be5e9` on
team `kitdev-browser-heavy-team`, stored root-only at
`/etc/kitdev-sandboxes/secrets/external-sdk-product.key`.

## Capacity model

This is the single most misunderstood part of the system, so it is stated
plainly.

**Sandbox memory is served from a reserved HugeTLB pool, not ordinary RAM.**
The pool is the hard ceiling on total concurrent sandbox memory, independent of
how much ordinary RAM is free. Concurrency is therefore
`pool size / per-sandbox RAM`, and it binds exactly:

| Profile | Per sandbox | Measured concurrent |
| --- | ---: | ---: |
| `kitdev-coding:stable` | 2,048 MiB | 12 |
| `kitdev-browser-heavy:stable` | 8,192 MiB | 3 |

Past the pool, `Sandbox.create` fails cleanly with a `SandboxError` and running
sandboxes are unharmed. That is per-request backpressure, not host overcommit,
and it is why a team limit above the pool is safe but buys nothing.

Team limits are a concurrency gauge, never a cumulative quota. `kitdev-browser-heavy-team`
is set to 12 concurrent sandboxes, 2 concurrent builds, 4 vCPU and 8,192 MiB
per sandbox, and a 24 hour maximum lifetime. **Paused sandboxes do not consume
a concurrency slot** — verified by filling the limit, confirming refusal,
pausing one, and successfully creating another.

Two consequences to design around: builds and snapshots need a transient
guest-sized mapping, so a pool filled with sandboxes will fail a build; and
`Sandbox.list()` includes paused sandboxes by default, so it is not a count of
concurrency usage.

Change limits only with `scripts/control-plane/set-team-limits.sh`, which
records prior values create-once at
`/var/lib/kitdev-sandboxes/team-limits/<slug>.prior`.

## The two end users

**Bare-metal operator.** Installs, secures, qualifies, runs, backs up, and
recovers the host. Runbook: [`bare-metal-operator-guide.md`](bare-metal-operator-guide.md).
Owns supported-OS installation, storage, KVM, hugepages, Docker, firewall,
ingress, systemd, DNS-01 credentials and renewal, API-key lifecycle, capacity
policy, and clean-host acceptance evidence.

**TypeScript product integration agent.** Works on a different server and
receives only a public API URL, sandbox domain, product-scoped key, and
template aliases. Guide: [`typescript-sdk-integration-guide.md`](typescript-sdk-integration-guide.md),
with the exact client API in [`research/e2b-typescript-sdk-api-surface.md`](research/e2b-typescript-sdk-api-surface.md).
It must pin `e2b@2.38.0`, keep the key out of Git, logs and arguments, always
clean up sandboxes, and never receive the admin token or DNS secrets.

```dotenv
E2B_API_URL=https://api.sandbox.kitdev.ai
E2B_DOMAIN=sandbox.kitdev.ai
E2B_API_KEY=<runtime secret>
E2B_VALIDATE_API_KEY=true
E2B_TEMPLATE=kitdev-coding:stable
```

Never set `E2B_SANDBOX_URL` externally and never enable `E2B_DEBUG` — it skips
real control-plane calls and silently bypasses the system under test.

## Host and topology

Dedicated OVH bare metal running Ubuntu Server 26.04 directly. Intel i7-7700K
(4 cores / 8 threads, VT-x), 64 GB RAM, 2 x ~450 GB NVMe in RAID1, 1 x ~4 TB
SATA. The endpoint, address, user, and keys are private: access configuration
is at `docs/private/ovh-lab-ssh.conf` (untracked, mode 0600, alias
`ovhkitdevlab`) and the full inventory at
`docs/private/ovh-26-04-server-inventory.md`. **Never copy any of it into
tracked files.**

Compose binds PostgreSQL `5432`, ClickHouse `8123/9000`, API `3000`, and client
proxy `3002/3003` to loopback. Redis `6379` and Loki `3100` stay on the private
container network. The orchestrator uses `5007`, `5008`, `5010`, and
`5016-5018`; those listen on the wildcard address but UFW scopes them to the
Docker bridge and guest veth. **A wildcard bind is never permission to open a
port.**

Nginx terminates TLS for the API and wildcard routes only and proxies to
loopback. The container needs exactly `CHOWN`, `KILL`, `NET_BIND_SERVICE`,
`SETGID` and `SETUID`: the master binds 443 as root and drops workers to an
unprivileged user, and removing any one makes nginx exit at startup.

Sandbox hostnames are `<port>-<sandboxId>.sandbox.kitdev.ai`, where sandbox IDs
are `i` plus 20 lowercase alphanumerics. The ingress wildcard route depends on
that exact shape.

DNS must use `A` records. **A wildcard `CNAME` breaks certificate issuance**: it
also matches `_acme-challenge`, sending DNS-01 validation into a zone this
project does not control. The prior record set is retained root-only at
`/var/lib/kitdev-sandboxes/ingress-dns-rollback.json`.

## Secrets and API-key lifecycle

The fresh-install administrator environment is
`/etc/kitdev-sandboxes/control-plane.env`; the legacy lab uses
`/etc/kitdev-sandboxes/e2b-lab.env`. Never print, copy into chat, or commit
either. Secret files are regular, single-link, `root:root`, mode `0600` or
stricter.

Verify a secret by its *properties* — ownership, mode, link count, size, a
shape regex, or an authenticated call that succeeds. That proves a credential
works without ever reading it.

```console
sudo ./kitdev api-key teams
sudo ./kitdev api-key create --team-slug <slug> --name <product> \
  --output /etc/kitdev-sandboxes/secrets/<product>.key
sudo ./kitdev api-key verify \
  --key-file /etc/kitdev-sandboxes/secrets/<product>.key \
  --metadata-file /etc/kitdev-sandboxes/secrets/<product>.key.metadata.json
sudo ./kitdev api-key list --team-slug <slug>
```

Rotate by issuing and proving a replacement, updating the product service, then
revoking the exact old key ID with duplicate confirmation and
`--delete-key-file`. Revocation is immediate and irreversible; recovery is
replacement, not restoration.

DNS provider tokens, ACME account data, TLS private keys, API keys, and
database secrets need an encrypted backup separate from ordinary project data.
The offline backup format deliberately excludes all of them.

## Backlog

Dependency-ordered. A task is complete only when every stated gate has recorded
evidence; one successful command is not completion.

### Now

| # | Workstream | Status | Completion gate |
|---:|---|---|---|
| 1 | Certificate renewal | **Time-boxed.** Expiry 2026-11-06; timer first attempts renewal ~2026-10-07 | Observe a real renewal installing a new certificate *and* reloading nginx. The reload defect is fixed and unit-tested but never exercised live. Also prove issuance-failure rollback. |
| 2 | Product-server qualification | Blocked on operator access | Run `scripts/external-sdk-matrix` from the product bare-metal server with its own installed key; all stages pass |
| 3 | Restricted firewall mode | Blocked on operator input | Collect the product server's stable public IPv4 `/32` and any IPv6 `/128`, move from `public` to `restricted`, prove allowed source succeeds, denied source fails, 80 stays closed, removal and rollback work |
| 4 | Host runtime admission control | Patch committed at `bc24873`, live-unproved | Build and install the patched orchestrator, pass preflight, bind its schema-2 manifest, and prove refusal, release, and crash cleanup before mutation rather than only at the API |
| 5 | 24 GiB hugepage migration | Apply/reapply proved; reboot and rollback open | Exact 12,288-page state survives reboot; authenticated remove restores captured prior state; second remove and post-removal reboot pass |

### Next

| # | Workstream | Status | Completion gate |
|---:|---|---|---|
| 6 | Fresh-host automation | Partial; end-to-end replay unproved | One reviewed flow owns storage, containerd, Docker, firewall, ingress, control plane, templates, services, verification, idempotent reapply, and bounded removal on a fresh supported host |
| 7 | Orchestrator installer defect | Known, deferred | `install-orchestrator-service.sh` passes `require_exact_file` its arguments reversed, exactly as the ingress installer did, so it validates the release tree instead of the installed file. Latent because its staged modes match, but it never checks installed ownership |
| 8 | Destructive backup/restore | Offline-qualified; live-unproved | Create authenticated off-host-capable artifacts, destroy disposable state, restore on a compatible clean release, pass SDK and snapshot checks, reject corruption and incompatibility |
| 9 | Security hardening | Planned/partial | Harden SSH and IPv6; prove secret permissions and rotation, rate limits, audit logs without credential leakage, sandbox egress policy, dependency scans, adversarial isolation tests |
| 10 | Sustained load | Unmeasured | Hold a full fleet under real work for hours; measure vCPU contention at 12 sandboxes on 8 threads; decide whether to raise the pool toward the 32 GiB policy ceiling |

### Later

| # | Workstream | Status | Completion gate |
|---:|---|---|---|
| 11 | Clean OS replay matrix | Not started | Reinstall clean Ubuntu 26.04 and complete install/reapply/reboot/restore/removal acceptance; separately qualify Ubuntu 25.04 for development only; confirm Ubuntu 24.04 rejection |
| 12 | Second template release | Not started | Publish a v2 alongside v1, move `stable` only after the same acceptance gate, and prove rollback and retirement |
| 13 | Final release gate | Not started | Combined unit/integration/live SDK suites, security and secret scans, docs and link validation, reproducibility checks, clean-worktree verification, one identified release revision |

### Standing exceptions

- Public TCP 443 is open to every Internet source by operator choice, pending
  task 3.
- `local-dev-team` and `system` remain at base-tier concurrency (20 sandboxes,
  8 vCPU) by operator decision. Neither key leaves the host. Tighten with
  `converge-admission-policy.sh --apply` if that changes.
- The ingress firewall runs under `KITDEV_UNMANAGED_CONTROL_PLANE_FIREWALL=acknowledged`
  because this host's control-plane rules were assembled by hand. It gives up
  only the managed-ownership proof; every substantive check still fails closed.
  **It is not a production posture.**

## Verification

Python `>=3.13,<3.15`. The suite needs `pyyaml` and `pytest`; without them three
modules fail to import and the run is **not** a clean result.

```console
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_*.py'
uvx --from ruff==0.12.8 ruff check src tests
bash -n <changed shell scripts>
git diff --check
```

The last full run was 399 tests with 2 expected platform skips and no failures.
Ruff reports pre-existing findings across the codebase that predate this work;
do not silently weaken a gate or describe an environment failure as a pass.

After any ingress, DNS, certificate, or limit change, re-run the external
matrix — it is the only evidence that counts for the public path:

```console
cd scripts/external-sdk-matrix && npm ci --ignore-scripts --no-audit --no-fund
E2B_API_URL=... E2B_DOMAIN=... E2B_API_KEY_FILE=... node matrix.ts
KITDEV_FLEET=12 node concurrency.ts
```

Live runs need the correct lifecycle locks, root-owned credentials, idle
Firecracker and build state, and explicit cleanup. Never run a mutating runner
concurrently with install, migration, backup, restore, key lifecycle, admission
convergence, or another qualification.

## Working on the live host

1. Read-only audit first: locks, Firecracker, hugepages, disk, containers,
   health, listeners, firewall, secret-file invariants.
2. Confirm the worktree is clean and pushed.
3. Archive that **exact commit**, stage it to a root-only directory on the
   host, and run from there. Never run uncommitted bytes against the host.
4. Never hand-edit an installed file under `/opt`. Use
   `install-ingress.sh update` or write the tool.
5. Clean up staged release trees afterwards.

Without explicit approval, never reboot, change SSH configuration, disable the
firewall, remove packages, stop unrelated services, delete Docker resources, or
run broad `rm -rf` outside verified project paths.

## Recovery and rollback

- Full reset for this hand-assembled lab is an OVH reinstall. Do it only once
  automation and off-host recovery material are ready, then replay from a
  reviewed immutable commit.
- Team limits: re-run `set-team-limits.sh` with the values in
  `/var/lib/kitdev-sandboxes/team-limits/<slug>.prior`.
- DNS: the prior record set is at
  `/var/lib/kitdev-sandboxes/ingress-dns-rollback.json`.
- Ingress: `install-ingress.sh remove` stops the listener and removes its exact
  rules while deliberately retaining ACME account, certificate, key, and
  operator configuration. Run it with the same acknowledgement environment.
  `kitdev firewall mode closed` removes public 443 on its own.
- Capacity migration retains authenticated exact-state evidence in a root-only
  controller tree below `/var/tmp`. `remove-check` passed; actual remove did
  not. Use the committed controller; never edit sysctl state manually.
- Control-plane `down` preserves persistent state, refuses active Firecracker,
  and attempts to restore the prior service set after a failure.
- Offline backup excludes secrets; restore needs a compatible release and
  separately protected secrets, and leaves services stopped.
- Never use `git reset --hard`, delete unknown rollback trees, or revert another
  agent's dirty files to obtain a clean status. Identify ownership first.

## Hard-won lessons

These each cost real debugging time. They are here so the next lead does not
rediscover them.

- **Unit tests that assert strings appear in a script prove nothing about
  execution.** Six ingress defects passed unit tests and only surfaced on first
  real run: a lego 4.x command line against a pinned lego 5.3.1, reversed
  `require_exact_file` arguments, no update path for a changed asset, a
  production-only unit refusing a development acknowledgement, missing nginx
  capabilities, and backslash-escaped Go templates.
- **The worst defect was silent.** A malformed `docker inspect` template sat
  behind `|| true`, so the post-renewal nginx reload never fired. It would have
  surfaced months later as an expired certificate with no error anywhere.
- **Changing a limit can falsify an existing assertion.** Raising concurrency to
  12 turned the matrix's concurrency-refusal check into a lie. It had to be
  replaced, not left green.
- **A loopback pass is not an external pass.** DNS resolution is not HTTPS, and
  a healthy container is not a reachable service.
- **Verify subagent reports.** They have been right on substance and wrong on
  detail here, and a report written before a change lands can assert something
  no longer true.
- The legacy orchestrator runs as root — accepted for a disposable lab, never a
  production approval.
- `/var/lib/containerd` can still fill the root filesystem; relocation is
  unimplemented. Monitor both filesystems.
- `sudo ./kitdev install` is not a fresh-host installer. Production mode and the
  full profile deliberately refuse.
- Never open public 80, Docker publications, orchestrator ports, or datastore
  ports to make a test pass.

## Clean-resume checklist

1. Read `PROMPT.md`, this file, `AGENTS.md`, both end-user guides, and the
   latest dated research before changing code or the server.
2. Run `git status --short`, `git rev-parse HEAD origin/master`, `git remote -v`.
   Resolve ownership of every dirty path.
3. Confirm the checked-out revision is committed and pushed.
4. Use the private SSH alias. Verify current host state read-only first, and
   record sanitized deviations from the state table above under
   `docs/research/`.
5. Confirm both lifecycle locks are free and no build, sandbox, Firecracker
   process, backup, restore, migration, or key mutation is active.
6. Pick the first unblocked backlog item, state its completion gate, implement
   and test it, collect live evidence, update docs, then push one coherent
   checkpoint.
7. Before claiming completion, prove cleanup, secret non-disclosure, service
   recovery, idempotent reapply, rollback, and external behavior from the
   correct network boundary.

## Authoritative documents

- [`../PROMPT.md`](../PROMPT.md): original product contract.
- [`../AGENTS.md`](../AGENTS.md): how agents must work in this repository.
- [`fresh-server-installation.md`](fresh-server-installation.md): stage-by-stage
  fresh-host runbook, and an explicit list of what is still manual.
- [`bare-metal-operator-guide.md`](bare-metal-operator-guide.md): operator
  runbook and currently implemented commands.
- [`typescript-sdk-integration-guide.md`](typescript-sdk-integration-guide.md):
  official SDK integration and feature boundaries.
- [`research/e2b-typescript-sdk-api-surface.md`](research/e2b-typescript-sdk-api-surface.md):
  exact pinned client API and its traps.
- [`architecture.md`](architecture.md): architecture and trust boundaries.
- [`operations.md`](operations.md): implemented lifecycle operations.
- [`disaster-recovery.md`](disaster-recovery.md): backup/restore contract and
  qualification gap.
- [`firewall-source-allowlist-guide.md`](firewall-source-allowlist-guide.md):
  exact source-manifest firewall procedure.
- [`browser-sandbox-guide.md`](browser-sandbox-guide.md): browser template
  workflow and limitations.
- [`research/activity-log.md`](research/activity-log.md): append-only work
  record.
- [`research/README.md`](research/README.md): index of all dated evidence.
- [`../versions.lock.yaml`](../versions.lock.yaml): reviewed dependency pins.

If two documents conflict, prefer the newer dated evidence and the current
code, then correct the stale document in the same reviewed change. Never
resolve a conflict by widening the supported or production-qualified surface.

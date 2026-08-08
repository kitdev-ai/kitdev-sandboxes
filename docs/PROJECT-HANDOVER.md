# Project handover

Checkpoint date: 2026-08-08
Checkpoint revision before this document: `3e20d7a`

**External SDK operation is qualified.** A client on a separate host drives
this deployment through the official `e2b@2.38.0` SDK over trusted public
HTTPS: 42 of 42 checks across all 10 stages. Only TCP 443 is reachable from
the Internet. See
[external HTTPS enablement](research/external-https-enablement-2026-08-08.md)
and [capacity qualification](research/host-capacity-qualification-2026-08-08.md).

This is the clean-resume document for a new project lead or implementation
agent. It separates committed implementation, last recorded live evidence,
and work that remains unqualified. Do not infer production readiness from a
successful development-lab test.

## Mission and nonnegotiable requirements

The end goal is a reusable, single-bare-metal E2B-compatible sandbox platform.
Software on another server must be able to use the official TypeScript E2B SDK
to create, reconnect to, inspect, operate, pause, snapshot, and destroy
sandboxes, including command, PTY, file, streaming, and wildcard HTTP traffic.

The project must satisfy all of these requirements:

- every manual server change must become reviewed repository automation that
  can reproduce the system on a newly installed server;
- document server work, design decisions, evidence, rollback, and limitations;
- put research output below `docs/research/`;
- pin dependencies and test the official SDK, currently `e2b@2.38.0` on Node
  `22.18.0`;
- commit and push coherent checkpoints;
- eventually reinstall the bare metal host and qualify a replay using only the
  reviewed automation;
- support Ubuntu 26.04 LTS for production qualification and Ubuntu 25.04 only
  for explicit development or migration work;
- reject Ubuntu 24.04. It is not a target and must never be described as one;
- do not expose databases, Redis, Loki, orchestrator ports, admin credentials,
  or Docker-published loopback services to the Internet;
- external end-to-end SDK support is proved from a development client over
  trusted HTTPS; do not extend that claim to the product server until the same
  matrix runs there.

## The two end users

### Bare-metal operator

This person installs, secures, qualifies, runs, backs up, and recovers the
whole host. Their primary runbook is
[`bare-metal-operator-guide.md`](bare-metal-operator-guide.md). They own:

- supported-OS installation and host prerequisites;
- storage, KVM, hugepages, Docker, firewall, ingress, systemd, and services;
- DNS-01 certificate credentials and renewal;
- project API-key issuance, rotation, and revocation;
- capacity and runtime-admission policy;
- backup, restore, upgrade, rollback, and clean-host acceptance evidence.

### TypeScript product integration agent

This AI coding agent works on a different server and receives only a public
API URL, sandbox domain, product-scoped project key, and published template
alias or ID. Its primary guide is
[`typescript-sdk-integration-guide.md`](typescript-sdk-integration-guide.md).
It must pin `e2b@2.38.0`, keep the key out of Git/logs/arguments, always clean
up sandboxes, and never receive the control-plane admin token or DNS secrets.

The intended external environment is:

```dotenv
E2B_API_URL=https://api.sandbox.kitdev.ai
E2B_DOMAIN=sandbox.kitdev.ai
E2B_API_KEY=<runtime secret>
E2B_VALIDATE_API_KEY=true
E2B_TEMPLATE=kitdev-coding:stable
```

Published templates are `kitdev-coding:stable` / `:v1` and
`kitdev-browser-heavy:stable` / `:v1`.

Do not set `E2B_SANDBOX_URL` externally and do not enable `E2B_DEBUG`.

## Supported host and sanitized OVH reference

The development system is a dedicated OVH bare-metal server running Ubuntu
Server 26.04 directly, not Proxmox and not a nested VM. Sanitized hardware:

| Area | Reference host |
| --- | --- |
| CPU | Intel Core i7-7700K, 4 cores / 8 threads, 4.2 / 4.5 GHz, VT-x |
| RAM | 64 GB, observed 67,193,135,104 bytes |
| System disks | 2 x approximately 450 GB Intel NVMe, healthy software RAID1 |
| Project disk | 1 x approximately 4 TB rotational SATA, about 3.6 TiB usable |
| Architecture | `x86_64` |
| Runtime facilities | systemd, cgroups v2, KVM, TUN/TAP, NBD, HugeTLB |

The endpoint, public address, login user, keys, and full inventory are private
and must not enter committed documentation. Local access configuration is at
`docs/private/ovh-lab-ssh.conf`, mode `0600`, with alias `ovhkitdevlab`, strict
known-host checking, and `~/.ssh/known_hosts`. The ignored full inventory is
`docs/private/ovh-26-04-server-inventory.md`. Verify a host key through the OVH
console or another independent path before trusting a new or changed key.

## Public topology

The selected public names are:

- `api.sandbox.kitdev.ai`: lifecycle API;
- `*.sandbox.kitdev.ai`: wildcard sandbox routing, including
  `<port>-<sandbox-id>.sandbox.kitdev.ai`;
- `sandbox.sandbox.kitdev.ai`: shared host-header routing.

One wildcard `A` record can cover these names. Do not publish `AAAA` until IPv6
is configured and tested end to end. Keep records DNS-only during qualification.
A trusted Let's Encrypt wildcard certificate requires DNS-01; HTTP-01 cannot
issue the wildcard. Desired public policy is source-allowlisted TCP 443 only,
with port 80 closed.

DNS resolves the API and wildcard names to IPv4 `A` records, DNS-only, with no
terminal IPv6 address. A wildcard **CNAME** cannot be used: it also matches
`_acme-challenge`, which sends DNS-01 validation into a zone this project does
not control. A trusted Let's Encrypt wildcard certificate is installed and
renews on a daily timer; TCP 443 is open to all sources by operator choice and
TCP 80 is closed. External SDK operation is **qualified from a development
client**; the same run from the product bare-metal server is still outstanding.

## Architecture, ports, and security boundary

The host runs PostgreSQL, Redis, ClickHouse, Loki, the lifecycle API, the client
proxy, an orchestrator, Firecracker guests, and an optional Nginx TLS ingress.
Fresh Compose binds PostgreSQL `5432`, ClickHouse `8123/9000`, API `3000`, and
client proxy `3002/3003` to loopback. Redis `6379` and Loki `3100` remain on the
private container network.

The orchestrator uses proxy `5007`, gRPC/upload `5008`, hyperloop `5010`, and
sandbox firewall `5016/5017/5018`. The legacy host has public-address listeners
on `22`, `5007`, `5008`, `5010`, `5016`, `5017`, and `5018`, but UFW exposes
only SSH publicly and scopes orchestrator traffic to the Docker bridge or guest
veth/CIDR. Never interpret a wildcard bind as permission to open these ports.

Commit `c64d30b` adds a source-manifest firewall CLI:

```console
sudo ./kitdev firewall source add --cidr <product-public-ipv4>/32
sudo ./kitdev firewall source add --cidr <product-public-ipv6>/128
sudo ./kitdev firewall source list
sudo ./kitdev firewall source remove --cidr <exact-cidr>
```

It owns tagged UFW and `DOCKER-USER` original-destination rules, preserves SSH,
rejects overlaps and `/0`, defaults to maximum IPv4 `/24` and IPv6 `/64`
breadth, and rolls a transaction back on failure. It is now applied live in
`public` mode.

Because this host's control-plane firewall was assembled by hand rather than by
this automation, the ingress firewall runs under an explicit development-only
acknowledgement, `KITDEV_UNMANAGED_CONTROL_PLANE_FIREWALL=acknowledged`. That
gives up only the managed-ownership proof; UFW defaults, IPv6 filtering,
listener scope, Docker publication scope and the sensitive-port source scan all
still fail closed. It is not a production posture.

Nginx/TLS terminates only the API and wildcard routes and proxies to loopback.
The listener is live and healthy, and container-level `nginx -t` passed against
the pinned image. The container needs `CHOWN`, `KILL`, `NET_BIND_SERVICE`,
`SETGID` and `SETUID`: the master binds 443 as root and drops its workers to an
unprivileged user, and removing any of those makes nginx exit at startup rather
than run with fewer privileges.

## Exact last recorded live state

The OVH host is a disposable, manually assembled development lab. It is not the
result of a fresh automation replay. At the last recorded successful heavy
qualification:

- HugeTLB was 24 GiB: 12,288 total and free 2 MiB pages, with reserved and
  surplus both zero;
- normal `MemAvailable` was 37,947,508 KiB;
- Firecracker process count was zero;
- PostgreSQL, Redis, ClickHouse, Loki, API, and client-proxy containers were
  healthy after cleanup;
- the transient root orchestrator service was `kitdev-orchestrator-lab`;
- API/proxy loopback health had returned HTTP 200;
- the heavy qualification staging trees were removed;
- the dedicated heavy team and its root-only rerun key were intentionally
  retained; disposable API-key-lifecycle test keys and metadata were removed;
- Redis had no retained sandbox keys and the qualified sandbox/alias were
  absent after cleanup.

These are historical recorded observations, not a current health assertion.
Recheck before any mutation.

The heavy team's limits are now 12 concurrent sandboxes, 2 concurrent builds,
4 vCPU and 8,192 MiB per sandbox, 16,384 MiB requested free disk, 25,600 MiB
maximum disk, and a 24 hour maximum lifetime, set with the reviewed
`scripts/control-plane/set-team-limits.sh`.

Measured concurrency on the 24 GiB hugepage pool: **12** concurrent 2 GiB
coding sandboxes, or **3** concurrent 8 GiB browser sandboxes. Both are exactly
`pool / per-sandbox RAM`, because sandbox memory comes from the reserved pool
rather than ordinary RAM. The fourth 8 GiB sandbox was refused with a clean
`SandboxError` and the three running were unharmed, so exhausting the pool is
per-request backpressure and not host overcommit. Running 3 heavy sandboxes
does consume the transient allowance the pool reserves for builds and
snapshots, so leave a slot free when a build must succeed.

Commit `bc24873` adds fail-closed host admission controls: required
`KITDEV_MAX_*` values, hard local minima against LaunchDarkly, direct gRPC
resource caps, and an atomic build slot. The selected limits are one live
sandbox, one concurrent start/resume, one build, 2 vCPU, 8,192 MiB RAM,
25,600 MiB disk, and NBD pool 4. Its schema-2 manifest is:

```text
/var/lib/kitdev-sandboxes/data/runtime/orchestrator/build-manifest.json
```

It must be `root:root`, mode `0600`, link count one, and bind the patch hash to
the exact limits. This admission build and its team-limit convergence have
**not** been deployed live. Do not change live database limits until the exact
orchestrator is built, installed, passes preflight, and the lifecycle locks are
held.

The 24 GiB capacity migration applied and reapplied with `changed=0`; its
authenticated `remove-check` passed. Reboot persistence and an actual rollback
remain open. Coding-template and browser-template qualification were ephemeral;
no stable production alias is published.

## Completed, committed evidence

Important pushed checkpoints, oldest to newest:

| Commit | Evidence |
| --- | --- |
| `51a2d18` | Pinned coding-template live qualification |
| `566be9b`, `ace72c4`, `a980656` | Gated heavy browser profile, legacy container fixes, final live qualification |
| `75c6450`, `f6364c0`, `1ed39dd`, `1e09e5e`, `a95f188`, `be75139` | Guarded 24 GiB migration and retained live evidence |
| `9a1a4af`, `5245aed`, `3b2c4df`, `a09fcbd`, `ed813c7` | Secure API-key lifecycle and live qualification fixes/evidence |
| `c64d30b` | Source-restricted SDK ingress firewall implementation and tests |
| `bc24873` | Host runtime admission patch, build/preflight binding, convergence tool, and tests |
| `2e8266e`, `63ad005`, `9f6a67f`, `a4c4338`, `81dc195`, `123867c` | Six ingress defects fixed to reach public HTTPS: lego 5.x CLI, reversed asset verification plus a new `update` mode, the development-only firewall acknowledgement and its unit drop-in, nginx capabilities, and invalid Go templates |
| `88a5206` | Live external SDK qualification, 42 of 42 checks over public HTTPS |
| `9328cb1` | Pinned SDK API surface reference |
| `3e20d7a` | Reviewed team limit tool |

The API-key live gate passed team discovery, exact-slug selection, create,
idempotent create, masked list, verify, exact-confirmation revoke, rejected
post-revoke verification, controlled file deletion, journal/metadata leak scan,
and cleanup. The raw key was never emitted to stdout.

As of this checkpoint, `master` and `origin/master` both pointed to
`bc248737c09d90308babd40217a51bb316a076cb` before the handover commit. Remote:
`git@github.com:kitdev-ai/kitdev-sandboxes.git`. No agent-owned uncommitted
implementation remained; an incomplete, untested template-catalog draft was
discarded rather than handed over as a false checkpoint. Re-run `git status`
and `git rev-parse HEAD origin/master` after resuming because this section is a
point-in-time record.

## Secrets and API-key lifecycle

The intended fresh-install administrator environment is
`/etc/kitdev-sandboxes/control-plane.env`. The legacy lab currently uses
`/etc/kitdev-sandboxes/e2b-lab.env`. Never print, copy into chat, or commit
either file. Secret files must be regular, single-link files with appropriate
ownership and mode `0600` or stricter.

Issue a product-scoped key without printing it:

```console
sudo ./kitdev api-key teams
sudo ./kitdev api-key create --team-slug <slug> --name <product> \
  --output /etc/kitdev-sandboxes/secrets/<product>.key
sudo ./kitdev api-key verify \
  --key-file /etc/kitdev-sandboxes/secrets/<product>.key \
  --metadata-file /etc/kitdev-sandboxes/secrets/<product>.key.metadata.json
sudo ./kitdev api-key list --team-slug <slug>
```

Rotate by issuing and proving a replacement, updating the product secret, then
revoking the exact old key ID:

```console
sudo ./kitdev api-key revoke --team-slug <slug> \
  --key-id <uuid> --confirm-key-id <same-uuid> \
  --metadata-file /etc/kitdev-sandboxes/secrets/<product>.key.metadata.json \
  --delete-key-file
```

The default metadata path is `<output>.metadata.json`. Metadata is masked but
still stored `root:root` mode `0600`. The external product receives only its
project key through a separate secure channel, never the admin token or private
environment. DNS provider tokens, ACME account data, TLS private keys, API
keys, and database secrets need an encrypted backup separate from ordinary
project data.

## Verification commands and environment caveats

Use Python `>=3.13,<3.15`. On the development Mac, the known interpreter is
`/opt/homebrew/opt/python@3.13/bin/python3.13`; the repository `.venv` has not
consistently contained all development dependencies.

Focused API-key regression:

```console
PYTHONPATH=src /opt/homebrew/opt/python@3.13/bin/python3.13 -m unittest \
  tests.unit.test_api_keys tests.unit.test_cli \
  tests.unit.test_control_plane_assets tests.unit.test_control_plane_seed
```

The last focused result was 64 tests with one expected skip. Firewall and
admission focus:

```console
PYTHONPATH=src /opt/homebrew/opt/python@3.13/bin/python3.13 -m unittest \
  tests.unit.test_firewall_sources tests.unit.test_ingress_assets
PYTHONPATH=src /opt/homebrew/opt/python@3.13/bin/python3.13 -m unittest \
  tests.unit.test_host_admission
bash -n scripts/ingress/configure-firewall.sh \
  scripts/control-plane/converge-admission-policy.sh \
  scripts/control-plane/build-orchestrator.sh \
  scripts/control-plane/preflight-orchestrator.sh
```

The firewall work recorded 36 focused passing tests. Admission recorded 36
targeted passes and one expected skip, plus passing patched Go packages:

```console
go test ./pkg/admission ./pkg/server ./pkg/template/server
```

Run the broad local suite only after installing the exact test dependencies:

```console
PYTHONPATH=src /opt/homebrew/opt/python@3.13/bin/python3.13 -m unittest \
  discover -s tests -p 'test_*.py'
uvx --from ruff==0.12.8 ruff check src tests
git diff --check
```

Recent broad runs executed 362-372 tests without an executed-test failure, but
collection had unrelated missing `pytest` and/or `PyYAML` imports depending on
the local interpreter. That is not a clean-suite claim. Strict mypy currently
reports pre-existing errors outside narrow changes. Do not silently weaken a
gate or describe an environment failure as a pass.

Live tests need the correct lifecycle locks, root-owned credentials, idle
Firecracker/build state, enough HugeTLB/NBD/disk capacity, and explicit cleanup.
Never run a mutating lab runner concurrently with install, migration, backup,
restore, key lifecycle, admission convergence, or another SDK qualification.

## User input required

Public HTTPS is deployed, so the earlier blockers are resolved: the provider is
Cloudflare, the ACME account email is on file, and the operator has installed a
scoped `Zone:DNS:Edit` + `Zone:Zone:Read` token at
`/etc/kitdev-sandboxes/ingress/cloudflare-dns-api-token`.

One value is still outstanding and cannot be guessed: the product server's
stable public IPv4 as `/32` and, if used, its IPv6 as `/128`. Until it is
supplied, TCP 443 stays open to every source in `public` mode instead of the
source-restricted policy.

Never request credentials in chat or pass them on a command line. Have the
operator place a real `root:root` `0600` single-link file directly on the
server.

## Ordered backlog and dependencies

The authoritative detailed backlog is [`open-tasks.md`](open-tasks.md). Resume
in this order:

1. Re-audit current host health, locks, disks, HugeTLB, NBD, Firecracker,
   services, listeners, firewall, and secret-file invariants without mutation.
2. Observe a real certificate renewal. The reload defect that would have broken
   it is fixed and unit-tested, but the first live renewal is unobserved. Also
   prove issuance failure rollback.
3. Run the external matrix from the product bare-metal server with its own
   installed key.
4. Complete 24 GiB reboot-persistence and authenticated rollback/reapply gates,
   then decide whether to raise the pool toward the 32 GiB policy ceiling.
5. Build and publish the exact `bc24873` patched orchestrator so admission is
   enforced at the host rather than only by API team limits, and prove
   oversized/second sandbox and build rejection plus release and crash
   recovery.
6. Collect the product server's stable public address and move the firewall
   from `public` to `restricted` source mode.
7. Fix the same reversed `require_exact_file` argument order in
   `install-orchestrator-service.sh`; it is latent there but means that
   installer never checks the installed file's ownership.
8. Finish one-command fresh-host automation, including storage and containerd
   ownership, Docker, firewall, ingress, services, templates, verification,
   idempotent reapply, and bounded removal.
9. Qualify destructive backup/restore, security hardening, clean Ubuntu 26.04
   reinstall/replay, Ubuntu 25.04 development/migration, explicit 24.04
   rejection, and the final release gate.

## Recovery and rollback

- The authoritative full reset for the manually assembled lab is an OVH
  reinstall. Do it only after automation and off-host recovery material are
  ready; then replay from a reviewed immutable commit.
- The capacity migration retains authenticated exact-state evidence from
  `a95f188` in a root-only controller tree below `/var/tmp` (approximately
  62 MiB at recording). `remove-check` passed; actual remove did not. Use the
  committed controller, locks, hashes, and idle checks. Do not edit sysctl state
  manually.
- Firewall source operations are transactional and have exact removal, but are
  not installed live yet. Use only the release-matched installed assets.
- Control-plane `down` preserves persistent state, refuses active Firecracker,
  and attempts to restore the prior service set after a later failure.
- Offline backup/clean-target restore has unit coverage but no destructive live
  rehearsal. It excludes secrets; restore requires a compatible release and
  matching separately protected secrets, and leaves services stopped.
- Certificate/ingress removal intentionally preserves ACME account, certificate,
  and secret material for controlled recovery. Treat all retained material as
  sensitive and verify ownership before reuse.
- Never use `git reset --hard`, delete unknown rollback trees, or revert another
  agent's dirty files to obtain a clean status. Identify ownership first.

## Warnings for the next lead

- The live host is a legacy experiment, while fresh automation assumes its own
  Compose identities, labels, paths, and reserved users. Legacy service UID
  collisions can correctly make the fresh prerequisite role refuse the host.
- The current legacy orchestrator runs as root. This is accepted only for the
  disposable lab and is not a production security approval.
- Project state is on the large data disk, but `/var/lib/containerd` can still
  consume the system/root filesystem. Containerd relocation is unimplemented;
  always monitor both filesystems.
- A loopback SDK pass is not an external pass. The external matrix is the only
  evidence that counts for the public path, and it must be re-run after any
  ingress, DNS, certificate, or limit change.
- Capacity is bounded by the hugepage pool, not by the team limit. Raising a
  limit above the pool is safe but buys nothing; it just moves the refusal from
  the API to the sandbox start.
- Changing team limits can silently invalidate test assertions. Raising
  concurrency to 12 falsified the matrix's concurrency-refusal check, which had
  to be replaced rather than left to pass by accident.
- `sudo ./kitdev install` is not a complete fresh-host installer. Production
  mode and full profile deliberately refuse; storage and Docker preparation
  remain outside the current partial flow.
- Do not expose public 80, direct Docker publications, orchestrator ports, or
  datastore ports to make an SDK test pass.

## Clean-resume checklist

1. Read `PROMPT.md`, this file, `open-tasks.md`, both end-user guides, and the
   latest dated research before changing code or the server.
2. Run `git status --short`, `git log -1 --oneline`, `git rev-parse HEAD
   origin/master`, and `git remote -v`. Resolve ownership of every dirty path;
   do not stage unrelated files.
3. Confirm the checked-out revision is committed and pushed. Use an archive or
   detached exact revision for live staging, not uncommitted local bytes.
4. Use the private SSH alias; never copy endpoint details or secret contents
   into committed logs. Verify current host state read-only first.
5. Inspect lifecycle locks and ensure there is no active build, sandbox,
   Firecracker process, backup, restore, install, migration, or key mutation.
6. Compare live state with the last-recorded section and record sanitized
   deviations under `docs/research/`.
7. Pick the first unblocked backlog item, state its objective completion gate,
   implement and test it, collect live evidence if applicable, update docs,
   then commit and push one coherent checkpoint.
8. Before claiming completion, prove cleanup, secret non-disclosure, service
   recovery, idempotent reapply, rollback, and external behavior from the
   correct network boundary.

## Authoritative documents

- [`../PROMPT.md`](../PROMPT.md): original project contract.
- [`open-tasks.md`](open-tasks.md): dependency-ordered execution backlog.
- [`bare-metal-operator-guide.md`](bare-metal-operator-guide.md): operator
  workflow and currently implemented commands.
- [`typescript-sdk-integration-guide.md`](typescript-sdk-integration-guide.md):
  official SDK integration and feature boundaries.
- [`architecture.md`](architecture.md): architecture and trust boundaries.
- [`operations.md`](operations.md): implemented lifecycle operations.
- [`disaster-recovery.md`](disaster-recovery.md): offline backup/restore
  contract and qualification gap.
- [`browser-sandbox-guide.md`](browser-sandbox-guide.md): browser template
  workflow and limitations.
- [`firewall-source-allowlist-guide.md`](firewall-source-allowlist-guide.md):
  exact source-manifest firewall procedure.
- [`research/activity-log.md`](research/activity-log.md): chronological work
  record.
- [`research/api-key-lifecycle-contract.md`](research/api-key-lifecycle-contract.md):
  key semantics and live evidence.
- [`research/browser-heavy-live-qualification-2026-08-07.md`](research/browser-heavy-live-qualification-2026-08-07.md):
  heavy browser evidence.
- [`research/ovh-legacy-capacity-migration.md`](research/ovh-legacy-capacity-migration.md):
  capacity migration, live state, and rollback ownership.
- [`research/host-runtime-admission-control.md`](research/host-runtime-admission-control.md):
  selected admission theorem and implementation status.
- [`research/external-ingress-readiness-2026-08-07.md`](research/external-ingress-readiness-2026-08-07.md):
  external DNS/TCP/TLS probe.
- [`research/control-plane-replay-slice.md`](research/control-plane-replay-slice.md):
  partial fresh-host replay contract.
- [`research/ovh-live-lab-services-first-run.md`](research/ovh-live-lab-services-first-run.md):
  legacy live-lab service assembly.
- [`research/coding-template-contract.md`](research/coding-template-contract.md):
  coding-template qualification boundary.
- [`../versions.lock.yaml`](../versions.lock.yaml): reviewed dependency pins.

If two documents conflict, prefer the newer dated evidence and current code,
then correct the stale document in the same reviewed change. Never resolve a
conflict by silently widening the supported or production-qualified surface.

# Open project tasks

Updated: 2026-08-08

This is the execution backlog for a reusable single-host E2B-compatible
platform. A task is complete only when every stated gate has recorded evidence;
partial implementation or one successful command is not completion.

## Now

| # | Workstream | Status | Objective completion gate | Dependencies / evidence |
|---:|---|---|---|---|
| 1 | API-key lifecycle CLI | Complete on current development lab | `create`, idempotent create, `teams`, `list`, `verify`, exact-ID `revoke`, rejected post-revoke auth, recoverable local deletion, secret-leak scan, focused tests, committed and pushed | No dependency. [Contract and live evidence](research/api-key-lifecycle-contract.md). Fresh-replay coverage is also required by task 12. |
| 2 | Managed ingress source firewall | In progress; live-unproved | `add/list/remove --cidr` handles exact IPv4 and IPv6 sources transactionally; owns only tagged UFW and Docker guard rules; 443 succeeds from an allowed external source, fails from a denied source, and port 80 stays closed; idempotency/removal/rollback pass live | Depends on existing SSH access and ingress listener controls. Blocks 6 and 7. |
| 3 | 24 GiB hugepage migration | In progress; apply/reapply proved, reboot/rollback open | Exact 12,288-page state survives reboot; apply is idempotent; authenticated remove restores the captured prior state; second remove and post-removal reboot pass; services and locks remain healthy | [Migration evidence](research/ovh-legacy-capacity-migration.md). Blocks 5, 9, and 12. |
| 4 | Heavy browser qualification | Complete on current development lab | One 8 GiB RAM / 2 vCPU / 16 GiB requested-free-disk build and sandbox passes Chromium, SDK files, build snapshot/finalize, runtime, kill, alias cleanup, API/Redis/Firecracker cleanup, and host capacity checks | Depends on current 24 GiB pool and dedicated team. [Live evidence](research/browser-heavy-live-qualification-2026-08-07.md). Stable publication remains task 8. |
| 5 | Runtime admission control | In progress; design/patch live-unproved | Enforce bounded CPU, RAM, HugeTLB, NBD, disk, concurrent build, and concurrent sandbox budgets before mutation; prove refusal, release, crash cleanup, and no overcommit under load | Depends on 3 and measurements from 4. Blocks 8, 9, and production claims. |
| 6 | DNS-01 wildcard HTTPS ingress | Blocked on the operator-supplied Cloudflare token only | Issue and renew a trusted DNS-01 wildcard certificate; publish HTTPS in the selected firewall mode; prove API and wildcard names, renewal/reload, failure rollback, 80 closed, and no public internal ports | Assets staged and DNS resolves; the token file is empty. Blocks 7 and 9. [Current gate](research/external-https-enablement-2026-08-08.md). |

## Next

| # | Workstream | Status | Objective completion gate | Dependencies / evidence |
|---:|---|---|---|---|
| 7 | External official SDK matrix | Runner implemented; blocked on public ingress | From a client host, pinned `e2b@2.38.0` passes auth, create/list/connect/info/metrics/timeout/kill, commands, PTY, files/watch, pause/resume, snapshots, guest HTTP, WebSocket/streaming, concurrency refusal, and cleanup | Depends on 6. Runner: `scripts/external-sdk-matrix`. [SDK guide](typescript-sdk-integration-guide.md). |
| 8 | Stable coding and heavy-browser templates | Complete on current development lab | Publish versioned, documented aliases; prove immutable inputs, resource contracts, boot, SDK use, rollback/retirement, and no test alias leakage | `kitdev-coding:v1`/`:stable` and `kitdev-browser-heavy:v1`/`:stable` published and consumer-verified. [Contract](research/stable-template-publication-contract.md). Rollback and retirement of a *second* release remain unexercised. |
| 9 | Fresh-host automation | Partial; end-to-end replay unproved | One reviewed command flow owns storage, containerd, Docker, firewall, ingress, control plane, templates, services, verification, idempotent reapply, and bounded removal on a fresh supported host | Depends on 2, 3, 5, 6, and 8. [Replay design](research/control-plane-replay-slice.md). |
| 10 | Destructive backup/restore and CLI | Offline-qualified; live-unproved | Top-level backup/restore commands create authenticated off-host-capable artifacts, destroy disposable state, restore it on a compatible clean release, pass SDK/snapshot checks, reject corruption/incompatibility, and document secret recovery | Depends on 9 and stable template contracts. [Backup contract](disaster-recovery.md). |
| 11 | Security hardening | Planned/partial | Harden SSH and IPv6; prove secret permissions/rotation, request rate limits, audit logs without credential leakage, sandbox egress policy, no public internal listeners, dependency/security scans, and adversarial isolation tests | Depends on 2, 6, and 9; blocks production approval and 13. |

## Later

| # | Workstream | Status | Objective completion gate | Dependencies / evidence |
|---:|---|---|---|---|
| 12 | Clean OS replay matrix | Not started | Reinstall clean Ubuntu 26.04 and complete install/reapply/reboot/restore/removal acceptance; separately qualify Ubuntu 25.04 for development/migration only; confirm Ubuntu 24.04 rejection | Depends on 3, 9, 10, and 11. [Supported host policy](bare-metal-operator-guide.md). |
| 13 | Final release gate | Not started | Run combined unit/integration/live SDK suites, security and secret scans, docs/link validation, reproducibility checks, clean-worktree verification, and push one identified release revision with all evidence links current | Depends on every task above. |

# External HTTPS enablement gate

Date: 2026-08-08

Objective: reach the minimum reviewed state in which software on a separate
server can use the official `e2b@2.38.0` TypeScript SDK against this host over
trusted public HTTPS. This document records the pre-mutation read-only audit,
the selected operator decisions, and the ordered gates with their evidence.

## Pre-mutation read-only audit

The audit ran before any mutation from the committed revision
`09ce17182767d7ffd2c8c221df677663c8bd519e`, with a clean worktree and
`master` equal to `origin/master`. No project state was changed.

| Observation | Result |
|---|---|
| Host release | Ubuntu 26.04 LTS, kernel 7.0.0-28-generic |
| Lifecycle lock | present and free |
| SDK end-to-end lock | present and free |
| Firecracker processes | 0 |
| HugeTLB | 12,288 total and 12,288 free 2 MiB pages; 0 reserved; 0 surplus |
| Ordinary `MemAvailable` | about 36.2 GiB |
| Project filesystem headroom | about 364 GiB free |
| Control-plane containers | PostgreSQL, Redis, ClickHouse, Loki, API, client proxy all up |
| Loopback API health | HTTP 200 |
| Loopback client-proxy health | HTTP 200 on the health port |
| Orchestrator | transient `kitdev-orchestrator-lab` active |
| Public TCP listeners | SSH only; orchestrator ports bound wide but UFW-scoped to bridge/veth sources |
| UFW ingress rules for 80/443 | none |
| Ingress certificate and key | absent |
| Ingress systemd service | absent from the unit list |

The Ubuntu 25.04 statement in the stable template publication contract was a
factual error. The live publication host is Ubuntu 26.04 LTS; that document is
corrected in the same change set as this one.

### Installed ingress state

`ingress.env`, `acme-provider.env`, `nginx.conf`, the pinned `lego` binary, and
the ingress Compose project are installed. All operator files are `root:root`,
mode `0600`, link count one. `ingress.env` selects the project domain, the
operator ACME email, provider `cloudflare`, and the Let's Encrypt **production**
directory. `acme-provider.env` contains only the token-file pointer.

`/etc/kitdev-sandboxes/ingress/cloudflare-dns-api-token` exists with size 0.
This is the single remaining blocker; only the operator can supply it.

### Published templates

Both publication journals are `root:root`, mode `0600`, single-link, and in
state `published`:

| Product | Alias | Version | State |
|---|---|---|---|
| Coding | `kitdev-coding` | `v1` | published |
| Browser heavy | `kitdev-browser-heavy` | `v1` | published |

### Credential and limit inventory

Four API keys exist across three teams. Only the dedicated external product key
on `kitdev-browser-heavy-team` is intended for off-host use; the other three are
internal qualification credentials. No raw key value was read.

Effective per-team limits:

| Team | Sandboxes | Builds | vCPU | RAM MiB | Max lifetime |
|---|---:|---:|---:|---:|---:|
| `kitdev-browser-heavy-team` | 1 | 1 | 2 | 8,192 | 1 h |
| `local-dev-team` | 20 | 20 | 8 | 8,192 | 1 h |
| `system` | 20 | 20 | 8 | 8,192 | 1 h |

The product team already carries an explicit `project_limits` override equal to
the admission contract. The two internal teams still resolve to base-tier
defaults.

## Recorded operator decisions

1. **Public exposure.** Temporary unrestricted public TCP 443 is selected in
   place of the source-restricted allowlist, because the product server's
   stable public address has not been supplied. TCP 80 stays closed.
2. **Internal team limits.** The operator chose to leave `local-dev-team` and
   `system` at base-tier defaults rather than converging every team to the hard
   caps. **Accepted risk:** those two credentials are internal-only, but once
   443 is open any holder of one of them could request up to 20 concurrent
   sandboxes and overcommit the host, because host-level admission control is
   not deployed on this topology. The compensating controls are that neither
   key leaves the host, the product key is separately capped at one sandbox,
   and `converge-admission-policy.sh --apply` can tighten every team in one
   reviewed step if that assumption changes.
3. **Qualification vantage.** The official SDK matrix runs first from the
   development Mac over the public Internet, which is a genuine off-host
   vantage. Qualification on the actual product bare-metal server remains a
   separate, later gate and must not be claimed from the Mac result.

## Ordered gates

| # | Gate | Objective completion evidence | Status |
|---:|---|---|---|
| 1 | Cloudflare DNS-01 token installed | Root-owned mode-0600 single-link file with nonzero size; value never printed | Blocked on operator |
| 2 | Let's Encrypt staging issuance | `issue-staging` returns pass using the staging directory, proving DNS-01 automation without consuming production rate limit | Not started |
| 3 | Production wildcard issuance | `issue` installs `wildcard.<domain>.crt` mode 0644 and `.key` mode 0600 under a root-only directory, SAN contains the wildcard, key matches certificate | Not started |
| 4 | Ingress apply and verify | Nginx ingress container running from the pinned image, renewal timer enabled, `verify` passes, TCP 80 closed | Not started |
| 5 | Public HTTPS mode | `firewall mode public` opens only 443 for IPv4 and IPv6; 80 refused; no datastore, orchestrator, or Docker-published port reachable externally | Not started |
| 6 | External SDK matrix | Pinned `e2b@2.38.0` on Node 22.18.0 from an off-host client passes auth, lifecycle, commands, PTY, files/watch, pause/resume, snapshots, guest HTTP, streaming, and cleanup | Not started |
| 7 | Product-server qualification | The same matrix passes from the product bare-metal server with its own installed key | Not started |

## Boundaries that do not change in this gate

- Host-level runtime admission control (commit `bc24873`) is **not** deployed.
  The live orchestrator is the transient lab topology and its schema-2 build
  manifest is absent. Enforcement during external use is the API-level team
  limit only.
- One 8 GiB browser sandbox is qualified; two concurrent heavy sandboxes are
  not.
- Fresh-host automation replay, destructive backup/restore rehearsal, reboot
  persistence of the capacity migration, and the clean-OS matrix remain open.
- Nothing in this gate authorizes a production readiness claim.

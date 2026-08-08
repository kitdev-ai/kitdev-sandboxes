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
| 1 | Cloudflare DNS-01 token installed | Root-owned mode-0600 single-link file with nonzero size; value never printed | **Pass** |
| 2 | Let's Encrypt staging issuance | `issue-staging` returns pass using the staging directory, proving DNS-01 automation without consuming production rate limit | **Pass** |
| 3 | Production wildcard issuance | `issue` installs `wildcard.<domain>.crt` mode 0644 and `.key` mode 0600 under a root-only directory, SAN contains the wildcard, key matches certificate | **Pass** |
| 4 | Ingress apply and verify | Nginx ingress container running from the pinned image, renewal timer enabled, `verify` passes, TCP 80 closed | **Pass** |
| 5 | Public HTTPS mode | `firewall mode public` opens only 443 for IPv4 and IPv6; 80 refused; no datastore, orchestrator, or Docker-published port reachable externally | **Pass** |
| 6 | External SDK matrix | Pinned `e2b@2.38.0` on Node 22.18.0 from an off-host client passes auth, lifecycle, commands, PTY, files/watch, pause/resume, snapshots, guest HTTP, streaming, and cleanup | **Pass** |
| 7 | Product-server qualification | The same matrix passes from the product bare-metal server with its own installed key | Not started |

## Execution record

The operator installed the scoped Cloudflare token. Its verification returned
an active token and resolved exactly one zone, without the value being read or
printed. Five defects then had to be fixed before any gate could pass; each is
committed separately with its own reproduction.

| Defect | Effect | Commit |
|---|---|---|
| `run_lego.py` built a lego 4.x command line against the pinned lego 5.3.1 | Issuance failed immediately; renewal used a removed subcommand and flag | `2e8266e` |
| `verify_assets`/`remove_exact_file` passed `require_exact_file` reversed arguments | Verified the release tree's mode instead of the installed file's; blocked apply, verify and remove on every host | `63ad005` |
| No reviewed way to update a changed installed asset | `publish_exact_file` is create-only, so a code change could only be delivered by hand-editing `/opt` | `63ad005` |
| Ingress unit hardcodes production lifecycle | The service refused the development-only firewall acknowledgement and never started | `a4c4338` |
| Container dropped all capabilities | nginx died on `chown` of its tmpfs temp paths and never became healthy | `81dc195` |
| `docker inspect` Go templates contained backslash-escaped quotes inside single-quoted shell strings | Aborted apply, and silently disabled the post-renewal nginx reload because that call is guarded by `\|\| true` | `123867c` |

The renewal-reload defect is the most consequential: a renewed certificate
would have been written to disk and never loaded, surfacing months later as an
expired certificate being served.

### DNS correction

The zone contained exactly one relevant record: a wildcard **CNAME** for
`*.sandbox.kitdev.ai` pointing at the host's OVH reverse name. That wildcard
also matched `_acme-challenge.sandbox.kitdev.ai`, so lego followed the CNAME
and tried to write the ACME TXT record into a zone the project does not
control. With the operator's explicit approval the wildcard CNAME was replaced
by wildcard and explicit `A` records, DNS-only, matching the documented design.
The exact prior record set is retained for rollback in a root-owned mode-0600
file at `/var/lib/kitdev-sandboxes/ingress-dns-rollback.json`.

A stale CNAME cached by the host's own `systemd-resolved` caused one further
failure after the records were correct; `resolvectl flush-caches` resolved it.

### Certificate and ingress

Staging issuance passed first, then production issuance installed a Let's
Encrypt wildcard certificate with SAN `DNS:*.sandbox.kitdev.ai`, valid from
2026-08-08 to 2026-11-06, mode 0644 with its mode-0600 key under a root-only
directory. The container-level `nginx -t` that previous qualification could not
run passed against the pinned image. The ingress service and daily renewal
timer are enabled and active, and the container reports healthy.

### External verification

From the development Mac over the public Internet:

| Check | Result |
|---|---|
| `https://api.sandbox.kitdev.ai/health` | HTTP 200, certificate verified (`ssl_verify_result=0`) |
| TCP 443 | open |
| TCP 80 | refused |
| TCP 3000, 3002, 3003, 3100, 5432, 6379, 8123, 9000 | refused |
| TCP 5007, 5008, 5010, 5016, 5017, 5018 | refused |

The official `e2b@2.38.0` matrix on Node 22.18.0 then passed **42 of 42 checks
across all 10 stages** with zero failures: authentication, invalid-key refusal,
lifecycle with metrics and host derivation, refusal of a second concurrent
sandbox, commands including non-zero exit semantics and streaming and
stdin/EOF and disconnect/reconnect, PTY create/resize/input/kill, files
including a 1 MiB binary round trip and recursive watch streaming, wildcard
guest HTTP, unbuffered chunked streaming, a WebSocket upgrade, pause with and
without memory, snapshot create/list/restore/delete, the 8 GiB browser profile
with Chromium/CDP/Playwright/screenshot, and a final assertion that no sandbox
from the run remained.

One matrix failure in the first run was a defect in the new harness, not the
deployment: the SDK raises `CommandExitError` on a non-zero exit instead of
returning a result. The harness now asserts that contract explicitly.

### Post-run state

Zero Firecracker processes, all 12,288 hugepages free, about 36 GiB
`MemAvailable`, all seven containers healthy including the new ingress, both
lifecycle locks free, and the renewal timer scheduled. Six superseded staged
release trees were removed; the local copy of the product key taken for the
matrix run was deleted after the run.

## Boundaries that this gate does not move

- Host-level runtime admission control (commit `bc24873`) is **not** deployed.
  The live orchestrator is the transient lab topology and its schema-2 build
  manifest is absent. Enforcement during external use is the API-level team
  limit only.
- One 8 GiB browser sandbox is qualified; two concurrent heavy sandboxes are
  not.
- Fresh-host automation replay, destructive backup/restore rehearsal, reboot
  persistence of the capacity migration, and the clean-OS matrix remain open.
- The control-plane firewall on this host is operator-managed, not
  project-managed. Public exposure runs under an explicit development-only
  acknowledgement, which is not a production posture.
- A certificate renewal cycle has not been exercised end to end. The reload
  defect that would have broken it is fixed and unit-tested, but the first real
  renewal is still unobserved.
- Public TCP 443 is open to every Internet source. Only project authentication
  and the ingress rate limits stand in front of the API.
- Nothing in this gate authorizes a production readiness claim.

# OVH ingress source allowlist audit

Date: 2026-08-07

Scope: read-only listener, Docker publication, UFW, SSH, and intended ingress
audit on the dedicated OVH bare-metal host. Evidence is sanitized: public and
private addresses, interface identifiers, container IDs, API keys, and DNS
provider credentials are omitted.

No package, user, disk, kernel, Docker container, firewall rule, listener, or
service was changed during this audit. No ingress source has been applied.

## Live exposure matrix

| Port | Bind/publication | UFW reachability | Purpose | Decision |
|---:|---|---|---|---|
| 22/tcp | IPv4 and IPv6 wildcard | public allow | SSH management | preserve untouched |
| 80/tcp | no listener/publication | default deny | HTTP/ACME HTTP-01 | keep closed |
| 443/tcp | no listener/publication | default deny | future SDK HTTPS | allow listed sources only |
| 3000/tcp | loopback Docker publish | not public | E2B API | proxy through 443 only |
| 3002-3003/tcp | loopback Docker publish | not public | client proxies | proxy through 443 only |
| 5432/tcp | loopback Docker publish | not public | PostgreSQL | keep loopback |
| 8123, 9000/tcp | loopback Docker publish | not public | ClickHouse | keep loopback |
| 6379/tcp | Docker-internal only | not public | Redis | keep internal |
| 3100/tcp | Docker-internal only | not public | Loki | keep internal |
| 5007, 5008/tcp | wildcard listener | exact bridge-source UFW allows | orchestrator bridge RPC | retain existing policy |
| 5010-5012, 5016-5018/tcp | wildcard listener subset | exact sandbox-veth UFW allows | guest services | retain existing policy |
| 6060/tcp | loopback listener | not public | orchestrator HTTP | keep loopback |

UFW is active with logging enabled, default deny for incoming traffic, default
deny for routed traffic, and default allow for outgoing traffic. The existing
project control-plane verifier owns and validates the bridge/veth exceptions.
The ingress verifier calls that verifier instead of treating the deliberately
wildcard orchestrator listeners as public exposure.

The effective SSH daemon configuration listens on port 22 on IPv4 and IPv6,
disables password authentication, and disallows direct password-based root
login. The ingress backend does not reset UFW, alter its defaults, or add/delete
SSH rules. It also refuses any operation unless every effective SSH port has an
existing UFW allow.

Docker currently publishes no port publicly. API, proxy, database, and
ClickHouse publications are explicitly loopback-bound. `DOCKER-USER` contained
no policy rules at audit time, which means a future public Docker publication
could otherwise precede UFW forwarding policy.

## Intended HTTPS routing

| Public host | Destination |
|---|---|
| `api.sandbox.kitdev.ai:443` | `127.0.0.1:3000` |
| `<port>-<sandbox-id>.sandbox.kitdev.ai:443` | `127.0.0.1:3002` |
| `sandbox.sandbox.kitdev.ai:443` | `127.0.0.1:3002` |

Nginx uses host networking only to reach those loopback services. TCP 80 is not
configured in Nginx because wildcard certificate issuance uses DNS-01. Unknown
TLS hostnames reject the handshake.

## Implemented policy

The root-owned source ownership manifest records canonical IPv4 and IPv6
networks plus reviewed exception flags. The policy is:

- reject `/0`, non-canonical networks, duplicates, and overlapping CIDRs;
- accept only globally routable sources by default;
- require `--allow-non-public` for private, loopback, link-local, multicast,
  reserved, or otherwise non-global sources;
- accept IPv4 no broader than `/24` and IPv6 no broader than `/64` by default;
- require the distinct `--allow-broad-range` review flag for broader ranges;
- create one exact UFW TCP 443 allow per source and no TCP 80 allow;
- reject foreign or duplicate UFW rules touching 80/443 rather than adopt them;
- reject public Docker publications for 80/443 or internal service ports;
- add IPv4 and, when available or required, IPv6 `DOCKER-USER` guards.

The Docker guards match conntrack's original destination port. This protects a
host publication even when Docker DNAT translates public 443 to a different
container port. Source matches return to Docker processing; TCP 80 and every
non-allowlisted TCP 443 connection are dropped.

Source changes hold a verified root-only host lock. Rules transition first and
the manifest commits atomically last. Add, delete, or manifest-commit failure
restores the old exact rules; rollback failure has a distinct hard-error code.
`list` verifies manifest/rule equality before returning data. A missing manifest
never adopts pre-existing project-commented rules.

## Remaining activation inputs

Do not add a source until the external product server's stable public IPv4 or
IPv6 CIDR is known. Certificate activation separately still needs the DNS
provider code, least-privilege DNS-01 credentials, and ACME account email.

After those inputs are supplied, run staging certificate issuance, add the
external server CIDR, activate HTTPS ingress, and execute the official SDK gate
from that external server. Until then the live host correctly has no 80/443
listener and no public SDK API.

# E2B external ingress contract

Date: 2026-08-07

Scope: reproducible public HTTPS ingress for the pinned E2B control plane on a
single Ubuntu 26.04 host. Research used the pinned upstream source and official
Nginx, Docker, lego, and Let's Encrypt documentation. No public DNS or server
listener was changed while producing this slice.

## Selected topology

```text
*.sandbox.kitdev.ai -> <server IPv4>

E2B_API_URL=https://api.sandbox.kitdev.ai
E2B_DOMAIN=sandbox.kitdev.ai
E2B_SANDBOX_URL=<unset>
```

One `*.sandbox.kitdev.ai` certificate covers all three public host forms:

| Host | Internal destination | Use |
|---|---|---|
| `api.sandbox.kitdev.ai` | `127.0.0.1:3000` | REST control API |
| `<port>-<sandbox-id>.sandbox.kitdev.ai` | `127.0.0.1:3002` | normal SDK envd and arbitrary sandbox ports |
| `sandbox.sandbox.kitdev.ai` | `127.0.0.1:3002` | upstream shared-host routing using `E2b-Sandbox-Id` and `E2b-Sandbox-Port` |

The current official SDK constructs the normal host as
`<port>-<sandbox-id>.<domain>`. The pinned proxy reads the left-most label,
parses its first field as the port and second field as the sandbox ID, then
validates the ID. Header routing is enabled only on localhost, IP hosts, or the
special `sandbox.<domain>` host. The ingress therefore rejects all other Host
values before proxying and allows only TCP ports 1 through 65535 and current
`i` plus 20 lower-case alphanumeric sandbox IDs.

Primary source:

- [Pinned E2B proxy host parser](https://github.com/e2b-dev/infra/blob/882a3b4786755db9e94be3297de6827f9100ce5e/packages/shared/pkg/proxy/host.go)
- [Pinned E2B parser tests](https://github.com/e2b-dev/infra/blob/882a3b4786755db9e94be3297de6827f9100ce5e/packages/shared/pkg/proxy/host_test.go)
- [Pinned TypeScript SDK URL construction](https://github.com/e2b-dev/e2b/blob/7a1fe4528cb29ccea0334adbee4dc86fadb7244d/packages/js-sdk/src/connectionConfig.ts)
- [Pinned upstream API route](https://github.com/e2b-dev/infra/blob/882a3b4786755db9e94be3297de6827f9100ce5e/iac/modules/job-api/jobs/api.hcl)
- [Pinned upstream client-proxy fallback route](https://github.com/e2b-dev/infra/blob/882a3b4786755db9e94be3297de6827f9100ce5e/iac/modules/job-client-proxy/jobs/client-proxy.hcl)

## Ingress decision

Use the official Nginx `1.29.6-alpine3.23` image by its Linux/amd64 manifest
digest. The container uses host networking so its only upstreams remain the
existing loopback API and client proxy. It is read-only, drops all capabilities
except `NET_BIND_SERVICE`, has no Docker socket or writable host mount, and
receives only the root-owned TLS/config mounts.

The configuration supplies:

- exact API, shared-host, and sandbox-host matching plus default HTTP `444` and
  TLS handshake rejection;
- original validated Host and E2B routing-header forwarding;
- HTTP/1.1 upgrade handling, disabled request/response proxy buffering, a
  24-hour sandbox stream timeout, and bounded connect/send timeouts;
- 1 GiB request limits so file operations are useful but bounded;
- separate API and sandbox per-IP request limits and connection limits;
- TLS 1.2/1.3, disabled session tickets, HSTS, `nosniff`, and no-referrer;
- access logs that deliberately omit the URI, query, and request headers so
  authenticated file or stream URLs cannot be copied into logs.

Public UFW mutation is explicit, project-owned, and source restricted. One
commented TCP 443 allow is added for each validated IPv4 or IPv6 source CIDR;
TCP 80 remains closed because wildcard issuance uses DNS-01. Existing SSH and
unrelated rules are preserved. A foreign rule touching 80/443 is a conflict,
not something the installer adopts or deletes. A conntrack original-port guard
in `DOCKER-USER` prevents future Docker publications from bypassing the source
policy after DNAT. The ingress verifier also delegates the intentional
orchestrator bridge/veth listeners to the exact control-plane firewall verifier
and refuses public binds or Docker publications for internal services.

Primary source:

- [Nginx WebSocket proxying](https://nginx.org/en/docs/http/websocket.html)
- [Nginx request-rate limiting](https://nginx.org/en/docs/http/ngx_http_limit_req_module.html)
- [Nginx connection limiting](https://nginx.org/en/docs/http/ngx_http_limit_conn_module.html)
- [Nginx request-body limits](https://nginx.org/en/docs/http/ngx_http_core_module.html#client_max_body_size)
- [Official Nginx image](https://hub.docker.com/_/nginx)

## Certificate lifecycle and current blocker

Let's Encrypt requires DNS-01 for wildcard identifiers. The challenge record
will be `_acme-challenge.sandbox.kitdev.ai`. The pinned lego 5.3.1 binary is
downloaded only over HTTPS, checked against the locked size and SHA-256, and
executes the selected built-in DNS provider. Staging and production state are
separate. A renewed certificate and key are validated for minimum remaining
lifetime, wildcard SAN, and matching public keys before atomic installation;
only then is the exact Compose-owned ingress container reloaded.

Live issuance is intentionally blocked until the operator supplies:

1. The DNS provider name, expressed as its lego provider code.
2. An ACME account email.
3. The provider-specific, least-privilege DNS credential variables needed to
   create and delete TXT records for `_acme-challenge.sandbox.kitdev.ai`.

Public configuration belongs in root-owned mode `0600`
`/etc/kitdev-sandboxes/ingress/ingress.env`. Provider credentials belong in a
separate root-owned mode `0600`
`/etc/kitdev-sandboxes/ingress/acme-provider.env`; they are parsed as data and
are never sourced by a shell. Provider secrets and issued private keys are not
tracked. Run staging issuance before changing the ACME server to production.

Primary source:

- [Let's Encrypt challenge types](https://letsencrypt.org/docs/challenge-types/)
- [Let's Encrypt staging environment](https://letsencrypt.org/docs/staging-environment/)
- [lego DNS providers and credential files](https://go-acme.github.io/lego/dns/)
- [lego wildcard DNS-01 example](https://go-acme.github.io/lego/usage/cli/obtain-a-certificate/)

## Replay and rollback order

```text
install-ingress.sh stage
  -> installs verified lego and pinned Nginx image/config/units, starts nothing

manage-certificate.sh issue-staging
  -> validates DNS automation against Let's Encrypt staging

manage-certificate.sh issue
install-ingress.sh apply
  -> validates certificate, converges exact UFW and Docker guard rules for the
     source manifest (an empty manifest keeps 443 closed), then starts ingress
     and renew timer

install-ingress.sh verify
  -> verifies installed bytes, service/container health, certificate and firewall

install-ingress.sh remove
  -> stops only project ingress, removes only project-commented UFW rules and
     exact Docker guard/program/config/unit files; retains the source ownership
     manifest, ACME state, keys and operator configuration for explicit backup
     or deletion
```

Manage sources with `kitdev firewall source add|list|remove`; see
`docs/firewall-source-allowlist-guide.md`. No source is applied until the
external SDK server's stable public address is supplied.

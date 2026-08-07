# Cloudflare DNS-01 readiness

Date: 2026-08-07

Selected nonsecret configuration:

| Setting | Value |
|---|---|
| ingress domain | `sandbox.kitdev.ai` |
| lego DNS provider | `cloudflare` |
| ACME email | `mohitagrwl97@gmail.com` |
| ACME directory | Let's Encrypt production |
| challenge type | DNS-01 |
| expected TXT owner | `_acme-challenge.sandbox.kitdev.ai` |

The initial live read-only audit found the ingress configuration, provider
credential file, wildcard certificate, and private key absent. No secret was
read or printed.

The only remaining operator secret is a scoped Cloudflare API token with
`Zone:DNS:Edit` and `Zone:Zone:Read` restricted to `kitdev.ai`. Put only the
token in this exact root-owned mode `0600` file:

```text
/etc/kitdev-sandboxes/ingress/cloudflare-dns-api-token
```

Then populate root-owned mode `0600`
`/etc/kitdev-sandboxes/ingress/acme-provider.env` with exactly:

```text
CLOUDFLARE_DNS_API_TOKEN_FILE=/etc/kitdev-sandboxes/ingress/cloudflare-dns-api-token
```

Do not use the Global API Key. The deployment must validate Let's Encrypt
staging issuance before production issuance. Public firewall mode and the
ingress listener remain disabled until a valid installed wildcard certificate
and key are present.

## Live staging result

Ingress release `401a45b` was staged on the OVH host without certificate
issuance, listener startup, or firewall mutation. Two earlier staging attempts
refused safely and produced follow-up fixes: Git executable modes for lifecycle
scripts, explicit lego executable normalization, and lego verification on the
trusted executable `/opt` filesystem rather than the host's `noexec` `/run`.
All fixes were committed and pushed before the successful exact-revision stage.

The successful stage verified the pinned lego binary and Nginx image. Live
nonsecret configuration now contains the selected domain, provider, email, and
Let's Encrypt production directory. The credential pointer and empty token file
are installed with these sanitized measurements:

| File/state | Result |
|---|---|
| `ingress.env` | root:root `0600`, config parser pass |
| `acme-provider.env` | root:root `0600`, pointer only |
| `cloudflare-dns-api-token` | root:root `0600`, empty pending operator input |
| TCP 80/443 listeners | 0 |
| project ingress UFW rules | 0 |
| ingress systemd service | inactive and disabled |

The next action is only for the operator: populate
`/etc/kitdev-sandboxes/ingress/cloudflare-dns-api-token`. After that, switch the
ACME directory to staging for the staging issuance gate, restore production,
issue the production wildcard certificate, apply ingress, and explicitly select
public firewall mode.

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

The live read-only audit found the ingress configuration, provider credential
file, wildcard certificate, and private key absent. No secret was read or
printed.

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

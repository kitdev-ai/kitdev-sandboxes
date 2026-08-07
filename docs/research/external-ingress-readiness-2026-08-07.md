# External ingress readiness check

Date: 2026-08-07

Status: blocked before TLS. The official TypeScript SDK is not externally
testable today.

## Scope

This was a credential-free, read-only check from a client outside the bare
metal server. It tested only public name resolution, TCP reachability, TLS
availability, and the safe unauthenticated health endpoint. No API key was
loaded and no sandbox or template request was attempted.

The checked names were:

```text
api.sandbox.kitdev.ai
49983-i00000000000000000000.sandbox.kitdev.ai
```

The second name is a synthetic, non-existent sandbox identity shaped like the
official SDK's envd hostname. It tests wildcard DNS only; it cannot route to a
real sandbox.

## Results

| Check | Result |
| --- | --- |
| API A/CNAME lookup | `NOERROR`; a CNAME chain terminates in one IPv4 address |
| API AAAA lookup | `NOERROR`; no terminal IPv6 address |
| Wildcard sandbox A/CNAME lookup | `NOERROR`; the synthetic SDK hostname follows the same CNAME-to-IPv4 shape |
| Wildcard sandbox AAAA lookup | `NOERROR`; no terminal IPv6 address |
| TCP 443 | Timed out after five seconds |
| TLS handshake and hostname verification | Not reachable because TCP 443 did not connect |
| `GET https://api.sandbox.kitdev.ai/health` | curl exit 28 after the five-second connect timeout; HTTP status `000` |
| TCP/HTTP 80 redirect path | Timed out after five seconds; HTTP status `000` |

Resolved addresses and CNAME targets are deliberately omitted. This record
retains only the public product hostnames and the result required for the
deployment gate.

## Decision

DNS is present, including wildcard resolution, but public ingress is not
reachable. A TLS certificate cannot be inspected and the API health endpoint
cannot be reached. Therefore an official `e2b@2.38.0` TypeScript SDK test from
another server is not actionable yet; it would fail at the network connection
stage before API-key validation or sandbox creation.

Do not spend an API key on repeated SDK attempts until the operator has:

1. issued and installed the wildcard certificate;
2. applied the reviewed ingress service and its exact TCP 80/443 firewall
   rules;
3. proved the certificate name, chain, and validity from an external client;
4. received HTTP 200 from the public `/health` endpoint; and
5. confirmed that internal control-plane ports remain non-public.

After those gates pass, run the official SDK from a separate server with
`E2B_API_URL=https://api.sandbox.kitdev.ai`,
`E2B_DOMAIN=sandbox.kitdev.ai`, no `E2B_SANDBOX_URL`, and a securely mounted
project API key.

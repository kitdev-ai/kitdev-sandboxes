# Heavy browser profile live qualification

Date: 2026-08-07

Scope: live development-only qualification of the pinned official E2B
TypeScript SDK browser workflow on the OVH bare-metal host. This evidence is
sanitized: it contains no API key, team ID, build ID, template ID, sandbox ID,
public address, or provider credential.

## Executed revision

The first provisioning attempt refused before its database transaction because
the live containers expose no Compose labels and `docker ps --quiet` returned
short IDs. A second pre-transaction attempt showed that the existing host uses
the legacy `postgres` database identity rather than the fresh-install `kitdev`
identity. Neither refusal created a build or changed the database.

Commit `ace72c4` made discovery explicit and deterministic:

- exact `kitdev-postgres` and `kitdev-redis` container names;
- `docker ps --no-trunc --quiet` followed by the existing 64-hex validation;
- a bounded allowlist of the fresh `kitdev/kitdev` and legacy
  `postgres/postgres` database identities, accepted only when the expected
  tables are present.

That commit was pushed, archived from Git, staged on the host, and used for the
qualification. No uncommitted runner was qualified.

## Measured result

The dedicated heavy team was converged to this exact entitlement:

| Resource | Value |
|---|---:|
| vCPU | 2 |
| RAM | 8,192 MiB |
| requested free root disk | 16,384 MiB |
| maximum disk | 25,600 MiB |

The official `e2b@2.38.0` TypeScript SDK gate exited zero and proved:

| Gate | Result |
|---|---|
| build database status | `ready` |
| build database vCPU/RAM/free disk | 2 / 8,192 / 16,384 MiB |
| build database total disk | 20,281 MiB |
| guest available disk after finalize | 16,021 MiB |
| browser identity and loopback CDP readiness | pass |
| Chromium Playwright CDP navigation and DOM | pass |
| SDK screenshot/download artifact collection | pass |
| browser alive immediately before destroy | pass |
| SDK sandbox kill | pass |
| template alias absence after cleanup | pass |

The guest measurement exceeds the 15,000 MiB acceptance floor by 1,021 MiB.
The build, snapshot/finalize, runtime browser, and SDK cleanup path therefore
all passed with the pinned 8 GiB profile.

## Post-run host state

After the runner exited and its cleanup completed:

| Measurement | Value |
|---|---:|
| Firecracker processes | 0 |
| HugePages total | 12,288 |
| HugePages free | 12,288 |
| HugePages reserved/surplus | 0 / 0 |
| normal `MemAvailable` | 37,947,508 KiB |

The authenticated cleanup checks inside the runner also proved the sandbox was
absent from the API, Redis contained no sandbox keys, and no Firecracker process
remained before it reported success.

## Qualification boundary

This result qualifies one concurrent heavy browser sandbox on the current
24 GiB HugeTLB pool and the dedicated team limits. It does not establish safe
capacity for two concurrent 8 GiB builds or sandboxes. Public wildcard HTTPS
routing is a separate ingress gate.

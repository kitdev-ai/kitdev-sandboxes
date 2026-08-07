# API-key lifecycle contract

Date: 2026-08-07

## Scope and sources

This report defines the implemented host-local lifecycle for project API keys.
It was derived from the repository-pinned E2B infra revision
`24a054bca26ec50a6d59031d9360c1582612b3f8`, specifically:

- `spec/openapi.yml` for authentication headers, routes, status codes, and
  response schemas;
- `packages/api/internal/handlers/admin_api_keys.go` for admin create/delete
  behavior;
- `packages/api/internal/team/apikeys.go` for one-time raw-key generation and
  immediate authentication-cache invalidation on deletion;
- the installed Compose contract and `scripts/control-plane/private_env.py`
  for the loopback API and root-owned `ADMIN_TOKEN` source.

No credential value or private host identifier was recorded.

## Observed upstream contract

- `POST /admin/teams/{teamUUID}/api-keys`, authenticated with
  `X-Admin-Token`, accepts `{"name":"..."}` and returns HTTP 201 with the raw
  key once, its UUID, mask, and timestamps.
- `GET /api-keys`, authenticated with `X-Admin-Token` and `X-Team-ID`, returns
  masked key metadata. It never returns prior raw key material.
- `DELETE /admin/teams/{teamUUID}/api-keys/{keyUUID}` returns HTTP 204 for a
  deletion and invalidates the API-key authentication cache immediately.
- `GET /sandboxes` accepts project authentication through `X-API-Key`; a
  bounded list request is therefore a nonmutating credential verification.
- The pinned admin surface has no administrator route that lists all teams.
  Optional team resolution must use local durable state rather than guessing.

## Implemented safety boundary

`kitdev api-key create|list|verify|revoke` runs only as root on supported
Ubuntu 25.04 development/migration or Ubuntu 26.04 hosts. It uses a fixed
`127.0.0.1:3000` direct HTTP transport with a ten-second timeout and 1 MiB
response ceiling; proxy environment variables cannot redirect the admin token.
Mutations share the control-plane lifecycle lock.

The default admin source is the exact root-owned, single-link, mode-`0600`
`/etc/kitdev-sandboxes/control-plane.env`. A raw-token file is accepted only
through the distinct `--admin-token-file` option. Secrets are never accepted as
arguments and no result or error object contains the raw key or admin token.

Create first commits a root-owned mode-`0600` journal. It assigns a unique
remote name, creates the upstream key, and atomically publishes the one-time
raw value into a root-owned, non-group/world-writable directory. The final file
can be service-owned and remains mode `0600`. Metadata stores the UUID and mask,
not the raw value. On rerun, the journal either verifies the completed key,
finishes metadata after a post-publication interruption, or revokes a remote
orphan whose raw value was never durably published before recreating it.

Revoke requires duplicate exact key IDs. With metadata, it records `revoked`
after remote revocation. Optional local deletion occurs only afterward and only
for the exact metadata-bound, ownership- and mode-validated regular file in a
root-owned directory. A rerun can complete deletion if it was interrupted.

`kitdev api-key teams` queries the single Compose Postgres container and lists
only UUID, slug, and name for teams whose blocked and banned flags are both
false. Selection accepts mutually exclusive UUID or exact slug inputs. If both
are omitted, exactly one eligible team is accepted; zero or multiple teams fail
closed. This supports dedicated heavy/browser teams without manual SQL.

## Offline verification

Offline unit coverage proves response validation, authentication errors,
atomic mode-`0600` publication, metadata secret exclusion, idempotent create,
both interrupted-create recovery branches, strict secret-source formats,
ambiguous-team refusal, root-owned parent enforcement, metadata-bound local
deletion, exact slug selection, CLI dispatch, structured errors, and dry-run
nonexecution. The focused API-key, CLI, and control-plane asset suite passed 64
tests with one expected platform skip before live staging. Ruff passed for the
new Python module and tests.

## Live host gate

The first exact `9a1a4af` read-only discovery stopped because Docker emits
12-character IDs. Commit `5245aed` accepted only 12- or 64-hex forms. Discovery
then stopped because the legacy lab containers have no Compose labels. Commit
`3b2c4df` added an exact `kitdev-postgres` legacy-name fallback alongside the
fresh Compose label pair. It reached PostgreSQL but the legacy database does
not use the fresh replay's `kitdev` identity. Commit `a09fcbd` changed the fixed
in-container query to use that container's declared `POSTGRES_USER` and
`POSTGRES_DB` without reading, printing, or passing its password. No key or
database mutation occurred during these three fail-closed attempts.

The exact `a09fcbd` archive then passed the complete development gate on the
OVH bare-metal host:

| Predicate | Result |
|---|---|
| eligible team discovery | pass; three teams, UUID/slug/name only |
| exact slug selection | pass |
| disposable create | `created`; raw value only in root-owned file |
| key and metadata | `root:root`, mode `0600`, link count one |
| identical create rerun | `existing`; no second POST/key |
| list | disposable key present with UUID and mask only |
| verify | `authenticated` through `GET /sandboxes?limit=1` |
| exact-ID revoke | `revoked` |
| verify after revoke | `api_key_authentication_failed`, exit 77 |
| deletion recovery rerun | `already-revoked`; local key deleted |
| final remote list | zero matching disposable key names |
| metadata and recent journal scan | zero raw-key regex matches |

The safe mask contains the literal `e2b_` prefix, so leakage checks use the
complete `e2b_[0-9a-f]{40}` raw-key pattern rather than treating that public
prefix as a secret. Final cleanup removed the disposable metadata, key, and all
four root-only temporary release trees. No credential or live identifier is
retained in this report.

This qualifies the implemented API-key lifecycle on the current development
lab. Fresh-host replay remains a separate whole-system gate.

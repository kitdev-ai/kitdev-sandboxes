# Offline backup/restore contract

Date: 2026-08-07

Status: locally implemented and unit-tested; no OVH mutation and no live
restore claim.

## Decision

The first format is an offline physical backup rather than independent logical
dumps. Admission is quiesced and all writers are stopped before collection.
This gives PostgreSQL, Redis AOF, ClickHouse, Loki, and filesystem template/
snapshot objects one common write-free boundary and preserves their upstream
physical formats. The tradeoff is an exact compatible-release restore gate.

The declared component set is:

| Name | Project-relative path | Purpose |
| --- | --- | --- |
| `postgres` | `data/postgres` | API relational state |
| `redis` | `data/redis` | AOF-backed coordination state |
| `clickhouse` | `data/clickhouse` | analytics/event state |
| `loki` | `data/loki` | project log-store state |
| `template-storage` | `data/runtime/orchestrator/template-storage` | local templates and snapshot-derived objects |

Caches and regenerable release artifacts are excluded. Persistent volumes are
also excluded because the current profile does not implement them; adding the
feature must extend the format explicitly rather than silently missing data.

## Integrity and compatibility

`kitdev-offline-physical-v1` uses one GNU/PAX tar per component and one
canonical JSON manifest. SHA-256 and byte size cover each complete archive.
The manifest also binds the installed Compose file, installed image lock,
root-only private environment, Linux architecture, and pinned E2B
infrastructure commit. Restore rejects a different release or secret set
instead of attempting an implicit database migration.

Source and extracted trees reject symbolic links, special files, cross-device
descent, and nested mounts. Archive validation rejects absolute paths, dot/dot-
dot components, paths outside the declared component, symbolic links, devices,
FIFOs, unknown member types, and hard links outside the same component.

The coordinator checks destination free bytes plus a reserve before writing.
Backup staging is renamed only after all archives and the manifest are durable.
Restore validates every input before checking the clean target a final time and
publishing a journal. A pre-publication interruption removes staging. A
publication interruption retains an immutable journal and resumes by
distinguishing staged components from already-published components.

## Credential boundary

Secrets are deliberately excluded, not weakly copied. The manifest declares
`external-encrypted-backup-or-reissue` and the required private-env path but
contains no values. It does contain a SHA-256 compatibility digest of the
complete high-entropy private environment so restore can reject credentials
that cannot open the physical databases. `/etc/kitdev-sandboxes`, ingress DNS
API tokens, ACME account keys/certificates, and SDK keys need a separate
encrypted operator-controlled system or reissuance runbook. The exact private
environment must be recovered because its datastore credentials bind the
physical state; independent ingress/SDK credentials may be reissued. The
root-only manifest is sensitive metadata even though it is not a usable secret
backup.

## Live mutation plan

The first disposable-host exercise should:

1. Seed unique records in all four datastores and create a template plus SDK
   snapshot.
2. Record the healthy/running service state and zero active sandbox count.
3. Create a backup and prove the same healthy/running state was restored.
4. Copy the backup off-host and revalidate it after a round trip.
5. Stop the platform and restore onto a clean compatible release data layout.
6. Start and verify API, proxy, orchestrator, datastore, template, snapshot,
   official TypeScript SDK, and terminal cleanup behavior.
7. Repeat with forced interruption during backup staging, restore extraction,
   and each component publication step.

Until that exercise passes, `kitdev backup` and `kitdev restore` are not added
to the public Python CLI. The installed low-level coordinator is an honest
implementation and qualification surface, not a production guarantee.

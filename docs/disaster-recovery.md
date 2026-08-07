# Offline backup and restore

This is the first implemented disaster-recovery slice for the minimal,
single-host control plane. It is intentionally an offline physical backup. It
has local unit coverage but has not yet passed a destructive restore rehearsal
on the OVH lab, so it is not yet a production-qualified recovery procedure.

## Protected state

One backup contains the stopped physical state of:

- PostgreSQL;
- Redis AOF data;
- ClickHouse;
- Loki;
- local template storage, including the template objects used for sandbox
  snapshots.

The coordinator first refuses active Firecracker processes. If the control
plane is healthy and running, it stops new API/proxy admission, checks for a
racing sandbox, stops the orchestrator, and gracefully stops Compose. This
creates a clean physical datastore state and a common write-free point. The
prior running/stopped service state is restored after backup success or
failure.

Sandbox VMs, build caches, runtime caches, Docker/containerd data, source
checkouts, installed binaries, host configuration, and persistent-volume data
are not protected by this first format. Persistent volumes are not implemented
in the current minimal profile.

## Secret boundary

No file below `/etc/kitdev-sandboxes` is included. In particular,
`control-plane.env`, DNS API credentials, TLS/ACME account material, ingress
keys, and SDK API keys are excluded. Store the exact control-plane environment
in a separate encrypted, access-controlled operator backup because its database
credentials must match the physical data. Reissue independent DNS, ACME, and
SDK credentials where the recovery design permits. Do not place an unencrypted
secret archive beside the data backup.

Restore requires an already provisioned, root-owned, mode-0600
`/etc/kitdev-sandboxes/control-plane.env` that passes the installed validator.
The root-only data manifest records a SHA-256 compatibility binding for the
complete private environment, never the secret values themselves. Because the
generated secrets have at least 256 bits of entropy, this detects a wrong
secret set without making an unencrypted copy. Treat the manifest as sensitive
metadata and keep it mode 0600.

## Create a backup

Run the installed script as root with the same explicit lifecycle intent as
the deployment:

```console
sudo env KITDEV_LIFECYCLE=development \
  /opt/kitdev-sandboxes/libexec/control-plane/backup-restore.sh backup
```

For production, use `KITDEV_LIFECYCLE=production`. Ubuntu 25.04 remains
development/migration-only; Ubuntu 26.04 is the production target.

The result is a root-only directory under
`/var/lib/kitdev-sandboxes/backups/<backup-id>`. It contains five PAX tar
archives and a canonical JSON manifest. The manifest binds archive sizes and
SHA-256 hashes to the installed Compose definition, image lock, architecture,
backup format, and pinned upstream E2B infrastructure commit. A partial backup
uses a hidden `.partial` directory and is removed on interruption.

This local directory is not disaster recovery by itself. Transfer the complete
directory to encrypted off-host storage, verify it again after transfer, and
apply an independent retention policy. Do not leave the only backup on the
same physical disk as the sandboxes.

## Restore gate

Restore accepts only a backup directly below the fixed backup root. Before any
target directory is removed it verifies:

- root ownership, modes, single links, exact backup entries, and no symlinks;
- every archive size, SHA-256, member count, member type, and confined path;
- exact Compose, image-lock, private-environment hashes, architecture, format,
  and upstream source compatibility;
- valid external control-plane secrets;
- zero Firecracker processes and fully stopped services;
- free space for staged extraction;
- empty, expected target directories with exact owners and modes;
- no links, special files, or nested mount boundaries in extracted state.

This means restore targets a clean installation of the compatible release. It
does not overwrite or merge an existing database or template store. The
current installer does not yet expose a first-boot restore hook, so operators
must not treat the following low-level command as a complete reinstall
workflow.

```console
sudo env KITDEV_LIFECYCLE=development \
  /opt/kitdev-sandboxes/libexec/control-plane/backup-restore.sh restore \
  --backup /var/lib/kitdev-sandboxes/backups/<backup-id>
```

Extraction occurs in a root-only hidden directory on the target data
filesystem. A root-only journal is published only after all validation passes.
Directory publication is restartable: an interruption before publication
removes the staging tree, while an interruption during publication retains the
journal and the same command resumes the exact backup. Every staged and
already-published component is authenticated against a content/metadata tree
digest before restore advances. Restore leaves services stopped. The
subsequent qualified recovery sequence must start the control plane, run health
and SDK tests, and compare database, template, snapshot, and observability
evidence.

## Qualification still required

Before production use, test backup and restore on a clean Ubuntu 26.04 host
with seeded PostgreSQL, Redis, ClickHouse, Loki, template, and snapshot data.
Inject interruptions during archive creation, extraction, and each publication
step. Then verify service health, official TypeScript SDK create/command/file/
pause/snapshot behavior, restored template identity, no unexpected Redis keys,
and zero leaked Firecracker/NBD/network resources after cleanup.

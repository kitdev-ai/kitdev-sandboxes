# OVH disposable-lab legacy capacity migration

Date: 2026-08-07

Status: migration automation implemented and locally tested; committed live
apply remains pending.

## Read-only audit

The lab was inspected over verified SSH before any mutation. Redacted results:

| Predicate | Result |
| --- | --- |
| OS | Ubuntu 26.04 x86_64 development lab |
| TypeScript SDK lock | root-owned mode `0600`, single link, free |
| Lifecycle lock | absent; the legacy stack predates it |
| Firecracker processes | 0 |
| Template-manager/build processes | 0 |
| Database build groups | `ready`: 24, `failed`: 3; no nonterminal group |
| Docker | active and enabled |
| Legacy orchestrator | active transient unit |
| Containers | six expected containers running |
| API/proxy health | HTTP 200 on both loopback health endpoints |
| HugeTLB | 2,048 total, 2,048 free, 0 reserved/surplus, 2 MiB pages |
| Ordinary memory | approximately 56 GiB `MemAvailable` |
| Legacy persistent setting | one root-owned file requesting 2,048 pages |
| Fresh-host manifest | absent |

The normal prerequisite playbook must not run over this state. Its fixed
service identities conflict with the legacy worker/group, while its ownership
manifest and managed kernel filenames are absent. Those guards are correct.

## One-time adoption contract

`scripts/legacy-capacity-migration.sh` is a deliberately separate path. It is
restricted to Ubuntu 26.04 `development` and the exact disposable-lab service,
container, database, lock, file, and 2,048-page inputs.

Before apply it:

1. verifies the pinned `ansible-core==2.21.2` controller and dependency lock;
2. acquires the exact existing SDK lock;
3. proves no Firecracker or template-manager process exists;
4. atomically creates the previously absent root-owned lifecycle lock and
   acquires it;
5. repeats process checks under both locks;
6. requires the legacy orchestrator, Docker, six named containers, API, and
   proxy to be healthy;
7. queries PostgreSQL and refuses any build outside terminal `ready`/`failed`
   groups;
8. proves there is exactly one persistent hugepage assignment and that its
   file is a root-owned, mode-`0644`, single-link regular file with exact
   expected content;
9. derives 12,288 pages from the committed 8 GiB/2-slot/8 GiB headroom model
   and validates the 50% total-RAM and 16 GiB normal-memory guards; and
10. writes root-only immutable prior state before changing the persistent file
    or live sysctl.

The mutation, verification, and manifest-publication sequence is an Ansible
block with explicit failure-injection points after persistent-file publication
and live sysctl application. On an incomplete first-apply failure, rescue
restores the exact prior file and live pool, verifies the rollback, removes any
incomplete manifest, and retains the authenticated prior record for audit and
retry. Discovery opens direct regular files with `O_NOFOLLOW`; a sysctl symlink,
oversized file, replacement during read, or second assignment blocks adoption.

Mutation-free `check` cannot lock a lifecycle file that does not yet exist. It
does hold the SDK lock and performs every role-level idle/database/service
proof, but a manual legacy operation that ignores the SDK lock could race that
preview. Apply closes this boundary by creating and holding the lifecycle lock
before running the same proofs again. Removal refuses if that lock is absent.

## Ownership and rollback

State is stored below
`/var/lib/kitdev-sandboxes/legacy-capacity-migration` as root-owned mode-`0600`
JSON. Prior state records the exact legacy file content, SHA-256, numeric
ownership, mode, initial live page count, platform tuple, and whether apply
created the lifecycle lock. The final manifest records the adopted file hash,
all capacity inputs, the derived pool, and post-apply free/available memory.

`remove-check` and `remove` require both authenticated records, the exact owned
file hash, the fully free 12,288-page pool, the same idle/service/database
proofs, and the existing lifecycle lock. Removal restores the exact recorded
file and 2,048-page live pool, verifies the result, and then deletes only the
migration records. It does not touch any other legacy resource.

## Live result

The first exact release archive staged successfully. Controller bootstrap then
stopped before package or capacity mutation: the existing `venv --help` probe
returned success even though Ubuntu's `ensurepip` payload was unavailable, so
venv creation failed. No manual package install was used. The repository
bootstrap now checks the exact dpkg state of `python3-venv` before creating the
controller. Live capacity apply remains pending a new committed release.

# Heavy browser resource profile contract

Date: 2026-08-07

This report records the pinned source contract behind the development-only
heavy Chromium profile. It does not record a live heavy-template pass. The live
gate is intentionally blocked until the host prerequisite audit confirms the
selected hugepage pool.

## Pinned inputs

- E2B JavaScript SDK: `e2b@2.38.0`, source commit
  `7a1fe4528cb29ccea0334adbee4dc86fadb7244d`.
- E2B infrastructure: commit
  `882a3b4786755db9e94be3297de6827f9100ce5e`.
- Existing browser qualification base image and toolchain remain unchanged;
  see `browser-template-contract.md`.

The SDK findings below were checked with `git show` at the selected SDK commit,
not against the later checkout head.

## SDK disk-size boundary

The official TypeScript build surface can select CPU and memory but cannot
select disk size:

- `packages/js-sdk/src/template/types.ts` defines `BasicBuildOptions` with
  `cpuCount` and `memoryMB`, but no disk field.
- `packages/js-sdk/src/template/buildApi.ts` defines `RequestBuildInput` with
  `name`, `tags`, `cpuCount`, and `memoryMB`. Its `POST /v3/templates` body
  contains only those fields.
- the generated `TemplateBuildRequestV3` schema likewise has no disk field.

Adding `diskSizeMB` to a local TypeScript object would therefore be false API
support: the official SDK would not serialize it and the pinned API schema does
not accept it as a supported request property.

The pinned backend chooses the build disk entitlement from the authenticated
team instead. `packages/api/internal/template/register_build.go` writes
`team.Limits.DiskMb` to `env_builds.free_disk_size_mb`.
`template_start_build_v2.go` passes that stored value into
`TemplateManager.CreateTemplate`, and `create_template.go` converts it to the
template-manager gRPC `int32 diskSizeMB` field.

Consequently, two different disk profiles require two different teams. CPU and
memory remain per-build SDK options. The standard qualification keeps its
existing team and 512 MiB free-rootfs entitlement. The heavy qualification uses
the dedicated slug `kitdev-browser-heavy-team`; this prevents provisioning the
heavy entitlement from changing standard builds.

## Disk semantics and bounds

The orchestrator's
`packages/orchestrator/pkg/template/build/config/config.go` defines
`DiskSizeMB` as the requested free rootfs after build steps and before finalize.
It explicitly warns that ext4 metadata and finalize writes may reduce guest
available space. It is not total virtual disk size.

The rootfs importer separately limits the unpacked base image with the
`BuildBaseRootfsSizeLimitMB` feature flag. At the selected infrastructure
revision its default is 25,000 MiB. That base-image ceiling is not a promise
that an arbitrary free-rootfs target will fit the host. The database entitlement
also exposes a total logical-rootfs ceiling: the `base_v1` migration sets
`max_disk_size_mb` to 25,600 MiB.

The selected heavy values are:

| Resource | Value | Reason |
| --- | ---: | --- |
| vCPU | 2 | Existing browser proof and host CPU constraints |
| Guest RAM | 8,192 MiB | Requested heavy-browser target |
| Free-rootfs target | 16,384 MiB | Material increase over 512 MiB while leaving room below the 25,600 MiB ceiling for the observed roughly 4.2 GiB occupied image |
| Total-rootfs ceiling | 25,600 MiB | Pinned `base_v1` backend entitlement |
| Guest available-space assertion | at least 15,000 MiB | Allows bounded ext4/finalize overhead while still proving the heavy disk |

The prior browser build measured 4,723 MiB total with a 512 MiB free target.
Using that same image suggests roughly 20.1 GiB total for a 16 GiB target. This
is a planning estimate, not live evidence. The heavy gate verifies the actual
database build row is between 16,384 and 25,600 MiB and verifies at least
15,000 MiB available in the guest.

The gRPC field is signed `int32`, but that serialization bound is not an
operationally supported maximum. No wider disk target is claimed without a
separate capacity and live-build qualification.

## Memory admission

An 8 GiB hugepage-backed live guest consumes 4,096 2 MiB pages. Template build
phases can hold two guest-sized mappings, so one heavy build can require 8,192
pages. The selected host profile reserves 12,288 pages (24 GiB), covering one
8 GiB live mapping plus one two-mapping build, or two live mappings plus one
additional mapping. It does not cover two live 8 GiB guests plus a two-mapping
build; that requires 16,384 pages (32 GiB).

The heavy verifier deliberately requires all 12,288 selected pages to be free
before it starts and at least 16 GiB `MemAvailable` outside the persistent
hugepage pool. It also shares the existing exclusive SDK lock and refuses a
pre-existing Firecracker process. These are conservative qualification gates,
not general runtime admission control.

## Reproducible implementation

`scripts/control-plane/provision-browser-heavy-profile.sh` creates or converges
the dedicated development team, its exact `project_limits` row, and one
root-only API key without printing the key. The operation is locked,
non-production-only, idempotent for the same key file, and rejects identity or
key conflicts. It invalidates the relevant auth cache entries after commit.

`scripts/control-plane/verify-typescript-sdk-browser-template.sh` retains its
original standard invocation. Passing `--resource-profile heavy` selects the
committed heavy JSON contract, verifies host capacity and the API key's exact
team entitlement, runs the same official SDK/browser acceptance, then verifies
the persisted build metadata and guest available space. Both modes pin and
hash-check their resource JSON.

No heavy provision or live gate was executed while producing this report.

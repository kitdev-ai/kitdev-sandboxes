# Stable template publication contract

Date: 2026-08-07

## Scope

This slice publishes the minimum reusable catalog needed by an external client
using the official E2B TypeScript SDK:

| Product | Immutable release | Default release pointer | Build profile |
| --- | --- | --- | --- |
| Coding | `kitdev-coding:v1` | `kitdev-coding:stable` | 2 vCPU, 2,048 MiB RAM |
| Browser | `kitdev-browser-heavy:v1` | `kitdev-browser-heavy:stable` | 2 vCPU, 8,192 MiB RAM, 16,384 MiB free disk |

The current implementation intentionally supports a single published version.
A later version must retain its immutable version tag and move `stable` only
after the same build-and-boot acceptance gate passes.

## Upstream behavior

The project pins `e2b@2.38.0` at source commit
`7a1fe4528cb29ccea0334adbee4dc86fadb7244d`. Its `Template.build()` accepts a
`name:tag` target and additional `tags`, while `Template.assignTags()`,
`Template.removeTags()`, and `Template.getTags()` use the official tag routes.
The source example explicitly builds a version and `stable` together. See the
[pinned SDK template implementation](https://github.com/e2b-dev/E2B/blob/7a1fe4528cb29ccea0334adbee4dc86fadb7244d/packages/js-sdk/src/template/index.ts#L102-L124).

The pinned self-hosted API implements tag assignment in one database
transaction. A tag is an assignment to a build, and resolution selects the
latest assignment for that tag. See the
[pinned tag handler](https://github.com/e2b-dev/infra/blob/882a3b4786755db9e94be3297de6827f9100ce5e/packages/api/internal/handlers/template_tags.go#L19-L166).

The SDK build initially creates a team-owned namespaced alias. External teams
need a public bare alias. The v1 template PATCH atomically creates or verifies
that bare alias when `public=true`, and returns conflict rather than replacing
an alias owned by another template. See the
[pinned update handler](https://github.com/e2b-dev/infra/blob/882a3b4786755db9e94be3297de6827f9100ce5e/packages/api/internal/handlers/template_update.go#L119-L173).
The PATCH target must be the owned alias, not the opaque template ID, because
the v1 compatibility path derives the new bare alias from that target.

## Safety and ownership

`publish-stable-template.sh` takes the lifecycle lock followed by the SDK lock,
requires one healthy legacy orchestrator, rejects any live Firecracker or
pending/in-progress build, and checks that the requested alias is unclaimed.
The browser publication additionally verifies all 24 GiB of reserved hugepages
are free and that the supplied key belongs to the dedicated heavy team with
exact concurrency and resource limits.

Each product/version has a `root:root` mode `0600` journal below
`/var/lib/kitdev-sandboxes/template-publication`. The state transitions are:

```text
reserved -> qualified_private -> published -> rolled_back
```

The journal binds product, fixed alias, version, definition hash, template ID,
and build ID. Writes use a mode-0600 temporary file, `fsync`, atomic rename, and
directory `fsync`. An existing mismatch fails closed. A rerun verifies and
returns unchanged instead of creating another build.

Rollback of a qualified private candidate may soft-delete only its exact
journaled template ID. Rollback of the first published release removes only the
`stable` tag through the official SDK and retains the immutable `v1` build.
No path deletes a published template or deletes by alias.

## Legacy lab boundary

The OVH host currently runs the transient `kitdev-orchestrator-lab` topology,
not the production systemd-owned orchestrator. Host-wide admission commit
`bc24873` cannot be forced into this topology without a separate migration.
Until that migration is complete, publication and temporary external use have
these hard operator rules:

1. Do not run local builds, qualification gates, or sandboxes while the
   external SDK key is enabled.
2. The dedicated browser team remains limited to one build and one sandbox.
3. Use one external product key at a time; revoke it before local maintenance.
4. Do not claim production readiness from this temporary legacy exposure.

## Live preflight observation

Before implementation, a read-only database query found no global or
namespaced aliases named `kitdev-coding` or `kitdev-browser-heavy`. The live
publication result and exact non-secret identifiers must be appended only
after the committed runner completes and post-publication SDK creation passes.

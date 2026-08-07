# Official TypeScript SDK template-build compatibility

Date: 2026-08-07

Status: live-proven through the official SDK on the server-side loopback path.
The public HTTPS path remains an independent ingress gate.

## Scope and pinned sources

This slice uses `e2b@2.38.0` on Node.js `22.18.0`. The npm dependency and Node
container are already pinned by lockfile and image digest in the control-plane
SDK workspace. The inspected upstream source identities are:

```text
e2b SDK commit: 7a1fe4528cb29ccea0334adbee4dc86fadb7244d
infra commit: 882a3b4786755db9e94be3297de6827f9100ce5e
```

The SDK template flow is:

1. `Template.buildInBackground()` or `Template.build()` requests a build with
   `POST /v3/templates`.
2. The SDK uploads `COPY` contexts when present. This minimal test deliberately
   has no `COPY`, so it does not depend on an external signed upload URL.
3. The SDK starts the build with
   `POST /v2/templates/{templateID}/builds/{buildID}`.
4. `Template.getBuildStatus()` polls
   `GET /templates/{templateID}/builds/{buildID}/status`.
5. `Template.exists()` resolves the template name through the alias route.
6. `Template.assignTags()`, `Template.removeTags()`, and `Template.getTags()`
   use the official template tag routes.

The pinned SDK has no public template-delete method. Test cleanup therefore
uses the backend's official `DELETE /templates/{templateID-or-name}` route.
That route soft-deletes the template, releases aliases, and clears active-build
assignments. Historical database build rows and immutable template artifacts
may remain by backend design; the gate must not recursively delete service
storage because other builds can reference snapshot ancestors.

Primary local sources:

- `/private/tmp/kitdev-upstream/e2b/packages/js-sdk/src/template/index.ts`
- `/private/tmp/kitdev-upstream/e2b/packages/js-sdk/src/template/buildApi.ts`
- `/private/tmp/kitdev-upstream/infra/packages/api/internal/handlers/template_request_build_v3.go`
- `/private/tmp/kitdev-upstream/infra/packages/api/internal/handlers/template_start_build_v2.go`
- `/private/tmp/kitdev-upstream/infra/packages/api/internal/handlers/template_tags.go`
- `/private/tmp/kitdev-upstream/infra/packages/api/internal/handlers/template_delete.go`

## Reproducible test contract

`scripts/control-plane/verify-typescript-sdk-template-build.sh`:

- rejects production lifecycle mode and any OS outside Ubuntu 25.04/26.04;
- shares `/run/kitdev-sandboxes/typescript-sdk-e2e.lock` with every SDK feature
  test and refuses to start if a Firecracker process already exists;
- accepts only a root-owned, mode-`0600`, single-link project API-key file;
- installs the exact npm lockfile with scripts disabled inside the pinned Node
  image;
- generates a bounded `kitdev-sdk-template-<12 hex>` name and records it before
  the SDK starts, allowing cleanup by name even if the create request succeeds
  but the SDK never returns a template ID;
- mounts credentials read-only, never places them in arguments or retained
  logs, and emits only an exception class on failure;
- kills any recorded sandbox and deletes the test template on every exit;
- waits for zero Firecracker processes before releasing the lock.

The TypeScript client performs a background build from the locally available
`e2bdev/base:latest`, polls it to `ready`, verifies alias existence and the tag
surface, boots a sandbox from the result, performs a blocking second build from
the first template, and boots another sandbox that verifies both build layers.

Build commands that write under `/opt` explicitly select `user: "root"`. The
base image's default user is intentionally unprivileged.

## Sanitized live activity

Read-only preflight observed:

```text
orchestrator/template-manager service: active
control-plane containers: 6
Firecracker processes: 0
shared SDK lock: free
local base image: e2bdev/base:latest present
template storage: local filesystem
build-cache storage: local filesystem
local upload endpoint: private Docker bridge address on orchestrator port 5008
artifact registry provider: Local
```

Manual mutations made for the first run:

1. Copied the new runner, common helper, npm manifests, and TypeScript client to
   a unique root-owned directory below `/tmp`.
2. Ran the runner as root with `KITDEV_LIFECYCLE=development`, mounting the
   existing root-only project key read-only.
3. The runner created an ephemeral root-only stage below `/run`, installed
   `e2b@2.38.0` there, and submitted one real template build.
4. The template manager converted the local base image and executed the build
   through Firecracker. The requested command failed with exit status 1 because
   the initial test used the image's default user to write under `/opt`.
5. The exit trap deleted the API template and removed the stage. Post-run checks
   found zero Firecracker processes, a free SDK lock, zero rows for the test
   alias, zero live environments for that alias, and zero ephemeral runner
   stages under `/run`.

The failed build remains as a soft-deleted historical database row, matching
the official delete handler's behavior. It produced no final directory in
local template storage. No package, user, kernel, firewall, service, or
persistent configuration change was made.

A second invocation while another feature suite owned the SDK lock exited 75
with `sdk_e2e_already_running` before creating a template or sandbox.

## First-run correction and final result

The client now sets `user: "root"` on both build steps. It also uses a unique
pre-recorded template name, falls back to deleting by that name when no ID was
returned, and no longer prints exception messages.

The corrected runner then passed all of these operations in one live run:

```text
Template.exists() false preflight
Template.buildInBackground() accepted
Template.getBuildStatus() reached ready
Template.exists() true after build
Template.getTags() initial build tag
Template.assignTags()
Template.removeTags()
Sandbox.create() from background build tag
guest command verified the background marker
Template.build() from the first template build
Template.getTags() final build tag
Sandbox.create() from blocking build tag
guest command verified both build-layer markers
```

Post-run cleanup verification found:

```text
Firecracker processes: 0
shared SDK lock: free
API-listed sandboxes: 0
live test aliases: 0
ephemeral /run test stages: 0
control-plane containers: 6
```

Both root-owned manual source staging directories below `/tmp` and the empty
failed staging directory below `/run` were deleted after verification. The API
soft-deleted both successful template builds. Their historical database rows
and immutable local template artifacts remain, as described in the cleanup
contract above. No secret, host identifier, sandbox ID, template ID, build ID,
or signed URL was retained in this document or repository.

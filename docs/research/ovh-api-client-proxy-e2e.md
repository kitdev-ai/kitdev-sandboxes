# OVH API-to-envd live end-to-end milestone

Date: 2026-08-06

Status: executed successfully on the disposable Ubuntu 26.04 OVH host. All
sandbox identifiers and credential values are omitted. The reusable snapshot
and network assets passed independent repository review; fresh-host replay
remains open.

## Scope and source identity

The live test used the clean pinned E2B infrastructure source at commit:

```text
882a3b4786755db9e94be3297de6827f9100ce5e
```

The source-build chain produced an incremental snapshot from base build
`6dfbb2b8-62a2-4a2b-a62a-cf94ffcdb5e5`. The tested incremental build ID and
generated template ID are intentionally omitted from this report because they
are runtime state, not configuration. The host supports Ubuntu 26.04 for
production and Ubuntu 25.04 for development or migration. Ubuntu 24.04 is not
supported.

This milestone exercised the full local path:

```text
pinned snapshot -> copy-build -> PostgreSQL template seed -> API POST
-> host orchestrator -> Firecracker -> client-proxy -> envd Process.Start
-> API DELETE -> terminal-state verification
```

## Proven result

The following assertions passed on the live host:

- `resume-build` directly resumed the base build and returned exact guest
  output `KITDEV_BASE_RESUME_OK`. A separate assertion resumed its incremental
  child and returned exact guest output `KITDEV_RESUME_OK`.
- `copy-build` copied the incremental snapshot and every rootfs blob referenced
  by its headers into the orchestrator's local template store.
- The template seed committed atomically and the resulting build was
  `uploaded`, its computed `status_group` was `ready`, the trigger backfilled
  the team ID, and exactly one `default` assignment existed.
- `POST /sandboxes` returned HTTP 201 with a 600-second timeout after the API
  had rediscovered the restarted orchestrator.
- A pinned-source ConnectRPC client called envd `Process.Start` through the
  loopback client-proxy. `/bin/sh -lc 'printf KITDEV_PROXY_COMMAND_OK'`
  returned exit code zero and exact stdout `KITDEV_PROXY_COMMAND_OK`.
- Deleting the active sandbox returned HTTP 204. Deleting the already expired
  first sandbox returned HTTP 404, which is the accepted idempotent terminal
  result.
- The final API list omitted both sandbox IDs, no Firecracker process remained,
  and Redis contained no key naming either sandbox.
- Ephemeral API response files, sandbox-ID files, Go source, and the temporary
  command-client binary were removed.

The first API-created sandbox used a 120-second timeout and expired while the
temporary ConnectRPC client was being prepared. Its subsequent proxy request
returned 502. This was a lifetime failure and is not counted as a successful
protocol assertion.

## Snapshot tooling

The live host built the tools once from the pinned source with the locked
`golang:1.26.5-bookworm` image. `copy-build` used `CGO_ENABLED=0`;
`resume-build` required `CGO_ENABLED=1` because the userfaultfd implementation
depends on C definitions. A network-disabled build initially lacked
`golang.org/x/term@v0.44.0`; that exact module was prefetched, verified against
`go.sum`, and the final compilation ran offline.

Observed binaries:

| Binary | SHA-256 | Bytes |
|---|---|---:|
| `copy-build` | `aaf516f7157c70be3be35b552d94fdf1dbd3b9739a8d03a0c978f96d03c45406` | 37908606 |
| `resume-build` | `d294e961a478f3ffa84ab9d10b10bb8fed723f844c5c49e891e70b7019df2ca9` | 62084336 |

Each resume assertion temporarily opened TCP 5516-5518 from `10.11.0.0/16` on
`veth+`. The rule was deleted immediately afterward and its absence was
verified. Both the base and incremental build command paths are therefore
proven independently.

## Copy and seed boundary

The normalized `copy-build` invocation was:

```text
copy-build \
  -build <incremental-build-uuid> \
  -from <local-source-storage> \
  -to /var/lib/kitdev-sandboxes/data/runtime/orchestrator/template-storage \
  -team <team-uuid> \
  -envd-version 0.6.13 \
  -vcpu 2 \
  -memory 1024 \
  -disk 1024 \
  -tag default
```

Two upstream local-storage behaviors must be encoded explicitly:

1. `copy-build` constructs a Google Cloud Storage client even for a local to
   local copy. The live run used a trap-cleaned, mode-0600, non-secret dummy
   authorized-user JSON file solely to let the constructor initialize. No GCS
   operation was made. This workaround must be removed when upstream no longer
   initializes GCS for a local-only copy.
2. The local path helper always adds `templates/`. The effective runtime URL is
   `file:///var/lib/kitdev-sandboxes/data/runtime/orchestrator/template-storage/templates`,
   not the parent directory.

The target build contained these six direct files:

| Relative name | Bytes |
|---|---:|
| `memfile` | 169869312 |
| `memfile.header` | 344 |
| `metadata.json` | 1337 |
| `rootfs.ext4` | 5992448 |
| `rootfs.ext4.header` | 47344 |
| `snapfile` | 30080 |

The command copied 11 files in total because the incremental headers referenced
five ancestor `rootfs.ext4` blobs. A verifier must follow and validate this
chain; checking only the six direct files is insufficient.

The version-2 metadata identified the incremental build, its base build,
kernel `vmlinux-6.1.158`, Firecracker `v1.14.1_431f1fc`, and guest user `user`.
It also contained the build environment and memory-prefetch map. The generated
SQL was plain stdout bounded by `BEGIN;` and `COMMIT;`, with three inserts into
`envs`, `env_builds`, and `env_build_assignments`; it contained no `psql`
meta-command.

`copy-build` has one `-disk` flag and emits that value as both free and total
disk. The artifact had 1024 MiB free but 3722 MiB total. The live transaction
was applied only after exact SQL validation and correction of
`total_disk_size_mb` to 3722. Automation must represent these as separate,
validated values and fail closed if the generated transaction shape changes.

The team was discovered by a unique `teams.slug` lookup and validated as
unblocked on tier `base_v1`. The API key existed only in the root process
environment while requests ran; its bytes and hash were never logged or
written to this repository.

## Readiness gates

Restarting the orchestrator to correct its template storage URL took about 42
seconds because systemd allowed graceful shutdown. The process health endpoint
became available before the API's approximately 20-second local discovery
cycle had repopulated a usable node. An immediate sandbox request therefore
failed with no available node even though process health was green.

Replay must poll bounded conditions rather than sleep or trust `/health` alone:

1. the orchestrator systemd unit is active and its HTTP health endpoint returns
   200;
2. API `GET /nodes` reports the intended local node with status `ready`;
3. the template manager can list the seeded template/build;
4. only then may `POST /sandboxes` run.

API credentials and the admin token must be read from root-owned, mode-0600,
single-link regular files or the existing validated private environment. They
must never be supplied on a command line or emitted in diagnostics.

## Proxy and firewall defects found live

The first client-proxy container had no `host.docker.internal` mapping. The
proxy read the sandbox route from Redis, then failed with DNS lookup errors
instead of reaching the host orchestrator proxy.

Using Docker's literal `host-gateway` mapping was also wrong on this host: it
resolved to the default bridge gateway while the container was attached to the
separate `kitdev-core` bridge. The durable mapping must use the gateway derived
from the actual `kitdev-core` network.

After correcting DNS, the request timed out because UFW allowed the core bridge
to reach host port 5008 but not proxy port 5007. The proven minimum rules are
scoped to the derived core subnet, derived core bridge, and derived core
gateway, with TCP 5007 for client-proxy traffic and TCP 5008 for API gRPC
traffic. Neither port is exposed on the public interface.

## ConnectRPC assertion contract

The ephemeral client used the protobuf and generated client from the pinned
source (`connect-go/1.18.1`, built with Go 1.26.5). Its contract was:

```text
base URL: http://127.0.0.1:3002
RPC: process.Process/Start
command: /bin/sh
arguments: -lc, printf KITDEV_PROXY_COMMAND_OK
stdin: false
headers:
  E2b-Sandbox-Id: <API response sandboxID>
  E2b-Sandbox-Port: 49983
  Authorization: Basic <root with empty password>
```

The verifier accumulated stdout and stderr separately, required a terminal end
event, required exit code zero and a nil stream error, and exact-compared
stdout. A raw `curl` body is not an equivalent test for this server-streaming
ConnectRPC method.

## Live mutations and retained state

Retained host state:

- the two hash-verified snapshot helper binaries;
- copied local template artifacts and ancestor rootfs blobs;
- the atomic PostgreSQL template/build/assignment seed;
- corrected orchestrator template storage configuration;
- the derived client-proxy host mapping;
- the scoped UFW rule for core-bridge traffic to TCP 5007;
- root-only action and component logs under `/var/log`.

Removed ephemeral state:

- temporary firewall access to build proxy ports 5516-5518;
- dummy Google application credentials;
- both test sandboxes and their Redis routing state;
- temporary sandbox-ID and API response files;
- temporary ConnectRPC source and binary.

The root-only audit log is `/var/log/kitdev-api-e2e-manual.log`. Separate
component logs capture the resume and copy runs and database seed boundary.
They contain runtime identifiers but no credential value. The persistent
template seed is deliberate reusable lab state and was not rolled back.

## Remaining gates

- Replay the complete path from repository scripts on the current host and
  compare installed configuration, firewall, container, database, and artifact
  state exactly.
- Reinstall the host and repeat from a fresh Ubuntu 26.04 image before calling
  the system reproducible.
- Add negative tests for changed copy-build SQL shape, incomplete ancestor
  chains, early process-health readiness, wrong Docker gateway selection,
  leaked credential material, command mismatch, and failed cleanup.

# Host runtime admission control

Date: 2026-08-07

This report records the admission contract for the OVH qualification host and
the pinned E2B infrastructure commit
`882a3b4786755db9e94be3297de6827f9100ce5e`. Research used read-only source and
database queries. The new admission build was not deployed while writing this
report.

## Upstream behavior

The pinned API enforces sandbox concurrency per team with an atomic Redis Lua
reservation. It rejects with HTTP 429 before fetching the build or calling the
orchestrator. Template-build concurrency is weaker: `register_build.go`
explicitly describes its count-then-insert check as a simple implementation
that does not guarantee the limit is not exceeded.

The orchestrator has two node limits:

- `max-sandboxes-per-node`, fallback 200;
- `max-starting-instances-per-node`, fallback 3.

Both are LaunchDarkly flags. With no `LAUNCH_DARKLY_API_KEY`, the client uses
compiled offline fallbacks; the pinned source has no environment or local-file
override. `NBD_POOL_SIZE` is separately configurable and defaults upstream to
64. The kitdev deployment previously selected 16.

The orchestrator reports allocated RAM and HugeTLB metrics to the API, but the
pinned BestOfK placement score uses CPU only. It does not reject a request
because its guest RAM would exceed the HugeTLB pool. Team limits therefore
cannot provide global admission across multiple teams, and the upstream node
fallbacks are unsafe for this four-core host.

## Live audit before convergence

A narrow read-only PostgreSQL query observed these effective limits after the
heavy team was provisioned:

| Team | Sandboxes | Builds | vCPU | RAM MiB | Free disk MiB | Max disk MiB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `local-dev-team` | 20 | 20 | 8 | 8192 | 512 | 25600 |
| `system` | 20 | 20 | 8 | 8192 | 512 | 25600 |
| `kitdev-browser-heavy-team` | 1 | 1 | 2 | 8192 | 16384 | 25600 |

The two default rows are not safe host-capacity policy. No database row was
changed during the audit.

## Selected policy

The 24 GiB HugeTLB pool has 12,288 pages of 2 MiB. A live 8 GiB guest needs one
8 GiB mapping; an 8 GiB template build can overlap two mappings. The selected
global limits are therefore:

| Control | Limit |
| --- | ---: |
| Live sandboxes | 1 |
| Concurrent starts/resumes | 1 |
| Concurrent template builds | 1 |
| Per sandbox/build vCPU | 2 |
| Per sandbox/build RAM | 8192 MiB |
| Logical disk ceiling | 25600 MiB |
| Orchestrator NBD pool | 4 |

The worst supported memory overlap is
`1 * 8192 + 2 * 1 * 8192 = 24576 MiB`. CPU overlap is one 2-vCPU live guest and
one 2-vCPU build on four physical cores. Four NBD slots leave bounded device
headroom above the two admitted operation classes.

## Reproducible enforcement

`patches/e2b-infra/882a3b4-host-admission.patch` adds required `KITDEV_MAX_*`
environment inputs. Missing, empty, zero, or non-integer values fail service
startup. Direct sandbox and template-manager gRPC calls are resource-capped,
not only API calls. The local live/start limits are applied as the minimum of
the hard local value and LaunchDarkly, so a remote flag cannot raise capacity.
An atomic process-wide build slot rejects excess builds with
`ResourceExhausted` before build-cache creation or Firecracker work.

The build script copies the pinned package tree, applies the patch only to that
copy, and records the patch hash and selected limits in manifest schema 2. The
preflight binds the manifest to the installed patch and exact environment,
then verifies that total and free HugeTLB pages cover live mappings plus two
mappings per concurrent build.

`converge-admission-policy.sh --apply` is the API-side companion. Under both
lifecycle locks and only with no Firecracker or active build, it caps every
existing project row to one sandbox, one build, 2 vCPU, 8192 MiB RAM, 16384 MiB
free disk, and 25600 MiB maximum disk, then invalidates team-auth cache keys.
`--check` proves every effective row is within the same bounds.

## Verification checkpoint

- The patch applies cleanly to the exact pinned source.
- In the pinned Go 1.26.5 builder,
  `go test ./pkg/admission ./pkg/server ./pkg/template/server` passed.
- The admission tests cover missing/invalid configuration, exact parsing,
  concurrent slot boundaries, and idempotent release.
- A staged no-index patch application matching `build-orchestrator.sh` passed.

Deployment, database convergence, service restart, reject-path SDK acceptance,
and final pressure/recovery measurements remain live qualification steps. They
must run under the lifecycle locks after this source checkpoint is committed.

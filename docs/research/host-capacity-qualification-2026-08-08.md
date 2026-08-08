# Host capacity qualification

Date: 2026-08-08

Measured concurrency of the OVH development host, and the reasoning behind the
team limits that were set from it. This supersedes the earlier statement that
two concurrent heavy sandboxes were unqualified.

## Why limits exist at all

They are not policy. Sandbox memory is served from the host's **persistent
HugeTLB pool**, which is reserved up front and cannot satisfy ordinary
allocations. The pool is therefore a hard ceiling on total concurrent sandbox
memory, independent of how much ordinary RAM is free.

```text
pool = 12,288 x 2 MiB pages = 24,576 MiB
```

Everything else follows from that one number. A team limit above what the pool
can serve is not dangerous, but it is not capacity either: the API will admit
the request and the sandbox will fail to start.

## Measured results

Both probes ran from an off-host client over public HTTPS using the product
key and `scripts/external-sdk-matrix/concurrency.ts`.

| Profile | Per sandbox | Concurrent | Pool used | Result |
| --- | ---: | ---: | ---: | --- |
| `kitdev-coding:stable` | 2,048 MiB | **12** | 24,576 MiB | all 12 started in about 1 second, each independently executing and filesystem-isolated |
| `kitdev-browser-heavy:stable` | 8,192 MiB | **3** | 24,576 MiB | 3 started; the 4th was refused |

Both figures are exactly `pool / per-sandbox memory`. The pool is the binding
constraint, and it binds precisely.

### Behaviour at the ceiling

The fourth 8 GiB sandbox failed with a `SandboxError` raised to the caller.
The three already running were unaffected: each still executed commands
afterwards, and all were killed normally. After both probes the host returned
to 12,288 free hugepages, zero Firecracker processes, about 36 GiB ordinary
`MemAvailable`, and seven healthy containers.

Exhausting the pool is therefore a **clean per-request failure**, not host
overcommit. That is what makes a team limit above the pool acceptable: the
hardware refuses safely.

## Selected limits

Applied to `kitdev-browser-heavy-team` with
`scripts/control-plane/set-team-limits.sh`:

| Limit | Before | After | Reason |
| --- | ---: | ---: | --- |
| Concurrent sandboxes | 1 | 12 | pool-exact for the 2 GiB coding profile; the pool still caps 8 GiB sandboxes at 3 |
| Concurrent template builds | 1 | 2 | NBD pool is 4; builds are the heaviest disk consumer |
| vCPU per sandbox | 2 | 4 | 4 cores / 8 threads; a single sandbox may use half the host |
| RAM per sandbox | 8,192 MiB | 8,192 MiB | unchanged; set at template build time, and 8 GiB is the largest published template |
| Maximum lifetime | 1 h | 24 h | the 1 hour default was a base-tier artefact, not a capacity limit |

The prior values are recorded create-once at
`/var/lib/kitdev-sandboxes/team-limits/kitdev-browser-heavy-team.prior`.

`--allow-oversubscription` was required because the worst case
(12 x 8,192 MiB) exceeds the pool. That is intentional and safe per the
ceiling behaviour above: it lets small sandboxes reach the hardware limit
without capping them at what the largest profile would allow.

## What this does not license

- **Headroom is now spendable.** The 24 GiB pool was derived as two 8 GiB live
  slots plus one 8 GiB transient allowance for build and snapshot mappings.
  Running 3 heavy sandboxes consumes that allowance, so a concurrent build or
  snapshot will fail. Leave a slot free when a build must succeed.
- **Sustained load is unmeasured.** These probes start, exercise and destroy.
  They do not hold a full pool under work for hours, and they do not measure
  vCPU contention at 12 sandboxes on 8 threads.
- **Host-level admission control is still undeployed.** Enforcement is the API
  team limit plus the pool. The `bc24873` orchestrator patch that would refuse
  before mutation is not on this topology.
- **The pool itself was not changed.** Raising it toward the 50%-of-RAM policy
  ceiling (32 GiB on this host) needs the capacity migration controller, whose
  reboot-persistence and rollback gates are still open.

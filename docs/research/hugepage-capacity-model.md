# Hugepage capacity model

Date: 2026-08-07

Status: implemented in the fresh-host prerequisite role and locally tested;
not yet applied or load-qualified on the OVH host.

## Decision

The initial host profile is explicit:

```text
max_sandbox_memory_mib = 8192
concurrent_hugepage_sandboxes = 2
build_snapshot_headroom_mib = 8192
normal_memory_reserve_mib = 16384
max_ram_percent = 50
```

The persistent pool is derived rather than entered separately:

```text
guest_pool_mib = max_sandbox_memory_mib * concurrent_hugepage_sandboxes
hugepage_pool_mib = guest_pool_mib + build_snapshot_headroom_mib
hugepages_2m = ceil(hugepage_pool_mib / 2)
```

For the initial profile this is `8192 * 2 + 8192 = 24576 MiB`, or `12288`
2 MiB pages. The first two guest-sized units are live-sandbox slots. The final
unit is a transient-mapping allowance. This covers either two live guests plus
one snapshot mapping, or one live guest plus a build requiring two guest-sized
resident mappings. It does not cover two live guests and that build at the same
time; that workload requires 32 GiB. On a nominal 64 GiB host, the selected
pool is 37.5% of RAM. The policy ceiling is 50%, leaving room for differently
sized hosts without accepting a pool that dominates normal memory.

The validator permits reusable profiles down to 512 MiB per sandbox; 8 GiB is
the selected default for browser/heavy workloads, not a hard-coded platform
minimum. Build/snapshot headroom must always cover at least one maximum-size
sandbox.

Before mutation, validation checks the persistent pool against total RAM and
charges only pages above the current persistent pool against `MemAvailable`.
At least 16 GiB must remain available to the kernel, control plane, builds,
page cache, and other normal-page workloads. HugeTLB pages are reserved and
cannot satisfy ordinary memory allocations, so both checks are required.

This model does not use `nr_overcommit_hugepages`. A fixed persistent pool is
easier to reason about for the first production qualification. Allocation can
still fail because contiguous hugepages are harder to obtain after host memory
becomes fragmented; converge the prerequisite role early after installation
and confirm the result after reboot.

## Upstream E2B observations

Source was inspected read-only at pinned E2B infra commit
`882a3b4786755db9e94be3297de6827f9100ce5e` on the disposable OVH lab. No host
state was changed during this research.

- `.github/actions/host-init/init-client.sh` allocates hugepages early. It
  reserves normal RAM, then splits the hugepage envelope between persistent
  and overcommit pages.
- The upstream GCP Nomad cluster configuration defaults persistent hugepages
  to 60% on build hosts and 80% on client hosts. Those cloud defaults are
  evidence of the dependency, not sizing values adopted by this project.
- The template build path passes the configured RAM size and 2 MiB hugepage
  mode to Firecracker.
- `block.NewEmpty` creates metadata for the memory file; it does not establish
  that the complete configured guest memory is eagerly allocated at that call.

The extra 8 GiB is therefore a conservative operational allowance for one
transient guest-sized mapping. A measured build can require two such mappings
and consequently consumes one of the two nominal live-sandbox slots as well as
the allowance. This is not a claim that `block.NewEmpty` eagerly materializes
the entire memory file.

## Remaining qualification

- apply/reboot/apply on clean Ubuntu 26.04;
- confirm all `12288` pages are available after reboot;
- run two simultaneous 8 GiB browser/heavy sandboxes plus snapshot activity;
- run one 8 GiB live sandbox alongside a two-mapping 8 GiB template build;
- prove failure cleanup and host normal-memory reserve under pressure; and
- add runtime admission control so a third maximum-size sandbox is rejected
  before host memory pressure.

## Primary sources

- [Linux HugeTLB pages](https://docs.kernel.org/admin-guide/mm/hugetlbpage.html)
- [Pinned E2B host initialization](https://github.com/e2b-dev/infra/blob/882a3b4786755db9e94be3297de6827f9100ce5e/.github/actions/host-init/init-client.sh)
- [Pinned E2B build config](https://github.com/e2b-dev/infra/blob/882a3b4786755db9e94be3297de6827f9100ce5e/packages/orchestrator/pkg/template/build/config/config.go)
- [Pinned E2B build sandbox creation](https://github.com/e2b-dev/infra/blob/882a3b4786755db9e94be3297de6827f9100ce5e/packages/orchestrator/pkg/template/build/layer/create_sandbox.go)
- [Pinned E2B empty block implementation](https://github.com/e2b-dev/infra/blob/882a3b4786755db9e94be3297de6827f9100ce5e/packages/orchestrator/pkg/sandbox/block/empty.go)
- [Pinned E2B Firecracker client](https://github.com/e2b-dev/infra/blob/882a3b4786755db9e94be3297de6827f9100ce5e/packages/orchestrator/pkg/sandbox/fc/client.go)

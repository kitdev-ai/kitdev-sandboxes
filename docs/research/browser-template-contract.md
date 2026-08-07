# Browser template contract and live evidence

Date: 2026-08-07

Status: live-proven on the disposable Ubuntu 26.04 bare-metal host through the
official `e2b@2.38.0` TypeScript SDK. This is a qualification template, not a
production-published alias.

## Pinned browser stack

The browser guest starts from the official Playwright Ubuntu 26.04 image. The
multi-architecture tag was resolved, then the Linux AMD64 manifest was pinned:

```text
image: mcr.microsoft.com/playwright:v1.62.0-resolute
index: sha256:2f9363c77a15fc2f1a95550d3343b94bc6da09ade530018c5997fba1ec1d4f84
linux/amd64: sha256:796dc8c6c3d7df246bf8b661402f8489189e278dca6456022a816e178d0211e9
Playwright: 1.62.0
Chromium revision: 1234
Chrome for Testing: 151.0.7922.34
Node.js: v24.18.0
npm: 11.16.0
```

The exact `playwright@1.62.0` and `playwright-core@1.62.0` tarballs are locked
by npm integrity in the browser runtime lock. Its SHA-256 is
`db5404269854f530b030d7c31b7ce8c0cd05e7182978af49c58b5e488f87c873`.
Install scripts are disabled because the matching browser is already in the
content-addressed base image.

The official image includes browser binaries and native dependencies, but not
the Playwright package; Microsoft requires consumers to install a package
whose version matches the image. Its Dockerfile confirms that the `resolute`
variant is based on Ubuntu 26.04, installs Node 24, creates `pwuser`, and places
browsers below `/ms-playwright`. Primary sources:

- [Playwright Docker documentation](https://playwright.dev/docs/docker)
- [Playwright v1.62.0 Ubuntu 26.04 Dockerfile](https://github.com/microsoft/playwright/blob/v1.62.0/utils/docker/Dockerfile.resolute)
- [Playwright v1.62.0 browser revision manifest](https://github.com/microsoft/playwright/blob/v1.62.0/packages/playwright-core/browsers.json)
- [Playwright v1.62.0 release](https://github.com/microsoft/playwright/releases/tag/v1.62.0)

## Runtime and security boundary

The built template requests 2 vCPUs, 2,048 MiB RAM, 512 MiB free disk, and a
measured 4,723 MiB total guest filesystem after conversion. The last successful
artifact consumed about 620 MiB of host blocks; this is not the guest disk
limit because E2B stores sparse/deduplicated memory and rootfs artifacts.

The runtime is root-installed and non-root-executed:

```text
user: pwuser
uid: 1001
workdir: /home/pwuser/workspace (0755)
downloads: /home/pwuser/downloads (0700)
profile: /home/pwuser/browser-profile (0700)
CDP: 127.0.0.1:9222 only
```

The start command launches one persistent headless Chromium context with its
own profile, Chromium sandboxing enabled, at most four renderer processes, and
a 1,024 MiB V8 old-space request. The ready command requires the private
marker, loopback `/json/version`, and the non-root owner process. The profile
survives within the sandbox and its pause/resume lifecycle; cross-sandbox or
durable profile persistence is not claimed.

The CDP endpoint deliberately does not bind `0.0.0.0`. CDP grants browser
control to its client, and Playwright describes `connectOverCDP()` as
Chromium-only and lower fidelity than its native protocol. Remote CDP and
arbitrary sandbox port access remain blocked on authenticated wildcard ingress
qualification. Primary source:
[Playwright `connectOverCDP`](https://playwright.dev/docs/api/class-browsertype#browser-type-connect-over-cdp).

## Live acceptance result

The destructive verifier uses the shared SDK lock, refuses a preexisting
Firecracker process, creates a unique alias, and guarantees cleanup. Its final
live run passed:

```text
browser-template-build
browser-template-tag
browser-sandbox-create-ready
browser-identity-readiness-loopback-cdp
browser-toolchain-integrity-manifest
browser-playwright-cdp-navigation-dom
browser-sdk-artifact-collection
browser-process-alive-before-sandbox-destroy
browser-sandbox-kill
verify-typescript-sdk-browser-template
```

The browser connected through Playwright over loopback CDP, opened a local HTTP
page, filled an accessible textbox, clicked a button, asserted the resulting
DOM, returned a valid PNG through the official E2B files API, downloaded an
exact 27-byte artifact, and remained alive until sandbox destruction. The
outer cleanup then observed no matching API sandbox, Redis key, or Firecracker
process. The local page avoids treating public Internet availability as a
browser correctness dependency.

## Hugepage and capacity evidence

The host currently has 64 GiB physical RAM, about 56 GiB available ordinary
memory when measured, and 2,048 preallocated 2 MiB HugeTLB pages: a 4 GiB guest
memory pool. A 2,048 MiB browser build passed. A 4,096 MiB build completed its
npm layer but failed resuming the next build layer because UFFD could not map a
new memfd while the 4 GiB pool had no overlap headroom. The cleanup returned
all 2,048 pages. This is direct evidence that `guest RAM <= pool` is not a safe
template-build sizing rule.

For this pinned orchestrator, a new build memfile is exactly the configured
guest RAM and uses the Firecracker version's hugepage setting. Each 2 MiB page
backs 2 MiB of guest memory. The pool must cover all simultaneously mapped
live sandboxes plus build-layer overlap, not image size or guest disk size.
Linux documents that reserved HugeTLB pages are a distinct, preallocated pool:
[kernel HugeTLB documentation](https://docs.kernel.org/admin-guide/mm/hugetlbpage.html).

Current measured and configured ceilings are:

| Resource | Current fact | Interpretation |
| --- | ---: | --- |
| Physical CPU | 4 cores / 8 threads | CPU saturation arrives far before the upstream 200-sandbox software default for browser workloads |
| HugeTLB pool | 2,048 x 2 MiB = 4 GiB | Enough for the proven 2 GiB build overlap, not an 8 GiB sandbox |
| Kernel NBD devices | 64 | Address-space ceiling after host prerequisite convergence |
| Orchestrator NBD pool | 16 | At most 16 concurrent device slots; CPU/RAM limits are lower here |
| Concurrent starts | 3 | Upstream offline feature-flag fallback, not a capacity guarantee |
| Sandboxes per node | 200 | Upstream fallback only; unsafe as a local sizing target |
| Data filesystem | 3.6 TiB total / 3.5 TiB available | Capacity is ample today but template/snapshot retention still needs policy |
| Browser guest disk | 4,723 MiB total / 512 MiB free | Too little free space for heavy downloads until disk sizing is explicitly exposed and tested |

The NBD pool source caps usable slots to the smaller of `NBD_POOL_SIZE` and the
kernel device count. The pinned defaults set three concurrent starts and 200
sandboxes per node; neither checks that the workloads fit CPU or HugeTLB RAM.

### Conservative 8 GiB plan

Do not advertise an 8 GiB browser sandbox on the present 4 GiB pool. Before
qualification:

1. Reserve at least 8,192 2 MiB pages (16 GiB) for one 8 GiB template build,
   based on the observed two-mapping build boundary. Run no other HugeTLB VM
   during that build.
2. Prefer 12,288 pages (24 GiB) if one 8 GiB live sandbox must coexist with one
   8 GiB build; prefer 16,384 pages (32 GiB) for the build plus two live 8 GiB
   sandboxes. These are qualification candidates, not proven production limits.
3. The selected host capacity profile reserves at least 16 GiB as ordinary
   memory. This browser plan is more conservative: keep 24-32 GiB ordinary RAM
   for the host, control plane, page cache, UFFD helpers, Docker, and
   non-HugeTLB allocations. The Ansible capacity gate must validate the chosen
   percentage and post-allocation available-memory floor before applying it.
4. Serialize 8 GiB template builds and initially cap 8 GiB browser runtime
   concurrency at one. Raise only after repeated build/start/pause/resume tests
   record HugePages Free/Reserved, ordinary memory, CPU latency, NBD slots, and
   teardown recovery.
5. Keep 2 vCPUs for the first 8 GiB profile on this four-core host. A second
   simultaneous browser already consumes the remaining physical-core budget;
   SMT is not equivalent to four additional physical cores.
6. Add and test an explicit guest free-disk request before heavy browser use.
   Guest RAM, OCI image/artifact size, guest filesystem total, and free disk are
   independent dimensions.

No hugepage setting was changed for this milestone. Host prerequisite
automation owns that state.

## Mutation record

All live work used one isolated source directory below `/tmp` and the existing
root-only API-key file. The first invocation deliberately used production mode
and refused before mutation. Eight development-mode attempts followed:

1. A 4 GiB build failed on a stale assumed Chromium path; cleanup left no VM.
2. The corrected 4 GiB build failed at a later UFFD build-layer resume because
   the HugeTLB pool lacked overlap capacity; cleanup restored all pages.
3. A 2 GiB build and browser startup passed, then the strict identity assertion
   rejected the assumed UID 1000.
4. A diagnostic repeat recorded the non-secret, image-pinned `pwuser` UID 1001
   and otherwise repeated the same safe cleanup.
5. With identity corrected, the acceptance command reached a later nonzero
   result; output was still intentionally suppressed by the credential-safe
   top-level error handler.
6. A callback-only diagnostic repeat captured the non-secret assertion: CDP
   reports `HeadlessChrome/151.0.7922.34`, not `Chrome/151.0.7922.34`.
7. The corrected cached build passed the complete acceptance sequence and
   cleanup.
8. After formatting the exact candidate source, one final cached replay passed
   the same complete sequence and cleanup before commit.

Every attempt used a unique template alias. Successful and failed build rows
and immutable artifacts remain as historical template-manager evidence after
soft deletion; the test alias and runtime sandbox are absent. No host package,
identity, kernel, hugepage, firewall, systemd, Docker configuration, or
persistent secret was changed by this browser milestone. The isolated `/tmp`
source directory was deleted after the final pass. The closing audit observed
zero Firecracker processes, six control-plane containers, and all 2,048
hugepages free with none reserved.

# Browser sandbox qualification guide

This guide is for an operator qualifying the browser template on a prepared,
disposable development host. It does not publish a stable production template.

## What the gate proves

The gate builds an Ubuntu 26.04 browser template from content-addressed inputs,
creates one sandbox through the official TypeScript SDK, and proves:

- a non-root Chromium process reaches an explicit readiness contract;
- Playwright connects over loopback CDP;
- local navigation and DOM interaction work;
- PNG and downloaded-file bytes can be collected with the E2B files API;
- killing the sandbox removes its API, Redis, and Firecracker runtime state.

The current profile is 2 vCPUs, 2,048 MiB RAM, and 512 MiB free guest disk.
CDP listens only on `127.0.0.1:9222` inside the sandbox. Public CDP and sandbox
port URLs are not supported until authenticated wildcard ingress is live-proven.

## Run the qualification

Use a reviewed immutable checkout on a prepared Ubuntu 26.04 development host.
The API key must be a root-owned regular file with mode `0600`; do not place the
key in an environment variable or command argument.

```console
sudo env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  KITDEV_LIFECYCLE=development \
  /usr/bin/bash scripts/control-plane/verify-typescript-sdk-browser-template.sh \
  --api-key-file /absolute/root-only/path/e2b-api-key
```

The runner refuses production mode, a busy shared SDK lock, a preexisting
Firecracker process, a modified SDK lock, a modified browser dependency lock,
or an unsafe key file. It creates a unique alias and deletes it during cleanup.
Do not use this command to provision the product's long-lived browser alias.

A passing run ends with:

```text
status=pass operation=verify-typescript-sdk-browser-template
```

After any result, confirm the runner has exited. Its cleanup already treats
API/Redis/Firecracker absence as part of the gate; do not manually delete
internal template artifacts while another build or sandbox may share them.

## Heavy resource profile

The default command above remains the qualified 2 vCPU, 2 GiB RAM profile. A
separate development profile prepares 2 vCPU, 8 GiB RAM, and a 16 GiB
free-rootfs target. Disk is team-scoped in the pinned backend, not a
`Template.build()` option, so the heavy test uses a dedicated team and API key.

Provision that team once, preserving the generated root-only key file:

```console
sudo env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  KITDEV_LIFECYCLE=development \
  /usr/bin/bash scripts/control-plane/provision-browser-heavy-profile.sh \
  --api-key-file /etc/kitdev-sandboxes/e2e-browser-heavy-api-key
```

After the host prerequisite audit confirms 12,288 total and free 2 MiB
hugepages, run:

```console
sudo env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  KITDEV_LIFECYCLE=development \
  /usr/bin/bash scripts/control-plane/verify-typescript-sdk-browser-template.sh \
  --resource-profile heavy \
  --api-key-file /etc/kitdev-sandboxes/e2e-browser-heavy-api-key
```

The verifier refuses production, fewer than 12,288 total or free hugepages,
less than 16 GiB normal-memory headroom, any pre-existing Firecracker process,
the wrong team entitlement, or a changed profile file. It proves the database
build row requested 8,192 MiB RAM and 16,384 MiB free rootfs, and that the guest
still exposes at least 15,000 MiB available after finalize. This heavy mode
passed live qualification on 2026-08-07: the finalized guest exposed 16,021 MiB
available and cleanup restored all 12,288 hugepages to the free pool. See the
[live evidence](research/browser-heavy-live-qualification-2026-08-07.md) and
[pinned resource contract](research/browser-heavy-resource-profile.md).

## Current limits

The original tested host's 4 GiB HugeTLB pool supported the 2 GiB browser
qualification but not a 4 GiB template build with build-layer overlap. The
current 24 GiB pool now qualifies one 8 GiB browser build and runtime at a time;
it does not qualify two concurrent heavy builds. See
`docs/research/browser-template-contract.md` for the measured failure boundary.

The browser image includes Firefox bits as part of the official Playwright
image, but this gate proves Chromium only. It does not prove public wildcard
routing, remote CDP, hostile-site isolation, cross-sandbox profile persistence,
desktop streaming, or computer-use input APIs.

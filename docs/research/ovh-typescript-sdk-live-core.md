# OVH official TypeScript SDK live core

Date: 2026-08-07

Status: passed against the disposable Ubuntu 26.04 OVH lab. This is a live
compatibility result for the existing transient control plane, not a clean
fresh-host replay result. No credential, hostname, team identifier, template
identifier, or sandbox identifier is retained here.

## Exact client

The test used official `e2b@2.38.0`, installed by `npm ci` from the committed
lockfile. The package integrity is recorded in
`e2b-typescript-sdk-self-host-contract.md`. Execution used the official SDK
repository's Node.js 22.18.0 baseline in the digest-pinned Linux/amd64
`node:22.18.0-bookworm-slim` image.

The client used explicit private-test connection options:

```text
apiUrl=http://127.0.0.1:3000
sandboxUrl=http://127.0.0.1:3002
domain=localhost
debug=false
```

The API key was minted through the official administrator endpoint for the
team that owns the seeded template. Its one-time response was captured in a
root-owned mode-0600 runtime file, reduced to the raw key and revocation ID,
and deleted. The first attempted key belonged to a different team; it was
revoked before replacement. SDK API-key validation remained enabled.

The SDK takes a template ID such as the value in `envs.id`, not an
`env_builds.id` build UUID. Passing the build UUID correctly returned 404 and
created no sandbox. The live runner resolves and mounts the non-secret
template ID separately from the API credential.

## Proven core path

The following official SDK calls passed:

- `Sandbox.create()` with a ten-minute timeout and test metadata
- `sandbox.commands.run()` with exact stdout, stderr, and exit-code assertions
- `sandbox.files.write()` followed by byte-exact `sandbox.files.read()`
- `sandbox.kill()`

The failed template-identifier probes left zero Firecracker processes. The
successful run killed its sandbox. The reusable runner keeps the sandbox ID
only in its root-owned runtime stage so an exit trap can issue an idempotent
API delete and prove API-list absence, Redis-key absence, and zero remaining
Firecracker processes.

This core result does not yet prove PTY, filesystem watch, background process
reconnection, arbitrary guest ports, pause/resume, or snapshot lifecycle.
Those remain subsequent conformance groups.

## Public-client boundary

This server-side test deliberately used the fixed loopback proxy mode. A
client on another server requires the selected public topology at
`api.sandbox.kitdev.ai` plus `*.sandbox.kitdev.ai`, TLS, DNS, and ingress that
preserves ConnectRPC streaming, WebSocket upgrades, routing headers, and
unbuffered file transfers. Public ingress is not implied by this result.

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

An isolated second sandbox proved the advanced command surface:

- background command start and active-process listing
- handle-bound `sendStdin()` followed by `closeStdin()` and exact output
- `disconnect()`, `commands.connect(pid)`, stdin after reconnect, and wait
- `commands.kill(pid)` followed by list absence

The EOF test deliberately sends a partial line before `closeStdin()`. Sending
a newline first lets the shell's `read` complete and the process exit, after
which `closeStdin()` correctly returns `NotFoundError`; that is a client-test
race rather than an unsupported EOF operation.

An isolated third sandbox proved the filesystem surface:

- multi-file text and binary upload with user metadata
- text, byte-array, Blob, and streaming download with byte-exact assertions
- directory list, file metadata, existence, rename, file removal, and tree removal
- recursive directory watch with entry information and a bounded event deadline

An isolated fourth sandbox proved the PTY surface:

- interactive PTY creation, input, exit, and output callbacks
- terminal resize with exact `stty size` verification
- process listing, stream disconnect, `pty.connect(pid)`, and continued input
- `pty.kill(pid)` followed by process-list absence

An isolated fifth sandbox proved the basic lifecycle surface:

- instance and static running-state information with exact deployed resources
- timeout extension and updated expiration time
- active sandbox pagination and identity match
- `Sandbox.connect()` followed by command execution through the new client
- metrics retrieval and per-sample resource validation
- `getHost()` using the expected self-host wildcard naming convention

An isolated sixth sandbox proved both pause modes:

- full-memory pause, paused-state info/list, `Sandbox.connect()`, and retention
  of a running process
- filesystem-only pause, paused-state info/list, cold `Sandbox.connect()`, and
  removal of the pre-pause process
- rootfs file persistence across both resume paths, plus timeout and running
  state after the cold resume

The first cold-resume probe placed its marker in `/tmp`, which the cold boot
correctly recreates as ephemeral state. The durable assertion uses
`/home/user`; losing `/tmp` is not a filesystem-snapshot failure.

An isolated seventh source sandbox proved snapshot lifecycle:

- named snapshot creation from a durable filesystem marker
- instance and static snapshot pagination with identity matching
- source sandbox deletion before restoration
- a second sandbox created from the snapshot with exact file and command checks
- restored sandbox deletion followed by snapshot deletion

Snapshot creation leaves one Redis audit key named
`snapshot:last:<source-sandbox-id>` after the source, restore, and snapshot are
deleted. The first full-suite terminal gate correctly rejected that residual:
the API list was empty, the restored sandbox had zero matching keys, and zero
Firecracker processes remained, but the source ID matched that one exact key.
The runner now accepts no broad exception. It requires the residual set to be
either empty or exactly that one audit key, deletes the exact key, and then
reruns the generic zero-key terminal check for both sandbox IDs.

A subsequent full-suite replay observed short-lived Redis transition keys next
to that audit key. Their exact shape was
`sandbox:storage:{<cluster-uuid>}:transition:<source-sandbox-id>:<transition-uuid>`,
their type was `string`, and a fresh atomic observation measured about 28.5
seconds remaining. The earlier sub-second reading had sampled the same keys
near expiry. The runner now reads matching key names, types, and TTLs
atomically. It waits for at most 60 seconds only when every extra key has that
exact shape and a positive
TTL no greater than 60 seconds, then deletes only the exact `snapshot:last`
key. A transition TTL increasing between polls is also rejected. Unknown keys,
wrong types, missing expiry, long expiry, or malformed identifiers remain a
hard failure.

The corrected snapshot-only replay passed source creation, snapshot creation
and listing, source deletion, restoration, restored command and file checks,
restored-sandbox deletion, snapshot deletion, bounded transition expiry, exact
audit-key deletion, and the final generic terminal gate. An independent
post-run audit then confirmed an empty authenticated API sandbox list, no
`snapshot:last` or transition keys, zero Firecracker processes, no SDK runtime
stage, and a free SDK lock.

The failed template-identifier probes left zero Firecracker processes. The
successful run killed its sandbox. The reusable runner keeps the sandbox ID
only in its root-owned runtime stage so an exit trap can issue an idempotent
API delete and prove API-list absence, Redis-key absence, and zero remaining
Firecracker processes.

This result does not yet prove arbitrary guest ports or direct URL helpers.
Those depend on the public ingress and DNS path and remain a subsequent gate.

## Public-client boundary

This server-side test deliberately used the fixed loopback proxy mode. A
client on another server requires the selected public topology at
`api.sandbox.kitdev.ai` plus `*.sandbox.kitdev.ai`, TLS, DNS, and ingress that
preserves ConnectRPC streaming, WebSocket upgrades, routing headers, and
unbuffered file transfers. Public ingress is not implied by this result.

# TypeScript SDK integration guide

This guide is for an AI coding agent integrating software on another server
with Kitdev Sandboxes. It targets the official E2B TypeScript SDK. Treat the
feature-status table as authoritative: SDK source compatibility does not prove
that the public network path is deployed.

## Exact client

Pin the client and runtime exactly:

```json
{
  "engines": { "node": "22.18.0" },
  "dependencies": { "e2b": "2.38.0" }
}
```

```console
npm install --save-exact e2b@2.38.0
npm ci --ignore-scripts --no-audit --no-fund
```

The selected container runtime is:

```text
docker.io/library/node:22.18.0-bookworm-slim@sha256:752ea8a2f758c34002a0461bd9f1cee4f9a3c36d48494586f60ffce1fc708e0e
```

The Linux/amd64 child manifest is
`sha256:0d130e2ee18e88e1561375276daced6bff032539200173f2daf48c2e33f38ff5`.
The `e2b@2.38.0` npm integrity is:

```text
sha512-l+3Quu3nI+BST9VVynFYiFhXowy7SxivyRKyvNAusrOnjgyTVKVGwi59PPntWLPgPSOkqEhWNM4vcLSg7E/s/A==
```

## Public configuration

Configure the external product server with:

```dotenv
E2B_API_URL=https://api.sandbox.kitdev.ai
E2B_DOMAIN=sandbox.kitdev.ai
E2B_API_KEY=<secret injected at runtime>
E2B_VALIDATE_API_KEY=true
```

Do not set `E2B_SANDBOX_URL` externally. It is a fixed-origin development
override used by the server-side verifier. Do not enable `E2B_DEBUG`; it
bypasses normal lifecycle API calls.

The expected DNS and TLS names are `api.sandbox.kitdev.ai` and
`*.sandbox.kitdev.ai`. The first serves lifecycle calls. Wildcard names such
as `<port>-<sandbox-id>.sandbox.kitdev.ai` route sandbox traffic.

## Credentials

Use a separate project key per product/environment. It has the form `e2b_`
plus 40 lowercase hexadecimal characters. Never commit it, put it in a command
argument, bake it into an image, print an SDK options object, or include it in
telemetry.

Prefer a secret manager or root-owned mode-0600 file:

```ts
import { readFile } from "node:fs/promises";
import type { ConnectionOpts } from "e2b";

const apiKey = (await readFile(process.env.E2B_API_KEY_FILE!, "ascii")).trim();
if (!/^e2b_[0-9a-f]{40}$/.test(apiKey)) throw new Error("invalid E2B API key");

export const e2b: ConnectionOpts = {
  apiKey,
  apiUrl: "https://api.sandbox.kitdev.ai",
  domain: "sandbox.kitdev.ai",
  requestTimeoutMs: 60_000,
};
```

Keep SDK debug and HTTP trace logging disabled around credentials. Sanitize
errors before forwarding them.

## Minimal runnable example

Set `E2B_API_KEY_FILE` to a private mounted key and `E2B_TEMPLATE` to a
template ID or alias:

```ts
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { Sandbox, type ConnectionOpts } from "e2b";

const apiKey = (await readFile(process.env.E2B_API_KEY_FILE!, "ascii")).trim();
const template = process.env.E2B_TEMPLATE;
if (!template) throw new Error("E2B_TEMPLATE is required");

const connection: ConnectionOpts = {
  apiKey,
  apiUrl: "https://api.sandbox.kitdev.ai",
  domain: "sandbox.kitdev.ai",
  requestTimeoutMs: 60_000,
};

let sandbox: Sandbox | undefined;
try {
  sandbox = await Sandbox.create(template, {
    ...connection,
    timeoutMs: 10 * 60_000,
    metadata: { product: "example", request_id: crypto.randomUUID() },
  });
  const result = await sandbox.commands.run("printf hello", { timeoutMs: 30_000 });
  assert.equal(result.exitCode, 0);
  assert.equal(result.stdout, "hello");
} finally {
  if (sandbox) await sandbox.kill().catch(() => false);
}
```

The equivalent server-side loopback flow is live-proven. This public-host flow
remains ingress-dependent.

## Lifecycle and commands

Persist the sandbox ID, not sandbox-scoped tokens. Another worker can reconnect:

```ts
const sandbox = await Sandbox.connect(sandboxId, {
  ...e2b,
  timeoutMs: 8 * 60_000,
});
await sandbox.setTimeout(8 * 60_000);
const info = await sandbox.getInfo();
const running = await sandbox.isRunning();
```

`Sandbox.list({...e2b}).nextItems()` is paginated. Metadata can associate a
sandbox with a request, but it is not a concurrency lock.

Foreground commands return stdout, stderr, and exit code. Background commands
return a handle:

```ts
const handle = await sandbox.commands.run("cat > /home/user/input.txt", {
  background: true,
  stdin: true,
});
await handle.sendStdin("payload");
await handle.closeStdin(); // delivers EOF
await handle.wait();
```

If a newline already let the process exit, `closeStdin()` can correctly return
`NotFoundError`. To transfer ownership, call `handle.disconnect()`, persist its
PID, then use `sandbox.commands.connect(pid)`. Use `commands.list()` for
reconciliation and `commands.kill(pid)` for cleanup.

## PTY

PTY output is bytes; input and resize calls are on `sandbox.pty`:

```ts
const decoder = new TextDecoder();
const pty = await sandbox.pty.create({
  cols: 100,
  rows: 30,
  timeoutMs: 60_000,
  onData: (chunk) => process.stdout.write(decoder.decode(chunk, { stream: true })),
});
await sandbox.pty.resize(pty.pid, { cols: 120, rows: 40 });
await sandbox.pty.sendInput(pty.pid, new TextEncoder().encode("pwd\n"));
```

Use `pty.disconnect()` plus `sandbox.pty.connect(pid, {onData})` to transfer a
session, and `sandbox.pty.kill(pid)` when the user closes it.

## Files and watch

SDK-managed transfer is the proven path:

```ts
await sandbox.files.makeDir("/home/user/app");
await sandbox.files.writeFiles([
  { path: "/home/user/app/config.json", data: JSON.stringify({ mode: "test" }) },
  { path: "/home/user/app/data.bin", data: new Uint8Array([0, 1, 2]).buffer },
]);
const text = await sandbox.files.read("/home/user/app/config.json");
const bytes = await sandbox.files.read("/home/user/app/data.bin", { format: "bytes" });
const stream = await sandbox.files.read("/home/user/app/data.bin", {
  format: "stream",
  streamIdleTimeoutMs: 30_000,
});
```

Always stop watches:

```ts
const watch = await sandbox.files.watchDir("/home/user/app", onEvent, {
  recursive: true,
  includeEntry: true,
  timeoutMs: 60_000,
});
try {
  await runProductWorkflow();
} finally {
  await watch.stop();
}
```

`uploadUrl()` and `downloadUrl()` return URLs for caller-managed HTTP. Their
public route is not proven; use `files.write/read` until wildcard ingress tests
pass.

## Pause and snapshot

There is no public `resume()` method. Connecting resumes a paused sandbox:

```ts
await sandbox.pause({ keepMemory: true });
const resumed = await Sandbox.connect(sandbox.sandboxId, e2b);
```

`keepMemory: true` preserves processes. `keepMemory: false` cold-boots, so
processes and `/tmp` state are lost while durable rootfs files remain.

Snapshot create/list/restore/delete is live-proven:

```ts
const snapshot = await sandbox.createSnapshot({ name: "product-checkpoint" });
const page = await Sandbox.listSnapshots({
  ...e2b,
  sandboxId: sandbox.sandboxId,
  limit: 20,
}).nextItems();

let restored: Sandbox | undefined;
try {
  restored = await Sandbox.create(snapshot.snapshotId, {
    ...e2b,
    timeoutMs: 10 * 60_000,
  });
} finally {
  if (restored) await restored.kill().catch(() => false);
  await Sandbox.deleteSnapshot(snapshot.snapshotId, e2b).catch(() => false);
}
```

Persist the snapshot ID before creating the restored sandbox so cleanup can
find both resources after interruption.

## Guest ports

```ts
await sandbox.commands.run("python3 -m http.server 3000", { background: true });
const url = `https://${sandbox.getHost(3000)}`;
```

Do not ship this path yet. It requires proof of wildcard DNS/TLS, original Host
preservation, guest-port routing, streaming/WebSocket behavior, and traffic
authentication. The SDK exposes `sandbox.trafficAccessToken`, but caller
`fetch()` requests do not automatically receive it.

## Templates

Template SDK source compatibility is selected, but remote builds are not yet
live-proven. Use a separate build-capable key:

```ts
import { Template } from "e2b";

const template = Template()
  .fromNodeImage("22")
  .setWorkdir("/home/user/app")
  .runCmd("npm --version");

const build = await Template.build(template, "my-product:v1", e2b);
```

Do not automate this example against production until its gate is proven.

## Reliability rules

- Bound sandbox lifetime, API requests, commands, watches, and PTYs separately.
- Register each sandbox, process, watch, restored sandbox, and snapshot as soon
  as it is created; clean in reverse order.
- Make cleanup idempotent. A false kill/delete may mean cleanup already ran.
- Use one application lease per sandbox before pause, snapshot, restore, or
  delete. SDK calls do not serialize competing workers.
- Retry bounded read-only calls with jitter. Reconcile list/metadata before
  retrying create or snapshot mutations.
- Disconnect only to transfer ownership; kill when work must stop.
- Sanitize URLs, headers, IDs, and credentials before logging errors.

## Feature status

| Surface | Status | Evidence boundary |
|---|---|---|
| Package/auth and self-host options | Live-proven | Official SDK, loopback API/proxy |
| List/create/connect/info/metrics/timeout/kill | Live-proven | Ubuntu 26.04 OVH lab |
| Commands, stdin/EOF, process list/connect/kill | Live-proven | Isolated SDK sandbox |
| PTY create/input/resize/connect/kill | Live-proven | Isolated SDK sandbox |
| Files CRUD/metadata/read formats/watch | Live-proven | Isolated SDK sandbox |
| Both pause/resume modes | Live-proven | Isolated SDK sandbox |
| Snapshot create/list/restore/delete | Live-proven | Source and restore sandboxes |
| External API from another server | Ingress-dependent | DNS/TLS/public auth unproven |
| Guest ports, streaming, WebSockets | Ingress-dependent | Wildcard proxy unproven |
| Direct URL upload/download | Ingress-dependent | Caller-managed URL unproven |
| Template SDK build | Pending | Source-compatible; live gate incomplete |
| Code/browser/desktop/CDP/screen/input | Pending | Product templates/tests incomplete |
| Persistent volumes | Pending | Volume service not deployed |

See the [live result](research/ovh-typescript-sdk-live-core.md) and
[exact upstream contract](research/e2b-typescript-sdk-self-host-contract.md).

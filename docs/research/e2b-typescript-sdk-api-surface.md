# E2B TypeScript SDK 2.38.0 API surface

Date: 2026-08-08

The exact API surface of the pinned `e2b@2.38.0` client, as used by this
project's runners. Every signature below was read from the installed
`node_modules/e2b/dist/index.d.ts` rather than inferred from documentation or
from the SDK's public website, because the published docs track a moving
version and this deployment is pinned.

This is a reference for writing new runners. The authoritative statements about
what is *deployed and proven* live in
[external HTTPS enablement](external-https-enablement-2026-08-08.md) and the
[SDK integration guide](../typescript-sdk-integration-guide.md); where this
document and those disagree, they win.

Two runner families use the SDK:

| Location | Vantage | Purpose |
| --- | --- | --- |
| `scripts/control-plane/e2e-typescript-sdk/` | on the host, loopback | development qualification gates |
| `scripts/external-sdk-matrix/` | off-host client, public HTTPS | external qualification |

## Pinning and execution model

- `e2b` is pinned exactly (no caret), with `"type": "module"` and
  `engines.node: "22.18.0"`.
- Runners are invoked as bare `node <file>.ts`. There is no bundler, no `tsc`,
  no `tsx`. Node 22.18 strips types natively.
- Local imports must carry an explicit `.ts` extension (`from "./harness.ts"`).
  Type stripping requires it.
- `npm ci --ignore-scripts --no-audit --no-fund` installs from the reviewed
  lockfile; the host runner additionally pins the lockfile's own SHA-256 and
  asserts the installed version equals `2.38.0` before running.

## Client configuration

The runners consume no `E2B_*` environment variables. Every field is built
explicitly. The `E2B_*` names are product-integration guidance for the external
consumer, not the runner contract.

```ts
import { Sandbox, type ConnectionOpts } from "e2b";

const connection: ConnectionOpts = {
  apiKey,                       // /^e2b_[0-9a-f]{40}$/
  apiUrl: "https://api.sandbox.kitdev.ai",
  domain: "sandbox.kitdev.ai",
  requestTimeoutMs: 120_000,
};
```

The object is spread into every call as `{ ...connection, ... }`.

`ConnectionOpts` offers `apiKey`, `validateApiKey`, `accessToken`
(deprecated), `domain`, `apiUrl`, `sandboxUrl`, `debug`, `requestTimeoutMs`
(default 60,000), `logger`, `headers` (deprecated), `proxy`, `apiHeaders`, and
`signal`.

Traps worth knowing:

- **`domain` is a DNS suffix, not a URL.** It supplies both the API suffix and
  the per-port sandbox hostname suffix used by `getHost()`.
- **`apiUrl` must include the scheme** and any non-default port.
- **`apiHeaders` is not a substitute for `apiKey`.** The client checks that
  `apiKey` exists before constructing the HTTP client, so headers alone cannot
  authenticate.
- **`proxy` is an outbound HTTP proxy** for SDK requests. It is not the E2B
  client-proxy origin.
- **`debug` is not verbosity.** When true the SDK *skips* control-plane
  create/connect/kill/timeout calls and talks to a dummy sandbox on localhost,
  bypassing the system under test. Never enable it in a gate.
- **`sandboxUrl` is a development-only fixed-origin override.** The host
  runners set it to the loopback client proxy. The external matrix must not
  set it, and refuses to start if `E2B_SANDBOX_URL` is present, because it
  would bypass the wildcard route being tested.

## Sandbox lifecycle

| Operation | Call |
| --- | --- |
| create | `Sandbox.create(template, { ...connection, metadata, timeoutMs })` |
| create from snapshot | `Sandbox.create(snapshotId, { ...connection, timeoutMs })` |
| create by alias | `Sandbox.create("kitdev-coding:stable", { ... })` |
| list | `await Sandbox.list({ ...connection, limit }).nextItems()` |
| list filtered | `Sandbox.list({ ...connection, query: { state: ["paused"] } })` |
| connect | `await Sandbox.connect(sandboxId, { ...connection, timeoutMs })` |
| info | `await sandbox.getInfo()` or `Sandbox.getInfo(id, connection)` |
| running | `await sandbox.isRunning()` |
| metrics | `await sandbox.getMetrics()` |
| extend | `await sandbox.setTimeout(ms)` |
| destroy | `await sandbox.kill()` |

`Sandbox.list` returns a **paginator**; `.nextItems()` is required.

Sandbox IDs match `/^i[a-z0-9]{20}$/` — the literal prefix `i` plus 20
characters from a lowercase alphanumeric alphabet. The ingress wildcard route
depends on exactly this shape.

`SandboxInfo` carries `sandboxId`, `state` (`"running"` / `"paused"`),
`envdVersion`, `metadata`, `cpuCount`, `memoryMB`, and `endAt` as a `Date`.
`SandboxMetrics` carries `timestamp`, `cpuCount`, `cpuUsedPct`, `memTotal`,
`memUsed`, `memCache`, `diskTotal`, and `diskUsed`.

**There are no `vcpu` or `ram` options on `Sandbox.create`.** CPU and memory
are fixed at template build time. `SandboxOpts` offers `template`, `metadata`,
`envs`, `timeoutMs` (default 300,000), `secure` (default true),
`allowInternetAccess`, `mcp`, `network`, `volumeMounts`, `sandboxUrl`, and
`lifecycle`.

## Commands

```ts
const result = await sandbox.commands.run("printf hi", { timeoutMs: 30_000 });
// result.exitCode / result.stdout / result.stderr
```

**A non-zero exit throws `CommandExitError`; it does not return a result.**
The error implements `CommandResult`, so `exitCode`, `stdout`, `stderr` and
`error` survive on the thrown object. Import the class to assert on it:

```ts
import { CommandExitError } from "e2b";
```

This is easy to miss because every host-side runner only ever executes
commands that succeed. The external matrix asserts the throwing contract
explicitly.

```ts
// background with stdin
const handle = await sandbox.commands.run("cat", { background: true, stdin: true });
handle.pid;
await handle.sendStdin("payload");
await handle.closeStdin();          // delivers EOF
const done = await handle.wait();
await handle.disconnect();          // transfer ownership, do not stop

// reconciliation
const processes = await sandbox.commands.list();       // ProcessInfo[], has .pid
const reattached = await sandbox.commands.connect(pid);
await sandbox.commands.kill(pid);                      // → boolean
```

`CommandStartOpts`: `background`, `cwd`, `user`, `envs`, `onStdout`,
`onStderr`, `stdin`, `timeoutMs`.

## PTY

```ts
const pty = await sandbox.pty.create({ cols, rows, onData, timeoutMs });
await sandbox.pty.resize(pty.pid, { cols, rows });
await sandbox.pty.sendInput(pty.pid, new TextEncoder().encode("pwd\n"));
const reattached = await sandbox.pty.connect(pid, { onData, timeoutMs });
await sandbox.pty.kill(pid);
```

**Callback payload asymmetry:** command `onStdout` / `onStderr` receive
`string`, while PTY `onData` receives `Uint8Array`. Decode PTY output
explicitly.

PTY processes appear in `sandbox.commands.list()`. There is no separate PTY
listing.

## Filesystem

```ts
await sandbox.files.makeDir(path);                            // → boolean
await sandbox.files.write(path, "text");
await sandbox.files.writeFiles([{ path, data }], { metadata });
await sandbox.files.read(path);                               // → string
await sandbox.files.read(path, { format: "bytes" });          // → Uint8Array
await sandbox.files.read(path, { format: "blob" });           // → Blob
await sandbox.files.read(path, { format: "stream", streamIdleTimeoutMs });
await sandbox.files.list(dir);                                // → EntryInfo[]
await sandbox.files.getInfo(path);                            // .type/.size/.metadata/.path
await sandbox.files.exists(path);                             // → boolean
await sandbox.files.rename(from, to);                         // → EntryInfo
await sandbox.files.remove(path);                             // recursive on directories
```

**Binary writes take an `ArrayBuffer`, not a `Uint8Array`.** The accepted union
is `string | ArrayBuffer | Blob | ReadableStream`. Pass `bytes.buffer`. Reads
with `format: "bytes"` return a `Uint8Array`, so a round trip is asymmetric:

```ts
const payload = new Uint8Array([0, 1, 2, 254, 255]);
await sandbox.files.write(path, payload.buffer);              // .buffer going in
const back = await sandbox.files.read(path, { format: "bytes" });
assert.deepEqual(back, payload);                              // Uint8Array coming out
```

Watches must always be stopped:

```ts
import { FileType, FilesystemEventType, type FilesystemEvent } from "e2b";

const watch = await sandbox.files.watchDir(dir, onEvent, {
  includeEntry: true, recursive: true, timeoutMs,
});
try { /* work */ } finally { await watch.stop(); }
```

`FileType` and `FilesystemEventType` are value imports, not type-only imports.

`uploadUrl()` and `downloadUrl()` exist but are exercised by no runner, so
their behavior through the public wildcard route is unverified.

## Guest ports

```ts
const host = sandbox.getHost(3000);        // "3000-<sandboxId>.<sandboxDomain>"
const url = `https://${host}`;
```

`getHost` returns a **host only** — no scheme, no path. The caller builds the
URL.

The `sandboxDomain` returned by the API takes precedence over the configured
`domain`. When `sandboxUrl` is set, the SDK sends envd requests to that fixed
origin with `E2b-Sandbox-Id` and `E2b-Sandbox-Port` headers (envd's guest port
is 49983), but `getHost(port)` still returns the wildcard name — so a fixed
shared origin does not remove the need for wildcard DNS and TLS.

Guest-port routing over the public wildcard is live-proven as of 2026-08-08
for plain HTTP, unbuffered chunked streaming, and WebSocket upgrades. The
wildcard route carries **no authentication of its own**: anyone who learns a
sandbox host name can reach that port. For a sandbox created with a traffic
token, send it as the `e2b-traffic-access-token` header; `sandbox.trafficAccessToken`
is exposed but is never attached to caller `fetch()` automatically. Never
publish a CDP port this way.

## Pause, resume, snapshot

```ts
await sandbox.pause({ keepMemory: true });        // processes survive
await sandbox.pause({ keepMemory: false });       // cold boot; filesystem persists
const resumed = await Sandbox.connect(sandboxId, { ...connection, timeoutMs });
```

**There is no `resume()`.** Connecting to a paused sandbox resumes it. Both
`pause()` and `betaPause()` exist as instance and static methods; the runners
use `pause()`.

Snapshots are a separate mechanism from pause:

```ts
const snapshot = await sandbox.createSnapshot({ name, requestTimeoutMs });
await sandbox.listSnapshots({ limit }).nextItems();
await Sandbox.listSnapshots({ ...connection, limit, sandboxId }).nextItems();
const restored = await Sandbox.create(snapshot.snapshotId, { ...connection });
await Sandbox.deleteSnapshot(snapshot.snapshotId, connection);
```

Restoring is `Sandbox.create` from the snapshot ID. Persist the snapshot ID
before creating the restored sandbox so an interrupted run can still clean up
both resources. Under a one-concurrent-sandbox limit the source must be killed
before the restore is created.

## Templates

`Template()` is a callable factory, not a constructor.

```ts
import { Template } from "e2b";

const template = Template()
  .fromImage(baseImage)                 // or .fromTemplate("name:tag")
  .runCmd(cmd, { user: "root" })
  .copy(["a/package.json"], "/opt/app/", { user: "root" })
  .setEnvs({ HOME: "/home/user" })
  .setWorkdir("/home/user/workspace")
  .setUser("user")
  .setStartCmd(startCommand, readinessProbeCommand);

await Template.exists(name, connection);
await Template.build(template, "name:v1", { ...connection, cpuCount: 2, memoryMB: 2048, tags: ["stable"], onBuildLogs });
await Template.buildInBackground(template, "name:v1", { ...connection });
await Template.getBuildStatus({ templateId, buildId }, { ...connection, logsOffset });
await Template.getTags(templateId, connection);
await Template.assignTags("name:v1", "stable", connection);
await Template.removeTags(name, ["stable"], connection);
```

`BasicBuildOptions` is exactly `alias` (deprecated), `tags`, `cpuCount`
(default 2), `memoryMB` (default 1024), `skipCache`, and `onBuildLogs`.

**There is no `freeDiskSizeMB` build option in 2.38.0.** The browser profile
declares and validates a free-disk figure, but the SDK cannot transmit it;
disk sizing must be arranged out of band. Do not read a passing build as proof
that a requested free-disk value was honoured.

Template IDs match `/^[a-z0-9]{16,32}$/`; build IDs are UUIDs. A build ID is
not a template ID and cannot be passed to `Sandbox.create` as one.

## Conventions worth copying

1. Emit a structured `status=pass operation=<name>` line per check, and on
   failure print only `error.constructor.name` — never the message. That is
   deliberate secret hygiene, since SDK errors can quote request context.
2. Record the sandbox ID durably the instant it exists, before any other work,
   so an external cleanup path can destroy an orphan after an interrupted run.
3. Assert `kill()` returns `true`, in a `finally`, always.
4. Guard cleanup with `isRunning().catch(() => false)` so it stays idempotent.
5. Assert the empty baseline before creating anything, and the empty result
   after cleanup. A gate that never checks for leftovers will not notice that
   it leaks.

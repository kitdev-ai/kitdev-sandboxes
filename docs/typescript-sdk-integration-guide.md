# TypeScript SDK integration guide

This guide is for an AI coding agent integrating software on another server
with Kitdev Sandboxes. It targets the official E2B TypeScript SDK. Treat the
feature-status table as authoritative: SDK source compatibility does not prove
that the public network path is deployed.

The operator must provide four deployment values before integration starts:
the API URL, sandbox domain, project API key, and a published template ID or
alias. Do not guess a template name or substitute a template-build UUID. The
SDK create call accepts a template ID such as the backend `envs.id`, or an
operator-published alias; an `env_builds.id` UUID is not a template ID.

## Current deployment checkpoint

The operator has issued a dedicated project key for the external product on
the exact `kitdev-browser-heavy-team` slug. Its protected source path and
nonsecret revocation handle are:

```text
source key: /etc/kitdev-sandboxes/secrets/external-sdk-product.key
metadata:   /etc/kitdev-sandboxes/secrets/external-sdk-product.key.metadata.json
key ID:     d63b17ec-07cb-4577-b33d-e576b01be5e9
```

The key passed host-local authentication and idempotency checks. It has not
been printed or committed. Public TCP 80 and 443 are still unreachable, so the
key is ready for secure installation but external SDK authentication is not
yet proved. No stable coding or heavy-browser template alias is published;
the operator must supply one after the template publication gate passes.

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
```

Commit both `package.json` and `package-lock.json`. CI and deployment should
then install only from that reviewed lock:

```console
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
E2B_API_KEY_FILE=/etc/my-product/secrets/e2b-api-key
E2B_VALIDATE_API_KEY=true
E2B_TEMPLATE=<operator-published-alias-or-id>
```

`E2B_API_KEY_FILE` is the product application's file-based secret input; the
example below reads it and passes `apiKey` explicitly to the SDK. Do not export
the raw value as `E2B_API_KEY` when a protected file mount is available.

Do not set `E2B_SANDBOX_URL` externally. It is a fixed-origin development
override used by the server-side verifier. Do not enable `E2B_DEBUG`; it
bypasses normal lifecycle API calls.

The expected DNS and TLS names are `api.sandbox.kitdev.ai` and
`*.sandbox.kitdev.ai`. The first serves lifecycle calls. Wildcard names such
as `<port>-<sandbox-id>.sandbox.kitdev.ai` route sandbox traffic.

The repository has proved the equivalent loopback SDK path, but not this
external HTTPS path. Do not deploy a product integration until the operator
confirms that public DNS, TLS, API authentication, ConnectRPC streaming, and
wildcard sandbox routing have passed from a separate client host.

The operator selected unrestricted public TCP 443 as a temporary development
posture after valid TLS is installed. It is not live yet: current probes still
time out on both 80 and 443. Public mode exposes the authenticated API and
wildcard ingress to every Internet source, increasing scanning, brute-force,
and denial-of-service risk. It must keep TCP 80 and every internal port closed,
retain rate limits and monitoring, and be replaced by source-restricted mode
as soon as the product server has a stable public address.

## Credentials

Use a separate project key per product/environment. It has the form `e2b_`
plus 40 lowercase hexadecimal characters. Never commit it, put it in a command
argument, bake it into an image, print an SDK options object, or include it in
telemetry.

Prefer a secret manager. If a file mount is required, make it a regular,
single-link file readable only by the product service identity, normally mode
`0400` or `0600`. Root ownership is appropriate only when the product process
runs as root or a root bootstrapper reads and passes the value without exposing
it to child process arguments or logs.

```ts
import { readFile } from "node:fs/promises";
import type { ConnectionOpts } from "e2b";

const keyFile = process.env.E2B_API_KEY_FILE;
if (!keyFile) throw new Error("E2B_API_KEY_FILE is required");
const apiKey = (await readFile(keyFile, "ascii")).trim();
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

Ask the bare-metal operator to issue the product key with the host-local
`kitdev api-key create` command and assign its final file to the product service
identity. The operator must transfer or mount that file over a separately
secured channel; the create command never prints the raw key. Record the
nonsecret key ID from the operator-managed metadata so rotation and incident
response can revoke the exact credential. The product integration must not
receive the control-plane admin token or its private environment file.

After deploying a replacement key and proving it with `kitdev api-key verify`,
the operator revokes the old key using its exact key ID and confirmation ID.
Revocation invalidates upstream authentication immediately. Treat a retained
local key file as a residual secret until the operator uses the metadata-bound
`--delete-key-file` flow or removes it under an equivalent controlled process.

### Secure installation on the product server

A managed secret store with audited delivery is preferred. When one is not
available, run the following once on the product server from a trusted
administrator session. Replace the SSH host and service identity placeholders.
The source SSH host key must already be independently verified. Disable shell
tracing and do not run this through a job system that captures pipeline data.

```console
set +x
set -euo pipefail
service_user='replace-with-product-service-user'
service_group='replace-with-product-service-group'
secret_dir=/etc/my-product/secrets
destination=$secret_dir/e2b-api-key

sudo install -d -o root -g "$service_group" -m 0750 "$secret_dir"
sudo test ! -e "$destination"
temporary="$(sudo mktemp -p "$secret_dir" .e2b-api-key.XXXXXX)"
trap 'sudo rm -f -- "$temporary"' EXIT

ssh -T sandbox-operator-ssh-host \
  'sudo -n dd if=/etc/kitdev-sandboxes/secrets/external-sdk-product.key iflag=nofollow status=none' \
  | sudo tee "$temporary" >/dev/null

sudo grep -Eq '^e2b_[0-9a-f]{40}$' "$temporary"
sudo chown "$service_user:$service_group" "$temporary"
sudo chmod 0400 "$temporary"
sudo mv -T "$temporary" "$destination"
trap - EXIT
sudo stat -c 'type=%F owner=%U:%G mode=%a links=%h size=%s' "$destination"
```

The final stat must report a regular file, the intended service identity, mode
`400`, link count one, and size 45. The pipeline directs the raw bytes from one
SSH channel into the protected temporary file; it never writes them to the
terminal, command arguments, shell variables, or an intermediate unprotected
copy. Remove the product server's ability to read the source host after the
one-time transfer if it is not needed operationally.

Configure only the nonsecret URLs and the destination filename in the product
service environment. Restart the service, then confirm through its service
manager that it runs as the intended identity. Do not inspect the environment
with commands that dump all process variables, and never use `cat`, clipboard,
chat, or a command-line `--api-key` value to move the credential.

### Authentication-only smoke test

This smoke test requires public TLS/API reachability but does not require a
template or create a sandbox. Save it as `sdk-auth-smoke.ts` in the product
repository and run it with the pinned Node 22.18.0 environment after
`npm ci`:

```ts
import { readFile } from "node:fs/promises";
import { Sandbox, type ConnectionOpts } from "e2b";

const keyFile = process.env.E2B_API_KEY_FILE;
if (!keyFile) throw new Error("E2B_API_KEY_FILE is required");
const apiKey = (await readFile(keyFile, "ascii")).trim();
if (!/^e2b_[0-9a-f]{40}$/.test(apiKey)) throw new Error("invalid E2B API key");

const connection: ConnectionOpts = {
  apiKey,
  apiUrl: "https://api.sandbox.kitdev.ai",
  domain: "sandbox.kitdev.ai",
  requestTimeoutMs: 30_000,
};

const page = await Sandbox.list(connection).nextItems();
console.log(JSON.stringify({ status: "authenticated", visible: page.length }));
```

```console
sudo -u replace-with-product-service-user \
  env E2B_API_KEY_FILE=/etc/my-product/secrets/e2b-api-key \
  node sdk-auth-smoke.ts
```

Do not log the connection object or caught HTTP headers. A connection timeout
before ingress is deployed is expected and is not an authentication result.
After a stable `E2B_TEMPLATE` is supplied, run the create/command/kill example
below as the first sandbox mutation.

### Rotation and revocation

On the sandbox host, create a replacement at a new path and label. Never
overwrite the active file or its metadata:

```console
sudo ./kitdev api-key create --lifecycle-mode development \
  --team-slug kitdev-browser-heavy-team \
  --name external-sdk-product-next \
  --output /etc/kitdev-sandboxes/secrets/external-sdk-product-next.key \
  --private-env-file /etc/kitdev-sandboxes/e2b-lab.env
sudo ./kitdev api-key verify --lifecycle-mode development \
  --key-file /etc/kitdev-sandboxes/secrets/external-sdk-product-next.key \
  --metadata-file /etc/kitdev-sandboxes/secrets/external-sdk-product-next.key.metadata.json
```

Securely install the replacement, restart the product, and prove the external
authentication smoke before revoking the old key. For a file-based rotation,
change the transfer's source to `external-sdk-product-next.key` and destination
to `e2b-api-key.next`; run the smoke against that path, atomically replace the
service's `e2b-api-key`, restart it, and smoke again. Then use the exact old ID
in both confirmation fields:

```console
sudo ./kitdev api-key revoke --lifecycle-mode development \
  --team-slug kitdev-browser-heavy-team \
  --key-id d63b17ec-07cb-4577-b33d-e576b01be5e9 \
  --confirm-key-id d63b17ec-07cb-4577-b33d-e576b01be5e9 \
  --metadata-file /etc/kitdev-sandboxes/secrets/external-sdk-product.key.metadata.json \
  --delete-key-file \
  --private-env-file /etc/kitdev-sandboxes/e2b-lab.env
```

Retain the revoked metadata as the nonsecret audit record. Remove the obsolete
product-server copy through its secret-management process and verify that the
replacement still authenticates. A revoke cannot be undone; recovery is key
replacement, not restoration of the old value.

## Minimal runnable example

Set `E2B_API_KEY_FILE` to a private mounted key and `E2B_TEMPLATE` to a
template ID or alias:

```ts
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";
import { Sandbox, type ConnectionOpts } from "e2b";

const keyFile = process.env.E2B_API_KEY_FILE;
if (!keyFile) throw new Error("E2B_API_KEY_FILE is required");
const apiKey = (await readFile(keyFile, "ascii")).trim();
if (!/^e2b_[0-9a-f]{40}$/.test(apiKey)) throw new Error("invalid E2B API key");
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
    metadata: { product: "example", request_id: randomUUID() },
  });
  const result = await sandbox.commands.run("printf hello", { timeoutMs: 30_000 });
  assert.equal(result.exitCode, 0);
  assert.equal(result.stdout, "hello");
} finally {
  if (sandbox) await sandbox.kill();
}
```

The equivalent server-side loopback flow is live-proven. This public-host flow
remains ingress-dependent. `E2B_TEMPLATE` must be trusted deployment
configuration supplied by the operator, not an arbitrary end-user value.

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
  if (restored) await restored.kill();
  await Sandbox.deleteSnapshot(snapshot.snapshotId, e2b);
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

Template builds are live-proven through the server-side loopback API with the
official SDK and local template manager. This includes background and blocking
builds, status polling, alias existence, tag assignment/removal, and sandbox
creation from both resulting tags. Use a dedicated team key for build
automation so the operator can revoke it independently:

```ts
import { Template } from "e2b";

const template = Template()
  .fromNodeImage("22")
  .setWorkdir("/home/user/app")
  .runCmd("npm --version");

const build = await Template.build(template, "my-product:v1", e2b);
```

The backend project key is team-scoped, not restricted to template builds.
Using a distinct key gives the operator an independent revocation handle; it
does not create least-privilege build permissions. Keep template building in a
trusted deployment workflow and never expose it to product users directly.

The external HTTPS path from another server remains ingress-dependent; do not
automate remote production builds until that public path passes its own gate.

## Coding template

A pinned non-graphical coding template has been live-proven through the
official SDK. Its tested contract includes an unprivileged `user` account and
workspace, Node.js 22.18.0, npm 10.9.3, Git, Python, GCC, Make, SDK-managed
files, shell commands, and PTYs. That test creates a unique template and
deletes its alias afterward; it does not publish a stable production alias.

Ask the operator for the deployed coding template ID or alias. Do not assume
that `base`, `coding`, or the test name is available. Keep the project API key
on the product server: never put it, product credentials, or tenant secrets in
sandbox environment variables or files unless the product has an explicit
per-sandbox secret policy and cleanup contract.

## Browser template

A 2 GiB Chromium template has also passed its complete server-side
qualification gate. The official SDK created the sandbox; its non-root browser
reached loopback CDP readiness; Playwright performed local navigation and DOM
interaction; and the files SDK collected exact screenshot and download
artifacts. Sandbox, API, Redis, and Firecracker cleanup passed afterward.

This proves a qualification template, not a published product alias. The gate
deletes its unique alias, covers Chromium only, and keeps CDP on
`127.0.0.1:9222` inside the guest. Ask the operator for a separately published
browser template contract before writing product behavior. Do not expose CDP
with `getHost(9222)` or assume arbitrary public browser ports work; authenticated
wildcard ingress remains unproven. See the
[browser qualification guide](browser-sandbox-guide.md) for the exact boundary.

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
| Dedicated external product key | Live-proven (host-local) | Active heavy-team key, masked listing, exact ID; public use pending |
| List/create/connect/info/metrics/timeout/kill | Live-proven | Ubuntu 26.04 OVH lab |
| Commands, stdin/EOF, process list/connect/kill | Live-proven | Isolated SDK sandbox |
| PTY create/input/resize/connect/kill | Live-proven | Isolated SDK sandbox |
| Files CRUD/metadata/read formats/watch | Live-proven | Isolated SDK sandbox |
| Both pause/resume modes | Live-proven | Isolated SDK sandbox |
| Snapshot create/list/restore/delete | Live-proven | Source and restore sandboxes |
| External API from another server | Ingress-dependent | DNS/TLS/public auth unproven |
| Guest ports, streaming, WebSockets | Ingress-dependent | Wildcard proxy unproven |
| Direct URL upload/download | Ingress-dependent | Caller-managed URL unproven |
| Template SDK build/status/exists/tags | Live-proven | Official SDK, loopback API/template manager |
| Coding template toolchain/files/commands/PTY | Live-proven | Ephemeral official-SDK build and sandbox; stable deployment alias pending |
| Browser/CDP/Playwright | Live-proven (loopback) | Ephemeral Chromium qualification template; stable alias and public CDP pending |
| Desktop/stream/screen/input | Pending | Product template and live test incomplete |
| Persistent volumes | Pending | Volume service not deployed |

See the [live result](research/ovh-typescript-sdk-live-core.md),
[external product-key result](research/external-sdk-product-key-qualification-2026-08-07.md),
[template-build result](research/ovh-typescript-sdk-template-build.md),
[coding-template result](research/coding-template-contract.md),
[browser-template result](research/browser-template-contract.md), and
[exact upstream contract](research/e2b-typescript-sdk-self-host-contract.md).

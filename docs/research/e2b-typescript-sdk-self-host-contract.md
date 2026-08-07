# E2B TypeScript SDK self-host contract

Date: 2026-08-07

Scope: package selection and exact public SDK behavior needed for the
single-host E2B compatibility tests. This note uses only official E2B source,
official E2B documentation, and npm registry metadata. It records the selected
public domain but contains no runtime identifiers or credentials.

## Decision

Pin the JavaScript/TypeScript package as:

```json
{
  "dependencies": {
    "e2b": "2.38.0"
  }
}
```

Use Node.js `>=20.18.1 <21 || >=22`, which is the package's declared engine
range. Do not use a caret range for the compatibility verifier: its purpose is
to test one immutable client contract.

For the reproducible verifier, use the exact runtime selected by the official
SDK repository:

```text
Node.js 22.18.0
docker.io/library/node:22.18.0-bookworm-slim@sha256:0d130e2ee18e88e1561375276daced6bff032539200173f2daf48c2e33f38ff5
```

That digest is the Linux/amd64 manifest, which is appropriate for the OVH
host. The corresponding multi-platform index digest is
`sha256:752ea8a2f758c34002a0461bd9f1cee4f9a3c36d48494586f60ffce1fc708e0e`.
The downloaded npm tarball is 504,322 bytes and has SHA-256
`417e95ea4515752be30fa81d1c54ad6c73bde4da5bd6e11a4e5462229fd3a793`.
An installation gate should require `npm ci`, verify the lockfile resolves
exactly `e2b@2.38.0`, and compare npm's SHA-512 integrity before executing it.

As retrieved on 2026-08-07, npm's `latest` dist-tag is `2.38.0`. The official
registry records:

| Field | Value |
|---|---|
| Package | `e2b` |
| Version | `2.38.0` |
| npm SHA-512 integrity | `sha512-l+3Quu3nI+BST9VVynFYiFhXowy7SxivyRKyvNAusrOnjgyTVKVGwi59PPntWLPgPSOkqEhWNM4vcLSg7E/s/A==` |
| npm SHA-1 | `a79bf44c1979daa38bfec8d7781f8b3c16359def` |
| Source tag | `e2b@2.38.0` |
| Peeled tag/release commit | `7a1fe4528cb29ccea0334adbee4dc86fadb7244d` |
| npm provenance source commit | `2821fb0b695b183f0011a8f73cf72adb4917e58f` |

The npm SLSA attestation says the package was published by E2B's official
`.github/workflows/release.yml` workflow from provenance commit `2821fb0...`.
At that commit the source manifest still says `2.37.0`; the release workflow
sets the published version. Comparing it with release commit `7a1fe45...`
shows that the JavaScript SDK differs only in `package.json` version and the
changelog. The runtime TypeScript source is identical. The release commit is
therefore an appropriate readable source pin, while the registry integrity and
provenance commit pin the actual package artifact.

The SDK release records infra spec ref
`24a054bca26ec50a6d59031d9360c1582612b3f8`. Repository research already
compared its public API and envd OpenAPI documents with backend candidate
`882a3b4786755db9e94be3297de6827f9100ce5e`; both SHA-256 comparisons are exact.
Thus `e2b@2.38.0` is the selected source-contract-compatible TypeScript client
for the pinned backend. Runtime behavior still has to be tested; source
contract equality alone does not prove the deployment topology or proxy.

Primary sources:

- [Official npm version metadata](https://registry.npmjs.org/e2b/2.38.0)
- [Official npm package metadata and dist-tags](https://registry.npmjs.org/e2b)
- [Official npm attestations](https://registry.npmjs.org/-/npm/v1/attestations/e2b@2.38.0)
- [Pinned SDK package manifest](https://github.com/e2b-dev/e2b/blob/7a1fe4528cb29ccea0334adbee4dc86fadb7244d/packages/js-sdk/package.json)
- [Pinned SDK infra ref](https://github.com/e2b-dev/e2b/blob/7a1fe4528cb29ccea0334adbee4dc86fadb7244d/spec/infra-ref)
- [Pinned backend OpenAPI](https://github.com/e2b-dev/infra/blob/882a3b4786755db9e94be3297de6827f9100ce5e/spec/openapi.yml)
- [Official SDK runtime pin](https://github.com/e2b-dev/e2b/blob/7a1fe4528cb29ccea0334adbee4dc86fadb7244d/.tool-versions)
- [Official Node Docker image source](https://github.com/nodejs/docker-node/tree/de1c8c994e1bf8a5843ff7d4d987eee0cad69243/22/bookworm-slim)

## Exact self-host configuration

### Environment variables

The SDK's exact configuration precedence is constructor/method options first,
then environment variables, then defaults. Use the following shape for a
single-host deployment; values below are placeholders rather than deployed
addresses or secrets:

```dotenv
E2B_API_KEY=<project-api-key>
E2B_VALIDATE_API_KEY=true
E2B_API_URL=https://api.sandbox.kitdev.ai
E2B_DOMAIN=sandbox.kitdev.ai
```

Do not set `E2B_SANDBOX_URL` in the normal externally reachable deployment.
It is a development-only escape hatch for a fixed client-proxy origin.

Semantics:

| Variable | Exact SDK behavior | Single-host requirement |
|---|---|---|
| `E2B_API_KEY` | Required by `Sandbox` and `Template` API clients. Sent as `X-API-KEY`. | Set to the project API key. Never put it in the repository or command output. |
| `E2B_VALIDATE_API_KEY` | Defaults to true. Validation accepts only `^e2b_[0-9a-f]+$`. | Keep it true (or omit it). The pinned backend's official admin route generates `e2b_` plus 40 lowercase hexadecimal characters. |
| `E2B_API_URL` | Overrides the control API base URL. Default is `https://api.${E2B_DOMAIN}`. | Set it when the API is not exposed at the conventional `api.<domain>` origin. Include scheme and any nondefault port. |
| `E2B_SANDBOX_URL` | Overrides the base URL used for envd HTTP/ConnectRPC and direct file URLs. | Omit it for the external `sandbox.kitdev.ai` deployment. In private tests, use an IP/localhost or `sandbox.<domain>` origin so the official proxy accepts routing headers. |
| `E2B_DOMAIN` | Defaults to `e2b.app`; otherwise supplies the API suffix and per-port sandbox hostname suffix. | Set to the wildcard sandbox DNS suffix for conventional public routing and `getHost()`. It is a DNS suffix, not a URL. |
| `E2B_DEBUG` | When true, skips control-plane create/connect/kill/timeout calls and uses a dummy sandbox ID plus localhost envd. | Do not enable it for self-host validation; it does not mean "verbose" and would bypass the system under test. |
| `E2B_ACCESS_TOKEN` | Deprecated; if present, sent as an API `Authorization: Bearer ...` header. | Do not use for ordinary project API-key flows. `apiHeaders` is the replacement for explicit extra control API headers. |

Equivalent explicit options exist on `SandboxOpts`, `SandboxConnectOpts`, and
template `BuildOptions`: `apiKey`, `validateApiKey`, `apiUrl`, `sandboxUrl`,
`domain`, `requestTimeoutMs`, `apiHeaders`, `signal`, and `proxy`. `proxy` means
an outbound HTTP proxy for SDK requests; it is not the E2B client-proxy origin.

An `apiHeaders` value alone cannot replace `apiKey` for these high-level APIs:
`ApiClient` checks that `config.apiKey` exists before it creates the HTTP
client. Supply `apiKey`, and use `validateApiKey: false` only when its local
format differs.

Primary sources:

- [Connection options and environment resolution](https://github.com/e2b-dev/e2b/blob/7a1fe4528cb29ccea0334adbee4dc86fadb7244d/packages/js-sdk/src/connectionConfig.ts)
- [Control API client authentication](https://github.com/e2b-dev/e2b/blob/7a1fe4528cb29ccea0334adbee4dc86fadb7244d/packages/js-sdk/src/api/index.ts)
- [Official pinned self-host guide SDK domain example](https://github.com/e2b-dev/infra/blob/882a3b4786755db9e94be3297de6827f9100ce5e/self-host.md#sdk)

### Sandbox proxy routing and authentication

Without `E2B_SANDBOX_URL`, a self-hosted domain uses:

```text
https://<guest-port>-<sandbox-id>.<sandbox-domain>
```

Envd uses guest port `49983`. `Sandbox.getHost(port)` returns only the host and
optional port, not a URL or scheme. Callers normally construct
`https://${sandbox.getHost(port)}`.

When `E2B_SANDBOX_URL` is set, the SDK sends envd requests to that fixed origin
and adds:

```text
E2b-Sandbox-Id: <sandbox-id>
E2b-Sandbox-Port: 49983
```

For a secure sandbox, the API returns a sandbox-scoped envd access token. The
SDK automatically adds it as `X-Access-Token` to envd REST and ConnectRPC
requests. Per-operation Unix user selection is a separate envd authorization
header produced by the SDK. The API may also return `trafficAccessToken`; the
SDK exposes that property for callers accessing restricted arbitrary guest
services, but it does not automatically attach it to caller-created `fetch`
requests.

Important limitation: `E2B_SANDBOX_URL` changes the SDK's envd and direct-file
origins, but `getHost(port)` still returns
`<port>-<sandbox-id>.<sandbox-domain>`. SDK-managed command and filesystem
requests include routing headers. A URL returned by `uploadUrl()` or
`downloadUrl()` is later fetched by caller code, so it does not automatically
inherit those SDK headers. The conventional external setup therefore requires
wildcard DNS/TLS even for direct URL helpers. A fixed shared origin works for a
caller fetch only when the caller also supplies both routing headers.

The API response's sandbox domain takes precedence for a created/connected
sandbox; `E2B_DOMAIN` is the fallback. The backend and ingress therefore need
to agree on the advertised sandbox suffix.

Primary sources:

- [Pinned sandbox constructor, routing headers, and access token](https://github.com/e2b-dev/e2b/blob/7a1fe4528cb29ccea0334adbee4dc86fadb7244d/packages/js-sdk/src/sandbox/index.ts)
- [Pinned envd HTTP authentication](https://github.com/e2b-dev/e2b/blob/7a1fe4528cb29ccea0334adbee4dc86fadb7244d/packages/js-sdk/src/envd/api.ts)
- [Official create API authentication/response](https://e2b.dev/docs/api-reference/sandboxes/create-sandbox)
- [Official envd file authentication](https://e2b.dev/docs/api-reference/filesystem/download-a-file)

### Project API-key lifecycle

The pinned backend has an official administrator route that produces exactly
the key format expected by `e2b@2.38.0`:

```http
POST /admin/teams/<team-uuid>/api-keys
X-Admin-Token: <root-admin-token>
Content-Type: application/json

{"name":"kitdev-sdk-runner"}
```

A `201` response contains `key`, `id`, `name`, masking details, and timestamps.
The raw `key` is returned only by this creation response. The generator reads
20 cryptographically random bytes and hex-encodes them, producing
`e2b_` followed by 40 lowercase hexadecimal characters. The database stores a
SHA-256-derived hash and mask metadata, not the raw value.

The provisioning script must capture that one response without printing it,
validate the key shape, and atomically install a root-owned mode-`0600` secret
file. A rerun must authenticate with the existing key rather than mint another
one. Store the returned key ID separately so explicit revocation is possible:

```http
DELETE /admin/teams/<team-uuid>/api-keys/<api-key-uuid>
X-Admin-Token: <root-admin-token>
```

Success is `204` with an empty body. Deletion is scoped by both team and key ID
and immediately invalidates the API authentication cache. Never put either the
administrator token or project key in systemd unit text, repository files,
shell history, process arguments, test logs, or retained HTTP responses.

Primary sources:

- [Pinned admin API-key OpenAPI routes](https://github.com/e2b-dev/infra/blob/882a3b4786755db9e94be3297de6827f9100ce5e/spec/openapi.yml)
- [Pinned create/revoke handlers](https://github.com/e2b-dev/infra/blob/882a3b4786755db9e94be3297de6827f9100ce5e/packages/api/internal/handlers/admin_api_keys.go)
- [Pinned team API-key lifecycle](https://github.com/e2b-dev/infra/blob/882a3b4786755db9e94be3297de6827f9100ce5e/packages/api/internal/team/apikeys.go)
- [Pinned cryptographic key generator](https://github.com/e2b-dev/infra/blob/882a3b4786755db9e94be3297de6827f9100ce5e/packages/shared/pkg/keys/key.go)

### `sandbox.kitdev.ai` external ingress contract

The selected public SDK configuration is:

```dotenv
E2B_API_URL=https://api.sandbox.kitdev.ai
E2B_DOMAIN=sandbox.kitdev.ai
# E2B_SANDBOX_URL intentionally unset
```

Public DNS needs `api.sandbox.kitdev.ai` and `*.sandbox.kitdev.ai` to resolve to
the server. The TLS ingress needs two precedence-ordered routes:

| Public host | Upstream | Purpose |
|---|---|---|
| `api.sandbox.kitdev.ai` | loopback API REST listener, currently `127.0.0.1:3000` | SDK lifecycle, template, snapshot, and metadata calls |
| `*.sandbox.kitdev.ai` fallback | loopback client-proxy listener, currently `127.0.0.1:3002` | Envd on `49983` and arbitrary guest ports |

The wildcard route must preserve the original `Host`, request bodies, response
streaming, WebSocket upgrade headers, and the two E2B routing headers. Disable
request/response buffering for streaming operations and set upstream idle/read
timeouts above the pinned client-proxy's 610-second idle timeout. Do not expose
client-proxy health `3003`, API internal gRPC, orchestrator proxy `5007`, or
orchestrator service `5008` to the Internet.

The official parser accepts two routing forms:

1. Normal wildcard host: `<port>-<sandbox-id>.sandbox.kitdev.ai`. It reads only
   the leftmost label, interprets the first hyphen-delimited component as the
   port and the second as the sandbox ID, then validates that ID.
2. Shared host: `sandbox.sandbox.kitdev.ai` with both `E2b-Sandbox-Id` and
   `E2b-Sandbox-Port`. The doubled label is intentional: the official proxy
   prepends `sandbox.` to the configured SDK suffix. Header routing is also
   accepted for localhost and IP hosts. Other named hosts do not opt into
   header parsing.

The API returns a sandbox-specific domain only for a team assigned to a
configured cluster whose node advertises one. The current single-host default
can return no domain; in that case the SDK correctly falls back to
`E2B_DOMAIN=sandbox.kitdev.ai`. This means cluster domain advertisement is not
required for the first external gate, but any future non-null advertised value
must equal the actual ingress suffix.

Primary sources:

- [Pinned host and routing-header parser](https://github.com/e2b-dev/infra/blob/882a3b4786755db9e94be3297de6827f9100ce5e/packages/shared/pkg/proxy/host.go)
- [Pinned parser tests](https://github.com/e2b-dev/infra/blob/882a3b4786755db9e94be3297de6827f9100ce5e/packages/shared/pkg/proxy/host_test.go)
- [Pinned client-proxy implementation](https://github.com/e2b-dev/infra/blob/882a3b4786755db9e94be3297de6827f9100ce5e/packages/client-proxy/internal/proxy/proxy.go)
- [Official upstream API route priority](https://github.com/e2b-dev/infra/blob/882a3b4786755db9e94be3297de6827f9100ce5e/iac/modules/job-api/jobs/api.hcl)
- [Official upstream sandbox fallback route](https://github.com/e2b-dev/infra/blob/882a3b4786755db9e94be3297de6827f9100ce5e/iac/modules/job-client-proxy/jobs/client-proxy.hcl)

### Wildcard TLS operational contract

Request a certificate containing both `sandbox.kitdev.ai` and
`*.sandbox.kitdev.ai`. The delegated-zone apex is a separate certificate
identifier; the wildcard covers exactly one label, including
`api.sandbox.kitdev.ai` and
`<port>-<sandbox-id>.sandbox.kitdev.ai`, but it does not cover
`sandbox.kitdev.ai` itself or a two-level name under it.

Let's Encrypt wildcard issuance requires DNS-01. The ACME client must create
the required TXT value at `_acme-challenge.sandbox.kitdev.ai`, wait for
authoritative DNS propagation, complete validation, and clean old challenge
values. Renewal must be unattended and deploy/reload the ingress only after a
new certificate has been issued successfully. The DNS provider API credential
must be a separate root-only secret with the narrowest available permission for
changing challenge records; it must never enter the repository or service
logs.

The DNS provider has not yet been recorded, so a provider-specific ACME plugin
cannot be selected reproducibly. Treat `DNS_PROVIDER` plus its least-privilege
credential file as a required deployment input and fail before mutation when
the adapter is missing. Test the complete issuance and reload path against the
Let's Encrypt staging directory before production. If the zone has CAA, permit
`letsencrypt.org`; it may be restricted to `dns-01`, and wildcard-specific
policy uses `issuewild`.

Primary sources:

- [Let's Encrypt challenge types](https://letsencrypt.org/docs/challenge-types/)
- [Let's Encrypt staging environment](https://letsencrypt.org/docs/staging-environment/)
- [Let's Encrypt integration guide](https://letsencrypt.org/docs/integration-guide/)
- [Let's Encrypt CAA documentation](https://letsencrypt.org/docs/caa/)

## Public SDK surface to test

### Create, connect, pause, and timeout

Exact high-level calls:

```ts
const sandbox = await Sandbox.create('<template-name-or-id>', {
  timeoutMs: 300_000,
  secure: true,
})

await sandbox.setTimeout(600_000)
await sandbox.pause()                       // full-memory pause
await sandbox.pause({ keepMemory: false }) // filesystem-only pause
const resumed = await Sandbox.connect(sandbox.sandboxId, {
  timeoutMs: 300_000,
})
```

There is no public `Sandbox.resume()` method in `2.38.0`. Both static
`Sandbox.connect(sandboxId, opts)` and instance `sandbox.connect(opts)` call
`POST /sandboxes/{sandboxID}/connect`; that endpoint returns an already-running
sandbox or resumes a paused one. Use the static form after process restart
because it creates a fresh SDK object with the current envd token.

`pause()` defaults to `keepMemory: true`; `false` preserves only the filesystem
and a later connect cold-boots, so running processes and open connections are
lost. `Sandbox.create()` defaults to the `base` template, a 300,000 ms sandbox
timeout, `secure: true`, and internet access enabled. Creation can instead use
the object form `Sandbox.create({ template, metadata, envs, timeoutMs, secure,
allowInternetAccess, network, lifecycle })`.

Three timeout concepts must not be confused:

| Timeout | Option | Default |
|---|---|---:|
| Sandbox lifetime | `Sandbox.create(..., { timeoutMs })`, `connect`, `setTimeout` | 300,000 ms for create/connect |
| Ordinary API/request handshake | `requestTimeoutMs` | 60,000 ms |
| Long-lived command, PTY, or watch operation | operation `timeoutMs` | 60,000 ms |

`requestTimeoutMs: 0` disables the request timeout. Long-lived operations also
accept `timeoutMs: 0` where documented. An `AbortSignal` can independently
cancel requests.

Lifecycle creation options support `onTimeout: 'kill' | 'pause'` and
`autoResume`. Auto-resume is valid only with pause and a memory-bearing
snapshot; filesystem-only pause must be resumed explicitly with `connect()`.

Primary sources:

- [Pinned Sandbox class](https://github.com/e2b-dev/e2b/blob/7a1fe4528cb29ccea0334adbee4dc86fadb7244d/packages/js-sdk/src/sandbox/index.ts)
- [Pinned sandbox option types and lifecycle API calls](https://github.com/e2b-dev/e2b/blob/7a1fe4528cb29ccea0334adbee4dc86fadb7244d/packages/js-sdk/src/sandbox/sandboxApi.ts)
- [Official connect API contract](https://e2b.dev/docs/api-reference/sandboxes/connect-to-sandbox)
- [Official auto-resume guide](https://e2b.dev/docs/sandbox/auto-resume)

### Commands

Core command coverage is:

```ts
const result = await sandbox.commands.run('printf sdk-ok')
const handle = await sandbox.commands.run('long-command', {
  background: true,
  stdin: true,
  cwd: '/workspace',
  user: 'user',
  envs: { TEST_MODE: '1' },
  onStdout: (data) => {},
  onStderr: (data) => {},
  timeoutMs: 0,
})
await handle.sendStdin('input\n')
await handle.closeStdin()
const final = await handle.wait()
```

`commands.run()` returns `CommandResult` when `background` is false/omitted
and `CommandHandle` when true. The module also exposes `list()`, `connect(pid)`,
`sendStdin(pid, data)`, `closeStdin(pid)`, and `kill(pid)`. `CommandResult`
contains `exitCode`, `stdout`, and `stderr`; waiting on a nonzero exit throws a
`CommandExitError` carrying those same fields. The SDK invokes commands through
`/bin/bash -l -c` and uses ConnectRPC streaming for output.

Primary source: [Pinned command module](https://github.com/e2b-dev/e2b/blob/7a1fe4528cb29ccea0334adbee4dc86fadb7244d/packages/js-sdk/src/sandbox/commands/index.ts).

### Files, upload/download, and watch

File content calls are:

```ts
await sandbox.files.write('/workspace/data.bin', data)
await sandbox.files.write([
  { path: '/workspace/a.txt', data: 'a' },
  { path: '/workspace/b.txt', data: 'b' },
])

const text = await sandbox.files.read('/workspace/a.txt')
const bytes = await sandbox.files.read('/workspace/data.bin', {
  format: 'bytes',
})
const stream = await sandbox.files.read('/workspace/data.bin', {
  format: 'stream',
  streamIdleTimeoutMs: 60_000,
})
```

`write` accepts `string`, `ArrayBuffer`, `Blob`, or `ReadableStream`; `read`
returns text by default and supports `bytes`, `blob`, or `stream`. `files`
also supplies `list`, `makeDir`, `rename`, `remove`, `exists`, and `getInfo`.
There is no local-path convenience method: Node callers read/write local files
themselves and pass the bytes to `files.write` or save the return from
`files.read`.

For direct HTTP transfer, `sandbox.uploadUrl(path?, opts)` and
`sandbox.downloadUrl(path, opts)` return URLs. Secure sandboxes get a signature
query parameter derived from the envd access token; optional
`useSignatureExpiration` is in seconds. The upload URL expects an HTTP POST
with multipart form data. These URL helpers are distinct from `files.write`
and `files.read`.

Watch coverage is:

```ts
const watch = await sandbox.files.watchDir('/workspace', onEvent, {
  recursive: true,
  includeEntry: true,
  timeoutMs: 0,
  onExit: (err) => {},
})
await watch.stop()
```

The pinned envd `0.6.13` exceeds the SDK gates for recursive watch (`0.1.4`),
entry info (`0.6.3`), and network-mount watch (`0.6.4`). The verifier should
still use a local directory first; network-mount watch is explicitly
best-effort.

Primary sources:

- [Pinned filesystem module](https://github.com/e2b-dev/e2b/blob/7a1fe4528cb29ccea0334adbee4dc86fadb7244d/packages/js-sdk/src/sandbox/filesystem/index.ts)
- [Pinned watch handle](https://github.com/e2b-dev/e2b/blob/7a1fe4528cb29ccea0334adbee4dc86fadb7244d/packages/js-sdk/src/sandbox/filesystem/watchHandle.ts)
- [Official upload/download guide](https://e2b.dev/docs/quickstart/upload-download-files)

### PTY

Exact PTY operations are:

```ts
const pty = await sandbox.pty.create({
  cols: 100,
  rows: 30,
  onData: (chunk) => {},
  timeoutMs: 0,
})
await sandbox.pty.sendInput(pty.pid, new TextEncoder().encode('printf pty-ok\n'))
await sandbox.pty.resize(pty.pid, { cols: 120, rows: 40 })
await sandbox.pty.kill(pty.pid)
```

The PTY module also supports `connect(pid, opts)`, `sendInput(pid, data)`, and
returns a `CommandHandle` from `create()`. Creation starts interactive login
bash and defaults `TERM=xterm-256color`, `LANG=C.UTF-8`, and `LC_ALL=C.UTF-8`.
Unlike a command handle, the PTY handle has no bound stdin sender; input must go
through `sandbox.pty.sendInput(pid, Uint8Array)`.
The exact option set includes `cols`, `rows`, `onData`, `timeoutMs`, `user`,
`envs`, `cwd`, `requestTimeoutMs`, and `signal`.

Primary source: [Pinned PTY module](https://github.com/e2b-dev/e2b/blob/7a1fe4528cb29ccea0334adbee4dc86fadb7244d/packages/js-sdk/src/sandbox/commands/pty.ts).

### Guest ports

The SDK has no `getUrl()` call. Start a guest service, then use:

```ts
await sandbox.commands.run('python3 -m http.server 3000', {
  background: true,
})
const origin = `https://${sandbox.getHost(3000)}`
```

Tests should cover HTTP and WebSocket/upgrade routing separately. The fixed
envd proxy path proves only port `49983`; `getHost(3000)` proves the general
per-port data plane and wildcard routing.

Primary source: [Pinned `getHost()` implementation](https://github.com/e2b-dev/e2b/blob/7a1fe4528cb29ccea0334adbee4dc86fadb7244d/packages/js-sdk/src/sandbox/index.ts#L502-L525).

### Snapshots

Snapshot coverage is:

```ts
const snapshot = await sandbox.createSnapshot({ name: 'sdk-e2e-snapshot' })
const restored = await Sandbox.create(snapshot.snapshotId)

const pages = Sandbox.listSnapshots({ sandboxId: sandbox.sandboxId })
const deleted = await Sandbox.deleteSnapshot(snapshot.snapshotId)
```

`createSnapshot()` pauses the source while creating a persistent image. It
returns `{ snapshotId, names }`; the snapshot ID is accepted in the same
template argument position as a normal template. Snapshot list is paginated.
This is distinct from `pause()`: a paused sandbox keeps its sandbox identity
and is resumed with `connect()`, while a persistent snapshot is used to create
a new sandbox identity.

Primary sources:

- [Pinned instance snapshot methods](https://github.com/e2b-dev/e2b/blob/7a1fe4528cb29ccea0334adbee4dc86fadb7244d/packages/js-sdk/src/sandbox/index.ts#L659-L703)
- [Pinned snapshot API methods and types](https://github.com/e2b-dev/e2b/blob/7a1fe4528cb29ccea0334adbee4dc86fadb7244d/packages/js-sdk/src/sandbox/sandboxApi.ts#L487-L540)

### Templates

The package exports a `Template()` builder and static operations on the
function's `TemplateBase` prototype:

```ts
const template = Template()
  .fromBaseImage()
  .runCmd('printf template-ok >/opt/template-ok')

const build = await Template.build(template, 'sdk-e2e-template', {
  cpuCount: 2,
  memoryMB: 1024,
  onBuildLogs: (entry) => {},
})

const status = await Template.getBuildStatus(build)
const exists = await Template.exists('sdk-e2e-template')
```

Other relevant operations are `buildInBackground`, `assignTags`, `removeTags`,
and `getTags`. The builder supports base OCI images, another E2B template,
Dockerfile input, copy/remove/rename/directory/symlink instructions, commands,
package installers, environment, user/workdir, and start/ready commands.

Template build uses the same `ConnectionOpts` and `ApiClient` authentication as
sandbox lifecycle calls. It first requests `POST /v3/templates`, can upload a
tar archive to a server-provided URL, then triggers
`POST /v2/templates/{templateID}/builds/{buildID}` and polls the status route.
Therefore SDK template compatibility requires the template-manager and upload
storage path in addition to a working sandbox orchestrator. A create-only
deployment does not prove `Template.build()`.

Primary sources:

- [Pinned template facade/builder](https://github.com/e2b-dev/e2b/blob/7a1fe4528cb29ccea0334adbee4dc86fadb7244d/packages/js-sdk/src/template/index.ts)
- [Pinned template types](https://github.com/e2b-dev/e2b/blob/7a1fe4528cb29ccea0334adbee4dc86fadb7244d/packages/js-sdk/src/template/types.ts)
- [Pinned template API routes/upload](https://github.com/e2b-dev/e2b/blob/7a1fe4528cb29ccea0334adbee4dc86fadb7244d/packages/js-sdk/src/template/buildApi.ts)
- [Official template quickstart](https://e2b.dev/docs/template/quickstart)

## Capability and live-test matrix

This table separates the core `e2b` package contract from the broader project
goal. "Contract exact" means the pinned SDK wire schema matches the pinned
backend source; it does not mean the live deployment has passed that feature.

| Project capability | `e2b@2.38.0` surface | Current evidence / next gate |
|---|---|---|
| Create, connect, timeout, kill | First-class | Low-level live API-to-envd create/command/delete passed; execute the actual TypeScript SDK gate next. |
| Commands and process streaming | First-class | REST/ConnectRPC contracts exact; SDK foreground/background/stdin/reconnect tests pending. |
| File read/write/list and watch | First-class | Envd `0.6.13` satisfies SDK feature gates; live SDK bytes, stream, and watch tests pending. |
| Direct upload/download URLs | First-class | Contract exact; external wildcard ingress and caller HTTP transfer pending. |
| PTY | First-class | Contract exact; live create/input/resize/reconnect/kill pending. |
| HTTP and WebSocket guest ports | `getHost(port)` plus caller HTTP client | Requires the `*.sandbox.kitdev.ai` TLS/data-plane route; test public and authenticated traffic separately. |
| Pause/resume | `pause()` and `connect()` | Backend routes exact; local paused-state storage and full-memory/filesystem-only continuity remain unproved. |
| Persistent snapshots | Create/list/delete and create from snapshot ID | Contract exact; storage-backed live cycle pending. |
| Template building | `Template` builder/static API | API contract exact; local template-manager and upload/object-storage path are not yet deployed and proven. |
| Persistent workspace volumes | Volume mounts in create options | Blocked in the current topology because the referenced Belt volume-content service is unavailable; do not claim support. |
| Metrics and basic runtime info | `getMetrics()`, `getInfo()`, `isRunning()` | SDK methods are testable; operator metrics/health remain a separate deployment concern. |
| Sandbox service logs | No general core-SDK log retrieval API | Use command streams for process output and project `kitdev logs`/Loki for service logs. |
| Git operations | No special protocol; use sandbox commands | Test the pinned Git binary inside the coding template after core command coverage. |
| Browser automation | Not a high-level API in core `e2b` | Requires a separately pinned browser template/package and Playwright/CDP acceptance tests. |
| Desktop, screen stream, input, screenshots | Not a high-level API in core `e2b` | Requires the separately pinned official desktop repository/template and authenticated stream/control tests. |
| Code interpreter conveniences | Not a high-level API in core `e2b` | Requires the coding template and any separately selected higher-level SDK package. |
| MCP integration | No required core-SDK API | Optional gateway/template feature; separately select, pin, and test after core compatibility. |

The core TypeScript verifier must report unsupported or topology-gated rows as
explicit skips with reason codes. It must not turn them into silent passes.

## Host storage implication

The live Docker 29 installation uses the containerd image store. Although
Docker reports a project data-root on the large data disk, image content is
currently under `/var/lib/containerd` on the approximately 21 GB NVMe root
filesystem. Pulling the pinned Node image or building verifier images during an
active sandbox test could exhaust root and destabilize the control plane.

Before adding SDK images, the installer must either relocate containerd's root
into `/var/lib/kitdev-sandboxes` on the data disk with a documented restart and
rollback, or enforce a conservative free-space gate that accounts for existing
containerd content and build amplification. `DockerRootDir` alone is not
sufficient evidence when the containerd snapshotter/store is enabled. Do not
perform that storage mutation while live SDK sandboxes are running.

## Recommended compatibility sequence

Run the exact `e2b@2.38.0` package through these increasing-cost gates:

1. Create by the known seeded template ID, assert SDK metadata, run a command,
   write/read bytes, and kill.
2. Start a background command, reconnect to its PID, stream stdout/stdin, and
   kill it.
3. Watch a local directory while creating/renaming/removing a file.
4. Create, resize, reconnect to, and kill a PTY.
5. Start an HTTP service and validate `getHost(port)` through the general
   per-port ingress, separately from envd traffic.
6. Set timeout, perform full-memory pause, reconnect through a fresh SDK
   process, and prove process plus filesystem continuity.
7. Perform filesystem-only pause, reconnect, and prove filesystem persistence
   plus expected process loss.
8. Create a persistent snapshot, create a second sandbox from its snapshot ID,
   verify content, then delete both sandbox and snapshot.
9. Build a minimal template with `Template.build`, create from its returned
   name/build, verify the baked file, and clean up.

The verifier must log only operation names, opaque-ID hashes if correlation is
needed, state transitions, exit codes, and assertion results. It must never
print API keys, envd access tokens, traffic tokens, signed URLs, or deployed
origins.

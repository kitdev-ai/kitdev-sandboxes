# External official-SDK qualification matrix

This runner proves that a client on a **different** host can drive the
deployment through the official `e2b@2.38.0` TypeScript SDK over public HTTPS
only. It is deliberately separate from
`scripts/control-plane/e2e-typescript-sdk`, which runs on the sandbox host
against loopback and is therefore not evidence of external behavior.

The runner never reads host-local state, never sets `sandboxUrl`, and never
prints the API key. It exits nonzero if any stage fails.

## Requirements

- Node.js 22.18.0 and the reviewed lockfile in this directory.
- Public HTTPS ingress deployed with a trusted wildcard certificate.
- A project API key file readable by the invoking user, mode `0600` or
  stricter, containing `e2b_` plus 40 lowercase hexadecimal characters.
- The published template aliases the operator supplied.

## Running

```console
npm ci --ignore-scripts --no-audit --no-fund
E2B_API_URL=https://api.sandbox.example.com \
E2B_DOMAIN=sandbox.example.com \
E2B_API_KEY_FILE=/path/to/private/key \
node matrix.ts
```

Optional overrides:

| Variable | Default |
|---|---|
| `KITDEV_CODING_TEMPLATE` | `kitdev-coding:stable` |
| `KITDEV_BROWSER_TEMPLATE` | `kitdev-browser-heavy:stable` |
| `KITDEV_SKIP_BROWSER` | unset; set to `1` to skip the 8 GiB stage |

`E2B_SANDBOX_URL` must not be set. The runner refuses to start if it is,
because that override would bypass the public wildcard route being tested.

## Concurrency

The deployment permits one concurrent sandbox per team, so stages run strictly
in sequence and each destroys its sandbox before the next begins. The
`lifecycle` stage deliberately asserts that a second concurrent create is
**refused**; if that assertion fails, the host concurrency cap is not in force
and the run must be treated as unsafe rather than successful.

Do not run this matrix at the same time as any host-side qualification, build,
migration, backup, restore, or key-lifecycle operation.

## Coverage

| Stage | Proves |
|---|---|
| `auth` | TLS chain, API reachability, project authentication |
| `invalid-key` | A wrong key is rejected rather than silently accepted |
| `lifecycle` | create, info, timeout, list, connect, metrics, host derivation, concurrency refusal, kill |
| `commands-pty` | exit codes, stdout/stderr, streaming callbacks, stdin/EOF, disconnect/reconnect, kill, PTY create/resize/input/kill |
| `files` | write/read text and binary, list, info, rename, remove, 1 MiB round trip, recursive watch streaming |
| `guest-traffic` | wildcard guest HTTP, unbuffered chunked streaming, and a WebSocket upgrade through the public ingress |
| `pause` | pause with memory, resume by connect, cold pause, cold resume |
| `snapshot` | create, list, restore into a new sandbox, delete |
| `browser` | heavy profile, Chromium, loopback CDP, Playwright DOM, screenshot |
| `cleanup` | no sandbox created by this run is left running |

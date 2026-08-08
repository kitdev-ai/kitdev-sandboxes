// External official-SDK qualification matrix.
//
// Runs from a client host that is NOT the sandbox host, over public HTTPS
// only. It never reads host-local state, never sets `sandboxUrl`, and never
// prints the API key. Every stage cleans up its own sandboxes and snapshots.
//
// The deployment allows exactly one concurrent sandbox, so stages run
// sequentially and each stage kills its sandbox before the next one starts.
//
// Usage:
//   E2B_API_URL=https://api.sandbox.example.com \
//   E2B_DOMAIN=sandbox.example.com \
//   E2B_API_KEY_FILE=/path/to/key \
//   node matrix.ts

import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

import {
  FileType,
  FilesystemEventType,
  Sandbox,
  type ConnectionOpts,
  type FilesystemEvent,
} from "e2b";

const CODING_TEMPLATE = process.env.KITDEV_CODING_TEMPLATE ?? "kitdev-coding:stable";
const BROWSER_TEMPLATE = process.env.KITDEV_BROWSER_TEMPLATE ?? "kitdev-browser-heavy:stable";
const SANDBOX_ID_PATTERN = /^i[a-z0-9]{20}$/;
const SANDBOX_TIMEOUT_MS = 15 * 60_000;

type Outcome = { operation: string; status: "pass" | "fail"; detail?: string };

const outcomes: Outcome[] = [];
const liveSandboxes = new Set<Sandbox>();

function pass(operation: string): void {
  outcomes.push({ operation, status: "pass" });
  console.log(`status=pass operation=${operation}`);
}

function record(operation: string, error: unknown): void {
  const detail = error instanceof Error ? error.constructor.name : "UnknownError";
  outcomes.push({ operation, status: "fail", detail });
  console.error(`status=error operation=${operation} kind=${detail}`);
}

function requiredEnv(name: string): string {
  const value = process.env[name];
  if (value === undefined || value === "") throw new Error(`${name} is required`);
  return value;
}

async function loadConnection(): Promise<ConnectionOpts> {
  const apiUrl = requiredEnv("E2B_API_URL");
  const domain = requiredEnv("E2B_DOMAIN");
  assert.equal(apiUrl.startsWith("https://"), true, "E2B_API_URL must be HTTPS");
  assert.equal(process.env.E2B_SANDBOX_URL, undefined, "E2B_SANDBOX_URL must not be set");
  const apiKey = (await readFile(requiredEnv("E2B_API_KEY_FILE"), "ascii")).trim();
  assert.match(apiKey, /^e2b_[0-9a-f]{40}$/);
  return { apiKey, apiUrl, domain, requestTimeoutMs: 120_000 };
}

async function create(template: string, connection: ConnectionOpts, group: string): Promise<Sandbox> {
  const sandbox = await Sandbox.create(template, {
    ...connection,
    metadata: { kitdev_test: `external-sdk-${group}` },
    timeoutMs: SANDBOX_TIMEOUT_MS,
  });
  liveSandboxes.add(sandbox);
  assert.match(sandbox.sandboxId, SANDBOX_ID_PATTERN);
  return sandbox;
}

async function destroy(sandbox: Sandbox | undefined): Promise<void> {
  if (sandbox === undefined) return;
  liveSandboxes.delete(sandbox);
  if (await sandbox.isRunning().catch(() => false)) await sandbox.kill();
}

async function stage(name: string, body: () => Promise<void>): Promise<boolean> {
  try {
    await body();
    return true;
  } catch (error: unknown) {
    record(name, error);
    return false;
  }
}

/** Fetch a guest port through the public wildcard route, retrying once with the traffic token. */
async function guestFetch(sandbox: Sandbox, port: number, path: string): Promise<Response> {
  const url = `https://${sandbox.getHost(port)}${path}`;
  const plain = await fetch(url, { redirect: "manual" });
  if (plain.status !== 401 && plain.status !== 403) return plain;
  const token = (sandbox as unknown as { trafficAccessToken?: string }).trafficAccessToken;
  if (token === undefined) return plain;
  return fetch(url, { redirect: "manual", headers: { "e2b-traffic-access-token": token } });
}

async function waitForGuestPort(sandbox: Sandbox, port: number, path: string): Promise<Response> {
  let lastError: unknown;
  for (let attempt = 0; attempt < 30; attempt += 1) {
    try {
      const response = await guestFetch(sandbox, port, path);
      if (response.ok) return response;
      lastError = new Error(`guest port status ${response.status}`);
    } catch (error: unknown) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 1_000));
  }
  throw lastError instanceof Error ? lastError : new Error("guest port unreachable");
}

function ptyCollector(): { onData: (data: Uint8Array) => void; text: () => string } {
  const chunks: Uint8Array[] = [];
  return {
    onData: (data) => chunks.push(data.slice()),
    text: () => {
      const size = chunks.reduce((total, chunk) => total + chunk.byteLength, 0);
      const all = new Uint8Array(size);
      let offset = 0;
      for (const chunk of chunks) {
        all.set(chunk, offset);
        offset += chunk.byteLength;
      }
      return new TextDecoder().decode(all);
    },
  };
}

function nextMatchingEvent(
  events: FilesystemEvent[],
  predicate: (event: FilesystemEvent) => boolean,
): Promise<FilesystemEvent> {
  return new Promise((resolve, reject) => {
    const deadline = setTimeout(() => reject(new Error("watch event timeout")), 20_000);
    const poll = (): void => {
      const event = events.find(predicate);
      if (event !== undefined) {
        clearTimeout(deadline);
        resolve(event);
      } else {
        setTimeout(poll, 50);
      }
    };
    poll();
  });
}

// ---------------------------------------------------------------- stages ---

async function stageAuth(connection: ConnectionOpts): Promise<void> {
  const page = await Sandbox.list(connection).nextItems();
  assert.equal(Array.isArray(page), true);
  pass("external-auth-over-https");
}

async function stageRejectedKey(connection: ConnectionOpts): Promise<void> {
  const bogus = { ...connection, apiKey: `e2b_${"0".repeat(40)}` };
  await assert.rejects(async () => {
    await Sandbox.list(bogus).nextItems();
  });
  pass("external-invalid-key-rejected");
}

async function stageLifecycle(connection: ConnectionOpts): Promise<void> {
  let sandbox: Sandbox | undefined;
  try {
    sandbox = await create(CODING_TEMPLATE, connection, "lifecycle");
    pass("lifecycle-create");

    const instanceInfo = await sandbox.getInfo();
    const staticInfo = await Sandbox.getInfo(sandbox.sandboxId, connection);
    for (const info of [instanceInfo, staticInfo]) {
      assert.equal(info.sandboxId, sandbox.sandboxId);
      assert.equal(info.state, "running");
      assert.equal(info.cpuCount, 2);
      assert.equal(info.metadata.kitdev_test, "external-sdk-lifecycle");
    }
    pass("lifecycle-info");

    await sandbox.setTimeout(10 * 60_000);
    assert.equal((await sandbox.getInfo()).endAt.getTime() > Date.now() + 5 * 60_000, true);
    pass("lifecycle-set-timeout");

    const listed = await Sandbox.list({ ...connection, limit: 20 }).nextItems();
    assert.equal(listed.some((item) => item.sandboxId === sandbox!.sandboxId), true);
    pass("lifecycle-list");

    const reconnected = await Sandbox.connect(sandbox.sandboxId, {
      ...connection,
      timeoutMs: 10 * 60_000,
    });
    const result = await reconnected.commands.run("printf KITDEV_EXTERNAL_RECONNECT");
    assert.equal(result.exitCode, 0);
    assert.equal(result.stdout, "KITDEV_EXTERNAL_RECONNECT");
    pass("lifecycle-connect");

    const metrics = await sandbox.getMetrics();
    assert.equal(Array.isArray(metrics), true);
    pass("lifecycle-metrics");

    assert.equal(sandbox.getHost(3000), `3000-${sandbox.sandboxId}.${connection.domain}`);
    pass("lifecycle-get-host");

    await assert.rejects(async () => {
      const second = await Sandbox.create(CODING_TEMPLATE, {
        ...connection,
        timeoutMs: 60_000,
      });
      liveSandboxes.add(second);
      await destroy(second);
    });
    pass("lifecycle-concurrency-refused");
  } finally {
    await destroy(sandbox);
    pass("lifecycle-kill");
  }
}

async function stageCommandsAndPty(connection: ConnectionOpts): Promise<void> {
  let sandbox: Sandbox | undefined;
  try {
    sandbox = await create(CODING_TEMPLATE, connection, "commands");

    const foreground = await sandbox.commands.run("printf out; printf err >&2; exit 7", {
      timeoutMs: 60_000,
    });
    assert.equal(foreground.exitCode, 7);
    assert.equal(foreground.stdout, "out");
    assert.equal(foreground.stderr, "err");
    pass("commands-foreground-exit-streams");

    const chunks: string[] = [];
    const streamed = await sandbox.commands.run(
      "for i in 1 2 3; do printf 'chunk%s\\n' \"$i\"; sleep 1; done",
      { background: true, onStdout: (data: string) => chunks.push(data) },
    );
    const streamedResult = await streamed.wait();
    assert.equal(streamedResult.exitCode, 0);
    assert.match(chunks.join(""), /chunk1[\s\S]*chunk3/);
    pass("commands-streaming-stdout");

    const stdin = await sandbox.commands.run(
      "IFS= read -r value; printf 'KITDEV_STDIN:%s' \"$value\"",
      { background: true, stdin: true },
    );
    assert.equal((await sandbox.commands.list()).some((p) => p.pid === stdin.pid), true);
    await stdin.sendStdin("accepted");
    await stdin.closeStdin();
    const stdinResult = await stdin.wait();
    assert.equal(stdinResult.exitCode, 0);
    assert.equal(stdinResult.stdout, "KITDEV_STDIN:accepted");
    pass("commands-stdin-eof");

    const detached = await sandbox.commands.run(
      "IFS= read -r token; printf 'KITDEV_RECONNECT:%s' \"$token\"",
      { background: true, stdin: true },
    );
    const detachedPid = detached.pid;
    await detached.disconnect();
    const reconnected = await sandbox.commands.connect(detachedPid);
    await reconnected.sendStdin("accepted");
    await reconnected.closeStdin();
    assert.equal((await reconnected.wait()).stdout, "KITDEV_RECONNECT:accepted");
    pass("commands-disconnect-reconnect");

    const sleeping = await sandbox.commands.run("sleep 600", { background: true });
    const sleepingPid = sleeping.pid;
    await sleeping.disconnect();
    assert.equal(await sandbox.commands.kill(sleepingPid), true);
    assert.equal((await sandbox.commands.list()).some((p) => p.pid === sleepingPid), false);
    pass("commands-kill");

    const output = ptyCollector();
    const pty = await sandbox.pty.create({
      cols: 80,
      rows: 24,
      onData: output.onData,
      timeoutMs: 60_000,
    });
    await sandbox.pty.resize(pty.pid, { cols: 101, rows: 41 });
    await sandbox.pty.sendInput(
      pty.pid,
      new TextEncoder().encode("stty size; printf KITDEV_PTY_OK; exit\n"),
    );
    assert.equal((await pty.wait()).exitCode, 0);
    assert.match(output.text(), /41 101/);
    assert.match(output.text(), /KITDEV_PTY_OK/);
    pass("pty-create-resize-input");

    const killable = await sandbox.pty.create({ cols: 80, rows: 24, onData: () => {}, timeoutMs: 60_000 });
    const killablePid = killable.pid;
    await killable.disconnect();
    assert.equal(await sandbox.pty.kill(killablePid), true);
    pass("pty-kill");
  } finally {
    await destroy(sandbox);
    pass("commands-sandbox-kill");
  }
}

async function stageFiles(connection: ConnectionOpts): Promise<void> {
  let sandbox: Sandbox | undefined;
  try {
    sandbox = await create(CODING_TEMPLATE, connection, "files");

    assert.equal(await sandbox.files.makeDir("/tmp/kitdev-external/nested"), true);
    const binary = new Uint8Array([0, 1, 2, 127, 128, 254, 255]);
    const writes = await sandbox.files.writeFiles([
      { path: "/tmp/kitdev-external/nested/text.txt", data: "KITDEV_FILES_TEXT" },
      { path: "/tmp/kitdev-external/nested/data.bin", data: binary.buffer },
    ]);
    assert.equal(writes.length, 2);
    assert.equal(
      await sandbox.files.read("/tmp/kitdev-external/nested/text.txt"),
      "KITDEV_FILES_TEXT",
    );
    assert.deepEqual(
      await sandbox.files.read("/tmp/kitdev-external/nested/data.bin", { format: "bytes" }),
      binary,
    );
    pass("files-write-read-binary");

    const entries = await sandbox.files.list("/tmp/kitdev-external/nested");
    assert.deepEqual(entries.map((entry) => entry.name).sort(), ["data.bin", "text.txt"]);
    const info = await sandbox.files.getInfo("/tmp/kitdev-external/nested/data.bin");
    assert.equal(info.type, FileType.FILE);
    assert.equal(info.size, binary.byteLength);
    const renamed = await sandbox.files.rename(info.path, "/tmp/kitdev-external/nested/renamed.bin");
    assert.equal(renamed.name, "renamed.bin");
    await sandbox.files.remove(renamed.path);
    assert.equal(await sandbox.files.exists(renamed.path), false);
    pass("files-list-info-rename-remove");

    // A megabyte round trip proves the ingress body limits and buffering are sane.
    const large = new Uint8Array(1_048_576);
    for (let index = 0; index < large.length; index += 1) large[index] = index % 251;
    await sandbox.files.write("/tmp/kitdev-external/large.bin", large.buffer);
    assert.deepEqual(
      await sandbox.files.read("/tmp/kitdev-external/large.bin", { format: "bytes" }),
      large,
    );
    pass("files-one-megabyte-round-trip");

    await sandbox.files.makeDir("/tmp/kitdev-external/watch");
    const events: FilesystemEvent[] = [];
    const watch = await sandbox.files.watchDir(
      "/tmp/kitdev-external/watch",
      (event) => {
        events.push(event);
      },
      { includeEntry: true, recursive: true, timeoutMs: 60_000 },
    );
    try {
      await sandbox.files.write("/tmp/kitdev-external/watch/event.txt", "KITDEV_WATCH");
      const event = await nextMatchingEvent(
        events,
        (candidate) =>
          candidate.name.endsWith("event.txt") &&
          (candidate.type === FilesystemEventType.CREATE ||
            candidate.type === FilesystemEventType.WRITE),
      );
      assert.equal(event.entry?.type, FileType.FILE);
    } finally {
      await watch.stop();
    }
    pass("files-watch-streaming");

    await sandbox.files.remove("/tmp/kitdev-external");
    pass("files-remove-tree");
  } finally {
    await destroy(sandbox);
    pass("files-sandbox-kill");
  }
}

async function stageGuestTraffic(connection: ConnectionOpts): Promise<void> {
  let sandbox: Sandbox | undefined;
  try {
    sandbox = await create(CODING_TEMPLATE, connection, "traffic");

    await sandbox.files.makeDir("/home/user/pub");
    await sandbox.files.write("/home/user/pub/probe.txt", "KITDEV_WILDCARD_HTTP_OK");
    const server = await sandbox.commands.run(
      "python3 -m http.server 8000 --directory /home/user/pub",
      { background: true },
    );
    const response = await waitForGuestPort(sandbox, 8000, "/probe.txt");
    assert.equal(await response.text(), "KITDEV_WILDCARD_HTTP_OK");
    pass("wildcard-guest-http");

    // Chunked streaming with no terminating length proves proxy buffering is off.
    const streamScript = [
      "import http.server, time",
      "class H(http.server.BaseHTTPRequestHandler):",
      "    def do_GET(self):",
      "        self.send_response(200)",
      "        self.send_header('Content-Type', 'text/event-stream')",
      "        self.end_headers()",
      "        for i in range(3):",
      "            self.wfile.write(b'data: tick%d\\n\\n' % i)",
      "            self.wfile.flush()",
      "            time.sleep(1)",
      "    def log_message(self, *a): pass",
      "http.server.HTTPServer(('0.0.0.0', 8001), H).serve_forever()",
    ].join("\n");
    await sandbox.files.write("/home/user/stream.py", streamScript);
    const streamServer = await sandbox.commands.run("python3 /home/user/stream.py", {
      background: true,
    });
    const streamResponse = await waitForGuestPort(sandbox, 8001, "/");
    const reader = streamResponse.body!.getReader();
    const decoder = new TextDecoder();
    const started = Date.now();
    let firstChunkAt = 0;
    let streamed = "";
    while (streamed.split("tick").length <= 3) {
      const { done, value } = await reader.read();
      if (done) break;
      if (firstChunkAt === 0) firstChunkAt = Date.now();
      streamed += decoder.decode(value, { stream: true });
    }
    await reader.cancel();
    assert.match(streamed, /tick0/);
    assert.equal(firstChunkAt - started < 2_500, true, "first chunk was buffered");
    pass("wildcard-guest-chunked-streaming");

    const wsScript = [
      "import { createHash } from 'node:crypto';",
      "import { createServer } from 'node:http';",
      "const GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11';",
      "createServer().on('upgrade', (request, socket) => {",
      "  const accept = createHash('sha1')",
      "    .update(request.headers['sec-websocket-key'] + GUID)",
      "    .digest('base64');",
      "  socket.write(",
      "    'HTTP/1.1 101 Switching Protocols\\r\\n' +",
      "    'Upgrade: websocket\\r\\nConnection: Upgrade\\r\\n' +",
      "    `Sec-WebSocket-Accept: ${accept}\\r\\n\\r\\n`,",
      "  );",
      "  socket.on('data', (frame) => {",
      "    const length = frame[1] & 0x7f;",
      "    const mask = frame.subarray(2, 6);",
      "    const payload = Buffer.from(frame.subarray(6, 6 + length));",
      "    for (let i = 0; i < payload.length; i += 1) payload[i] ^= mask[i % 4];",
      "    const echo = Buffer.concat([Buffer.from([0x81, payload.length]), payload]);",
      "    socket.write(echo);",
      "  });",
      "}).listen(8002, '0.0.0.0');",
    ].join("\n");
    await sandbox.files.write("/home/user/ws.mjs", wsScript);
    const wsServer = await sandbox.commands.run("node /home/user/ws.mjs", { background: true });
    await new Promise((resolve) => setTimeout(resolve, 2_000));
    const echoed = await new Promise<string>((resolve, reject) => {
      const socket = new WebSocket(`wss://${sandbox!.getHost(8002)}/`);
      const deadline = setTimeout(() => reject(new Error("websocket timeout")), 30_000);
      socket.addEventListener("open", () => socket.send("KITDEV_WS_OK"));
      socket.addEventListener("message", (event) => {
        clearTimeout(deadline);
        socket.close();
        resolve(String(event.data));
      });
      socket.addEventListener("error", () => {
        clearTimeout(deadline);
        reject(new Error("websocket error"));
      });
    });
    assert.equal(echoed, "KITDEV_WS_OK");
    pass("wildcard-guest-websocket");

    for (const handle of [server, streamServer, wsServer]) {
      await sandbox.commands.kill(handle.pid).catch(() => false);
    }
  } finally {
    await destroy(sandbox);
    pass("traffic-sandbox-kill");
  }
}

async function stagePause(connection: ConnectionOpts): Promise<void> {
  let sandbox: Sandbox | undefined;
  try {
    sandbox = await create(CODING_TEMPLATE, connection, "pause");
    const statePath = "/home/user/kitdev-pause-state";
    await sandbox.files.write(statePath, "KITDEV_PAUSE_PERSISTED");
    const held = await sandbox.commands.run("sleep 600", { background: true });
    const heldPid = held.pid;
    await held.disconnect();

    assert.equal(await sandbox.pause({ keepMemory: true }), true);
    assert.equal((await Sandbox.getInfo(sandbox.sandboxId, connection)).state, "paused");
    pass("pause-keep-memory");

    let resumed = await Sandbox.connect(sandbox.sandboxId, {
      ...connection,
      timeoutMs: 10 * 60_000,
    });
    liveSandboxes.add(resumed);
    assert.equal(await resumed.isRunning(), true);
    assert.equal((await resumed.commands.list()).some((p) => p.pid === heldPid), true);
    assert.equal(await resumed.files.read(statePath), "KITDEV_PAUSE_PERSISTED");
    assert.equal(await resumed.commands.kill(heldPid), true);
    pass("pause-keep-memory-resume");

    assert.equal(await resumed.pause({ keepMemory: false }), true);
    resumed = await Sandbox.connect(sandbox.sandboxId, {
      ...connection,
      timeoutMs: 10 * 60_000,
    });
    liveSandboxes.add(resumed);
    assert.equal(await resumed.files.read(statePath), "KITDEV_PAUSE_PERSISTED");
    assert.equal((await resumed.commands.list()).some((p) => p.pid === heldPid), false);
    pass("pause-cold-resume");

    await destroy(resumed);
    sandbox = undefined;
  } finally {
    await destroy(sandbox);
    pass("pause-sandbox-kill");
  }
}

async function stageSnapshot(connection: ConnectionOpts): Promise<void> {
  let source: Sandbox | undefined;
  let restored: Sandbox | undefined;
  let snapshotId: string | undefined;
  try {
    source = await create(CODING_TEMPLATE, connection, "snapshot");
    await source.files.write("/home/user/kitdev-snapshot-state", "KITDEV_SNAPSHOT_PERSISTED");
    const snapshot = await source.createSnapshot({
      name: "kitdev-external-matrix",
      requestTimeoutMs: 300_000,
    });
    snapshotId = snapshot.snapshotId;
    assert.equal(typeof snapshotId, "string");
    pass("snapshot-create");

    const listed = await Sandbox.listSnapshots({
      ...connection,
      limit: 20,
      sandboxId: source.sandboxId,
    }).nextItems();
    assert.equal(listed.some((item) => item.snapshotId === snapshotId), true);
    pass("snapshot-list");

    await destroy(source);
    source = undefined;

    restored = await create(snapshotId, connection, "snapshot-restore");
    assert.equal(
      await restored.files.read("/home/user/kitdev-snapshot-state"),
      "KITDEV_SNAPSHOT_PERSISTED",
    );
    pass("snapshot-restore");
  } finally {
    await destroy(restored);
    await destroy(source);
    if (snapshotId !== undefined) {
      assert.equal(await Sandbox.deleteSnapshot(snapshotId, connection), true);
      pass("snapshot-delete");
    }
  }
}

async function stageBrowser(connection: ConnectionOpts): Promise<void> {
  let sandbox: Sandbox | undefined;
  try {
    sandbox = await create(BROWSER_TEMPLATE, connection, "browser");
    const info = await sandbox.getInfo();
    assert.equal(info.cpuCount, 2);
    assert.equal(info.memoryMB, 8192);
    pass("browser-create-profile");

    const result = await sandbox.commands.run("node /opt/kitdev-browser/acceptance.mjs", {
      timeoutMs: 180_000,
    });
    assert.equal(result.exitCode, 0);
    const proof = JSON.parse(result.stdout) as Record<string, unknown>;
    assert.equal(proof.dom, "KITDEV_BROWSER_DOM_OK");
    assert.equal(proof.cdp_host, "127.0.0.1");
    assert.equal((proof.screenshot_bytes as number) > 1_000, true);
    pass("browser-chromium-cdp-playwright");
  } finally {
    await destroy(sandbox);
    pass("browser-sandbox-kill");
  }
}

async function stageCleanupVerify(connection: ConnectionOpts): Promise<void> {
  const remaining = await Sandbox.list({ ...connection, limit: 50 }).nextItems();
  const mine = remaining.filter((item) =>
    String(item.metadata?.kitdev_test ?? "").startsWith("external-sdk-"),
  );
  assert.deepEqual(mine, []);
  pass("cleanup-no-sandboxes-left");
}

// ------------------------------------------------------------------ main ---

const connection = await loadConnection();
console.log(`operation=external-sdk-matrix api=${connection.apiUrl} domain=${connection.domain}`);

const skipBrowser = process.env.KITDEV_SKIP_BROWSER === "1";
const plan: Array<[string, (connection: ConnectionOpts) => Promise<void>]> = [
  ["auth", stageAuth],
  ["invalid-key", stageRejectedKey],
  ["lifecycle", stageLifecycle],
  ["commands-pty", stageCommandsAndPty],
  ["files", stageFiles],
  ["guest-traffic", stageGuestTraffic],
  ["pause", stagePause],
  ["snapshot", stageSnapshot],
  ...(skipBrowser ? [] : ([["browser", stageBrowser]] as Array<[string, (c: ConnectionOpts) => Promise<void>]>)),
  ["cleanup", stageCleanupVerify],
];

let failed = 0;
for (const [name, body] of plan) {
  const ok = await stage(name, () => body(connection));
  if (!ok) failed += 1;
}

for (const sandbox of liveSandboxes) {
  await destroy(sandbox).catch(() => undefined);
}

const summary = {
  stages: plan.length,
  failedStages: failed,
  checks: outcomes.length,
  failedChecks: outcomes.filter((item) => item.status === "fail").length,
};
console.log(`operation=external-sdk-matrix-summary ${JSON.stringify(summary)}`);
process.exit(failed === 0 ? 0 : 1);

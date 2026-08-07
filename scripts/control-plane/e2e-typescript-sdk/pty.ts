import assert from "node:assert/strict";

import { fail, pass, withSandbox } from "./harness.ts";

function collector(): {
  onData: (data: Uint8Array) => void;
  text: () => string;
} {
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

await withSandbox("pty", async (sandbox) => {
  const firstOutput = collector();
  const first = await sandbox.pty.create({
    cols: 80,
    rows: 24,
    onData: firstOutput.onData,
    timeoutMs: 30_000,
  });
  await sandbox.pty.resize(first.pid, { cols: 101, rows: 41 });
  await sandbox.pty.sendInput(
    first.pid,
    new TextEncoder().encode("stty size; printf KITDEV_PTY_RESIZE_OK; exit\n"),
  );
  assert.equal((await first.wait()).exitCode, 0);
  assert.match(firstOutput.text(), /41 101/);
  assert.match(firstOutput.text(), /KITDEV_PTY_RESIZE_OK/);
  pass("pty-create-resize-input");

  const detachedOutput = collector();
  const detached = await sandbox.pty.create({
    cols: 90,
    rows: 30,
    onData: () => {},
    timeoutMs: 30_000,
  });
  const detachedPid = detached.pid;
  await detached.disconnect();
  let processes = await sandbox.commands.list();
  assert.equal(processes.some((process) => process.pid === detachedPid), true);
  const reconnected = await sandbox.pty.connect(detachedPid, {
    onData: detachedOutput.onData,
    timeoutMs: 30_000,
  });
  await sandbox.pty.sendInput(
    detachedPid,
    new TextEncoder().encode("printf KITDEV_PTY_RECONNECT_OK; exit\n"),
  );
  assert.equal((await reconnected.wait()).exitCode, 0);
  assert.match(detachedOutput.text(), /KITDEV_PTY_RECONNECT_OK/);
  pass("pty-list-disconnect-reconnect");

  const killed = await sandbox.pty.create({
    cols: 80,
    rows: 24,
    onData: () => {},
    timeoutMs: 30_000,
  });
  const killedPid = killed.pid;
  await killed.disconnect();
  assert.equal(await sandbox.pty.kill(killedPid), true);
  processes = await sandbox.commands.list();
  assert.equal(processes.some((process) => process.pid === killedPid), false);
  pass("pty-kill");
}).catch((error: unknown) => fail("pty", error));

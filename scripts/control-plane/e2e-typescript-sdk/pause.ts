import assert from "node:assert/strict";

import { Sandbox, type ConnectionOpts } from "e2b";

import { fail, pass, withSandbox } from "./harness.ts";

async function assertPaused(
  sandboxId: string,
  connection: ConnectionOpts,
): Promise<void> {
  const info = await Sandbox.getInfo(sandboxId, connection);
  assert.equal(info.state, "paused");
  const paused = await Sandbox.list({
    ...connection,
    limit: 10,
    query: { state: ["paused"] },
  }).nextItems();
  assert.equal(paused.some((item) => item.sandboxId === sandboxId), true);
}

await withSandbox("pause", async (sandbox, connection) => {
  const statePath = "/home/user/kitdev-pause-state";
  await sandbox.files.write(statePath, "KITDEV_PAUSE_PERSISTED");
  const memoryProcess = await sandbox.commands.run("sleep 600", { background: true });
  const memoryPid = memoryProcess.pid;
  await memoryProcess.disconnect();
  assert.equal(await sandbox.pause({ keepMemory: true }), true);
  await assertPaused(sandbox.sandboxId, connection);
  pass("pause-full-memory");

  let resumed = await Sandbox.connect(sandbox.sandboxId, {
    ...connection,
    timeoutMs: 480_000,
  });
  assert.equal(await resumed.isRunning(), true);
  let processes = await resumed.commands.list();
  assert.equal(processes.some((process) => process.pid === memoryPid), true);
  assert.equal(await resumed.commands.kill(memoryPid), true);
  assert.equal(
    await resumed.files.read(statePath),
    "KITDEV_PAUSE_PERSISTED",
  );
  pass("pause-full-memory-resume");

  const coldProcess = await resumed.commands.run("sleep 600", { background: true });
  const coldPid = coldProcess.pid;
  await coldProcess.disconnect();
  assert.equal(await resumed.pause({ keepMemory: false }), true);
  await assertPaused(sandbox.sandboxId, connection);
  pass("pause-filesystem-only");

  resumed = await Sandbox.connect(sandbox.sandboxId, {
    ...connection,
    timeoutMs: 480_000,
  });
  assert.equal(await resumed.isRunning(), true);
  assert.equal(
    await resumed.files.read(statePath),
    "KITDEV_PAUSE_PERSISTED",
  );
  processes = await resumed.commands.list();
  assert.equal(processes.some((process) => process.pid === coldPid), false);
  await resumed.setTimeout(420_000);
  assert.equal((await resumed.getInfo()).state, "running");
  pass("pause-filesystem-only-cold-resume");
}).catch((error: unknown) => fail("pause", error));

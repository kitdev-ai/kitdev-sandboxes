import assert from "node:assert/strict";

import { fail, pass, withSandbox } from "./harness.ts";

await withSandbox("commands", async (sandbox) => {
  const stdin = await sandbox.commands.run(
    "IFS= read -r value; printf 'KITDEV_STDIN:%s' \"$value\"",
    { background: true, stdin: true },
  );
  pass("commands-background-start");
  let processes = await sandbox.commands.list();
  assert.equal(processes.some((process) => process.pid === stdin.pid), true);
  pass("commands-list-active");
  await stdin.sendStdin("accepted");
  pass("commands-send-stdin");
  await stdin.closeStdin();
  pass("commands-close-stdin");
  const stdinResult = await stdin.wait();
  assert.equal(stdinResult.exitCode, 0);
  assert.equal(stdinResult.stdout, "KITDEV_STDIN:accepted");
  assert.equal(stdinResult.stderr, "");
  pass("commands-background-stdin-close");

  const detached = await sandbox.commands.run(
    "IFS= read -r token; printf 'KITDEV_COMMAND_RECONNECT:%s' \"$token\"",
    { background: true, stdin: true },
  );
  const detachedPid = detached.pid;
  await detached.disconnect();
  processes = await sandbox.commands.list();
  assert.equal(processes.some((process) => process.pid === detachedPid), true);
  const reconnected = await sandbox.commands.connect(detachedPid);
  await reconnected.sendStdin("accepted");
  await reconnected.closeStdin();
  const reconnectResult = await reconnected.wait();
  assert.equal(reconnectResult.exitCode, 0);
  assert.equal(reconnectResult.stdout, "KITDEV_COMMAND_RECONNECT:accepted");
  assert.equal(reconnectResult.stderr, "");
  pass("commands-list-disconnect-reconnect");

  const sleeping = await sandbox.commands.run("sleep 600", { background: true });
  const sleepingPid = sleeping.pid;
  await sleeping.disconnect();
  assert.equal(await sandbox.commands.kill(sleepingPid), true);
  processes = await sandbox.commands.list();
  assert.equal(processes.some((process) => process.pid === sleepingPid), false);
  pass("commands-kill");
}).catch((error: unknown) => fail("commands", error));

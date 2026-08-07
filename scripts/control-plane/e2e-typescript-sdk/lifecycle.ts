import assert from "node:assert/strict";

import { Sandbox } from "e2b";

import { fail, pass, withSandbox } from "./harness.ts";

await withSandbox("lifecycle", async (sandbox, connection) => {
  assert.equal(await sandbox.isRunning(), true);
  const instanceInfo = await sandbox.getInfo();
  const staticInfo = await Sandbox.getInfo(sandbox.sandboxId, connection);
  for (const info of [instanceInfo, staticInfo]) {
    assert.equal(info.sandboxId, sandbox.sandboxId);
    assert.equal(info.state, "running");
    assert.equal(info.envdVersion, "0.6.13");
    assert.equal(info.metadata.kitdev_test, "typescript-sdk-lifecycle");
    assert.equal(info.cpuCount, 2);
    assert.equal(info.memoryMB, 1024);
  }
  pass("lifecycle-running-info");

  await sandbox.setTimeout(480_000);
  const timeoutInfo = await sandbox.getInfo();
  assert.equal(timeoutInfo.endAt.getTime() > Date.now() + 300_000, true);
  pass("lifecycle-set-timeout");

  const listed = await Sandbox.list({ ...connection, limit: 10 }).nextItems();
  assert.equal(listed.some((item) => item.sandboxId === sandbox.sandboxId), true);
  pass("lifecycle-list-active");

  const reconnected = await Sandbox.connect(sandbox.sandboxId, {
    ...connection,
    timeoutMs: 480_000,
  });
  const result = await reconnected.commands.run("printf KITDEV_SANDBOX_RECONNECT");
  assert.equal(result.exitCode, 0);
  assert.equal(result.stdout, "KITDEV_SANDBOX_RECONNECT");
  assert.equal(result.stderr, "");
  pass("lifecycle-sandbox-reconnect");

  const metrics = await sandbox.getMetrics();
  assert.equal(Array.isArray(metrics), true);
  for (const metric of metrics) {
    assert.equal(metric.cpuCount, 2);
    assert.equal(metric.memTotal > 0, true);
    assert.equal(metric.diskTotal > 0, true);
  }
  pass("lifecycle-metrics");

  assert.equal(sandbox.getHost(3000), `3000-${sandbox.sandboxId}.localhost`);
  pass("lifecycle-get-host");
}).catch((error: unknown) => fail("lifecycle", error));

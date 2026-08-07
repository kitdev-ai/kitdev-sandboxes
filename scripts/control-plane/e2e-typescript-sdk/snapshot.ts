import assert from "node:assert/strict";
import { writeFile } from "node:fs/promises";

import { Sandbox } from "e2b";

import { fail, loadInputs, pass } from "./harness.ts";

try {
  const { connection, template } = await loadInputs();
  let source: Sandbox | undefined;
  let restored: Sandbox | undefined;
  let snapshotId: string | undefined;

  try {
    source = await Sandbox.create(template, {
      ...connection,
      metadata: { kitdev_test: "typescript-sdk-snapshot-source" },
      timeoutMs: 600_000,
    });
    await writeFile("/run/state/sandbox-id", `${source.sandboxId}\n`, {
      encoding: "ascii",
      flag: "wx",
      mode: 0o600,
    });
    await source.files.write(
      "/home/user/kitdev-snapshot-state",
      "KITDEV_SNAPSHOT_PERSISTED",
    );
    pass("snapshot-source-create");

    const snapshot = await source.createSnapshot({
      name: "kitdev-sdk-e2e-snapshot",
      requestTimeoutMs: 300_000,
    });
    snapshotId = snapshot.snapshotId;
    assert.equal(typeof snapshotId, "string");
    assert.equal(snapshotId.length > 0 && snapshotId.length <= 256, true);
    await writeFile("/run/state/snapshot-id", `${snapshotId}\n`, {
      encoding: "utf8",
      flag: "wx",
      mode: 0o600,
    });
    pass("snapshot-create");

    const instanceSnapshots = await source.listSnapshots({ limit: 10 }).nextItems();
    assert.equal(instanceSnapshots.some((item) => item.snapshotId === snapshotId), true);
    const staticSnapshots = await Sandbox.listSnapshots({
      ...connection,
      limit: 10,
      sandboxId: source.sandboxId,
    }).nextItems();
    assert.equal(staticSnapshots.some((item) => item.snapshotId === snapshotId), true);
    pass("snapshot-list");

    assert.equal(await source.kill(), true);
    pass("snapshot-source-kill");
    restored = await Sandbox.create(snapshotId, {
      ...connection,
      metadata: { kitdev_test: "typescript-sdk-snapshot-restore" },
      timeoutMs: 600_000,
    });
    await writeFile("/run/state/secondary-sandbox-id", `${restored.sandboxId}\n`, {
      encoding: "ascii",
      flag: "wx",
      mode: 0o600,
    });
    assert.equal(
      await restored.files.read("/home/user/kitdev-snapshot-state"),
      "KITDEV_SNAPSHOT_PERSISTED",
    );
    const command = await restored.commands.run("printf KITDEV_SNAPSHOT_RESTORE");
    assert.equal(command.stdout, "KITDEV_SNAPSHOT_RESTORE");
    pass("snapshot-restore");
  } finally {
    if (restored !== undefined && (await restored.isRunning().catch(() => false))) {
      assert.equal(await restored.kill(), true);
      pass("snapshot-restore-kill");
    }
    if (source !== undefined && (await source.isRunning().catch(() => false))) {
      assert.equal(await source.kill(), true);
      pass("snapshot-source-cleanup-kill");
    }
    if (snapshotId !== undefined) {
      assert.equal(await Sandbox.deleteSnapshot(snapshotId, connection), true);
      pass("snapshot-delete");
    }
  }

  pass("snapshot-group-complete");
} catch (error: unknown) {
  fail("snapshot", error);
}

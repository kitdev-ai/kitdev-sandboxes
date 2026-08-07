import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

import { Sandbox } from "e2b";

import { loadInputs } from "./harness.ts";

try {
  const snapshotId = (await readFile("/run/state/snapshot-id", "utf8")).trim();
  assert.match(snapshotId, /^[A-Za-z0-9._:/-]{1,256}$/);
  const { connection } = await loadInputs();
  await Sandbox.deleteSnapshot(snapshotId, connection);
} catch {
  process.exitCode = 1;
}

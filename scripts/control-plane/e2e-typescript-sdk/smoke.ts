import assert from "node:assert/strict";
import { readFile, writeFile } from "node:fs/promises";

import { Sandbox, type ConnectionOpts } from "e2b";

const credentialPath = "/run/secrets/e2b-api-key";
const templatePath = "/run/config/e2b-template-id";

function pass(operation: string): void {
  console.log(`status=pass operation=${operation}`);
}

async function main(): Promise<void> {
  const apiKey = (await readFile(credentialPath, "ascii")).trim();
  assert.match(apiKey, /^e2b_[0-9a-f]{40}$/);
  const template = (await readFile(templatePath, "ascii")).trim();
  assert.match(template, /^[a-z0-9]{16,32}$/);
  const connection: ConnectionOpts = {
    apiKey,
    apiUrl: "http://127.0.0.1:3000",
    domain: "localhost",
    requestTimeoutMs: 180_000,
    sandboxUrl: "http://127.0.0.1:3002",
  };
  let sandbox: Sandbox | undefined;
  try {
    const baseline = await Sandbox.list({ ...connection, limit: 10 }).nextItems();
    assert.deepEqual(baseline, []);
    pass("sandbox-list-baseline");

    sandbox = await Sandbox.create(template, {
      ...connection,
      metadata: { kitdev_test: "typescript-sdk" },
      timeoutMs: 600_000,
    });
    assert.match(sandbox.sandboxId, /^i[a-z0-9]{20}$/);
    await writeFile("/run/state/sandbox-id", `${sandbox.sandboxId}\n`, {
      encoding: "ascii",
      flag: "wx",
      mode: 0o600,
    });
    pass("sandbox-create");

    const result = await sandbox.commands.run(
      "printf KITDEV_SDK_STDOUT; printf KITDEV_SDK_STDERR >&2",
    );
    assert.equal(result.exitCode, 0);
    assert.equal(result.stdout, "KITDEV_SDK_STDOUT");
    assert.equal(result.stderr, "KITDEV_SDK_STDERR");
    pass("commands-run");

    const payload = new Uint8Array([0, 1, 2, 127, 128, 254, 255]);
    await sandbox.files.write("/tmp/kitdev-sdk.bin", payload.buffer);
    const downloaded = await sandbox.files.read("/tmp/kitdev-sdk.bin", {
      format: "bytes",
    });
    assert.deepEqual(downloaded, payload);
    pass("files-upload-download");
  } finally {
    if (sandbox !== undefined) {
      const killed = await sandbox.kill();
      assert.equal(killed, true);
      pass("sandbox-kill");
    }
  }
}

main().catch((error: unknown) => {
  const kind = error instanceof Error ? error.constructor.name : "UnknownError";
  console.error(`status=error operation=typescript-sdk-smoke kind=${kind}`);
  process.exitCode = 1;
});

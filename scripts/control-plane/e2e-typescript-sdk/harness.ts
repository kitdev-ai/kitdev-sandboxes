import assert from "node:assert/strict";
import { readFile, writeFile } from "node:fs/promises";

import { Sandbox, type ConnectionOpts } from "e2b";

export function pass(operation: string): void {
  console.log(`status=pass operation=${operation}`);
}

export async function loadInputs(): Promise<{
  connection: ConnectionOpts;
  template: string;
}> {
  const apiKey = (await readFile("/run/secrets/e2b-api-key", "ascii")).trim();
  assert.match(apiKey, /^e2b_[0-9a-f]{40}$/);
  const template = (await readFile("/run/config/e2b-template-id", "ascii")).trim();
  assert.match(template, /^[a-z0-9]{16,32}$/);
  return {
    connection: {
      apiKey,
      apiUrl: "http://127.0.0.1:3000",
      domain: "localhost",
      requestTimeoutMs: 180_000,
      sandboxUrl: "http://127.0.0.1:3002",
    },
    template,
  };
}

export async function withSandbox(
  group: string,
  test: (sandbox: Sandbox, connection: ConnectionOpts) => Promise<void>,
): Promise<void> {
  const { connection, template } = await loadInputs();
  let sandbox: Sandbox | undefined;
  try {
    sandbox = await Sandbox.create(template, {
      ...connection,
      metadata: { kitdev_test: `typescript-sdk-${group}` },
      timeoutMs: 600_000,
    });
    assert.match(sandbox.sandboxId, /^i[a-z0-9]{20}$/);
    await writeFile("/run/state/sandbox-id", `${sandbox.sandboxId}\n`, {
      encoding: "ascii",
      flag: "wx",
      mode: 0o600,
    });
    pass(`${group}-sandbox-create`);
    await test(sandbox, connection);
  } finally {
    if (sandbox !== undefined) {
      assert.equal(await sandbox.kill(), true);
      pass(`${group}-sandbox-kill`);
    }
  }
}

export function fail(group: string, error: unknown): never {
  const kind = error instanceof Error ? error.constructor.name : "UnknownError";
  console.error(`status=error operation=${group} kind=${kind}`);
  process.exit(1);
}

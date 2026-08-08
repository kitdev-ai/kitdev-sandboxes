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

// Report where a failure happened, never what it compared.
//
// The message of a failed assertion embeds the values it compared, and these
// clients assert over the API key and over sandbox output, so printing it would
// put a credential into a log. Printing only the class name went too far the
// other way: `kind=AssertionError` identifies nothing, and every failure needed
// a hand-written probe to locate. The first stack frame inside our own sources
// names the exact check without disclosing a single value.
export function failureSite(error: unknown): string {
  if (!(error instanceof Error) || typeof error.stack !== "string") return "";
  const frame = error.stack
    .split("\n")
    .slice(1)
    .map((line) => line.trim())
    .find((line) => /\/(?:e2e-typescript-sdk|workspace)\/[A-Za-z0-9._-]+\.ts:\d+/.test(line));
  if (frame === undefined) return "";
  const location = /((?:[A-Za-z0-9._-]+)\.ts:\d+:\d+)/.exec(frame);
  return location === null ? "" : ` at=${location[1]}`;
}

export function fail(group: string, error: unknown): never {
  const kind = error instanceof Error ? error.constructor.name : "UnknownError";
  console.error(`status=error operation=${group} kind=${kind}${failureSite(error)}`);
  process.exit(1);
}

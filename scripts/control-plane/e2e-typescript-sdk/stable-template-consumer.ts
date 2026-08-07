import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

import { Sandbox, type ConnectionOpts } from "e2b";

type ConsumerConfig = {
  schemaVersion: 1;
  alias: "kitdev-coding" | "kitdev-browser-heavy";
  version: string;
};

async function main(): Promise<void> {
  const apiKey = (await readFile("/run/secrets/e2b-api-key", "ascii")).trim();
  assert.match(apiKey, /^e2b_[0-9a-f]{40}$/);
  const config = JSON.parse(
    await readFile("/run/config/template-publication.json", "ascii"),
  ) as ConsumerConfig;
  assert.equal(config.schemaVersion, 1);
  assert(config.alias === "kitdev-coding" || config.alias === "kitdev-browser-heavy");
  const connection: ConnectionOpts = {
    apiKey,
    apiUrl: "http://127.0.0.1:3000",
    domain: "localhost",
    requestTimeoutMs: 180_000,
    sandboxUrl: "http://127.0.0.1:3002",
  };
  let sandbox: Sandbox | undefined;
  try {
    sandbox = await Sandbox.create(`${config.alias}:stable`, {
      ...connection,
      metadata: { kitdev_test: "stable-template-consumer" },
      timeoutMs: 600_000,
    });
    assert.match(sandbox.sandboxId, /^i[a-z0-9]{20}$/);
    if (config.alias === "kitdev-coding") {
      const result = await sandbox.commands.run(
        "set -eu; test \"$(id -un)\" = user; test \"$(node --version)\" = v22.18.0; " +
          "printf KITDEV_STABLE_CODING_OK",
        { timeoutMs: 30_000 },
      );
      assert.equal(result.exitCode, 0);
      assert.equal(result.stdout, "KITDEV_STABLE_CODING_OK");
      assert.equal(result.stderr, "");
    } else {
      const result = await sandbox.commands.run(
        "node /opt/kitdev-browser/acceptance.mjs",
        { timeoutMs: 120_000 },
      );
      assert.equal(result.exitCode, 0);
      assert.equal(result.stderr, "");
      const proof = JSON.parse(result.stdout) as Record<string, unknown>;
      assert.equal(proof.dom, "KITDEV_BROWSER_DOM_OK");
      assert.equal(proof.cdp_host, "127.0.0.1");
      assert((proof.screenshot_bytes as number) > 1_000);
    }
    console.log(`status=pass operation=stable-template-consumer alias=${config.alias}:stable`);
  } finally {
    if (sandbox !== undefined) {
      assert.equal(await sandbox.kill(), true);
    }
  }
}

main().catch((error: unknown) => {
  const kind = error instanceof Error ? error.constructor.name : "UnknownError";
  console.error(`status=error operation=stable-template-consumer kind=${kind}`);
  process.exit(1);
});

import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

import { Template, type ConnectionOpts } from "e2b";

type ReleaseConfig = {
  schemaVersion: 1;
  alias: "kitdev-coding" | "kitdev-browser-heavy";
  version: string;
};

async function connection(): Promise<ConnectionOpts> {
  const apiKey = (await readFile("/run/secrets/e2b-api-key", "ascii")).trim();
  assert.match(apiKey, /^e2b_[0-9a-f]{40}$/);
  return {
    apiKey,
    apiUrl: "http://127.0.0.1:3000",
    domain: "localhost",
    requestTimeoutMs: 180_000,
    sandboxUrl: "http://127.0.0.1:3002",
  };
}

async function config(): Promise<ReleaseConfig> {
  const document = JSON.parse(
    await readFile("/run/config/template-publication.json", "ascii"),
  ) as Record<string, unknown>;
  assert.deepEqual(Object.keys(document).sort(), ["alias", "schemaVersion", "version"]);
  assert.equal(document.schemaVersion, 1);
  assert(
    document.alias === "kitdev-coding" ||
      document.alias === "kitdev-browser-heavy",
  );
  assert.match(String(document.version), /^v[1-9][0-9]{0,5}$/);
  return document as ReleaseConfig;
}

async function main(): Promise<void> {
  assert.equal(process.argv[2], "remove-stable");
  const release = await config();
  const options = await connection();
  await Template.removeTags(release.alias, ["stable"], options);
  const tags = await Template.getTags(release.alias, options);
  assert(tags.some((item) => item.tag === release.version));
  assert(!tags.some((item) => item.tag === "stable"));
  console.log("status=pass operation=remove-stable-template-tag");
}

main().catch((error: unknown) => {
  const kind = error instanceof Error ? error.constructor.name : "UnknownError";
  console.error(`status=error operation=template-publication-release kind=${kind}`);
  process.exit(1);
});

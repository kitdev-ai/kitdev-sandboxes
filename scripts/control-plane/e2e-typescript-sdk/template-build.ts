import assert from "node:assert/strict";
import { readFile, rm, writeFile } from "node:fs/promises";

import { Sandbox, Template, type ConnectionOpts } from "e2b";

const stateRoot = "/run/state";
const backgroundTag = "background";
const synchronousTag = "synchronous";
const assignedTag = "verified";

function pass(operation: string): void {
  console.log(`status=pass operation=${operation}`);
}

async function loadConnection(): Promise<ConnectionOpts> {
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

async function loadTemplateName(): Promise<string> {
  const value = (await readFile("/run/config/e2b-template-name", "ascii")).trim();
  assert.match(value, /^kitdev-sdk-template-[0-9a-f]{12}$/);
  return value;
}

async function recordState(name: string, value: string): Promise<void> {
  await writeFile(`${stateRoot}/${name}`, `${value}\n`, {
    encoding: "ascii",
    flag: "wx",
    mode: 0o600,
  });
}

async function withSandbox(
  template: string,
  marker: string,
  connection: ConnectionOpts,
): Promise<void> {
  let sandbox: Sandbox | undefined;
  try {
    sandbox = await Sandbox.create(template, {
      ...connection,
      metadata: { kitdev_test: "typescript-sdk-template-build" },
      timeoutMs: 600_000,
    });
    assert.match(sandbox.sandboxId, /^i[a-z0-9]{20}$/);
    await recordState("sandbox-id", sandbox.sandboxId);
    const result = await sandbox.commands.run(
      "set -eu; cat /opt/kitdev-template-background; " +
        "if [ -f /opt/kitdev-template-synchronous ]; then cat /opt/kitdev-template-synchronous; fi",
      { timeoutMs: 30_000 },
    );
    assert.equal(result.exitCode, 0);
    assert.equal(result.stderr, "");
    assert.equal(result.stdout, marker);
    pass(`sandbox-from-${template.split(":").at(-1)}`);
  } finally {
    if (sandbox !== undefined) {
      assert.equal(await sandbox.kill(), true);
      await rm(`${stateRoot}/sandbox-id`, { force: true });
    }
  }
}

async function waitForBuild(
  data: { templateId: string; buildId: string },
  connection: ConnectionOpts,
): Promise<void> {
  const deadline = Date.now() + 20 * 60_000;
  let offset = 0;
  while (Date.now() < deadline) {
    const status = await Template.getBuildStatus(data, {
      ...connection,
      logsOffset: offset,
    });
    assert.equal(status.templateID, data.templateId);
    assert.equal(status.buildID, data.buildId);
    offset += status.logEntries.length;
    console.log(
      `status=progress operation=template-build state=${status.status} ` +
        `log_entries=${status.logEntries.length}`,
    );
    if (status.status === "ready") {
      pass("template-background-status-ready");
      return;
    }
    if (status.status === "error") {
      throw new Error(
        `template build failed: ${status.reason?.message ?? "unspecified reason"}`,
      );
    }
    await new Promise((resolve) => setTimeout(resolve, 1_000));
  }
  throw new Error("template build status polling timed out");
}

async function main(): Promise<void> {
  const connection = await loadConnection();
  const templateName = await loadTemplateName();
  assert.equal(await Template.exists(templateName, connection), false);
  pass("template-absent-preflight");

  const backgroundTemplate = Template()
    .fromImage("e2bdev/base:latest")
    .runCmd("printf 'background\\n' > /opt/kitdev-template-background", {
      user: "root",
    });
  const background = await Template.buildInBackground(
    backgroundTemplate,
    `${templateName}:${backgroundTag}`,
    {
      ...connection,
      cpuCount: 2,
      memoryMB: 1024,
    },
  );
  assert.match(background.templateId, /^[a-z0-9]{16,32}$/);
  assert.match(background.buildId, /^[0-9a-f-]{36}$/);
  await recordState("template-id", background.templateId);
  pass("template-build-in-background-request");
  await waitForBuild(background, connection);

  assert.equal(await Template.exists(templateName, connection), true);
  pass("template-exists");
  const initialTags = await Template.getTags(background.templateId, connection);
  assert(
    initialTags.some(
      (item) => item.tag === backgroundTag && item.buildId === background.buildId,
    ),
  );
  pass("template-get-tags-initial");

  const assigned = await Template.assignTags(
    `${templateName}:${backgroundTag}`,
    assignedTag,
    connection,
  );
  assert.equal(assigned.buildId, background.buildId);
  assert.deepEqual(assigned.tags, [assignedTag]);
  assert(
    (await Template.getTags(background.templateId, connection)).some(
      (item) => item.tag === assignedTag && item.buildId === background.buildId,
    ),
  );
  pass("template-assign-tags");
  await Template.removeTags(templateName, assignedTag, connection);
  assert(
    !(await Template.getTags(background.templateId, connection)).some(
      (item) => item.tag === assignedTag,
    ),
  );
  pass("template-remove-tags");

  await withSandbox(
    `${templateName}:${backgroundTag}`,
    "background\n",
    connection,
  );

  let synchronousLogEntries = 0;
  const synchronousTemplate = Template()
    .fromTemplate(`${templateName}:${backgroundTag}`)
    .runCmd("printf 'synchronous\\n' > /opt/kitdev-template-synchronous", {
      user: "root",
    });
  const synchronous = await Template.build(
    synchronousTemplate,
    `${templateName}:${synchronousTag}`,
    {
      ...connection,
      cpuCount: 2,
      memoryMB: 1024,
      onBuildLogs: () => {
        synchronousLogEntries += 1;
      },
    },
  );
  assert.equal(synchronous.templateId, background.templateId);
  assert.match(synchronous.buildId, /^[0-9a-f-]{36}$/);
  assert.notEqual(synchronous.buildId, background.buildId);
  assert(synchronousLogEntries > 0);
  pass("template-build-blocking");

  const finalTags = await Template.getTags(synchronous.templateId, connection);
  assert(
    finalTags.some(
      (item) => item.tag === synchronousTag && item.buildId === synchronous.buildId,
    ),
  );
  pass("template-get-tags-final");
  await withSandbox(
    `${templateName}:${synchronousTag}`,
    "background\nsynchronous\n",
    connection,
  );
}

main().catch((error: unknown) => {
  const kind = error instanceof Error ? error.constructor.name : "UnknownError";
  console.error(`status=error operation=template-build kind=${kind}`);
  process.exit(1);
});

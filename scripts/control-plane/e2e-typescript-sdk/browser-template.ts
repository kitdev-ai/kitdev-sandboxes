import assert from "node:assert/strict";
import { readFile, writeFile } from "node:fs/promises";

import { Sandbox, Template, type ConnectionOpts } from "e2b";

const stateRoot = "/run/state";
const baseImage =
  "mcr.microsoft.com/playwright@" +
  "sha256:796dc8c6c3d7df246bf8b661402f8489189e278dca6456022a816e178d0211e9";
const playwrightVersion = "1.62.0";
const nodeVersion = "v24.18.0";
const npmVersion = "11.16.0";
const chromiumVersion = "151.0.7922.34";
const chromiumRevision = "1234";
const packageLockSha256 =
  "db5404269854f530b030d7c31b7ce8c0cd05e7182978af49c58b5e488f87c873";
const templateTag = "browser";

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
  assert.match(value, /^kitdev-browser-template-[0-9a-f]{12}$/);
  return value;
}

async function recordState(name: string, value: string): Promise<void> {
  await writeFile(`${stateRoot}/${name}`, `${value}\n`, {
    encoding: "ascii",
    flag: "wx",
    mode: 0o600,
  });
}

async function exerciseBrowserSandbox(
  template: string,
  connection: ConnectionOpts,
): Promise<void> {
  let sandbox: Sandbox | undefined;
  try {
    sandbox = await Sandbox.create(template, {
      ...connection,
      metadata: { kitdev_test: "typescript-sdk-browser-template" },
      timeoutMs: 600_000,
    });
    assert.match(sandbox.sandboxId, /^i[a-z0-9]{20}$/);
    await recordState("sandbox-id", sandbox.sandboxId);
    pass("browser-sandbox-create-ready");

    const identity = await sandbox.commands.run(
      "set -eu; id -un; id -u; pwd; node --version; npm --version; " +
        "stat -c '%U:%G:%a' /home/pwuser/workspace " +
        "/home/pwuser/downloads /home/pwuser/browser-profile; " +
        "test -s /tmp/kitdev-browser-ready; " +
        "pgrep -u pwuser -f '/opt/kitdev-browser/start-browser.mjs' >/dev/null; " +
        "curl --fail --silent --show-error http://127.0.0.1:9222/json/version >/dev/null",
      { timeoutMs: 30_000 },
    );
    assert.equal(identity.exitCode, 0);
    assert.equal(identity.stderr, "");
    const lines = identity.stdout.trimEnd().split("\n");
    console.log(
      `status=progress operation=browser-identity observed=${JSON.stringify(lines)}`,
    );
    assert.equal(lines[0], "pwuser");
    assert.equal(lines[1], "1001");
    assert.equal(lines[2], "/home/pwuser/workspace");
    assert.equal(lines[3], nodeVersion);
    assert.equal(lines[4], npmVersion);
    assert.deepEqual(lines.slice(5), [
      "pwuser:pwuser:755",
      "pwuser:pwuser:700",
      "pwuser:pwuser:700",
    ]);
    pass("browser-identity-readiness-loopback-cdp");

    const manifest = await sandbox.files.read("/etc/kitdev-browser-toolchain");
    assert.equal(
      manifest,
      `base_image=${baseImage}\nplaywright=${playwrightVersion}\n` +
        `node=${nodeVersion}\nnpm=${npmVersion}\n` +
        `chromium=${chromiumVersion}\nchromium_revision=${chromiumRevision}\n` +
        `package_lock_sha256=${packageLockSha256}\ncdp_bind=127.0.0.1:9222\n`,
    );
    pass("browser-toolchain-integrity-manifest");

    const acceptanceStdout: string[] = [];
    const acceptanceStderr: string[] = [];
    const acceptance = await sandbox.commands.run(
      "node /opt/kitdev-browser/acceptance.mjs",
      {
        timeoutMs: 120_000,
        onStdout: (data) => acceptanceStdout.push(data),
        onStderr: (data) => acceptanceStderr.push(data),
      },
    ).catch((error: unknown) => {
      const observed =
        `stdout=${JSON.stringify(acceptanceStdout.join(""))} ` +
        `stderr=${JSON.stringify(acceptanceStderr.join(""))}`;
      console.error(
        `status=progress operation=browser-acceptance-failed ${observed}`,
      );
      throw error;
    });
    assert.equal(acceptance.exitCode, 0);
    assert.equal(acceptance.stderr, "");
    const result = JSON.parse(acceptance.stdout.trim()) as Record<string, unknown>;
    assert.equal(result.browser, chromiumVersion);
    assert.equal(result.cdp_host, "127.0.0.1");
    assert.equal(result.dom, "KITDEV_BROWSER_DOM_OK");
    assert.equal(result.download_bytes, 27);
    assert.equal(typeof result.screenshot_bytes, "number");
    assert((result.screenshot_bytes as number) > 1_000);
    assert.match(String(result.screenshot_sha256), /^[0-9a-f]{64}$/);
    pass("browser-playwright-cdp-navigation-dom");

    const screenshot = await sandbox.files.read(
      "/home/pwuser/downloads/browser-proof.png",
      { format: "bytes" },
    );
    assert(screenshot instanceof Uint8Array);
    assert(screenshot.byteLength > 1_000);
    assert.deepEqual(
      [...screenshot.subarray(0, 8)],
      [137, 80, 78, 71, 13, 10, 26, 10],
    );
    const download = await sandbox.files.read(
      "/home/pwuser/downloads/browser-proof.txt",
    );
    assert.equal(download, "KITDEV_BROWSER_DOWNLOAD_OK\n");
    pass("browser-sdk-artifact-collection");

    const stillRunning = await sandbox.commands.run(
      "set -eu; pgrep -u pwuser -f '/opt/kitdev-browser/start-browser.mjs' >/dev/null; " +
        "curl --fail --silent http://127.0.0.1:9222/json/version >/dev/null",
      { timeoutMs: 30_000 },
    );
    assert.equal(stillRunning.exitCode, 0);
    pass("browser-process-alive-before-sandbox-destroy");
  } finally {
    if (sandbox !== undefined) {
      assert.equal(await sandbox.kill(), true);
      pass("browser-sandbox-kill");
    }
  }
}

async function main(): Promise<void> {
  const connection = await loadConnection();
  const templateName = await loadTemplateName();
  assert.equal(await Template.exists(templateName, connection), false);
  pass("browser-template-absent-preflight");

  const prepare =
    "set -eu; install -d -o root -g root -m 0755 /opt/kitdev-browser; " +
    "install -d -o pwuser -g pwuser -m 0755 /home/pwuser/workspace; " +
    "install -d -o pwuser -g pwuser -m 0700 /home/pwuser/downloads /home/pwuser/browser-profile";
  const install =
    "set -eu; cd /opt/kitdev-browser; " +
    "npm ci --ignore-scripts --omit=dev --no-audit --no-fund; " +
    "test \"$(node -p " +
    `\"require('./node_modules/playwright/package.json').version\")\" = ${playwrightVersion}; ` +
    "test -x \"$(node -e \"const { chromium } = require('playwright'); " +
    "process.stdout.write(chromium.executablePath())\")\"; " +
    "chown -R root:root /opt/kitdev-browser; chmod 0755 /opt/kitdev-browser/*.mjs; " +
    "find /opt/kitdev-browser -type d -exec chmod go-w {} +; " +
    "find /opt/kitdev-browser -type f -exec chmod go-w {} +";
  const writeManifest =
    "set -eu; printf '%s\\n' " +
    `'base_image=${baseImage}' 'playwright=${playwrightVersion}' ` +
    `'node=${nodeVersion}' 'npm=${npmVersion}' ` +
    `'chromium=${chromiumVersion}' 'chromium_revision=${chromiumRevision}' ` +
    `'package_lock_sha256=${packageLockSha256}' 'cdp_bind=127.0.0.1:9222' ` +
    "> /etc/kitdev-browser-toolchain; chmod 0644 /etc/kitdev-browser-toolchain";

  const browserTemplate = Template()
    .fromImage(baseImage)
    .runCmd(prepare, { user: "root" })
    .copy(
      [
        "browser-template-assets/package.json",
        "browser-template-assets/package-lock.json",
        "browser-template-assets/start-browser.mjs",
        "browser-template-assets/acceptance.mjs",
      ],
      "/opt/kitdev-browser/",
      { user: "root" },
    )
    .runCmd(install, { user: "root" })
    .runCmd(writeManifest, { user: "root" })
    .setEnvs({
      HOME: "/home/pwuser",
      PLAYWRIGHT_BROWSERS_PATH: "/ms-playwright",
    })
    .setWorkdir("/home/pwuser/workspace")
    .setUser("pwuser")
    .setStartCmd(
      "exec node /opt/kitdev-browser/start-browser.mjs",
      "test -s /tmp/kitdev-browser-ready && " +
        "curl --fail --silent http://127.0.0.1:9222/json/version >/dev/null && " +
        "pgrep -u pwuser -f '/opt/kitdev-browser/start-browser.mjs' >/dev/null",
    );

  let buildLogEntries = 0;
  const build = await Template.build(
    browserTemplate,
    `${templateName}:${templateTag}`,
    {
      ...connection,
      cpuCount: 2,
      memoryMB: 2048,
      onBuildLogs: () => {
        buildLogEntries += 1;
      },
    },
  );
  assert.match(build.templateId, /^[a-z0-9]{16,32}$/);
  assert.match(build.buildId, /^[0-9a-f-]{36}$/);
  assert(buildLogEntries > 0);
  await recordState("template-id", build.templateId);
  await recordState("build-id", build.buildId);
  pass("browser-template-build");

  const tags = await Template.getTags(build.templateId, connection);
  assert(tags.some((item) => item.tag === templateTag && item.buildId === build.buildId));
  pass("browser-template-tag");
  await exerciseBrowserSandbox(`${templateName}:${templateTag}`, connection);
}

main().catch((error: unknown) => {
  const kind = error instanceof Error ? error.constructor.name : "UnknownError";
  console.error(`status=error operation=browser-template kind=${kind}`);
  process.exit(1);
});

import assert from "node:assert/strict";
import { readFile, writeFile } from "node:fs/promises";

import { Sandbox, Template, type ConnectionOpts } from "e2b";

const stateRoot = "/run/state";
const baseImage =
  "e2bdev/base@sha256:4a369f01a820fe5e65f53c2c5727a78899daf86f0541b721097f289559c8b73f";
const nodeArchive = "node-v22.18.0-linux-x64.tar.xz";
const nodeArchiveSha256 =
  "c1bfeecf1d7404fa74728f9db72e697decbd8119ccc6f5a294d795756dfcfca7";
const nodeDownload = `https://nodejs.org/dist/v22.18.0/${nodeArchive}`;
const templateTag = "coding";

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
  assert.match(value, /^kitdev-coding-template-[0-9a-f]{12}$/);
  return value;
}

async function recordState(name: string, value: string): Promise<void> {
  await writeFile(`${stateRoot}/${name}`, `${value}\n`, {
    encoding: "ascii",
    flag: "wx",
    mode: 0o600,
  });
}

function collectPty(): {
  onData: (data: Uint8Array) => void;
  text: () => string;
} {
  const chunks: Uint8Array[] = [];
  return {
    onData: (data) => chunks.push(data.slice()),
    text: () => {
      const size = chunks.reduce((total, chunk) => total + chunk.byteLength, 0);
      const output = new Uint8Array(size);
      let offset = 0;
      for (const chunk of chunks) {
        output.set(chunk, offset);
        offset += chunk.byteLength;
      }
      return new TextDecoder().decode(output);
    },
  };
}

async function exerciseCodingSandbox(
  template: string,
  connection: ConnectionOpts,
): Promise<void> {
  let sandbox: Sandbox | undefined;
  try {
    sandbox = await Sandbox.create(template, {
      ...connection,
      metadata: { kitdev_test: "typescript-sdk-coding-template" },
      timeoutMs: 600_000,
    });
    assert.match(sandbox.sandboxId, /^i[a-z0-9]{20}$/);
    await recordState("sandbox-id", sandbox.sandboxId);
    pass("coding-sandbox-create-ready");

    const identity = await sandbox.commands.run(
      "set -eu; id -un; id -u; pwd; node --version; npm --version; " +
        "git --version; python3 --version; gcc --version | head -1; make --version | head -1; " +
        "test -s /tmp/kitdev-coding-ready; pgrep -u user -x sleep >/dev/null; " +
        "stat -c '%U:%G:%a' /home/user/workspace",
      { timeoutMs: 30_000 },
    );
    assert.equal(identity.exitCode, 0);
    assert.equal(identity.stderr, "");
    assert.equal(
      identity.stdout,
      "user\n1000\n/home/user/workspace\nv22.18.0\n10.9.3\n" +
        "git version 2.39.5\nPython 3.11.6\n" +
        "gcc (Debian 12.2.0-14) 12.2.0\nGNU Make 4.3\nuser:user:755\n",
    );
    pass("coding-identity-toolchain-readiness");

    const manifest = await sandbox.files.read("/etc/kitdev-coding-toolchain");
    assert.equal(
      manifest,
      `base_image=${baseImage}\nnode=v22.18.0\n` +
        `node_archive_sha256=${nodeArchiveSha256}\nnpm=10.9.3\n`,
    );
    pass("coding-toolchain-integrity-manifest");

    const typescript = [
      "type Result = { value: string };",
      'const result: Result = { value: "KITDEV_TYPESCRIPT_OK" };',
      "console.log(result.value);",
      "",
    ].join("\n");
    await sandbox.files.write("/home/user/workspace/main.ts", typescript);
    assert.equal(
      await sandbox.files.read("/home/user/workspace/main.ts"),
      typescript,
    );
    const node = await sandbox.commands.run("node main.ts", { timeoutMs: 30_000 });
    assert.equal(node.exitCode, 0);
    assert.equal(node.stderr, "");
    assert.equal(node.stdout, "KITDEV_TYPESCRIPT_OK\n");
    pass("coding-typescript-node");

    const shell = [
      "#!/bin/bash",
      "set -euo pipefail",
      "printf 'KITDEV_SHELL_OK:%s\\n' \"$(pwd)\"",
      "",
    ].join("\n");
    await sandbox.files.write("/home/user/workspace/check.sh", shell);
    const shellResult = await sandbox.commands.run(
      "chmod 0755 check.sh && ./check.sh",
      { timeoutMs: 30_000 },
    );
    assert.equal(shellResult.exitCode, 0);
    assert.equal(shellResult.stderr, "");
    assert.equal(shellResult.stdout, "KITDEV_SHELL_OK:/home/user/workspace\n");
    pass("coding-shell-files");

    const output = collectPty();
    const pty = await sandbox.pty.create({
      cols: 90,
      rows: 30,
      onData: output.onData,
      timeoutMs: 30_000,
    });
    await sandbox.pty.sendInput(
      pty.pid,
      new TextEncoder().encode(
        "printf 'KITDEV_CODING_PTY:%s:%s' \"$(id -un)\" \"$(pwd)\"; exit\n",
      ),
    );
    assert.equal((await pty.wait()).exitCode, 0);
    assert.match(
      output.text(),
      /KITDEV_CODING_PTY:user:\/home\/user\/workspace/,
    );
    pass("coding-pty");
  } finally {
    if (sandbox !== undefined) {
      assert.equal(await sandbox.kill(), true);
      pass("coding-sandbox-kill");
    }
  }
}

async function main(): Promise<void> {
  const connection = await loadConnection();
  const templateName = await loadTemplateName();
  assert.equal(await Template.exists(templateName, connection), false);
  pass("coding-template-absent-preflight");

  const installNode =
    `set -eu; archive=/tmp/${nodeArchive}; ` +
    `curl --proto '=https' --tlsv1.2 --fail --silent --show-error ` +
    `--location --output \"$archive\" '${nodeDownload}'; ` +
    `printf '%s  %s\\n' '${nodeArchiveSha256}' \"$archive\" | sha256sum -c -; ` +
    `tar -xJf \"$archive\" --strip-components=1 -C /usr/local; rm -f \"$archive\"; ` +
    `test \"$(node --version)\" = v22.18.0; test \"$(npm --version)\" = 10.9.3`;
  const createWorkspace =
    "set -eu; install -d -o user -g user -m 0755 /home/user/workspace; " +
    "printf '%s\\n' " +
    `'base_image=${baseImage}' 'node=v22.18.0' ` +
    `'node_archive_sha256=${nodeArchiveSha256}' 'npm=10.9.3' ` +
    "> /etc/kitdev-coding-toolchain; chmod 0644 /etc/kitdev-coding-toolchain";
  const codingTemplate = Template()
    .fromImage(baseImage)
    .runCmd(installNode, { user: "root" })
    .runCmd(createWorkspace, { user: "root" })
    .setEnvs({
      CI: "1",
      COREPACK_ENABLE_DOWNLOAD_PROMPT: "0",
    })
    .setWorkdir("/home/user/workspace")
    .setUser("user")
    .setStartCmd(
      "umask 077; mkdir -p /home/user/workspace/.kitdev; " +
        "printf 'ready\\n' > /tmp/kitdev-coding-ready; exec sleep infinity",
      "[ -s /tmp/kitdev-coding-ready ] && pgrep -u user -x sleep >/dev/null",
    );

  let buildLogEntries = 0;
  const build = await Template.build(
    codingTemplate,
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
  pass("coding-template-build");

  const tags = await Template.getTags(build.templateId, connection);
  assert(tags.some((item) => item.tag === templateTag && item.buildId === build.buildId));
  pass("coding-template-tag");
  await exerciseCodingSandbox(`${templateName}:${templateTag}`, connection);
}

main().catch((error: unknown) => {
  const kind = error instanceof Error ? error.constructor.name : "UnknownError";
  console.error(`status=error operation=coding-template kind=${kind}`);
  process.exit(1);
});

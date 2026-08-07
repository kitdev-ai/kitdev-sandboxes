import { mkdir, writeFile } from "node:fs/promises";

import { chromium } from "playwright";

const cdpPort = 9222;
const profile = "/home/pwuser/browser-profile";
const ready = "/tmp/kitdev-browser-ready";

await mkdir(profile, { recursive: true, mode: 0o700 });

const context = await chromium.launchPersistentContext(profile, {
  args: [
    `--remote-debugging-address=127.0.0.1`,
    `--remote-debugging-port=${cdpPort}`,
    "--renderer-process-limit=4",
    "--js-flags=--max-old-space-size=1024",
  ],
  chromiumSandbox: true,
  headless: true,
  viewport: { width: 1280, height: 720 },
});

let endpoint;
for (let attempt = 0; attempt < 100; attempt += 1) {
  try {
    const response = await fetch(`http://127.0.0.1:${cdpPort}/json/version`);
    if (response.ok) {
      const payload = await response.json();
      if (typeof payload.webSocketDebuggerUrl === "string") {
        endpoint = payload.webSocketDebuggerUrl;
        break;
      }
    }
  } catch {
    // Chromium can accept connections a moment after launch returns.
  }
  await new Promise((resolve) => setTimeout(resolve, 100));
}
if (endpoint === undefined) {
  await context.close();
  throw new Error("loopback CDP endpoint did not become ready");
}

await writeFile(ready, "ready\n", { encoding: "ascii", mode: 0o600 });

let closing = false;
async function close() {
  if (closing) return;
  closing = true;
  await context.close();
}
process.once("SIGINT", () => void close());
process.once("SIGTERM", () => void close());
await context.pages()[0].waitForEvent("close", { timeout: 0 });

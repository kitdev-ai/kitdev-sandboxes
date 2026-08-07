import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";

import { chromium } from "playwright";

const cdpUrl = "http://127.0.0.1:9222";
const siteUrl = "http://127.0.0.1:4173";
const screenshotPath = "/home/pwuser/downloads/browser-proof.png";
const downloadPath = "/home/pwuser/downloads/browser-proof.txt";
const downloadBody = "KITDEV_BROWSER_DOWNLOAD_OK\n";
const html = `<!doctype html>
<html lang="en">
  <head><meta charset="utf-8"><title>Kitdev browser proof</title></head>
  <body>
    <label>Message <input aria-label="Message"></label>
    <button type="button">Apply</button>
    <output>waiting</output>
    <a href="/artifact" download="browser-proof.txt">Download artifact</a>
    <script>
      document.querySelector('button').addEventListener('click', () => {
        document.querySelector('output').textContent =
          document.querySelector('input').value;
      });
    </script>
  </body>
</html>`;

const server = createServer((request, response) => {
  if (request.url === "/artifact") {
    response.writeHead(200, {
      "content-disposition": "attachment; filename=browser-proof.txt",
      "content-type": "text/plain; charset=utf-8",
    });
    response.end(downloadBody);
    return;
  }
  response.writeHead(200, { "content-type": "text/html; charset=utf-8" });
  response.end(html);
});
await new Promise((resolve, reject) => {
  server.once("error", reject);
  server.listen(4173, "127.0.0.1", resolve);
});

try {
  const versionResponse = await fetch(`${cdpUrl}/json/version`);
  assert.equal(versionResponse.status, 200);
  const versionPayload = await versionResponse.json();
  assert.equal(versionPayload.Browser, "HeadlessChrome/151.0.7922.34");
  assert.match(versionPayload.webSocketDebuggerUrl, /^ws:\/\/127\.0\.0\.1:9222\//);

  const browser = await chromium.connectOverCDP(cdpUrl);
  assert.equal(browser.version(), "151.0.7922.34");
  const context = browser.contexts()[0];
  assert(context !== undefined);
  const page = context.pages()[0] ?? (await context.newPage());
  await page.goto(siteUrl, { waitUntil: "domcontentloaded", timeout: 30_000 });
  assert.equal(await page.title(), "Kitdev browser proof");
  await page.getByRole("textbox", { name: "Message" }).fill("KITDEV_BROWSER_DOM_OK");
  await page.getByRole("button", { name: "Apply" }).click();
  assert.equal(await page.getByRole("status").textContent(), "KITDEV_BROWSER_DOM_OK");

  const screenshot = await page.screenshot({
    animations: "disabled",
    path: screenshotPath,
    type: "png",
  });
  assert(screenshot.byteLength > 1_000);
  assert.deepEqual([...screenshot.subarray(0, 8)], [137, 80, 78, 71, 13, 10, 26, 10]);

  const downloadPromise = page.waitForEvent("download", { timeout: 30_000 });
  await page.getByRole("link", { name: "Download artifact" }).click();
  const download = await downloadPromise;
  await download.saveAs(downloadPath);
  assert.equal(await readFile(downloadPath, "utf8"), downloadBody);

  console.log(JSON.stringify({
    browser: browser.version(),
    cdp_host: "127.0.0.1",
    dom: "KITDEV_BROWSER_DOM_OK",
    download_bytes: Buffer.byteLength(downloadBody),
    screenshot_bytes: screenshot.byteLength,
    screenshot_sha256: createHash("sha256").update(screenshot).digest("hex"),
  }));
} finally {
  await new Promise((resolve) => server.close(resolve));
}

// Exit drops only this CDP client. The template-owned browser stays alive until
// the sandbox is killed, which lets the outer gate verify lifecycle cleanup.
process.exit(0);

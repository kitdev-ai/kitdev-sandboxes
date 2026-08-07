import assert from "node:assert/strict";

import { FileType, FilesystemEventType, type FilesystemEvent } from "e2b";

import { fail, pass, withSandbox } from "./harness.ts";

async function readStream(stream: ReadableStream<Uint8Array>): Promise<Uint8Array> {
  const chunks: Uint8Array[] = [];
  let size = 0;
  for await (const chunk of stream) {
    chunks.push(chunk);
    size += chunk.byteLength;
  }
  const result = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    result.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return result;
}

function nextMatchingEvent(
  events: FilesystemEvent[],
  predicate: (event: FilesystemEvent) => boolean,
): Promise<FilesystemEvent> {
  return new Promise((resolve, reject) => {
    const deadline = setTimeout(() => reject(new Error("watch event timeout")), 10_000);
    const poll = (): void => {
      const event = events.find(predicate);
      if (event !== undefined) {
        clearTimeout(deadline);
        resolve(event);
      } else {
        setTimeout(poll, 25);
      }
    };
    poll();
  });
}

await withSandbox("files", async (sandbox) => {
  assert.equal(await sandbox.files.makeDir("/tmp/kitdev-sdk/nested"), true);
  const binary = new Uint8Array([0, 1, 2, 127, 128, 254, 255]);
  const writes = await sandbox.files.writeFiles(
    [
      { path: "/tmp/kitdev-sdk/nested/text.txt", data: "KITDEV_FILES_TEXT" },
      { path: "/tmp/kitdev-sdk/nested/data.bin", data: binary.buffer },
    ],
    { metadata: { suite: "files" } },
  );
  assert.equal(writes.length, 2);
  assert.equal(await sandbox.files.read("/tmp/kitdev-sdk/nested/text.txt"), "KITDEV_FILES_TEXT");
  assert.deepEqual(
    await sandbox.files.read("/tmp/kitdev-sdk/nested/data.bin", { format: "bytes" }),
    binary,
  );
  const blob = await sandbox.files.read("/tmp/kitdev-sdk/nested/data.bin", {
    format: "blob",
  });
  assert.deepEqual(new Uint8Array(await blob.arrayBuffer()), binary);
  const stream = await sandbox.files.read("/tmp/kitdev-sdk/nested/data.bin", {
    format: "stream",
    streamIdleTimeoutMs: 10_000,
  });
  assert.deepEqual(await readStream(stream), binary);
  pass("files-multiwrite-read-formats");

  const entries = await sandbox.files.list("/tmp/kitdev-sdk/nested");
  assert.deepEqual(
    entries.map((entry) => entry.name).sort(),
    ["data.bin", "text.txt"],
  );
  const info = await sandbox.files.getInfo("/tmp/kitdev-sdk/nested/data.bin");
  assert.equal(info.type, FileType.FILE);
  assert.equal(info.size, binary.byteLength);
  assert.deepEqual(info.metadata, { suite: "files" });
  assert.equal(await sandbox.files.exists(info.path), true);
  const renamed = await sandbox.files.rename(info.path, "/tmp/kitdev-sdk/nested/renamed.bin");
  assert.equal(renamed.name, "renamed.bin");
  assert.equal(await sandbox.files.exists(info.path), false);
  assert.equal(await sandbox.files.exists(renamed.path), true);
  await sandbox.files.remove(renamed.path);
  assert.equal(await sandbox.files.exists(renamed.path), false);
  pass("files-list-info-rename-remove");

  await sandbox.files.makeDir("/tmp/kitdev-sdk/watch");
  const events: FilesystemEvent[] = [];
  const watch = await sandbox.files.watchDir(
    "/tmp/kitdev-sdk/watch",
    (event) => {
      events.push(event);
    },
    { includeEntry: true, recursive: true, timeoutMs: 20_000 },
  );
  try {
    await sandbox.files.write("/tmp/kitdev-sdk/watch/event.txt", "KITDEV_WATCH");
    const event = await nextMatchingEvent(
      events,
      (candidate) =>
        candidate.name.endsWith("event.txt") &&
        (candidate.type === FilesystemEventType.CREATE ||
          candidate.type === FilesystemEventType.WRITE),
    );
    assert.equal(event.entry?.type, FileType.FILE);
  } finally {
    await watch.stop();
  }
  pass("files-watch-recursive-entry");

  await sandbox.files.remove("/tmp/kitdev-sdk");
  assert.equal(await sandbox.files.exists("/tmp/kitdev-sdk"), false);
  pass("files-remove-tree");
}).catch((error: unknown) => fail("files", error));

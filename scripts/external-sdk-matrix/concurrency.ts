// Concurrency and capacity probe.
//
// Answers two questions the feature matrix cannot: how many sandboxes this
// host actually runs at once, and what happens at the ceiling. Concurrent
// sandbox memory is served from the persistent HugeTLB pool, so the ceiling is
// the pool, not the team limit. The team limit only decides how far the API
// lets you push before the pool decides.
//
// Usage:
//   E2B_API_URL=... E2B_DOMAIN=... E2B_API_KEY_FILE=... \
//   KITDEV_FLEET=6 node concurrency.ts

import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

import { Sandbox, type ConnectionOpts } from "e2b";

const CODING = process.env.KITDEV_CODING_TEMPLATE ?? "kitdev-coding:stable";
const HEAVY = process.env.KITDEV_BROWSER_TEMPLATE ?? "kitdev-browser-heavy:stable";
const FLEET = Number(process.env.KITDEV_FLEET ?? "6");
const PROBE_HEAVY = process.env.KITDEV_PROBE_HEAVY === "1";
const TIMEOUT_MS = 10 * 60_000;

function requiredEnv(name: string): string {
  const value = process.env[name];
  if (value === undefined || value === "") throw new Error(`${name} is required`);
  return value;
}

async function loadConnection(): Promise<ConnectionOpts> {
  const apiKey = (await readFile(requiredEnv("E2B_API_KEY_FILE"), "ascii")).trim();
  assert.match(apiKey, /^e2b_[0-9a-f]{40}$/);
  return {
    apiKey,
    apiUrl: requiredEnv("E2B_API_URL"),
    domain: requiredEnv("E2B_DOMAIN"),
    requestTimeoutMs: 180_000,
  };
}

async function killAll(sandboxes: Sandbox[]): Promise<void> {
  await Promise.allSettled(
    sandboxes.map(async (sandbox) => {
      if (await sandbox.isRunning().catch(() => false)) await sandbox.kill();
    }),
  );
}

const connection = await loadConnection();
const started: Sandbox[] = [];

try {
  // --- Fleet: prove N small sandboxes are genuinely concurrent -------------
  const begin = Date.now();
  const fleet = await Promise.all(
    Array.from({ length: FLEET }, (_unused, index) =>
      Sandbox.create(CODING, {
        ...connection,
        metadata: { kitdev_test: `concurrency-${index}` },
        timeoutMs: TIMEOUT_MS,
      }),
    ),
  );
  started.push(...fleet);
  const elapsed = Math.round((Date.now() - begin) / 1000);
  console.log(`status=pass operation=fleet-create count=${fleet.length} seconds=${elapsed}`);

  assert.equal(new Set(fleet.map((sandbox) => sandbox.sandboxId)).size, fleet.length);

  // Every one must be independently alive and executing at the same moment.
  // Guest hostnames are template-derived and therefore identical, so identity
  // is proved by round-tripping a per-sandbox token through each guest.
  const identities = await Promise.all(
    fleet.map(async (sandbox, index) => {
      const token = `KITDEV_FLEET_${index}_${sandbox.sandboxId}`;
      await sandbox.files.write("/home/user/fleet-token", token);
      const result = await sandbox.commands.run("cat /home/user/fleet-token", {
        timeoutMs: 60_000,
      });
      assert.equal(result.exitCode, 0);
      assert.equal(result.stdout, token);
      return result.stdout;
    }),
  );
  assert.equal(new Set(identities).size, fleet.length, "sandboxes are not distinct");
  console.log(`status=pass operation=fleet-concurrent-exec count=${identities.length}`);

  const listed = await Sandbox.list({ ...connection, limit: 100 }).nextItems();
  const live = listed.filter((item) =>
    String(item.metadata?.kitdev_test ?? "").startsWith("concurrency-"),
  );
  assert.equal(live.length, fleet.length);
  console.log(`status=pass operation=fleet-all-running count=${live.length}`);

  await killAll(fleet);
  started.length = 0;
  console.log(`status=pass operation=fleet-kill count=${fleet.length}`);

  // --- Ceiling: what happens when the hugepage pool runs out --------------
  if (PROBE_HEAVY) {
    const heavy: Sandbox[] = [];
    let refusedAt = 0;
    for (let index = 0; index < 4; index += 1) {
      try {
        const sandbox = await Sandbox.create(HEAVY, {
          ...connection,
          metadata: { kitdev_test: `capacity-heavy-${index}` },
          timeoutMs: TIMEOUT_MS,
        });
        heavy.push(sandbox);
        started.push(sandbox);
        console.log(`status=progress operation=heavy-create index=${index} result=started`);
      } catch (error: unknown) {
        refusedAt = index;
        const kind = error instanceof Error ? error.constructor.name : "UnknownError";
        console.log(`status=pass operation=heavy-refused-at index=${index} kind=${kind}`);
        break;
      }
    }
    console.log(`status=pass operation=heavy-capacity concurrent=${heavy.length} refused_at=${refusedAt}`);

    // A refusal must not damage the survivors.
    for (const sandbox of heavy) {
      const result = await sandbox.commands.run("printf ALIVE", { timeoutMs: 60_000 });
      assert.equal(result.stdout, "ALIVE");
    }
    console.log(`status=pass operation=heavy-survivors-healthy count=${heavy.length}`);
    await killAll(heavy);
    started.length = 0;
    console.log("status=pass operation=heavy-kill");
  }

  const remaining = await Sandbox.list({ ...connection, limit: 100 }).nextItems();
  const leaked = remaining.filter((item) =>
    /^(concurrency|capacity-heavy)-/.test(String(item.metadata?.kitdev_test ?? "")),
  );
  assert.deepEqual(leaked, []);
  console.log("status=pass operation=cleanup-verified");
} catch (error: unknown) {
  const kind = error instanceof Error ? error.constructor.name : "UnknownError";
  console.error(`status=error operation=concurrency kind=${kind}`);
  await killAll(started);
  process.exit(1);
}

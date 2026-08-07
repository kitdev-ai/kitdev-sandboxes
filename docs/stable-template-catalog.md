# Stable template catalog

This page is the handoff between the bare-metal operator and the product-side
AI agent. Host installation remains in the
[bare-metal operator guide](bare-metal-operator-guide.md); client setup remains
in the [TypeScript SDK integration guide](typescript-sdk-integration-guide.md).

The live development catalog currently contains:

| Workload | Stable reference | Pinned reference | Status |
| --- | --- | --- | --- |
| Coding | `kitdev-coding:stable` | `kitdev-coding:v1` | Published and product-key verified |
| Browser | `kitdev-browser-heavy:stable` | `kitdev-browser-heavy:v1` | Published and product-key verified |

## Bare-metal operator

On the current Ubuntu 25.04 development lab, publish serially with the
team-specific root-only key files:

```console
sudo env KITDEV_LIFECYCLE=development \
  ./scripts/control-plane/publish-stable-template.sh publish \
  --product coding --version v1 --api-key-file <coding-team-key-file>

sudo env KITDEV_LIFECYCLE=development \
  ./scripts/control-plane/publish-stable-template.sh publish \
  --product browser-heavy --version v1 \
  --api-key-file /etc/kitdev-sandboxes/e2e-browser-heavy-api-key
```

Both commands build with `e2b@2.38.0`, boot and qualify a sandbox, publish the
bare alias, verify the exact database relationships, and atomically commit the
ownership journal. A successful rerun reports `result=unchanged`.

Verify each alias with the product key, without template-management calls:

```console
sudo env KITDEV_LIFECYCLE=development \
  ./scripts/control-plane/publish-stable-template.sh verify-consumer \
  --product coding --version v1 --api-key-file <product-key-file>
```

To withdraw the first release's default pointer without deleting its immutable
versioned build:

```console
sudo env KITDEV_LIFECYCLE=development \
  ./scripts/control-plane/publish-stable-template.sh rollback \
  --product coding --version v1 --api-key-file <coding-team-key-file>
```

The current legacy lab has no deployed host-wide admission service. While an
external key is active, do not run local builds or sandboxes. Revoke the
external key before maintenance. This restriction ends only after the
documented admission migration is live-qualified.

## Product-side AI agent

Use only the public catalog names supplied by the operator:

```ts
import { Sandbox } from "e2b";

const sandbox = await Sandbox.create("kitdev-coding:stable", {
  apiKey: process.env.E2B_API_KEY,
  apiUrl: process.env.E2B_API_URL,
  domain: process.env.E2B_DOMAIN,
  sandboxUrl: process.env.E2B_SANDBOX_URL,
});

try {
  const result = await sandbox.commands.run("node --version");
  if (result.exitCode !== 0) throw new Error(result.stderr);
} finally {
  await sandbox.kill();
}
```

Choose `kitdev-browser-heavy:stable` only for browser automation. Use the
versioned aliases (`:v1`) when reproducibility is more important than receiving
the operator's next qualified release. The AI agent must never build templates,
change tags, publish templates, or receive an administrator/team-owner key.

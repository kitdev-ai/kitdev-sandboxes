# Coding template contract and live record

Date: 2026-08-07

Status: live-proven through the official SDK and local template manager.

## Product boundary

The coding template is a non-graphical environment for AI-driven repository
work: shell commands, Git, TypeScript/Node.js, Python, native compilation,
SDK-managed files, and PTYs. Browser automation, desktop streaming, CDP,
screenshots, and input injection are deliberately excluded and belong to later
templates with different dependencies and resource profiles.

## Pinned inputs

The exact base is:

```text
e2bdev/base@sha256:4a369f01a820fe5e65f53c2c5727a78899daf86f0541b721097f289559c8b73f
```

The disposable host's local Docker image reports that same repository digest
and content ID. Its observed toolchain is Debian 12, Bash 5.2.15, Git 2.39.5,
Python 3.11.6, GCC/G++ 12.2.0, and GNU Make 4.3. Those tools are locked as a
unit by the base image digest rather than reinstalled from moving apt indexes.

The template overlays the official Linux x64 archive for Node.js `22.18.0`:

```text
archive: node-v22.18.0-linux-x64.tar.xz
bytes: 30450292
sha256: c1bfeecf1d7404fa74728f9db72e697decbd8119ccc6f5a294d795756dfcfca7
npm: 10.9.3
```

The SHA-256 was extracted from the official release's `SHASUMS256.txt`, and the
archive is verified with `sha256sum -c` before extraction. The Node.js archive
page identifies npm `10.9.3` for this release. Primary sources:

- [Node.js v22.18.0 release directory](https://nodejs.org/dist/v22.18.0/)
- [Official SHA-256 manifest](https://nodejs.org/dist/v22.18.0/SHASUMS256.txt)
- [Node.js v22.18.0 archive metadata](https://nodejs.org/en/download/archive/v22.18.0)
- [Node.js v22.18.0 release notes](https://nodejs.org/en/blog/release/v22.18.0)

## Template behavior

The official `e2b@2.38.0` template API builds the image with:

```text
vCPU: 2
RAM: 2048 MB
default user: user (UID 1000)
workdir: /home/user/workspace
CI: 1
COREPACK_ENABLE_DOWNLOAD_PROMPT: 0
```

The build creates the workspace as `user:user` mode `0755` and writes a
root-owned, world-readable integrity manifest at
`/etc/kitdev-coding-toolchain`. It then sets the template's user and working
directory through SDK instructions, not shell profile side effects.

The sandbox start command, running as `user`, creates a private `.kitdev`
directory, writes `/tmp/kitdev-coding-ready`, and remains alive with
`sleep infinity`. The SDK create call is not considered ready until the file is
nonempty and that user-owned process exists. This provides a concrete startup
contract without adding an application server to a general coding image.

Every SDK, build, command, and PTY operation has a timeout or is bounded by the
outer build status behavior. The runner rejects production lifecycle mode,
shares the host SDK lock, refuses preexisting Firecracker processes, and uses
unique pre-recorded template names. Credentials are a read-only root-owned
mount and exception messages are not emitted.

## Live gate

The gate builds the coding template, boots one sandbox from its tagged build,
then verifies:

1. `user`, UID 1000, workspace, readiness marker, and startup process.
2. Exact Node, npm, Git, Python, GCC, and Make version output.
3. The persisted base/archive integrity manifest.
4. Native Node.js execution of an erasable-syntax TypeScript file written with
   the official filesystem SDK.
5. A strict Bash script written with the SDK and executed from the workspace.
6. PTY creation, input, output, default identity, and working directory.

The cleanup trap kills the sandbox, verifies API/Redis/Firecracker absence,
soft-deletes the template by returned ID or pre-recorded name, and removes the
ephemeral test stage. It then requires the test alias to resolve as absent. The
official template delete handler retains historical
database build rows and immutable artifacts by design; the gate does not
recursively remove internal template storage that can contain shared ancestor
objects.

## Sanitized activity

Read-only inventory and research performed before the live build:

- inspected the pinned SDK template/start-readiness implementation;
- ran an ephemeral local Docker container from the already-present base image
  to collect OS, identity, tool versions, and filesystem ownership;
- inspected local image content/repository digests;
- retrieved the official Node.js release index, archive metadata, release
  notes, and SHA-256 manifest;
- observed zero Firecracker processes while another SDK suite held the shared
  lock.

The Docker inventory container was removed automatically. No package, user,
kernel, firewall, systemd, persistent configuration, template, or sandbox was
created by this inventory phase.

Live mutations and results:

1. Copied the isolated runner, SDK client, common helper, and pinned npm
   manifests into one unique root-owned source directory below `/tmp`.
2. The first runner invocation lost a race for the shared SDK lock and exited
   75 with `sdk_e2e_already_running` before npm installation or any API call.
3. After the lock became free with zero Firecracker processes, the second
   invocation created a root-only ephemeral stage below `/run`, installed the
   exact `e2b@2.38.0` client with npm scripts disabled, and submitted one real
   coding-template build.
4. The build converted the immutable base image, downloaded the exact Node.js
   archive inside its isolated build VM, verified the published SHA-256, and
   produced one tagged template with the specified CPU, RAM, identity,
   workspace, environment, start command, and readiness check.
5. A sandbox booted from that tag and passed every planned gate:

```text
coding-sandbox-create-ready
coding-identity-toolchain-readiness
coding-toolchain-integrity-manifest
coding-typescript-node
coding-shell-files
coding-pty
coding-sandbox-kill
```

6. The runner's trap observed the SDK kill, retried the idempotent API delete,
   waited until the sandbox was absent from API, Redis, and Firecracker state,
   soft-deleted the template, removed the ephemeral `/run` stage, and exited 0.

As with the generic template-build gate, the soft-deleted historical database
row and immutable template artifact remain under service ownership. No host
package, user, kernel, firewall, service, or persistent configuration was
changed. The temporary manual source directory is removed separately after the
coordinated SDK diagnostic window. Its exact path was checked against the core
agent's distinct source directory before deletion, and the core directory was
left untouched.

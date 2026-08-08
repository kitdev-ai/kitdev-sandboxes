# Bare-metal operator guide

This guide is for the end user responsible for preparing, installing,
qualifying, operating, and recovering the complete sandbox system on one bare
metal server. It describes what that operator can do with the repository today
and what is still planned. It is not a production-readiness declaration.

## Read this first

The current system has a working pinned control plane, Firecracker runtime,
local template, official TypeScript SDK and template-build tests, a live-proven
coding-template gate, a live-proven loopback Chromium/Playwright gate, and
optional HTTPS ingress. It does **not** yet have a complete fresh-host installer
or production-published coding/browser templates.

`sudo ./kitdev install` itself applies only the minimal control plane on an
already prepared host in explicit `development` or `migration` mode. A separate
partial prerequisite flow installs host packages, creates service identities,
and configures required kernel state. Neither flow formats or mounts storage,
installs Docker, or completes production template publication. Production
install refuses before mutation. Do not bypass these gates.

The disposable lab was prepared manually to discover the correct host state.
Those manual steps are evidence, not a supported installation procedure. A
clean Ubuntu 26.04 reinstall followed solely by reviewed automation remains the
production qualification gate.

## Supported target

| Area | Current contract |
| --- | --- |
| Host | One physical bare-metal server; direct Ubuntu is preferred over nested virtualization |
| Production OS | Ubuntu 26.04 LTS, not yet fully qualified end to end |
| Development/migration OS | Ubuntu 25.04 only with an explicit lifecycle mode; it is not production eligible |
| Unsupported OS | Ubuntu 24.04 and every unlisted release |
| Architecture | `x86_64` |
| Init/cgroups | systemd and cgroups v2 |
| Virtualization | Hardware virtualization enabled, KVM usable through `/dev/kvm` |
| Kernel facilities | TUN/TAP, NBD with at least 16 devices, IPv4 forwarding, 2 MiB hugepages |
| Containers | Docker Engine with Buildx and Compose v2 |

There is no defensible universal CPU, RAM, disk, inode, NBD, or hugepage
minimum yet. Capacity depends on sandbox concurrency and template sizes. The
development lab uses a 4-core/8-thread CPU, 64 GB RAM, mirrored NVMe system
storage, and a dedicated 4 TB data disk; that is a tested reference, not a
minimum or production sizing promise.

The prerequisite profile provides two 8 GiB live-sandbox slots plus one 8 GiB
transient-mapping allowance. Its 24 GiB persistent pool (`12288` 2 MiB
hugepages) covers either two live guests plus one snapshot mapping, or one live
guest plus a build requiring two guest-sized mappings. It does not cover two
live guests and that build together; that needs 32 GiB. The role refuses to
reserve more than 50% of total RAM and requires 16 GiB of normal memory to
remain available after new pages are allocated. The runtime startup gate still
checks only its older 512-page free floor, so this profile is a host
reservation, not runtime admission control. Do not treat it as proof that a
third sandbox will be rejected or that the profile has passed sustained load
qualification.

## Storage layout

Keep project ownership explicit:

| Path | Purpose | Backup class |
| --- | --- | --- |
| `/opt/kitdev-sandboxes` | Installed immutable assets and pinned source | Reproducible, but retain release metadata |
| `/etc/kitdev-sandboxes` | Operator configuration, generated secrets, TLS keys | Secret, required |
| `/var/lib/kitdev-sandboxes` | Databases, templates, snapshots, caches, sandbox data | Durable, required |
| `/var/log/kitdev-sandboxes` | Project logs outside journald/container logs | Operational |
| `/run/kitdev-sandboxes` | Locks and transient test inputs | Ephemeral; do not back up |

Use a dedicated project filesystem or mount for
`/var/lib/kitdev-sandboxes/data`. Prefer ext4 until another filesystem is
qualified. Do not select, partition, format, or mount a disk based only on its
device name or size. Storage preparation and migration are not implemented by
`kitdev` yet.

Docker's active containerd store may still occupy `/var/lib/containerd` on the
system disk even when project data is elsewhere. Containerd relocation is not
implemented. Monitor both the system filesystem and the project data
filesystem.

## DNS plan

For the documented public topology, create one wildcard IPv4 record:

```text
*.sandbox.kitdev.ai  A  <server-public-ipv4>
```

This covers:

- `api.sandbox.kitdev.ai` for the E2B API;
- `<port>-<sandbox-id>.sandbox.kitdev.ai` for SDK sandbox traffic;
- `sandbox.sandbox.kitdev.ai` for shared-host header routing.

Do not publish an `AAAA` record unless IPv6 is configured and tested end to
end. Use DNS-only mode while qualifying ingress. A wildcard certificate
requires DNS-01, so the DNS provider must offer API credentials that can create
and delete TXT records below `_acme-challenge.sandbox.kitdev.ai`.

## Secure operator setup

1. Use SSH public-key authentication and retain a separate recovery path.
2. Keep the Git checkout and installed assets non-writable by service users.
3. Never put API keys, ACME credentials, private keys, or provider tokens in
   Git, command arguments, chat, shell history, or copied logs.
4. Store operator secret files as `root:root`, mode `0600`, with link count one
   so no additional hard link aliases exist.
5. Give DNS credentials only the zone permissions required for ACME TXT
   records; use provider-supported token-file variables where possible.

The control-plane installer generates its internal database/admin secrets and
preserves valid existing values. It does not print them. Public E2B API-key
provisioning is not yet exposed through `kitdev`; the current test commands
require an independently created root-owned API-key file.

## Obtain and inspect a release

Work from a reviewed immutable tag or commit, never a floating branch:

```console
git clone https://github.com/kitdev-ai/kitdev-sandboxes.git
cd kitdev-sandboxes
git switch --detach <reviewed-tag-or-full-commit>
git status --short
```

The last command must print nothing. Review `versions.lock.yaml`, the release
notes, and the documented limitations before using `sudo`.

## Read-only qualification

Run doctor without `sudo`:

```console
./kitdev doctor --json --verbose
./kitdev install --dry-run --json
```

For Ubuntu 25.04, select the lifecycle explicitly:

```console
./kitdev doctor --lifecycle-mode development --json --verbose
./kitdev install --lifecycle-mode development --dry-run --json
```

`doctor` and `install --dry-run` are strictly read-only. The current doctor
still reports required-port policy as unknown, so exit code 5 is expected on a
host that may otherwise look suitable. Do not reinterpret that as a pass.

Doctor exit codes are:

| Code | Meaning |
| ---: | --- |
| 0 | Required checks passed; warnings may remain |
| 2 | Invalid invocation or configuration |
| 3 | Unsupported platform or missing hard requirement |
| 4 | Resource, port, service, network, or ownership conflict |
| 5 | Required fact could not be collected |
| 6 | Installed deployment is unhealthy |
| 10 | Unexpected internal error |

## Host prerequisite preparation

The first fresh-host Ansible slice converges Ubuntu archive trust, a narrow host
package set, reserved service identities, KVM membership, and persistent
KVM/TUN/NBD/hugepage/IPv4-forwarding state. It does not prepare storage,
install Docker, or configure the firewall. Those later roles remain required
before `kitdev install` can run on a clean host.

Review the exact plan from an immutable checkout:

```console
sudo ./scripts/host-prerequisites.sh bootstrap production
sudo ./scripts/host-prerequisites.sh check production
sudo ./scripts/host-prerequisites.sh apply production
```

For Ubuntu 25.04, replace `production` with `development` or `migration`.
Bootstrap creates only the hash-locked repository-local Ansible environment;
if Ubuntu omitted the standard `venv` module, it installs only `python3-venv`.
Apply refuses unsupported APT sources, identity collisions, insufficient
hugepage capacity, unsafe live NBD reconfiguration, and foreign rollback state
before project host mutation.

Pre-change state and the final ownership manifest are root-only files below
`/var/lib/kitdev-sandboxes/host-prerequisites`. Removal first verifies managed
file hashes and that service identities own no processes:

```console
sudo ./scripts/host-prerequisites.sh remove-check production
sudo ./scripts/host-prerequisites.sh remove production
```

Removal restores prior files and sysctls and removes only identities/packages
created by the prerequisite slice. It deliberately does not unload live kernel
modules; perform a controlled reboot after removal if returning module state to
the pre-install boot baseline. Do not assemble any remaining production steps
from the disposable-lab experiment scripts.

## Prepared-host install

On a host that has already been prepared to the exact reviewed contract, the
only implemented apply is minimal development/migration:

```console
sudo ./kitdev install --profile minimal --lifecycle-mode development
```

The command checks prerequisites before control-plane mutation, then converges
layout, generated secrets, the private Docker network, pinned source and
images, runtime artifacts, firewall rules, systemd service, Compose services,
and the development template. Re-running the same release is intended to be
convergent, but clean-host apply/apply qualification is still pending.

These invocations intentionally fail before mutation:

```console
sudo ./kitdev install --profile minimal --lifecycle-mode production
sudo ./kitdev install --profile full --lifecycle-mode development
```

The first lacks a production-qualified template publication path. The second
requests an unimplemented profile.

## Configuration

`config/default.yaml` is the non-secret default contract, validated by
`config/schema.json`. A command can use a separate validated operator file:

```console
./kitdev doctor --config /absolute/path/to/operator.yaml --json
sudo ./kitdev install --config /absolute/path/to/operator.yaml \
  --lifecycle-mode development
```

The intended installed path is `/etc/kitdev-sandboxes/config.yaml`, but a
`kitdev configure` command and full installed-configuration convergence are
not implemented. Unknown keys and unsafe project paths are rejected. Secrets
do not belong in this YAML file.

## Lifecycle operations

Run day-two commands from the same reviewed checkout. Install publishes the
shell assets below `/opt`; the repository-local launcher dispatches to that
installed copy when it exists.

```console
sudo ./kitdev up --lifecycle-mode development
sudo ./kitdev status --lifecycle-mode development
sudo ./kitdev status --lifecycle-mode development --json
sudo ./kitdev restart --lifecycle-mode development
sudo ./kitdev down --lifecycle-mode development
```

`down` preserves containers, images, databases, templates, networks, and
configuration. It refuses active Firecracker processes, quiesces API/proxy
admission, checks for a racing sandbox, stops the orchestrator, and then stops
Compose. A later failure attempts to restore the running service set. Delete
active sandboxes through the official SDK before retrying; do not kill
Firecracker manually.

Dry-run is available for day-two commands and changes nothing:

```console
sudo ./kitdev restart --lifecycle-mode development --dry-run --json
```

A standalone installed `/usr/local/bin/kitdev` command is not implemented; run
the reviewed checkout's top-level `./kitdev` entry point.

## Project API keys

The control plane's admin API is loopback-only. Run API-key lifecycle commands
as root on the sandbox host. By default they read `ADMIN_TOKEN` from the exact
root-owned mode-`0600` file
`/etc/kitdev-sandboxes/control-plane.env`. To use a file containing only the
raw admin token, select `--admin-token-file`; to select another environment
file, use `--private-env-file`. The two formats are never auto-detected.

For a control plane with exactly one nonblocked, nonbanned team, the common
create flow is:

```console
sudo ./kitdev api-key create \
  --name my-app \
  --output /etc/kitdev-sandboxes/secrets/my-app.key
sudo ./kitdev api-key verify \
  --key-file /etc/kitdev-sandboxes/secrets/my-app.key \
  --metadata-file /etc/kitdev-sandboxes/secrets/my-app.key.metadata.json
sudo ./kitdev api-key list
sudo ./kitdev api-key teams
```

Create resolves the team only when exactly one eligible local team exists;
otherwise inspect the read-only `teams` output and pass `--team-slug <slug>` or
`--team-id <uuid>`. For example, use
`--team-slug kitdev-browser-heavy-team` for a separately provisioned heavy
browser team. The selectors are mutually exclusive and slug matching is exact.
The output directory must already exist, be
root-owned, and not be group- or world-writable. The final key may be assigned
to a service with `--owner` and `--group`, but its parent remains root-owned.
The key is atomically published as mode `0600`; it is never printed. Root-owned
mode-`0600` metadata defaults to `<output>.metadata.json` and records only the
key ID, mask, ownership, and crash-recovery journal. The upstream key name gains
a `--kitdev-<operation-id>` suffix so an interrupted create can be reconciled
without exposing or duplicating its secret. Repeating the identical create
command verifies and returns the existing key, or completes a journaled
interruption.

Revocation requires the same UUID twice:

```console
sudo ./kitdev api-key revoke \
  --team-slug <team-slug> \
  --key-id <key-uuid> \
  --confirm-key-id <same-key-uuid> \
  --metadata-file /etc/kitdev-sandboxes/secrets/my-app.key.metadata.json \
  --delete-key-file
```

Without `--delete-key-file`, revocation deliberately retains the local raw key
for operator-controlled audit or cleanup even though it no longer authenticates.
Deletion requires matching metadata. The remote revoke and revoked metadata
journal become durable before the exact metadata-bound regular key file is
removed, so rerunning the command can finish an interrupted deletion. Retain
the metadata as a nonsecret audit and idempotency record.

## Post-install tests

Tests create real microVMs and are forbidden in production mode. First ensure
no other sandbox suite or Firecracker process is active. Supply transient
root-owned mode-0600 files containing the API key and template ID:

```console
sudo ./kitdev test core --lifecycle-mode development \
  --api-key-file /run/kitdev-sandboxes/e2e-api-key

sudo ./kitdev test sdk --lifecycle-mode development \
  --api-key-file /run/kitdev-sandboxes/e2e-api-key \
  --template-id-file /run/kitdev-sandboxes/e2e-template-id

sudo ./kitdev test smoke --lifecycle-mode development \
  --api-key-file /run/kitdev-sandboxes/e2e-api-key \
  --template-id-file /run/kitdev-sandboxes/e2e-template-id
```

`core` verifies the API, Firecracker, client proxy, and envd path with the
pinned Go client. `sdk` runs the official pinned TypeScript SDK gate. `smoke`
runs both. Each verifier owns cleanup, but always confirm final status:

```console
sudo ./kitdev status --lifecycle-mode development --json
```

API-key provisioning is available through `kitdev api-key`; stable template-ID
or alias publication is not yet automated.

### Template qualification gates

The top-level `kitdev test` command does not expose the template-build or
product-template gates. From the same reviewed checkout, an operator can run
the three currently passing low-level gates explicitly:

```console
sudo env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  KITDEV_LIFECYCLE=development \
  /usr/bin/bash scripts/control-plane/verify-typescript-sdk-template-build.sh \
  --api-key-file /run/kitdev-sandboxes/e2e-api-key

sudo env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  KITDEV_LIFECYCLE=development \
  /usr/bin/bash scripts/control-plane/verify-typescript-sdk-coding-template.sh \
  --api-key-file /run/kitdev-sandboxes/e2e-api-key

sudo env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  KITDEV_LIFECYCLE=development \
  /usr/bin/bash scripts/control-plane/verify-typescript-sdk-browser-template.sh \
  --api-key-file /run/kitdev-sandboxes/e2e-api-key
```

These development-only tests use the same shared SDK lock, refuse a preexisting
Firecracker process, build uniquely named templates, create real sandboxes, and
attempt cleanup on every exit. The generic gate proves background/blocking SDK
builds, status, tags, and boot from the result. The coding gate proves its
pinned toolchain, unprivileged workspace, TypeScript and shell execution,
SDK-managed files, and PTY. The browser gate proves non-root Chromium readiness,
loopback CDP, Playwright navigation/DOM interaction, and screenshot/download
collection through the SDK. See the
[browser qualification guide](browser-sandbox-guide.md) for its Chromium-only,
non-public boundary. All three gates remove their test aliases; none publishes
a stable product template. Confirm `kitdev status` after each run.

## Public HTTPS ingress

Ingress is implemented as a separate reviewed script flow; it is not wired
into `kitdev install` or `kitdev up` yet. Complete DNS first.

Stage pinned ingress assets without opening public ports:

```console
sudo env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  KITDEV_LIFECYCLE=development \
  /usr/bin/bash scripts/ingress/install-ingress.sh stage
```

Create the two operator files from the installed examples, then edit them with
`sudoedit`:

```console
sudo install -o root -g root -m 0600 \
  /etc/kitdev-sandboxes/ingress/ingress.env.example \
  /etc/kitdev-sandboxes/ingress/ingress.env
sudo install -o root -g root -m 0600 \
  /etc/kitdev-sandboxes/ingress/acme-provider.env.example \
  /etc/kitdev-sandboxes/ingress/acme-provider.env
sudoedit /etc/kitdev-sandboxes/ingress/ingress.env
sudoedit /etc/kitdev-sandboxes/ingress/acme-provider.env
```

For this deployment set `sandbox.kitdev.ai`, `mohitagrwl97@gmail.com`, provider
`cloudflare`, and the staging ACME server in `ingress.env`. Put exactly
`CLOUDFLARE_DNS_API_TOKEN_FILE=/etc/kitdev-sandboxes/ingress/cloudflare-dns-api-token`
in `acme-provider.env`. Put only the token in that separate root-owned mode
`0600` file. Use a Cloudflare API token limited to `Zone:DNS:Edit` and
`Zone:Zone:Read` for `kitdev.ai`; do not use the Global API Key. The scripts
parse provider configuration as data and never source it.

### Creating the Cloudflare DNS-01 token

Create the token in the Cloudflare dashboard under **My Profile → API Tokens →
Create Token → Create Custom Token**:

| Field | Value |
|---|---|
| Token name | `kitdev-sandboxes-dns01` |
| Permissions | `Zone` / `DNS` / `Edit` **and** `Zone` / `Zone` / `Read` |
| Zone resources | Include / Specific zone / the project apex zone |
| Client IP filtering | Optional; restrict to the sandbox host's public IPv4 |
| TTL | Leave unrestricted, or renew before expiry; ACME renewal needs it valid |

Two permission rows are required. `Zone:Read` alone cannot create the
`_acme-challenge` TXT record and `DNS:Edit` alone cannot resolve the zone ID.
Cloudflare displays the secret exactly once.

Install it without placing it in shell history, an argument, or a variable.
Run this from a trusted administrator session, paste the token, press Enter,
then press `Ctrl-D`:

```console
sudo tee /etc/kitdev-sandboxes/ingress/cloudflare-dns-api-token >/dev/null
```

Confirm the result without printing the value:

```console
sudo stat -c 'owner=%U:%G mode=%a links=%h size=%s' \
  /etc/kitdev-sandboxes/ingress/cloudflare-dns-api-token
```

It must report `root:root`, mode `600`, link count one, and a nonzero size.
`tee` preserves the existing ownership and mode of the staged empty file; the
trailing newline is stripped by the provider loader. If the file was recreated
with the wrong metadata, fix it with `sudo chown root:root` and
`sudo chmod 0600` before issuing a certificate.

Prove DNS automation against Let's Encrypt staging:

```console
sudo env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  KITDEV_LIFECYCLE=development \
  /usr/bin/bash scripts/ingress/manage-certificate.sh issue-staging
```

After staging succeeds, use `sudoedit` to change only `KITDEV_ACME_SERVER` to
`https://acme-v02.api.letsencrypt.org/directory`, issue the production
certificate, and apply ingress:

```console
sudo env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  KITDEV_LIFECYCLE=development \
  /usr/bin/bash scripts/ingress/manage-certificate.sh issue
sudo env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  KITDEV_LIFECYCLE=development \
  /usr/bin/bash scripts/ingress/install-ingress.sh apply
sudo env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  KITDEV_LIFECYCLE=development \
  /usr/bin/bash scripts/ingress/install-ingress.sh verify
```

Apply starts the read-only Nginx ingress container, enables the renewal timer,
and converges the persisted firewall mode. TCP 80 always stays closed.

The firewall step first proves that this automation installed and owns the
control-plane rules. A manually assembled development lab has correctly scoped
rules that this automation did not install, so that proof cannot succeed there.
Only in that case, and only in development lifecycle, acknowledge it explicitly
by adding `KITDEV_UNMANAGED_CONTROL_PLANE_FIREWALL=acknowledged` to the `env -i`
list. The acknowledgement gives up the ownership proof and nothing else: UFW
default-deny, IPv6 filtering, the internal-listener scope check, the Docker
publication scope check, and the rule scan that refuses any sensitive port
allowed from an unrestricted source all still run and still fail closed. Never
use it on a production host; converge the control plane instead.

Before the external gate, deliberately enable temporary public HTTPS:

```console
sudo kitdev firewall mode public
```

This opens only TCP 443 to all IPv4/IPv6 sources and emits a warning. Return to
the saved CIDRs with `sudo kitdev firewall mode restricted`, or close external
HTTPS with `sudo kitdev firewall mode closed`. Internal API, proxy, database,
and orchestrator ports remain non-public. See the
[firewall mode guide](firewall-source-allowlist-guide.md).

External SDK clients use:

```text
E2B_API_URL=https://api.sandbox.kitdev.ai
E2B_DOMAIN=sandbox.kitdev.ai
E2B_API_KEY=<operator-provisioned-key>
E2B_SANDBOX_URL=<unset>
```

### Rolling a reviewed ingress change onto an installed host

`stage` publishes assets create-only and refuses to overwrite an installed file
whose content differs, so it cannot deliver a code change. Use `update`, which
proves the installed file's exact type, ownership, mode and link count before
replacing its bytes, reloads systemd, reverifies every asset, and sends `HUP`
to a running ingress container:

```console
sudo env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  KITDEV_LIFECYCLE=development \
  /usr/bin/bash scripts/ingress/install-ingress.sh update
```

`update` never touches the firewall, certificates, operator configuration, or
service enablement. Run `verify` afterwards, and `apply` only when the ingress
listener itself needs to change.

Remove only the installed ingress assets and its exact firewall rules with:

```console
sudo env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  KITDEV_LIFECYCLE=development \
  /usr/bin/bash scripts/ingress/install-ingress.sh remove
```

Removal retains ACME account state, certificates, private keys, and operator
configuration for explicit backup or deletion.

## Logs and health evidence

There is no `kitdev logs` command yet. Use bounded queries:

```console
sudo journalctl -u kitdev-e2b-orchestrator.service -n 200 --no-pager
sudo systemctl status kitdev-e2b-orchestrator.service --no-pager
sudo docker ps --filter label=com.docker.compose.project=kitdev-control-plane
sudo docker ps --filter label=com.docker.compose.project=kitdev-ingress
sudo docker logs --tail 200 kitdev-ingress
```

For one control-plane service, resolve its container by project/service label:

```console
sudo docker ps --filter label=com.docker.compose.project=kitdev-control-plane \
  --filter label=com.docker.compose.service=api --format '{{.ID}} {{.Status}}'
```

Do not publish raw environment, Compose renderings containing substituted
secrets, credential files, authenticated URLs, or unredacted request logs.

## Backup, restore, update, and uninstall status

| Operation | Current status |
| --- | --- |
| Backup | Low-level offline coordinator implemented; public `kitdev backup` and live qualification pending |
| Restore | Low-level clean-target restore implemented; destructive clean-install rehearsal pending |
| Update/rollback | Planned; no `kitdev update` command or release rollback workflow exists |
| Full uninstall | Planned; no `kitdev uninstall` command exists |
| Ingress removal | Implemented by `scripts/ingress/install-ingress.sh remove` |

Do not treat a live filesystem copy as a verified database/template backup.
The first coordinator now performs quiesce, integrity manifests, exact-release
gating, and restartable clean-target publication, but it has not passed a live
destructive rehearsal. The only authoritative whole-lab reset remains an
operating-system reinstall until that gate passes. See
[`disaster-recovery.md`](disaster-recovery.md). Preserve excluded secrets only
through an operator-controlled encrypted backup or deliberate reissuance.

## Troubleshooting

### Install refuses production

`reason=production_template_install_not_implemented` is intentional. Use the
development lifecycle only on a disposable/prepared host. Do not bypass it for
production.

### Install rejects the worker identity

The worker UID and primary GID must be in `61000-61999`, with exactly the KVM
supplementary group, and must not collide with container identities. The
original lab's UID 999 worker is rejected. Do not renumber it in place while
persistent datastore files exist; reinstall and create identities in the
correct order.

### Down reports active sandboxes

Stop/delete sandboxes through the official SDK, wait for cleanup, and retry.
Do not remove the lifecycle lock or kill Firecracker/NBD processes manually.

### A lifecycle operation reports `lifecycle_operation_running`

Another operation holds the non-blocking project lock. Wait for it to finish.
Investigate the owning process before considering recovery; never delete a
lock file while a process may hold its descriptor.

### Status is degraded

Check orchestrator journald output, container health, free hugepages, NBD
devices, disk space/inodes, and loopback health. `status` reports only bounded
component states; use the logs above for diagnosis.

### Ingress certificate issuance fails

Confirm wildcard DNS points to the server, the provider code is correct, the
credential can create/delete the ACME TXT record, and staging is selected for
the first test. Do not switch to production issuance until staging succeeds.

### Ingress apply reports a firewall conflict

The ingress installer refuses to adopt or delete foreign TCP 80/443 rules.
Review the existing owner and service; do not weaken the verifier or expose
internal ports.

## Current qualification gaps

Before production use, this project still needs:

- remaining fresh-host storage, Docker, and firewall automation plus clean
  reinstall replay;
- production template build/publication and complete profile support;
- apply/apply, reboot, restart, failure-recovery, and rollback qualification;
- a standalone installed CLI and installation manifest/journal ownership;
- concurrent sandbox, security isolation, and external TLS/SDK acceptance;
- stable coding/browser template publication and complete desktop acceptance;
- persistent workspace semantics;
- public and live-qualified backup/restore, update/rollback, and full uninstall;
- sustained capacity/load qualification, observability, alerting, and
  containerd data-root policy.

Treat every unchecked item as a release blocker, not an optional enhancement.

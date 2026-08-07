# Bare-metal operator guide

This guide describes what an operator can do with the repository today and
what is still planned. It is not a production-readiness declaration.

## Read this first

The current system has a working pinned control plane, Firecracker runtime,
local template, official TypeScript SDK tests, and optional HTTPS ingress. It
does **not** yet have a complete fresh-host installer.

`sudo ./kitdev install` currently applies only the minimal control plane on an
already prepared host in explicit `development` or `migration` mode. It does
not install packages, create service identities, configure the kernel, format
or mount disks, install Docker, or provision production templates. Production
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

The current runtime gate requires at least 512 free 2 MiB hugepages when the
orchestrator starts. Default sandbox configuration requests 2 vCPUs, 2 GiB
memory, and a 10 GiB disk, but the seeded development template currently has a
different measured build contract. Size a host from measured concurrent
workloads, not by multiplying defaults alone.

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
4. Store operator secret files as `root:root`, mode `0600`, with one hard link.
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

## Prerequisite preparation: not yet implemented

The repository does not currently provide a supported command that converges
all of these fresh-host prerequisites:

- exact Ubuntu/Docker packages and repository trust;
- reserved `kitdev` service identities and KVM membership;
- dedicated data filesystem and mount;
- KVM, TUN, NBD, hugepage, forwarding, and persistence settings;
- baseline UFW policy and coexistence checks.

Do not assemble a production procedure from the disposable-lab experiment
scripts. The staged experiment harness deliberately marks the corresponding
mutation stages blocked. Reinstall is the authoritative reset for the current
lab.

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

A standalone installed `/usr/local/bin/kitdev` command is not implemented.

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

There is no stable automated API-key/template-ID provisioning command yet.

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

Set the domain, ACME email, lego DNS provider code, and staging ACME server in
`ingress.env`. Put only the selected provider's required variables in
`acme-provider.env`; the scripts parse this file as data and never source it.

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

Apply adds only project-commented UFW rules for TCP 80 and 443, starts the
read-only Nginx ingress container, and enables the renewal timer. Internal API,
proxy, database, and orchestrator ports remain non-public.

External SDK clients use:

```text
E2B_API_URL=https://api.sandbox.kitdev.ai
E2B_DOMAIN=sandbox.kitdev.ai
E2B_API_KEY=<operator-provisioned-key>
E2B_SANDBOX_URL=<unset>
```

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
| Backup | Planned; no consistent `kitdev backup` command exists |
| Restore | Planned and unqualified; no clean-install restore test exists |
| Update/rollback | Planned; no `kitdev update` command or release rollback workflow exists |
| Full uninstall | Planned; no `kitdev uninstall` command exists |
| Ingress removal | Implemented by `scripts/ingress/install-ingress.sh remove` |

Do not treat a live filesystem copy as a verified database/template backup.
Until coordinated quiesce, backup manifests, integrity checks, secret handling,
and restore tests are implemented, the only authoritative whole-lab reset is
an operating-system reinstall. Preserve `/etc/kitdev-sandboxes` and durable
`/var/lib/kitdev-sandboxes` data only through an operator-controlled encrypted
backup process whose restore has been tested separately.

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

- fresh Ubuntu 26.04 prerequisite automation and clean reinstall replay;
- production template build/publication and complete profile support;
- apply/apply, reboot, restart, failure-recovery, and rollback qualification;
- a standalone installed CLI and installation manifest/journal ownership;
- concurrent sandbox, security isolation, and external TLS/SDK acceptance;
- coding, browser, and desktop template acceptance;
- persistent workspace semantics;
- backup/restore, update/rollback, and full uninstall;
- capacity limits, observability, alerting, and containerd data-root policy.

Treat every unchecked item as a release blocker, not an optional enhancement.

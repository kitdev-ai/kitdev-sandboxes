# Fresh server installation

How to stand this platform up on a newly installed bare-metal Ubuntu server,
from bare OS to an external client running sandboxes over public HTTPS.

## Read this before you start

**There is no single command that does this yet.** `sudo ./kitdev install` is
real but narrow: it converges the control plane on a host whose prerequisites
are *already* prepared, and only in `development` or `migration` lifecycle. It
deliberately refuses production mode, and the `standard` and `full` profiles.

The sequence below is nine stages. Six are automated by reviewed tooling, one
is partly automated, and two are genuinely manual. **This exact sequence has
never been executed start-to-finish on a fresh host** — the reference
deployment was assembled incrementally and brought under automation
afterwards. Treat this as a careful runbook, not a proven replay, and expect to
hit at least one thing this document did not anticipate.

Budget most of a day. The builds dominate.

### Known blockers on a truly fresh host

A code audit found three defects that stop the automated path before it
completes. Two are fixed; **one is not**, and you will hit it.

| Blocker | Status |
|---|---|
| The shared `kitdev` group had no creator, though five control-plane scripts require it. `kitdev install` died immediately with `kitdev_group_required` | **Fixed** — stage 1 now creates it |
| `iptables`, `rsync` and `procps` are required at orchestrator start but were not installed. The reference host only worked because the manual Docker step happened to pull `iptables` in | **Fixed** — added to stage 1 |
| `seed-local-template.sh` required a `local-build-smoke` tree pinned to exact hashes for eleven blobs totalling ~1.63 GB that nothing creates | **Fixed** — the step skips when the fixture is absent, which on any fresh host is always. Templates come from stage 5 instead |
| Nothing created the first team, so `api-key create` had no slug to resolve and install completed into an unusable system | **Fixed** — install now bootstraps a default team after the migrators run |
| `ufw` is required by the very first install gate and was installed by nothing | **Fixed** — added to stage 1 |
| The APT source validator hardcoded one provider's mirror in a fail-closed allowlist, aborting stage 1 on any other provider | **Fixed** — mirror is now an explicit operator setting |
| Eight scripts looked up PostgreSQL and Redis by container names that Compose never creates, breaking stages 4-6 on a fresh install | **Fixed** — a shared resolver now accepts both Compose labels and legacy names |
| The ingress domain is hard-refused in two scripts and baked into `nginx.conf` six times | **Open.** Only `sandbox.kitdev.ai` works without code changes |

`kitdev install` therefore cannot return success on a fresh host today. Earlier
drafts of this document claimed everything before the final step succeeds; that
was wrong, and two earlier gates were failing first until they were fixed.
Everything completed does persist and the run is convergent, so a retry resumes
safely — but you will need to seed a first template by another route, or wait
for that step to be made optional. Track it in [`HANDOVER.md`](HANDOVER.md).

Note also that `install` never creates an API key and the seed step publishes no
alias, so a first `Sandbox.create` needs stages 4 and 5 regardless.

These were found by reading code, not by executing it on a fresh host. Expect
others.

| Stage | What | Status |
|---:|---|---|
| 0 | OS install, disks, network | **Manual** — verified, not created |
| 1 | Host prerequisites | Automated (Ansible) |
| 2 | Docker Engine | Automated (Ansible), pinned |
| 3 | Control plane | Automated (`kitdev install`) |
| 4 | API key | Automated CLI |
| 5 | Templates | Scripted |
| 6 | Team limits | Automated CLI |
| 7 | DNS, TLS, ingress | Scripted, proven end to end |
| 8 | Public firewall mode | Automated CLI |
| 9 | External verification | Automated runner |

---

## Requirements

**Hardware.** x86-64 with hardware virtualization (Intel VT-x or AMD-V) exposed
to the OS — not a nested VM unless nested virtualization is genuinely enabled.
The reference host is 4 cores / 8 threads with 64 GB RAM.

Memory drives everything downstream. Sandbox RAM is served from a reserved
HugeTLB pool, and concurrency is `pool size / per-sandbox RAM`. The reference
24 GiB pool yields 12 concurrent 2 GiB sandboxes or 3 concurrent 8 GiB browser
sandboxes. The pool may not exceed 50% of RAM and must leave 16 GiB for
ordinary use, so 64 GB RAM is a sensible floor for browser workloads.

**Disks.** Two concerns, and the second one catches people out:

- A large data disk for project state at `/var/lib/kitdev-sandboxes` — templates,
  snapshots, build cache, databases. Hundreds of GB.
- **`/var/lib/containerd` lives on the root filesystem regardless of Docker's
  `data-root`.** On the reference host it reached ~21 GB of overlay snapshots
  from image pulls alone. Give root at least 100 GB, or relocate containerd
  yourself. Relocation is not implemented in this repo.

**OS.** Ubuntu 26.04 LTS for production. Ubuntu 25.04 only for explicit
development or migration work, and it is end-of-life. **Ubuntu 24.04 is not
supported** and the preflight will refuse it.

**Network.** A static public IPv4, and a domain you control with API-driven DNS.

**Accounts.** A non-root user with sudo, and a DNS provider API token.

---

## Stage 0 — OS, storage, network — *manual*

Install Ubuntu Server 26.04 LTS. Do not install Docker from the Ubuntu archive;
stage 2 pins upstream versions.

Mount the data disk at `/var/lib/kitdev-sandboxes` before anything else. Moving
it later means moving live databases and template blobs.

**This is a manual prerequisite by design.** `mkfs` on a misidentified device is
unrecoverable, and disk identification is not something automation should
guess. Stage 1 *verifies* the result instead and refuses to continue unless:

- `/var/lib/kitdev-sandboxes` is a mount point, not a directory on root;
- its device differs from the root filesystem's device;
- it is at least 1.8 TB, which is the practical floor for a nominal 2 TB disk
  once formatted.

```console
sudo mkdir -p /var/lib/kitdev-sandboxes
sudo mkfs.ext4 -L kitdev-data /dev/<data-disk>
echo 'LABEL=kitdev-data /var/lib/kitdev-sandboxes ext4 defaults,noatime 0 2' \
  | sudo tee -a /etc/fstab
sudo mount -a
```

Confirm virtualization and cgroups v2 before continuing — nothing later works
without them:

```console
grep -Eoc '(vmx|svm)' /proc/cpuinfo    # non-zero
ls -l /dev/kvm                          # must exist
stat -fc %T /sys/fs/cgroup              # cgroup2fs
```

Enable UFW with SSH allowed. The project's firewall tooling requires UFW active
with `deny (incoming)` and `deny (routed)` defaults, and refuses to proceed
otherwise:

```console
sudo ufw allow 22/tcp comment 'SSH management'
sudo ufw default deny incoming
sudo ufw default deny routed
sudo ufw enable
grep -Fx 'IPV6=yes' /etc/default/ufw    # required
```

Do not reboot into an untested kernel later in the process. Do it now if
needed.

### Verify

```console
git clone git@github.com:kitdev-ai/kitdev-sandboxes.git
cd kitdev-sandboxes
./kitdev doctor --lifecycle-mode development --json
```

`doctor` is strictly read-only and changes nothing. Some checks report blocking
`unknown` at this point; that is expected before stage 1.

---

## Stage 1 — Host prerequisites — *automated*

Converges packages, the reserved worker identity, kernel modules (KVM, TUN,
NBD), sysctls, the HugeTLB pool, and project directories. Every managed change
is written to project-specific files and recorded for rollback.

```console
sudo env KITDEV_LIFECYCLE_MODE=development \
  ansible-playbook -i ansible/inventory ansible/site.yaml --check
sudo env KITDEV_LIFECYCLE_MODE=development \
  ansible-playbook -i ansible/inventory ansible/site.yaml
```

Run `--check` first. It executes the read-only probes and predicts changes
without mutating.

The role refuses before its first mutation if the platform is unsupported, APT
trust is wrong, an identity or numeric ID collides, KVM/TUN are unavailable, or
there is not enough RAM for the requested hugepage pool. **A refusal here is
the tool working**; do not force past it.

### The hugepage pool

The default derives 24 GiB from two 8 GiB sandbox slots plus one 8 GiB
transient allowance for build and snapshot mappings. Validation caps it at 50%
of RAM and requires 16 GiB left for ordinary use. Smaller profiles are valid
down to 512 MiB per sandbox.

Decide this now. Changing it later needs the migration controller, whose reboot
and rollback gates are still open.

### Verify

```console
grep -E 'HugePages_(Total|Free)' /proc/meminfo
lsmod | grep -E 'kvm|nbd|tun'
id kitdev-worker
```

### Rollback

```console
sudo ansible-playbook -i ansible/inventory ansible/remove-host-prerequisites.yaml
```

Removal refuses on managed-file or live-sysctl drift and restores only recorded
prior state.

---

## Stage 2 — Docker Engine — *automated*

Installed by the `docker` role as part of stage 1's playbook. It adds the
repository under project-owned paths, verifies the signing key's fingerprint
before trusting it, installs the pinned versions, holds them against unattended
upgrades, and confirms that `buildx` and `compose` both respond — five build
steps need buildx and every control-plane operation needs compose.

```text
docker-ce               5:29.7.2-1~ubuntu.26.04~resolute
docker-ce-cli           5:29.7.2-1~ubuntu.26.04~resolute
containerd.io           2.3.3-1~ubuntu.26.04~resolute
docker-buildx-plugin    0.36.1-1~ubuntu.26.04~resolute
docker-compose-plugin   5.4.0-1~ubuntu.26.04~resolute
```

**An existing Docker installation is refused by default.** A foreign engine may
carry another workload's containers, networks and daemon configuration, and the
project's host-change policy is to preserve what it did not install. To install
the pinned versions over an existing engine deliberately:

```console
sudo env KITDEV_LIFECYCLE_MODE=development \
  ansible-playbook -i ansible/inventory ansible/site.yaml \
  -e kitdev_docker_override=true
```

The role does not touch `/etc/docker/daemon.json`. Docker's own storage
therefore stays on the root filesystem, which is why root needs headroom
independently of the data disk.

### Verify

```console
docker version && docker compose version
sudo docker run --rm hello-world
```

---

## Stage 3 — Control plane — *automated*

Now `kitdev install` applies. It gates hard on stage 1 and 2 having completed:
packages, reserved worker identity, KVM, TUN, NBD, IPv4 forwarding, hugepages,
Docker, and UFW must already be prepared. It does not create them.

```console
sudo ./kitdev install --dry-run --json
sudo ./kitdev install --lifecycle-mode development --profile minimal
```

Internally this runs, in order: prepare the filesystem layout, bootstrap the
private environment and generated secrets, ensure the project network, acquire
pinned E2B sources, build the control-plane images, install and start the
Compose project, install runtime artifacts, build `envd`, build snapshot tools,
build the orchestrator, install its systemd service, and seed a local template.

**This stage is where the hours go.** It clones upstream sources and compiles
Go binaries and container images.

`--profile standard` and `--profile full` refuse. `--lifecycle-mode production`
refuses before mutation, because production-qualified template publication is
not implemented.

### Verify

```console
sudo ./kitdev status --json
sudo docker ps --filter label=com.docker.compose.project=kitdev-control-plane
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:3000/health   # 200
```

You should see PostgreSQL, Redis, ClickHouse, Loki, the API, and the client
proxy healthy, plus the orchestrator service active.

### Rollback

```console
sudo ./kitdev down
```

Preserves persistent state, refuses while Firecracker processes are running,
and attempts to restore the prior service set on failure.

---

## Stage 4 — API key — *automated*

The template gates in stage 5 consume an API key file, so issue it first.

```console
sudo ./kitdev api-key teams
sudo ./kitdev api-key create --team-slug <slug> --name <product> \
  --output /etc/kitdev-sandboxes/secrets/<product>.key
sudo ./kitdev api-key verify \
  --key-file /etc/kitdev-sandboxes/secrets/<product>.key \
  --metadata-file /etc/kitdev-sandboxes/secrets/<product>.key.metadata.json
```

The raw key is never printed. Both files land `root:root`, mode `0600`, single
link. Record the non-secret key ID for later rotation and revocation.

To move the key to a client machine, stream it host-to-host so it never renders
in a terminal or shell history:

```console
ssh -T <sandbox-host> 'sudo -n dd if=/etc/kitdev-sandboxes/secrets/<product>.key \
  iflag=nofollow status=none' | sudo tee /etc/my-product/secrets/e2b-api-key >/dev/null
sudo chmod 0400 /etc/my-product/secrets/e2b-api-key
```

---

## Stage 5 — Templates — *scripted*

Sandboxes need a published template. Qualify the build, then publish a stable
alias. Every command below takes an **absolute** path to a key file.

```console
sudo env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin KITDEV_LIFECYCLE=development \
  /usr/bin/bash scripts/control-plane/verify-typescript-sdk-coding-template.sh \
  --api-key-file /etc/kitdev-sandboxes/secrets/<product>.key

sudo env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin KITDEV_LIFECYCLE=development \
  /usr/bin/bash scripts/control-plane/publish-stable-template.sh \
  publish --product coding --version v1 \
  --api-key-file /etc/kitdev-sandboxes/secrets/<product>.key
```

`publish-stable-template.sh` takes **exactly seven arguments** in that order —
an operation first, then the three flag pairs. Omitting the operation or any
pair fails with `invalid_arguments` (exit 64).

### The browser product needs its team provisioned first

Do not simply repeat the coding commands. `publish-stable-template.sh` gates
the browser product on a dedicated team whose limits row matches exactly, plus
a fully free hugepage pool and sufficient ordinary memory. Provision it first:

```console
sudo env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin KITDEV_LIFECYCLE=development \
  /usr/bin/bash scripts/control-plane/provision-browser-heavy-profile.sh \
  --api-key-file /etc/kitdev-sandboxes/secrets/<product>.key
```

Then run the browser qualification and publish with `--product browser-heavy`.
Because the gate requires the exact starting limits row, **do stage 6 after
this**, not before — raising limits first will make the gate refuse.

Publication takes the lifecycle and SDK locks, requires a healthy orchestrator,
and refuses any live Firecracker process or in-progress build. Each product
gets a root-only journal under
`/var/lib/kitdev-sandboxes/template-publication`. A rerun verifies and returns
unchanged rather than building again.

---

## Stage 6 — Team limits — *automated*

New teams inherit base-tier defaults. Set them from your actual pool.

```console
sudo env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin KITDEV_LIFECYCLE=development \
  /usr/bin/bash scripts/control-plane/set-team-limits.sh \
  --team-slug <slug> --check \
  --concurrent-sandboxes 12 --concurrent-builds 2 \
  --max-vcpu 4 --max-ram-mb 8192 --max-length-hours 24
```

Drop `--check` to apply. The tool prints your live pool and the worst case, and
refuses if the worst case exceeds the pool unless you pass
`--allow-oversubscription` — which is correct when small sandboxes should reach
the hardware limit rather than being capped at the largest profile's arithmetic.

Prior values are recorded create-once at
`/var/lib/kitdev-sandboxes/team-limits/<slug>.prior`.

---

## Stage 7 — DNS, TLS, ingress — *scripted*

This flow is proven end to end. Follow it in order.

### DNS records

Create **`A` records**, DNS-only (not proxied):

```text
api.sandbox.example.com   A   <server-ipv4>
*.sandbox.example.com     A   <server-ipv4>
```

> **Do not use a `CNAME` for the wildcard.** It also matches
> `_acme-challenge.sandbox.example.com`, which sends DNS-01 validation into
> whatever zone the CNAME targets — typically your hosting provider's, which
> your API token cannot write to. Certificate issuance will fail with
> "zone could not be found". This cost real debugging time on the reference
> deployment.

Do not publish `AAAA` until IPv6 is configured and tested end to end.

### Stage the assets

```console
sudo env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin KITDEV_LIFECYCLE=development \
  /usr/bin/bash scripts/ingress/install-ingress.sh stage
```

Create the two operator files from the installed examples and edit them with
`sudoedit`. Set your domain, ACME email, and `lego` provider code in
`ingress.env`, and the provider's credential pointer in `acme-provider.env`.

### DNS provider token

Create a least-privilege token. For Cloudflare that is a custom token with
**both** `Zone:DNS:Edit` and `Zone:Zone:Read`, scoped to your zone. One
permission alone fails: `DNS:Edit` cannot resolve the zone ID and `Zone:Read`
cannot write the challenge record. Never use a global API key.

Install it without it entering shell history — paste, Enter, then `Ctrl-D`:

```console
sudo tee /etc/kitdev-sandboxes/ingress/cloudflare-dns-api-token >/dev/null
sudo stat -c 'owner=%U:%G mode=%a links=%h size=%s' \
  /etc/kitdev-sandboxes/ingress/cloudflare-dns-api-token
```

### Issue against staging first

Set `KITDEV_ACME_SERVER` to the Let's Encrypt **staging** directory, then:

```console
sudo env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin KITDEV_LIFECYCLE=development \
  /usr/bin/bash scripts/ingress/manage-certificate.sh issue-staging
```

This proves DNS-01 automation without consuming production rate limits. If it
fails on a stale cached record after you have just fixed DNS, flush the local
resolver: `sudo resolvectl flush-caches`.

### Issue production and apply

Switch `KITDEV_ACME_SERVER` back to production, then:

```console
sudo env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin KITDEV_LIFECYCLE=development \
  /usr/bin/bash scripts/ingress/manage-certificate.sh issue
sudo env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin KITDEV_LIFECYCLE=development \
  /usr/bin/bash scripts/ingress/install-ingress.sh apply
sudo env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin KITDEV_LIFECYCLE=development \
  /usr/bin/bash scripts/ingress/install-ingress.sh verify
```

Apply starts the Nginx ingress, enables the daily renewal timer, and converges
the firewall mode. TCP 80 always stays closed.

**If your control-plane firewall was not installed by this automation**, the
firewall step refuses. Add
`KITDEV_UNMANAGED_CONTROL_PLANE_FIREWALL=acknowledged` to the `env -i` list.
That is development-only, gives up only the managed-ownership proof, and is not
a production posture. On a host built by following this document from stage 1,
you should not need it.

To roll a later code change onto installed assets, use
`install-ingress.sh update` — `stage` is create-only and refuses to overwrite a
changed file.

---

## Stage 8 — Public exposure — *automated*

```console
sudo ./kitdev firewall source add --cidr <client-public-ipv4>/32
sudo ./kitdev firewall mode restricted
```

Source-restricted is the recommended steady state. If your client has no stable
address yet:

```console
sudo ./kitdev firewall mode public
```

That opens TCP 443 to every Internet source. Authentication still applies, but
so does every scanner on the internet. Move to `restricted` as soon as you can.
`kitdev firewall mode closed` withdraws external HTTPS entirely.

### Verify from outside the host

```console
curl -sS -o /dev/null -w 'http=%{http_code} tls=%{ssl_verify_result}\n' \
  https://api.sandbox.example.com/health
for p in 80 3000 3002 3003 3100 5432 6379 8123 9000 5007 5008 5010 5016 5017 5018; do
  nc -z -G 5 -w 5 api.sandbox.example.com $p && echo "port $p OPEN — PROBLEM"
done
```

Expect `http=200 tls=0` and no open port but 443.

---

## Stage 9 — External verification — *automated*

The only evidence that counts for the public path. Run it from a **different
machine**:

```console
cd scripts/external-sdk-matrix
npm ci --ignore-scripts --no-audit --no-fund
E2B_API_URL=https://api.sandbox.example.com \
E2B_DOMAIN=sandbox.example.com \
E2B_API_KEY_FILE=/path/to/key \
node matrix.ts
```

Ten stages, 42 checks: authentication, invalid-key refusal, lifecycle, commands,
PTY, files, wildcard guest HTTP, chunked streaming, WebSocket upgrade,
pause/resume, snapshots, the browser profile, and a cleanup assertion. Anything
less than all-pass means the deployment is not ready.

Then measure your actual capacity:

```console
KITDEV_FLEET=12 node concurrency.ts
KITDEV_FLEET=2 KITDEV_PROBE_HEAVY=1 node concurrency.ts
```

The second walks the heavy profile up until the host refuses, which tells you
your real ceiling.

---

## What is not automated

Be clear-eyed about these before committing to this platform:

- **Storage layout and Docker installation.** Stages 0 and 2 are manual. The
  `docker` Ansible role is an empty stub.
- **containerd relocation.** Unimplemented. Monitor root filesystem usage.
- **Production lifecycle mode.** `kitdev install` refuses it; only
  `development` and `migration` work today.
- **`standard` and `full` profiles.** Both refuse. Only `minimal` installs.
- **Observability.** Grafana, Loki dashboards, Tempo, Prometheus — the role is
  a stub. Loki runs as a log sink only.
- **Backup and restore.** The offline coordinator exists and has unit coverage,
  but no destructive live rehearsal has been done and there is no top-level
  `kitdev backup` command.
- **Update and rollback.** No `kitdev update`. Upgrades are manual.
- **Uninstall.** No top-level `kitdev uninstall`. Stage 1 has a removal
  playbook; ingress has `install-ingress.sh remove`.
- **Reboot persistence.** The hugepage pool's survival across reboot is
  unqualified. Test it on your host before relying on it.
- **Multi-node.** Single host only, by design in this version.

## What will probably bite you

Ordered by how much time each cost on the reference deployment:

1. **Wildcard CNAME instead of A records.** Certificate issuance fails with a
   confusing "zone could not be found". See stage 7.
2. **Stale DNS in the local resolver.** After fixing records, `systemd-resolved`
   serves the old answer until flushed, and lego uses the system resolver.
3. **The builds take a long time** and a failure late in stage 3 means
   re-running the sequence. Run it in `tmux` or `screen`.
4. **Insufficient root disk.** Image pulls fill `/var/lib/containerd` on root,
   not on your data disk.
5. **Prerequisite refusals that look like bugs.** Identity collisions and
   unsupported-platform refusals are the tooling protecting you from a
   half-configured host. Read the reason code.
6. **A passing local test is not a working service.** Run stage 9 from another
   machine before believing any of it.

## Where to go next

- [`HANDOVER.md`](HANDOVER.md) — current state, capacity model, backlog.
- [`bare-metal-operator-guide.md`](bare-metal-operator-guide.md) — day-two
  operations.
- [`typescript-sdk-integration-guide.md`](typescript-sdk-integration-guide.md) —
  hand this to whoever writes the client.
- [`research/`](research/README.md) — dated evidence for every claim above.

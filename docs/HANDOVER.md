# Project handover

Checkpoint: 2026-08-08, late evening. Revision `240aad1`.
Remote: `git@github.com:kitdev-ai/kitdev-sandboxes.git`

**A fresh-host install is in progress right now and is currently blocked.** Read
§1 first. This document assumes no prior context.

---

## 1. Where you are, right now

The OVH host was **wiped and reinstalled tonight**, specifically to qualify the
one-command fresh install. The previous working deployment is gone: there is no
external SDK endpoint at present, and rebuilding it is the current task.

### Immediate blocker

`kitdev install` fails within seconds at its first step:

```text
install: invalid user: '999'
```

`scripts/control-plane/prepare-layout.sh:20-23` creates datastore directories
owned by container UIDs:

```bash
ensure_directory "$KITDEV_DATA_ROOT/postgres"   999   0     700
ensure_directory "$KITDEV_DATA_ROOT/redis"      999   0     750
ensure_directory "$KITDEV_DATA_ROOT/clickhouse" 101   101   750
ensure_directory "$KITDEV_DATA_ROOT/loki"       10001 10001 750
```

On a freshly installed Ubuntu 26.04, **UID 999 and UID 10001 do not exist**
(101 does), and GNU `install -o` refuses an owner it cannot resolve. The old
host had those UIDs from its hand-built history, which is why this never
surfaced before.

Probable fix: create the directory, then set ownership with `chown`, which
accepts unresolvable numeric IDs, rather than passing `-o` to `install`.
**Verify that before relying on it** — check how `ensure_directory` in
`scripts/control-plane/common.sh` invokes `install`, and confirm
`require_exact_directory` still passes afterwards.

### How to resume

```console
ssh -F docs/private/ovh-lab-ssh.conf ovhkitdevlab

/var/tmp/kitdev-fresh/kitdev-sandboxes    # staged release, revision 240aad1
/var/tmp/kitdev-install.log               # last install output
```

After committing a fix, restage that exact revision and relaunch:

```console
git archive --format=tar --prefix=kitdev-sandboxes/ <commit> \
  | ssh -F docs/private/ovh-lab-ssh.conf ovhkitdevlab 'sudo sh -c "rm -rf /var/tmp/kitdev-fresh && install -d -o root -g root -m 0700 /var/tmp/kitdev-fresh && cat > /var/tmp/kitdev-fresh/r.tar && cd /var/tmp/kitdev-fresh && tar -xf r.tar && rm -f r.tar && chown -R root:root kitdev-sandboxes"'

ssh -F docs/private/ovh-lab-ssh.conf ovhkitdevlab 'sudo sh -c "cd /var/tmp/kitdev-fresh/kitdev-sandboxes && nohup ./kitdev install --lifecycle-mode development --profile minimal > /var/tmp/kitdev-install.log 2>&1 &"'
```

Never run uncommitted bytes against the host.

---

## 2. Host facts

| Item | Value |
| --- | --- |
| Address | `139.99.124.89`, `ns547708.ip-139-99-124.net` — unchanged by the reinstall |
| SSH | alias `ovhkitdevlab` in `docs/private/ovh-lab-ssh.conf` (untracked); user `ubuntu`, passwordless sudo |
| New host key | `SHA256:1Uc9fsrPlUt4ETSFx9ac84aNxxfDIO3bpKvP3vkImEY` |
| OS | Ubuntu 26.04 LTS, kernel 7.0.0-29 |
| CPU / RAM | 8 threads, 62 GB, VT-x |
| System disks | 2 x 419 GB NVMe, RAID1 → `/` (410 GB), `/boot`, `/boot/efi` |
| Data disk | `/dev/sda`, 3.6 TB, **formatted tonight**, UUID `f1fccad2-5e62-4329-984e-769223b22c79`, mounted at `/var/lib/kitdev-sandboxes` with an fstab entry |
| DNS | `api.sandbox.kitdev.ai` and `*.sandbox.kitdev.ai` are `A` records to the same IP, DNS-only. **No DNS change needed.** |

### Already converged

`ansible/site.yaml` completed cleanly — **78 tasks, 18 changed, 0 failed** —
after a clean `--check`. Verified on the host afterwards:

- `kitdev` group at GID 61003; `kitdev-worker` (61002) with **only** `kvm`
- HugeTLB 12,288 pages, `ip_forward=1`, kvm/kvm_intel/nbd loaded
- Docker 29.7.2, buildx 0.36.1, compose v5.4.0 — all pinned
- UFW active: `deny (incoming), allow (outgoing), deny (routed)`, SSH allowed

Nothing else exists yet: no containers, no control plane, no templates, no API
key, no ingress.

---

## 3. What was fixed tonight

Seven blockers, all found by running on real hardware, each landed with a test
that would have caught it. The record that matters: **code review had found
none of these.**

| # | Defect | Consequence |
| --- | --- | --- |
| 1 | APT validator rejected comment-only deb822 paragraphs | Rejected a **stock Ubuntu cloud image**, before any mutation |
| 2 | Storage check read `findmnt` JSON with uppercase keys | util-linux 2.41 emits lowercase; my own code |
| 3 | Package convergence asserted in check mode | `--check` could not run on an unprepared host — its entire purpose |
| 4 | Four Docker verifications ran in check mode | Inspected state check mode had not created |
| 5 | Docker install evaluated in check mode | Cannot install from a repo check mode did not write |
| 6 | 17 control-plane scripts committed non-executable | `lifecycle.sh` invokes them directly; install died at step one on **any** clean checkout |
| 7 | `install-orchestrator-service.sh` reversed `require_exact_file` args | Validated the checkout's mode, not the installed file's |

**A process lesson worth keeping.** Fix 6 had to be committed twice. The first
attempt used `git update-index --chmod=+x`, which the following `git add -A`
silently reverted by re-reading the working tree's 644 mode. The test passed
because it read the *index*, which `update-index` had just modified. Set modes
with `chmod` on disk so tree and index agree, and prefer tests that read the
committed tree (`git ls-tree`) over the index.

---

## 4. The backup — everything from the old deployment

`~/kitdev-backup-2026-08-08/` on the development Mac. Every archive was
verified by restoring it, not merely written.

| File | Contents |
| --- | --- |
| `secrets.tar.enc` + `backup.key` | Both ACME accounts, the production wildcard certificate and key, `e2b-lab.env`, the product API key and metadata, full ingress config including the Cloudflare token, DNS rollback record, publication journals, team-limits prior state |
| `seed-fixture.tar.zst` | The 2.5 GB `local-build-smoke` fixture; its `memfile` hash confirmed byte-exact against the value pinned in code |
| `published-templates.tar.zst` | Both production template builds (coding 198 MB, browser-heavy 741 MB) |
| `README.md` | Restore procedure, and two traps: these are artifacts not templates (database rows absent), and snapshots are CPU- and kernel-version-bound |

**Restore the ACME material before issuing a certificate** — it avoids
consuming Let's Encrypt rate limits across retry cycles. Issue a fresh product
API key and revoke the old ID rather than reusing it.

`backup.key` should move into a password manager and be deleted from disk.

---

## 5. The remaining sequence

1. **Clear the blocker; `kitdev install` completes.** Sixteen steps; Go and
   image builds dominate. Run under `nohup`. Expect more failures — most of this
   code has never executed.
2. **Verify:** `kitdev status`, six healthy containers, orchestrator active,
   loopback API 200. A 503 for the first ~20 seconds is node discovery.
3. **API key:** `kitdev api-key create --team-slug local-dev-team`. Install
   bootstraps that team; nothing else creates one.
4. **Templates:** `publish-stable-template.sh publish --product coding
   --version v1 --api-key-file <path>` — exactly seven arguments, operation
   first. For browser, run `provision-browser-heavy-profile.sh` **first**: its
   gate needs the exact starting limits row, so limit changes come after.
5. **Ingress:** stage, restore ACME material from backup, apply, verify.
6. **Expose:** `kitdev firewall mode public`, or `restricted` with a client
   CIDR. Confirm from off-host that only 443 answers.
7. **The verdict:** `scripts/external-sdk-matrix/matrix.ts` from another
   machine. 42 checks; anything less than all-pass is not done.
8. **Then reboot and re-run the matrix.** This is the open question from the old
   host, which ran a *transient* orchestrator unit that could not survive
   reboot. The shipped unit is properly persistent —
   `WantedBy=multi-user.target`, `Restart=on-failure`, `RequiresMountsFor` the
   data disk — and install enables and verifies it. A fresh install is the first
   chance to prove that.

---

## 6. Things that will bite you

- **`doctor` cannot exit 0.** It injects a blocking `unknown` for unapproved
  port policy. Exit 5 on a healthy host is expected.
- **Wildcard `CNAME` breaks certificate issuance** — it matches
  `_acme-challenge` and sends DNS-01 into a zone you do not control. Use `A`
  records; already correct here.
- **Stale resolver cache** after DNS changes: `resolvectl flush-caches`. lego
  uses the system resolver.
- **Root disk fills from containerd.** `/var/lib/containerd` stays on root
  regardless of Docker's `data-root`; it reached 21 GB on the old host.
- **`verify-api-proxy-e2e.sh` still pins the old lab build ID**, so
  `kitdev test-core` is broken. Not an install blocker.
- **The ingress domain is hardcoded** to `sandbox.kitdev.ai` in
  `ingress_config.py`, `run_lego.py`, and six places in `nginx.conf`. Fine here,
  blocking for any other deployment.
- **Hugepage gates disagree**: install accepts 512 free pages,
  `preflight-orchestrator.sh` demands 12,288. The Ansible role sets 12,288 so
  both pass here, but the mismatch is real.
- **Install needs egress** to GitHub, Docker Hub, `storage.googleapis.com` and
  the Go module proxy. `acquire-source.sh` runs git under `env -i` with no proxy
  variables, so a proxy-only host cannot clone.

---

## 7. Facts worth not rediscovering

**Capacity is memory, not policy.** Sandbox RAM comes from the reserved HugeTLB
pool, so concurrency is `pool ÷ per-sandbox RAM`. On 24 GiB that measured as
**12** concurrent 2 GiB sandboxes or **3** concurrent 8 GiB ones. Past the pool,
`Sandbox.create` fails cleanly and running sandboxes are unharmed. **Paused
sandboxes hold no slot** — verified.

**Templates are frozen VMs, not images.** A template is a snapshotted running
machine: `memfile` (RAM), `snapfile` (CPU and device state), `rootfs.ext4`.
Creating a sandbox resumes it, which is why twelve start in about a second. See
[`vision.md`](vision.md) for the mechanism, the unexploited `fork` primitive,
and the multi-node design.

**Build hashes reproduce.** `envd` rebuilt from pinned source reproduced its
exact size and sha256. That was the largest unknown in the fresh-install plan
and it is settled.

**The ingress firewall self-heals after reboot.** UFW rules persist while
`DOCKER-USER` guards do not; that half-applied state used to wedge both `apply`
and `remove`. Both now reconcile, clearing only this project's own tagged rules,
and a foreign rule still refuses.

---

## 8. How to work here

`AGENTS.md` is binding. The three rules that matter most:

1. **Evidence, not intent.** Nothing is working, proven, or complete without a
   recorded result. Tonight is the argument: seven defects that careful code
   review missed, all found in one session of actually running the thing.
2. **Never print or commit a secret.** Verify credentials by their properties —
   ownership, mode, link count, a shape regex, an authenticated call that
   succeeds.
3. **Run the host from an exact committed revision**, staged root-only, and turn
   every manual server change into reviewed automation.

Tests need `pyyaml` and `pytest`; without them three modules fail to import and
the run is not clean. Current suite: **445 tests, 2 expected skips**.

---

## 9. Documents

- [`../AGENTS.md`](../AGENTS.md) — binding working rules.
- [`fresh-server-installation.md`](fresh-server-installation.md) — stage-by-stage runbook.
- [`fresh-host-remediation-plan.md`](fresh-host-remediation-plan.md) — the plan this session is executing.
- [`vision.md`](vision.md) — templates, snapshots, product direction, multi-node.
- [`typescript-sdk-integration-guide.md`](typescript-sdk-integration-guide.md) — the client contract. A copy, plus a portable install prompt for another agent, is in `~/Downloads`.
- [`bare-metal-operator-guide.md`](bare-metal-operator-guide.md) — day-two operations.
- [`research/activity-log.md`](research/activity-log.md) — chronological record.

If two documents disagree, prefer the newer dated evidence and the current code,
then correct the stale one in the same change.

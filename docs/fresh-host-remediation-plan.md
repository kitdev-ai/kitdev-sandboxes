# Fresh-host remediation plan

Goal: one command takes a freshly installed Ubuntu 26.04 bare-metal server to a
working external E2B SDK endpoint, using only reviewed automation.

Status at time of writing: not achievable. Three independent audits found
twelve defects in this path. Three of those were introduced by the fixes for
the others, all within a single day, all by code reading.

## The premise this plan is built on

**Reading has stopped paying.** Twelve defects found by inspection; three
created by inspection-driven fixes; zero of the fixes verified on hardware. The
one correction today with real evidence behind it — the container resolver —
was the only one testable against a running host, and it was right first time.

So this plan front-loads *execution*, and treats further code review as a way
to prepare for a run rather than a substitute for one. Every phase below ends
in something observed, not something argued.

A corollary that matters: the current OVH host is the only running evidence
this platform works at all. It gets reinstalled in Phase 3. Everything
recoverable must be off it before then, and the external endpoint is down from
that moment until Phase 5 passes.

---

## Phase 0 — Preserve what the reinstall destroys

The reinstall wipes both disks. Recoverable material must leave the host first.

| Item | Action | Why |
|---|---|---|
| ACME account key + wildcard certificate + private key | Copy to encrypted off-host storage | Avoids re-issuance and protects against Let's Encrypt rate limits during repeated attempts |
| Product API key + metadata | Already off-host in your Downloads; confirm the other server has it installed | The key is regenerated after reinstall, but knowing the old key ID lets you revoke cleanly |
| DNS rollback record | Copy `/var/lib/kitdev-sandboxes/ingress-dns-rollback.json` | Records the pre-change zone state |
| Prior-state and manifest files | Copy the root-only trees under `/var/lib/kitdev-sandboxes` | Post-mortem value if the replay diverges |
| Template publication journals | Copy | Lets you compare the rebuilt templates against what was published |

Do **not** copy the private environment or database contents forward. The point
of the exercise is that a fresh install regenerates them.

**Gate:** an encrypted archive exists off-host, and its restore has been opened
and inspected once. An unverified backup is not a backup.

---

## Phase 1 — Close the known blockers offline

These are all confirmed and bounded. None needs a host; each needs a test that
would have caught it.

### 1.1 Seed template — remove the dependency

`seed-local-template.sh` requires roughly 1.63 GB of hash-pinned artifacts —
a Firecracker memory snapshot, its rootfs, and five ancestor layers — that
nothing in the repository creates. They are a shortcut: a pre-booted VM so a
smoke test can run before any template exists. The product path does not need
them, because `publish-stable-template.sh` builds templates properly through
the template manager.

**Change:** make the seed step conditional. When the source tree is absent,
skip it and report that no default template was seeded, rather than failing
install. Apply the same treatment to `verify-api-proxy-e2e.sh`, which pins the
same build ID and would otherwise keep `kitdev test-core` broken.

**Acceptance:** `kitdev install` returns success on a host with no seed
material; a test asserts the skip path is taken and reported, not silently
swallowed.

**Follow-on it exposes:** `seed-local-template.sh` also requires a
`local-dev-team` row that no in-repo code creates. It is masked today because
the artifact check fires first. Determine whether the pinned upstream
migrations seed it; if not, seed it explicitly. Do not assume.

### 1.2 Ingress domain — make it configurable

`sandbox.kitdev.ai` is hard-refused in `ingress_config.py` and again in
`run_lego.py`, and baked into `nginx.conf` six times. `install-ingress.sh`
byte-compares the installed config, so editing it fails.

**Change:** drive the domain from `KITDEV_INGRESS_DOMAIN` everywhere. Render
`nginx.conf` from a template at install time with the domain substituted, and
verify the rendered output rather than a fixed file. Remove both hard refusals
in favour of validating the configured value.

**Acceptance:** a second domain passes `install-ingress.sh stage` and
`manage-certificate.sh issue-staging` end to end. Until that runs, the ingress
flow is proven for exactly one domain.

### 1.3 Hugepage gates — make them agree

`require_prepared_host` accepts 512 free pages; `preflight-orchestrator.sh`
demands 12,288. A host can clear install and fail orchestrator start by 24x.

**Change:** derive both from the same capacity model the Ansible role uses, so
the install gate refuses early with a capacity reason rather than late with a
service failure.

**Acceptance:** a host below the orchestrator's requirement is refused at
install time with a capacity-specific reason code.

### 1.4 Egress — document and stop scrubbing the proxy

Install needs GitHub, Docker Hub, `storage.googleapis.com` and the Go module
proxy. `acquire-source.sh` runs git under `env -i` without proxy variables, and
the lifecycle runner scrubs the environment, so a proxy-only host cannot clone
even though Docker pulls would work.

**Change:** document every endpoint as a prerequisite, and pass `https_proxy`,
`http_proxy` and `no_proxy` through the scrub allowlist when set.

**Acceptance:** the endpoint list appears in the runbook; a proxied host is
either supported or explicitly refused with a reason, not left to fail
obscurely.

### 1.5 Port conflicts — implement the preflight

`preflight.py` states the required-port check is not implemented, while six
fixed loopback ports are assumed free. A surveyed host already showed
`127.0.0.1:5432` and `*:3000` in direct conflict.

**Acceptance:** `doctor` reports a blocking conflict on a host with something
already bound, and `doctor` can finally exit 0 on a clean host — today it
cannot, by construction.

### 1.6 Installed SDK assets

The asset loop now skips directories so install can proceed, which leaves the
installed copy without the browser asset directories.

**Change:** publish directories recursively, or state permanently that the
browser verifier runs only from a release tree. Pick one; the current state is
an accident.

### 1.7 Orchestrator installer argument order

`install-orchestrator-service.sh` passes `require_exact_file` its arguments
reversed, exactly as the ingress installer did, so it validates the release
tree instead of the installed file. Latent, but it means that installer never
checks installed ownership.

### 1.8 Status honesty

`kitdev status` never probes port 5008, so a wedged orchestrator whose unit
reads active reports `pass`. Probe it.

**Phase 1 gate:** full suite green, both playbooks syntax-clean, and — the part
that matters — **every fix has a test that would have failed before it**. Three
of today's defects passed a green suite because no test crossed the
Ansible-to-shell boundary. Add tests that cross it.

---

## Phase 2 — Automate the two manual stages

Required by the "one command" goal. Both roles exist as empty scaffolding.

### 2.1 Docker role

Install the pinned versions from Docker's own repository with key verification.
Structurally merge `/etc/docker/daemon.json` rather than overwriting. Enable
and start the service. Refuse if a foreign Docker is already present rather
than adopting it silently.

The reference pins: `docker-ce 5:29.7.2-1~ubuntu.26.04~resolute`,
`containerd.io 2.3.3`, buildx `0.36.1`, compose `5.4.0`, key fingerprint
`9DC858229FC7DD38854AE2D88D81803C0EBFCD88`.

### 2.2 Storage role

Identify the data disk, refuse unless the choice is unambiguous, create the
filesystem, and mount it at `/var/lib/kitdev-sandboxes` with an fstab entry.
This is the most destructive automation in the project: it must refuse on any
ambiguity, never touch a disk with an existing filesystem or partition table
unless explicitly named, and record prior state.

Also decide containerd's location here. It consumed 21 GB of root on the
reference host regardless of Docker's `data-root`, and relocation is
unimplemented.

**Phase 2 gate:** both roles converge, reapply with zero changes, and their
removal paths restore recorded prior state. Storage removal must never destroy
data by default.

---

## Phase 3 — Reinstall and run

This is where the plan stops being theoretical.

1. Confirm Phase 0's archive restores.
2. Reinstall Ubuntu 26.04 on the OVH host.
3. Run the one-command flow from an exact committed revision.
4. When it fails — it will — record the reason code, fix forward, commit, and
   re-run from a clean reinstall rather than patching in place.

**Discipline that makes this work:** every failure gets a reason code and a
test before its fix. A fix without a test is how three of today's defects
happened. Reinstall between attempts rather than iterating on a dirty host,
because a half-converged host hides ordering bugs.

Expect several cycles. Budget for that rather than being surprised by it.

**Phase 3 gate:** one uninterrupted run from bare OS to `kitdev status`
reporting healthy, with no manual step outside the documented flow.

---

## Phase 4 — Prove it is repeatable, not lucky

A single successful run proves very little.

| Check | Why |
|---|---|
| Reapply changes nothing | Convergence, not one-shot luck |
| Reboot, everything recovers | Hugepage persistence is currently unqualified; the pool may not survive |
| Removal restores prior state, then reapply succeeds | Today removal is blocked by preflight asserting the end state as a precondition |
| Second reinstall + replay from the same revision | The actual definition of reproducible |

The reboot check is the one I would bet against passing first time. The
prerequisite role writes `vm.nr_hugepages` to sysctl but never touches the
kernel command line, and hugepage allocation gets harder as memory fragments.

---

## Phase 5 — Re-qualify what the reinstall destroyed

1. Reissue the wildcard certificate (staging first, to protect rate limits).
2. Deploy ingress, open 443 in the chosen firewall mode.
3. Build and publish both templates through `publish-stable-template.sh`.
4. Issue a new product API key; install it on the product server; revoke the
   old key ID.
5. Run the full external SDK matrix from an off-host client — all 42 checks.
6. Re-run the capacity probes and confirm the concurrency figures still hold.

**Phase 5 gate:** the matrix passes from a machine that is not the sandbox
host, and the port scan shows 443 and nothing else.

---

## Phase 6 — Close the remaining qualification debt

Not blockers for "works end to end", but open items that keep the project from
being production-callable:

- Observe a real certificate renewal, including the nginx reload.
- Deploy host-level runtime admission control.
- Rehearse destructive backup and restore.
- Prove the three byte-exact build hashes reproduce off the reference host.
- Security hardening: SSH, IPv6, rate limits, audit logging, egress policy.
- Ubuntu 25.04 development-mode qualification and explicit 24.04 rejection.

---

## Risks that reading cannot settle

These need execution. Each could add a cycle to Phase 3.

| Risk | Consequence if it bites |
|---|---|
| Three byte-exact build hashes (`envd`, `copy-build`, `resume-build`) were captured on the reference host and never reproduced elsewhere | Install fails at a build step with a hash mismatch and no path forward but re-pinning |
| `git fsck --strict` on a blobless partial clone | Aborts with git's raw exit code and no reason identifier |
| The no-symlink rule versus the real upstream tree | One tracked symlink upstream fails every build |
| `configure-firewall.sh apply` has never successfully run anywhere | The route-rule fix is reasoned, not executed |
| `local-dev-team` may have no creator | Surfaces the moment the seed blocker is fixed |
| Hugepage pool may not survive reboot | Phase 4 fails; needs kernel command line work |

---

## What I would not promise

That this list is complete. Every audit so far has found more, including audits
of the fixes. The honest expectation is that Phase 3 surfaces defects nobody
has predicted, and that the number of cycles is unknown until the first run
happens.

What is different after this plan is that failures will be specific, ordered,
and cheap to iterate on — instead of a runbook that claims to work and doesn't.

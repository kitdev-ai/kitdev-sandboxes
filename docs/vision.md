# Vision

Forward-looking design notes: how the sandbox mechanism actually works, what it
makes possible that is not being exploited, how the architecture scales
horizontally, and where the product could go.

**This document is aspirational, not a status report.** Where something is
verified in the pinned source it says so; where it is an idea it says that too.
Nothing here is qualified, and none of it should be read as a capability the
platform currently offers. For what is actually true today, see
[`HANDOVER.md`](HANDOVER.md).

---

## Part 1 — How templates, images and snapshots actually work

The mechanism is worth understanding precisely, because every product idea
below is a consequence of it, and so is every limit.

### The problem

Booting a Linux VM takes seconds. Sandboxes need a fresh, isolated VM per
request. Those two facts are incompatible — so E2B does not boot a VM per
request. It boots one **once**, freezes it, and thaws copies.

### Build time: Docker image to frozen VM

When `Template().fromImage("node:22").runCmd(...).setStartCmd(cmd, readyCmd)`
is built, four things happen, all verified in the pinned infra source at
`882a3b4`:

1. **Pull the OCI image.** `oci.GetPublicImage()` fetches the image from a
   registry — ordinary Docker layers.
2. **Flatten it to a disk.** `pkg/template/build/core/rootfs/rootfs.go` and
   `core/filesystem/ext4.go` collapse those layers into a single
   `rootfs.ext4`. Firecracker cannot run OCI layers; it needs a block device.
   `envd` — the in-guest agent that runs commands, serves files and drives
   PTYs — is injected here, which is why every template has it regardless of
   base image.
3. **Boot it for real.** Firecracker starts a microVM on the pinned kernel
   (`vmlinux-6.1.158`). The start command runs. The **readiness command** is
   polled until it passes — that is the entire reason `setStartCmd` takes a
   second argument.
4. **Freeze it.** Firecracker snapshots the running VM.

### What a template is on disk

| File | Reference size | Contents |
| --- | ---: | --- |
| `memfile` | 169 MB | The guest's entire RAM, mid-execution |
| `snapfile` | 30 KB | CPU registers and device state |
| `rootfs.ext4` | 6 MB | The filesystem |
| `memfile.header`, `rootfs.ext4.header` | small | Page-map indexes for lazy loading |
| `metadata.json` | 1.3 KB | Pins kernel and Firecracker versions |

Plus an **ancestor chain**: templates built with `fromTemplate()` derive from
earlier templates, the same idea as Docker layers but one level up — layers of
snapshotted machines. On the reference host the largest ancestor rootfs is
1.42 GB.

**A template is not an image. It is a paused, already-booted machine.**

### Run time: create is thaw, not boot

`Sandbox.create()` copies the rootfs copy-on-write, maps `memfile` into a fresh
Firecracker process, restores CPU and device state from `snapfile`, and lets
the VM continue from exactly where it froze. Measured on the reference host:
twelve sandboxes reached a running state in about one second — twelve resumes,
not twelve boots.

This is also why **the HugeTLB pool is the hard ceiling**. The memfile is
mapped into hugepage-backed RAM at resume. An 8 GiB template consumes 8 GiB of
pool the moment it thaws. 24 GiB ÷ 8 GiB = 3. The concurrency arithmetic is not
policy; it is how much frozen RAM can be thawed simultaneously.

### Pause, snapshot and fork are the same trick

- `pause({keepMemory: true})` snapshots a live sandbox; processes survive
  because their RAM is in the memfile. `Sandbox.connect()` resumes it.
- `pause({keepMemory: false})` discards memory, keeps the disk.
- `createSnapshot()` produces a durable, named version of the same object —
  which is why restore is `Sandbox.create(snapshotId)`. A snapshot and a
  template are the same kind of thing.

This also explains a measured result: **paused sandboxes do not consume a
concurrency slot**, because their memory is on disk rather than in the pool.

### Where Docker sits

Docker is **build-time only**. At run time there is no Docker anywhere near a
sandbox — only Firecracker processes with mapped memory files. The containers
on the host are the control plane, entirely separate from sandbox execution.

---

## Part 2 — What this makes possible

The primitive stack is: **build → boot → freeze → fork → tag → derive.** That
is, approximately, version control for running machines. Most of it is exposed
and unexploited.

### Fork already exists and has never been used

The pinned API carries:

```text
POST /sandboxes/{sandboxID}/fork
"checkpoint the running sandbox in place (it is briefly paused, snapshotted
with its full memory state, and resumed on its node, keeping its ID and
expiration untouched) and create count new sandboxes from that snapshot"
```

`SandboxForkOpts` is present in the SDK types. **This has never been
exercised** — it is not in the external SDK matrix and no evidence of a fork
call exists in this repository.

### Three shapes, increasing in difficulty

**1. Warm project templates.** An agent builds a template with dependencies
installed, the project compiled, and the dev server already running behind a
readiness probe. Subsequent tasks start in about a second instead of minutes of
dependency installation. Every API needed already exists; the only real design
question is invalidation — when does a lockfile change force a rebuild.

**2. Fork-based agent branching.** Agents are search processes that mostly
explore wrong paths, and today backtracking means redoing work. With fork: run
to a decision point, fork N ways, try N approaches concurrently, keep the
winner. Each fork inherits full memory state, so there is no re-setup cost.

This is the strongest fit for the primitive and appears to be broadly
unexploited in the ecosystem: cheap speculative execution for coding agents,
as an API call.

**3. A registry of pre-warmed OSS environments.** Real, and much harder — see
the constraints below.

### Constraints that shape all three

**Concurrency does not scale with variety.** A thousand templates cost disk;
running them costs hugepages. The ceiling stays `pool ÷ per-sandbox RAM`
regardless of library size. Variety is cheap; simultaneity is expensive. Any
product framing must be "the right environment instantly", not "many
environments at once".

**Snapshots are CPU-bound.** The memfile and snapfile encode CPU features as
they were on the machine that froze them. Resuming on a different CPU model can
fault unless features are masked with Firecracker CPU templates. For any shared
registry this is the central engineering problem, and solving it costs
performance.

**Version lock.** `metadata.json` pins the kernel and Firecracker versions, and
a snapshot is only resumable against matching versions. A registry inherits a
compatibility matrix, and kernel upgrades invalidate the catalogue.

**Security is the blocker for community content.** A Dockerfile is reviewable.
A memfile is not. Accepting a community snapshot means mapping hundreds of
megabytes of attacker-chosen memory into a VM and resuming execution inside it.
There is no meaningful review step, and hashes prove provenance rather than
safety.

The workable form: **accept build recipes from the community and build every
snapshot yourself**, on your own hardware. The ecosystem benefit remains; no
foreign RAM is ever resumed.

**Frozen state leaks.** Whatever was in RAM at freeze time is in every sandbox
forever — environment variables, tokens, sockets to hosts that no longer exist.
Template builds need a hygiene pass that Docker builds do not.

**Builds compete with runtime.** A build needs a guest-sized transient mapping
from the same pool, so agent-driven template building consumes the capacity
that serves sandboxes.

### Suggested order

Fork first: highest leverage, zero new trust surface, and one day of work tells
you whether the thesis holds. Then warm project templates, which make the
capability felt immediately. Treat a public registry as a separate, later
product with its own trust model.

---

## Part 3 — Horizontal scale

### Multi-node is an upstream capability

The pinned infra source carries a pluggable service-discovery layer:

```text
packages/api/internal/clusters/discovery/
  static.go       a fixed list of orchestrator addresses
  kubernetes.go
  remote.go
  local.go
```

`NewStaticFromAddress(addr)` takes a host and port. **The `static` provider is
exactly what a small bare-metal fleet needs** — enumerate the workers, no
Consul or Kubernetes involved.

So multi-node is supported architecturally and simply not automated here.
`PROMPT.md` scoped version 0.1 to a single host and explicitly reserved the
design space for later workers.

### Target topology

| Node | Role | Components |
| --- | --- | --- |
| 1 | Product dashboard | No E2B components; an ordinary HTTPS client of node 2's API |
| 2 | Control plane + worker | PostgreSQL, Redis, ClickHouse, Loki, API, client proxy, and an orchestrator |
| 3 | Worker only | Orchestrator, Firecracker, hugepages; no datastores |

A worker-only node needs the existing host-prerequisite convergence — KVM, TUN,
NBD, hugepages, identities — plus the orchestrator binary and unit. It is a
materially smaller install than the control plane, and much of it exists.

### The four work items

**1. Shared template storage — the hard one.** Templates live on local disk
today, and a second worker cannot resume a snapshot it does not have. Two
upstream paths exist: S3-compatible object storage, which `PROMPT.md` already
anticipates, or the `pkg/sandbox/template/peerserver` package, which implies
node-to-node fetching. Size matters: every template is a ~170 MB memfile plus
rootfs plus ancestor chain, so cold-start penalties apply until a node has
cached what it needs.

**2. Private networking — security-critical.** The orchestrator ports (5007,
5008, 5010, 5016-5018) are currently UFW-scoped to the Docker bridge and guest
veths. Crossing machines means traversing a network, and **they must never
touch the public Internet**. Use provider private networking or WireGuard. The
orchestrator gRPC endpoint is effectively root on the box.

**3. Discovery configuration — small.** Point static discovery at every
orchestrator. Confirm which provider the deployment currently uses before
building on it, since the single-node case may be hardcoded.

**4. Automation that assumes one host.** The lifecycle flow, firewall rules and
install path all assume co-location. Separating "control-plane install" from
"worker install" into composable roles is the real design work.

### Why this is the only real capacity lever

Concurrency is bounded by the HugeTLB pool, and no software change moves that
number. A second 64 GB worker adds its own pool: roughly three to four more
heavy sandboxes, or a dozen small ones. Horizontal workers are the only way to
raise the ceiling.

It also isolates blast radius: a worker destabilised by a hostile sandbox does
not take the database with it.

---

## Sequencing

None of this is buildable on a platform that cannot survive a reinstall. The
single-host install does not currently complete on fresh hardware; multi-node
is strictly harder, and debugging distributed template fetch on an
irreproducible host would be miserable.

The remediation plan in
[`fresh-host-remediation-plan.md`](fresh-host-remediation-plan.md) comes first.

**But the target shape should influence that work now.** If the storage and
Docker roles are written as "the host", they will need rewriting for
multi-node. Written as "a control-plane host" and "a worker host" from the
start, horizontal scale becomes an addition rather than a refactor. That
distinction is cheap while the roles are unwritten and expensive afterwards.

One experiment is worth running before the reinstall destroys the current
deployment: **exercise `fork`**. It is the central primitive of Part 2, it has
never been called, and the host is going to be wiped anyway.

---

## Part 4 — Product direction

Where the platform is heading, and what each direction demands of the
infrastructure. The ideas here are the operator's; the framing, grouping and
constraint analysis are mine.

### Where it starts

Today: a multi-tenant dashboard with per-user chat threads, filesystem and
process tools, and **one sandbox per organisation** carrying per-user workspace
directories.

That last detail is the one to hold onto. It means isolation between users
inside an organisation is *directory-level, not VM-level*, and that the whole
organisation shares one machine's memory, CPU and blast radius. It is a
reasonable starting point and it does not survive most of what follows.

### Grouped by what they demand

**A. The product thesis — agentic development environments**

A web terminal multiplexer in the dashboard, running real coding agents inside
sandboxes — Claude Code, Codex, opencode — supervised by first-party agents,
against either a local LLM endpoint or the user's own subscription. Around it,
a generated project UI and Git forge connections. "Claude in a box."

Its natural conclusion is the workflow idea: **a user supplies a repository URL
and first-party agents adopt it as a long-lived coding and devops project.**
Not a chat session that happens to touch code — a durable engagement with
memory, state and its own environment.

This is the centre of gravity. Everything else is either an enabler or a
distribution channel for it.

What it demands: per-user or per-session sandboxes rather than per-org;
credentials near agent code without ever being *in* the sandbox; PTY at scale
(proven, but never under load); and persistence beyond a sandbox's lifetime.

**B. Reach — additional channels**

WhatsApp, Telegram, Discord and similar. Architecturally the cheapest item
here: a gateway concern that barely touches the sandbox layer.

The one caveat is second-order. Every channel multiplies concurrent sessions,
and sessions are what the capacity ceiling counts.

**C. Integration surface — external tools and vertical products**

Third-party integration (Nango, Git forge search, Google, Slack, email), and
packaged vertical agents built on top: read-only AWS/Cloudflare/Grafana devops
assistants, or Blender-in-a-sandbox for 3D printing.

These are the same shape wearing different clothes: **an agent that needs
someone else's credentials to be useful.** The vertical products are really
templates — a Grafana agent is a warm image with tooling pre-installed and a
scoped token, which is exactly what the snapshot mechanism is good at.

Worth noting that "read-only" in a devops agent is enforced by *credential
scope*, not by the sandbox. The VM boundary protects the host from the agent;
it does nothing to stop an over-scoped token from dropping a production table.

**D. The template economy**

A library of pre-warmed open-source environments — Mattermost, Supabase, boot
any OSS project — and, the multiplier, **first-party agents given tools to
author, evolve and manage templates and sandboxes themselves.**

These belong together. A hand-curated library does not scale; agents that
author templates make the catalogue a living thing. Part 2's constraints apply
in full, particularly the security model: accept recipes, build snapshots
yourself.

Note the sizing problem. A full Supabase or Mattermost is not a 2 GiB template.
A library of heavy environments is a library where very few entries run at
once.

**E. Selling the substrate**

A commercial sandbox service — fork-based branching and the rest of Part 2 sold
as infrastructure.

This is a genuinely different business from A. Selling a product built on
sandboxes and selling sandboxes are different customers, different SLAs,
different support burdens, and a much harsher multi-tenancy bar. Worth being
deliberate about rather than drifting into, and it is the one direction that
should probably wait until the platform can be rebuilt from scratch on demand.

### The four things that gate all of it

Independent of which direction is chosen first, four pieces of infrastructure
are load-bearing and mostly absent.

**1. A credential broker.** A, C and the repo-adoption workflow all need
third-party credentials near agent code. The sandbox security model assumes
sandbox code is hostile, so those credentials must never enter the VM. The
pattern that works is a broker on the control plane: tools execute outside the
sandbox, or short-lived scoped tokens are minted per call and never persisted
inside.

`PROMPT.md` explicitly deferred this — "provide an interface for short-lived
credential proxying later, but do not implement a broad credential broker in
milestone one". Every direction above collects that debt. It is the single
largest architectural gap between what exists and what is described here.

**2. A revised isolation model.** One sandbox per organisation is incompatible
with per-user coding agents. Long-running agent sessions want their own
machine, which multiplies demand against a ceiling of twelve small sandboxes.

The economics work only because **paused sandboxes do not consume a
concurrency slot** — verified by measurement. An idle agent session should be
frozen, not resident. Pause-on-idle and resume-on-message is what makes
per-user sessions affordable, and it is a behaviour the product layer has to
implement deliberately.

**3. Capacity, which means multiple workers.** Every direction adds concurrent
sandboxes; the pool gives twelve small or three heavy. Part 3's horizontal
scaling is not an optimisation for later, it is a prerequisite for more than
one of these at a time.

**4. Persistence.** "Long-term coding project" cannot mean a sandbox with a
24-hour maximum lifetime. It needs durable workspaces that outlive any
individual sandbox — snapshot per milestone, fork for speculative work, and a
volume that survives both. Persistent volumes are listed in the product brief
and are not deployed.

### A sequencing view

Ordered by what is cheap given what exists, versus what needs new
infrastructure first.

| Direction | Depends on | Relative cost |
| --- | --- | --- |
| Additional channels | Nothing structural | Low |
| Fork-based branching | Fork being exercised | Low — the API already exists |
| Warm project templates | Template build automation | Low to moderate |
| Terminal multiplexer + coding agents | Credential broker, per-session isolation, pause-on-idle | High |
| External tool integration | Credential broker | High |
| Vertical products | Credential broker, template library | High |
| OSS template library | Template authoring, storage, capacity | High |
| Agents authoring templates | All of the above, plus build quota | Highest |
| Repo-adoption workflow | Persistence, credential broker, isolation, capacity | Highest — but it is the thesis |
| Commercial sandbox service | Reproducibility, multi-node, quotas, billing | Separate business |

The honest read: **the two cheapest items — fork and warm templates — are also
the two that most directly prove the thesis.** A repository adopted as a
long-lived project is exactly a warm template that evolves, plus forks for
speculative work, plus a volume that persists. Building those two first buys
evidence for the expensive work rather than betting on it.

And none of it is buildable on a platform that cannot survive a reinstall.

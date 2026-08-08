# Working on kitdev-sandboxes

Guidance for any AI agent contributing to this repository. `CLAUDE.md` points
here; this file is the single source.

This project builds an E2B-compatible sandbox platform on one bare-metal Ubuntu
server. It executes hostile code in Firecracker microVMs and it operates a live
host over SSH. Both facts shape everything below.

## Read first

1. [`PROMPT.md`](PROMPT.md) — the product contract.
2. [`docs/HANDOVER.md`](docs/HANDOVER.md) — current state, capacity model,
   dependency-ordered backlog, and what is *not* qualified.
3. The newest dated file in [`docs/research/`](docs/research/README.md).

If two documents disagree, prefer the newer dated evidence and the current
code, then fix the stale document in the same change.

## The one rule that matters most

**Evidence, not intent.** A task is done when a stated gate has a recorded
result, not when the code looks right. Never describe something as working,
proven, qualified, or complete unless a command actually produced that result
and you can point at it. If a step was skipped, say so. If a test failed, show
the output. Narrowing scope silently is worse than reporting a blocker.

Corollaries that have already caught real defects here:

- A loopback pass is not an external pass.
- A static config test is not a running service.
- A unit test asserting a string appears in a script is not proof the script
  runs. Six ingress defects survived unit tests and only surfaced on first
  execution.
- Changing a limit can silently falsify an existing assertion. Raising sandbox
  concurrency to 12 turned a passing concurrency-refusal test into a lie; it
  had to be replaced, not left green.

## Working on the live host

Access is via a private SSH alias config that is untracked and must never be
copied into committed files, along with the host's address, hostname, keys, or
inventory.

Before any mutation:

- run a read-only audit first — locks, Firecracker processes, hugepages, disk,
  containers, health endpoints, listeners, firewall;
- confirm both lifecycle locks are free;
- confirm the worktree is clean and pushed, then stage that **exact commit** to
  a root-only directory on the host and run from there. Never run uncommitted
  bytes against the host, and never hand-edit an installed file under `/opt`.

Every manual server change must become reviewed repository automation that can
reproduce it on a freshly installed host. When you find yourself about to edit
something by hand, that is the signal to write the tool instead — that is how
`install-ingress.sh update` and `set-team-limits.sh` came to exist.

Never do these without explicit approval: reboot, change SSH configuration,
disable the firewall, remove packages, stop unrelated services, delete Docker
resources, or run broad `rm -rf` outside verified project paths.

## Secrets

Never print, echo, commit, or paste a secret — API keys, DNS tokens, TLS
private keys, database credentials, `.env` contents. Not into chat, not into a
commit message, not into a research document, not into a log.

To verify a secret, verify its *properties*: ownership, mode, link count, size,
a shape regex, or an authenticated call that returns success. That is enough to
prove a credential works without ever reading it.

Secret files are `root:root`, mode `0600`, regular, single-link. When a
credential must reach a human or another machine, move it host-to-host over a
channel that never renders it, and tell the operator to place it themselves.

## Making a change

Small, coherent, independently reviewable commits. Every commit message states
what changed, how it was tested, known limitations, and rollback.

Before committing:

```console
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_*.py'
bash -n <any changed shell script>
git diff --check
```

The suite needs `pyyaml` and `pytest`. Without them three modules fail to
import and the run is not a clean result — do not report it as one.

For anything touching the public path, re-run the external matrix:

```console
cd scripts/external-sdk-matrix && npm ci --ignore-scripts
E2B_API_URL=... E2B_DOMAIN=... E2B_API_KEY_FILE=... node matrix.ts
```

## Supported hosts

Ubuntu 26.04 LTS on x86-64 is the production target. Ubuntu 25.04 is accepted
only for explicit development or migration work. **Ubuntu 24.04 is not a target
and must never be described as one.** Never widen the supported surface to make
something pass.

## Capacity model

Sandbox memory comes from a reserved HugeTLB pool, not ordinary RAM. Concurrency
is `pool size / per-sandbox RAM`, and past that point creates fail cleanly while
running sandboxes are unharmed. A team limit above the pool is safe but buys
nothing. Builds and snapshots need a transient guest-sized mapping, so a pool
filled with sandboxes will fail a build.

## Delegating to subagents

Fan out for read-only investigation — searching, reading many files, evaluating
a condition across a host. Give the agent explicit prohibitions when it can
reach the live host; "read-only" must be spelled out as specific forbidden
commands, not implied.

Verify what comes back. Subagent reports have been right on substance and wrong
on detail here, and a report written before a change lands can assert something
that is no longer true. Check claims against the code or the host before acting
on them, and especially before persisting them as documentation.

# Disposable OVH lab experiments

This directory is a versioned experiment harness for a disposable Ubuntu 26.04
bare-metal lab. It is not the production installer and is not evidence that a
host can be upgraded in place. Final acceptance requires an OVH reinstall,
followed only by the reviewed `kitdev`/Ansible installation path on the clean
image.

The harness exists to replace undocumented interactive shell work with fixed,
reviewable stages. It contains no endpoint, account, credential, host key,
address, or secret. Runtime access uses an operator-managed SSH alias and a
separate verified `known_hosts` file. Scripts are streamed to root's standard
input; they are not copied to the server. Redacted transcripts and summaries
are created off-host under ignored `artifacts/ovh-lab/` by default.
Each remote invocation is bounded to 90 seconds and one MiB of redacted output.

## Safety contract

Every invocation requires a `DISPOSABLE_OVH_LAB` approval bound to the selected
stage, operation, SSH alias, and exact streamed bundle SHA-256. Every
remote stage uses `set -Eeuo pipefail`, checks for production markers before any
work, captures a before and after snapshot, runs explicit postconditions, and
documents rollback or reinstall recovery. Stages fail closed on unknown state.
The lab marker does not authorize a later stage whose manifest status is
`blocked`.

The runner requires strict host-key checking and accepts only a simple SSH
configuration alias, not an endpoint. Configure the alias and verified host key
outside this repository. `OVH_LAB_SSH_CONFIG` must name an absolute, regular,
non-symlink file owned by the invoking user with no group or other permission
bits; mode `0600` is recommended. The path is control-free and bounded, and the
file is limited to one MiB. `Include` is rejected: this is deliberately a
single-file configuration boundary. For execution, the runner creates one
private mode-0600 snapshot in the guarded local run directory, hashes it into
the approval, passes that same snapshot to every `ssh -F` call, and removes it
on exit. An alias-mapping change therefore invalidates the prior approval. The
config path and content are not logged.
Do not put secrets in environment variables, command arguments, stage output,
or evidence. The redactor is defense in depth, not a license to print sensitive
data.

Generate the exact approval locally, then supply it unchanged. This is an
example shape, not authorization to run a stage:

```bash
approval="$(OVH_LAB_TARGET=operator-managed-alias \
  OVH_LAB_SSH_CONFIG=/operator/private/ssh-config \
  ./experiments/ovh-lab/run-stage.sh 00 approval)"
DISPOSABLE_OVH_LAB="$approval" \
OVH_LAB_TARGET=operator-managed-alias \
OVH_LAB_SSH_CONFIG=/operator/private/ssh-config \
OVH_LAB_KNOWN_HOSTS=/operator/private/verified-known-hosts \
./experiments/ovh-lab/run-stage.sh 00 execute
```

Only `00` and `30` are currently executable, and both are read-only. Every
mutation stage, the lab marker, and final acceptance intentionally return status
`20` because an exact crash-consistent transition does not yet exist.
`stages.json` is authoritative for stage selection and status.

## Stage lifecycle

The runner calls each selected script four times: `before`, `execute` (or an
explicit `rollback`), `after`, and `postconditions`. Normal failures get an
after snapshot and postcondition attempt. External termination can interrupt
that evidence sequence; no mutation is executable while this limitation
remains. Stage output is normalized
key/value evidence; raw firewall rules, addresses, serials, hostnames, account
names, environment, and configuration contents must never be printed.

Rollback is not implemented because all mutable stages are blocked. Once a
future reviewed stage changes storage, network, firewall, kernel, or workloads,
the authoritative whole-lab rollback is an OVH operating-system reinstall.

## Promotion rule

Experimental commands are not copied into production automation by default.
For each useful result, add typed discovery, a deterministic `kitdev` dry-run
action, pinned dependencies, Ansible convergence and rollback, hermetic tests,
and clean Ubuntu 26.04 apply/apply/reinstall verification. Only that reviewed
path can qualify the reusable system described by `PROMPT.md`.

# OVH disposable lab Stage 05 apply/apply qualification

Date: 2026-08-06
Runs: `run-05-ZgYEf0Rm`, `run-05-JTmISBLY`
Status: initial apply and idempotent reapply successful; rollback qualification pending

## Scope and evidence

The approved disposable-lab runner executed Stage `05` twice against the Ubuntu
26.04 bare-metal lab after the independently reviewed usr-merge compatibility
correction. Each run streamed the same immutable bundle through its normal
`before`, `execute`, `after`, and `postconditions` phases and retained redacted
evidence and a summary off-host. The first run applied the plan; the second
exercised the validated idempotent path. No endpoint, account, host key,
address, device name, serial, or private path is included in this report.

The immediately preceding run, `run-05-GRF4C5bu`, failed closed during the
read-only `before` production check because the standard `/lib -> usr/lib`
layout was classified through a redundant lexical scan. That run performed no
Stage 05 mutation. The reviewed correction removed only that redundant alias;
the canonical unit paths and all systemd `LoadState` checks remain enforced.

## Immutable identity

| Evidence | Exact value |
| --- | --- |
| Bundle SHA-256 | `sha256:cfa5d8120cda53ca4059778f017aa358fd4d88131a4069bb1270a66be1a0830c` |
| Plan SHA-256 | `sha256:1fbabd0b7bdee03fcc981b65689444b2252c56d533c26e61016adc8bf23010c0` |
| Marker SHA-256 | `sha256:971099e673034ae69d9caa771cf544d316367ffc1ea7a6294aa1c6cf82914732` |

The same three values appeared in every normalized phase. The off-host summary
for each run also recorded operation, after, and postcondition return codes as
zero and stored the exact Stage 05 plan hash. SSH-config and verified-host-key
hashes remain in the ignored artifacts only; they are not needed in this
redacted report.

## Normalized result

| Phase | Journal/root state | Resource result |
| --- | --- | --- |
| `before` | Journal root and journal absent; zero transitions | All managed resources absent; workspace classified empty; no retained provenance |
| `execute` | Bootstrap residue reconciled to `validated`; four transitions | Exact desired state; empty workspace; retained provenance present |
| `after` | Exact journal root; `validated`; four transitions | Exact desired state; empty workspace; retained provenance present |
| `postconditions` | Exact journal root; `validated`; four transitions | Exact desired state; empty workspace; retained provenance present |

The second run began with the exact state left by the first:

| Second-run phase | Journal/root state | Resource result |
| --- | --- | --- |
| `before` | Exact journal root; `validated`; four transitions | Exact desired state; empty workspace; retained provenance present |
| `execute` | Exact journal root; `validated`; four transitions | Exact desired state; empty workspace; retained provenance present |
| `after` | Exact journal root; `validated`; four transitions | Exact desired state; empty workspace; retained provenance present |
| `postconditions` | Exact journal root; `validated`; four transitions | Exact desired state; empty workspace; retained provenance present |

The four journal states are the fixed
`planned -> applying -> applied -> validated` sequence. Both independent
post-mutation observations reopened the fixed paths and journal and returned
`next_action=stage-specific-approval`; the marker alone does not authorize any
later blocked stage.

The second `execute` retained the same four transitions and exact hashes in
every phase. Combined with the reviewed terminal-state implementation, this is
the remote apply/apply result: the second operation performed validation only,
published no new journal transition, and left the Stage 05 resources unchanged.

## Exact mutation boundary

Stage 05 created only its reviewed project-owned transaction and authorization
objects:

- `/var/lib/kitdev-sandboxes` and its root-only journal directory and canonical
  Stage 05 journal;
- `/var/lib/kitdev-sandboxes/experiments` and the empty root-only `ovh-lab`
  workspace;
- `/etc/kitdev-sandboxes` and the canonical root-only disposable-lab marker.

The successful evidence proves those resources reached the exact contract
state and that the workspace was empty after execution. Stage 05 contains no
package, account/group, storage, kernel, Docker/container, network/firewall,
systemd service, mount, or reboot action. None of those host areas was changed
by this run. Ordinary SSH and operating-system audit/session telemetry remain
outside the Stage 05 mutation model.

The state root, journal root, and journal are retained provenance. A future
approved Stage 05 rollback removes the marker and empty workspace resources but
retains that journal. These successful runs establish Stage 05 apply/apply
qualification. They do not yet establish rollback/rollback host qualification,
do not enable later mutation stages, and do not replace the final Ubuntu
reinstall and clean automation acceptance gate.

## Next gate

Use a separately generated exact approval for rollback. Before that operation,
preserve the current off-host evidence and require the same production refusal,
platform gate, fixed bundle identity, and exact journal/resource
reconciliation. Later mutation stages remain blocked until their own plans,
journals, tests, and reviews exist.

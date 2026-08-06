# Stage 05 disposable-lab marker and workspace contract

Date: 2026-08-06
Status: independently approved for local qualification; disposable-host execution pending

## Purpose and boundary

Stage `05` establishes durable, host-local evidence that the operator applied
the disposable-lab bootstrap plan and creates the otherwise empty workspace for
later experiments. It must not install software, create service identities,
change kernel/network state, mount storage, start services, or establish any
production installation state.

This contract replaces the rejected marker transition described in
`ovh-disposable-lab-framework.md`. The prior design was correctly blocked
because ordinary `mkdir`/file creation followed by best-effort cleanup would
not provide durable provenance, no-follow ancestry enforcement, or a
retry-safe answer after interruption.

This document specifies and records the implemented Stage 05 contract. Stage
05 is the only executable mutation in the experiment manifest; every later
mutation remains blocked. Implementation and hermetic testing did not contact
the host or authorize a remote run. A separately generated bundle-bound
approval is still required for disposable-host execution.

## Existing primitives and required extension

`src/kitdev_sandboxes/journal.py` already supplies the durable journal record
and state machine required here:

- canonical, bounded, secret-rejecting records;
- exact `install_id`, `plan_id`, `plan_hash`, and sorted resource matching;
- `planned -> applying -> applied -> validated` and rollback/failure paths;
- root ownership, exact directory/file modes, no-follow descriptor traversal,
  nonblocking directory locking, exclusive publication, file and directory
  `fsync`, canonical crash-residue cleanup, and fail-closed conflict handling.

It deliberately does not inspect or reconcile host resources. Stage 05 needs a
new typed reconciler around that API. The existing shell script must not
reimplement journal encoding, filesystem traversal, or recovery logic.

The original directory lock covered one `create()` or `transition()` call, not
the resource mutations between journal transitions. The implementation adds a
`JournalStore` transaction/session API that opens and exclusively
locks the validated journal-root descriptor once, exposes load/create/transition
operations on that already-locked descriptor, and holds the lock through
collection, mutation, reconciliation and terminal journal publication. It must
not acquire a second flock through the current public methods while holding the
session lock. Process death releases the kernel lock; durable state, not the
lock, determines recovery.

That API must extend the current policy checks rather than relying on a shell
preflight: require GID 0 for the journal root and files, link count 1 for a
published journal file, and stable descriptor reads. It captures type, mode,
UID, GID, link count, size, device, inode, `mtime_ns` and `ctime_ns` before and
after each bounded read and rejects any change. Create/replace then reopens the
published name through the same locked root descriptor and repeats the checks.
The sole linked temporary exception is initial `planned` journal publication
where the final journal and its one valid temp name resolve to the same inode
with link count 2. Locked recovery removes the temp, `fsync`s the directory,
and requires link count 1 before returning the published record. A later
transition may leave one separate link-count-one temp only when it is the exact
canonical legal outbound record from the current final state; locked recovery
may remove it before either a forward or rollback decision. Separate residue
beside a terminal final, an impossible linked terminal residue, or any other
content is preserved and blocks.

There is one unavoidable bootstrap boundary: `JournalStore` requires its
trusted root to exist, while Stage 05 is the first project-state mutation. The
implementation must add a narrowly scoped secure-root allocator for exactly
`/var/lib/kitdev-sandboxes` and its `journal` child. These directories are
transaction infrastructure, not authorization markers. The allocator must
leave an exact, safely recognizable residue if interrupted. No config,
workspace, marker, or other project resource may be created before the journal
is durable in `planned` state.

The streamed bundle must contain the reviewed Python journal and reconciler
code, or invoke an artifact whose digest is included in the approved bundle.
It cannot import an unverified checkout or preinstalled `kitdev` package from
the remote host. It uses the fixed `/usr/bin/python3` only after checking that
the interpreter satisfies the project's supported version; Stage 05 cannot
install or select a different interpreter. The runner continues to hash the
full effective bundle.

## Fixed identifiers and paths

| Purpose | Exact value |
| --- | --- |
| Stage | `05` |
| Install ID | `ovh-lab-stage05-v1` |
| Plan ID | `ovh-lab-stage05-marker-workspace-v1` |
| State root | `/var/lib/kitdev-sandboxes` |
| Journal root | `/var/lib/kitdev-sandboxes/journal` |
| Journal file | `/var/lib/kitdev-sandboxes/journal/ovh-lab-stage05-v1.journal.json` |
| Config directory | `/etc/kitdev-sandboxes` |
| Marker file | `/etc/kitdev-sandboxes/disposable-ovh-lab` |
| Experiments directory | `/var/lib/kitdev-sandboxes/experiments` |
| Workspace | `/var/lib/kitdev-sandboxes/experiments/ovh-lab` |

No environment variable, command-line option, working directory, symlink, bind
mount, or configuration file may redirect these host paths. A future path or
schema change requires a new plan ID and review.

## Ownership and modes

| Resource | Type | Owner | Mode | Entry policy |
| --- | --- | --- | --- | --- |
| State root | directory | UID 0, GID 0 | `0755` | Before journal creation: empty or `journal` only; initially validated: `journal` plus `experiments` only |
| Journal root | directory | UID 0, GID 0 | `0700` | Empty or exact same-plan canonical journal residue; later stage journals require their own contracts |
| Journal file | regular file | UID 0, GID 0 | `0600` | Canonical `JournalRecord`; link count 1 |
| Config directory | directory | UID 0, GID 0 | `0755` | Marker only at initial Stage 05 validation |
| Marker file | regular file | UID 0, GID 0 | `0600` | Exact canonical JSON plus newline; link count 1 |
| Experiments directory | directory | UID 0, GID 0 | `0700` | `ovh-lab` only at initial Stage 05 validation |
| Workspace | directory | UID 0, GID 0 | `0700` | Empty at initial Stage 05 validation |

Stage 05 supports only UID/GID `0:0`. It must not infer or create a group. The
workspace is root-only because every lab stage currently executes under
`sudo`. Later stages may create narrower service-owned children through their
own journals, but must not change Stage 05-owned directory metadata; such a
change invalidates the authorization proof and requires a new contract.

Directories or files with broader modes, unexpected ACLs, unexpected extended
attributes that alter access, multiple hard links, different ownership, a
different type, or a mount boundary are conflicts. Extra entries are conflicts
during initial Stage 05 validation and Stage 05 rollback; after initial
validation they require downstream journal ownership as described next. Stage
05 must not repair, empty, rename, replace, or adopt them.

One explicit crash-prefix amendment was approved during implementation. A
retry may finish the `0700 -> 0755` permission transition only for the exact
descriptor-opened provisional directory at the next legal operation prefix:
the state root before journal creation, or the config directory when the exact
same-plan journal proves `applying` with config as its next resource. The
directory must be root-owned, have link count two, be empty, have no xattrs,
ACLs or capabilities, remain on the expected mount, and retain the same
device/inode identity throughout validation. Recovery performs one descriptor
`fchmod`, `fsync`s it and its parent, then revalidates. A provisional directory
at any other path or prefix is a conflict. Read-only observation may classify
this residue but never repairs it. This amendment resolves interruption after
`mkdirat(0700)` and before the originally specified `fchmod(0755)`; it is not a
general existing-directory repair rule.

The initial entry policies above prove that Stage 05 starts from an unclaimed
workspace. They are not permanent claims over future contents. A later reviewed
stage may add a resource only under its own exact write-ahead journal. Once
that occurs, Stage 05 continues to own and reconcile the four directory/file
objects in its resource list, while the downstream journal owns the added
entry. Unknown or unjournaled content blocks the stage that targets it and
blocks Stage 05 rollback; it does not make Stage 05 silently adopt the content.

## Canonical marker and plan

The marker is authorization evidence, not a Boolean file. Its bytes must be
canonical ASCII JSON with sorted keys, compact separators, and one final
newline. Its complete schema is:

```json
{"authorization_scope":"disposable-ovh-lab","bundle_sha256":"sha256:<64 lowercase hex>","install_id":"ovh-lab-stage05-v1","plan_id":"ovh-lab-stage05-marker-workspace-v1","schema_version":1}
```

`bundle_sha256` is the exact Stage 05 streamed bundle digest already bound into
the operator approval. The marker contains no endpoint, SSH alias, SSH config
digest, account, timestamp, host identifier, credential, or secret.

The implementation computes `plan_hash` as SHA-256 over this separate plan
object, serialized with sorted keys, compact separators, ASCII encoding and one
final newline:

```json
{
  "bundle_sha256": "sha256:<Stage 05 bundle digest>",
  "install_id": "ovh-lab-stage05-v1",
  "operations": [
    "create-directory.config",
    "create-directory.experiments",
    "create-directory.workspace",
    "publish-file.authorization"
  ],
  "plan_id": "ovh-lab-stage05-marker-workspace-v1",
  "resources": [
    {"desired_state":"directory:uid=0:gid=0:mode=0755","id":"directory.config","prior_state":"absent","target":"/etc/kitdev-sandboxes"},
    {"desired_state":"directory:uid=0:gid=0:mode=0700","id":"directory.experiments","prior_state":"absent","target":"/var/lib/kitdev-sandboxes/experiments"},
    {"desired_state":"directory:uid=0:gid=0:mode=0700","id":"directory.workspace","prior_state":"absent","target":"/var/lib/kitdev-sandboxes/experiments/ovh-lab"},
    {"desired_state":"file:uid=0:gid=0:mode=0600:nlink=1:sha256:<marker digest>:bundle_sha256=sha256:<64 lowercase hex>","id":"file.authorization","prior_state":"absent","target":"/etc/kitdev-sandboxes/disposable-ovh-lab"}
  ],
  "schema_version": 1,
  "stage": "05"
}
```

Whitespace in this displayed object is explanatory; the hashed bytes are the
compact canonical serialization. The journal stores the resulting lowercase
`sha256:<hex>` value. The marker does not contain `plan_hash`, avoiding a hash
cycle. Later readers can reconstruct the plan from the marker's bundle digest
and fixed contract, then compare the recomputed plan hash with the journal.
Before the marker exists, a retry obtains the original bundle digest from the
`file.authorization` desired-state string in the canonical journal. It accepts
forward or rollback recovery only when the currently approved immutable bundle
has that exact digest. A changed checkout/bundle cannot resume the transaction;
the operator must use the original reviewed revision or reinstall. The digest
must never be accepted as an unverified free-form recovery argument.

ADR 0002's production installation manifest calls for creation/update times,
phase and release. This Stage 05 journal is an explicit disposable-lab
exception, not that production manifest: transition history supplies phase,
and the exact bundle digest supplies immutable revision identity, while host
timestamps are intentionally excluded from the canonical retry identity. The
off-host run evidence may record its local collection time. This exception
expires at the mandatory reinstall and does not relax the production manifest
contract.

The exact journal resources are:

| Resource ID | Target | Prior state | Desired state |
| --- | --- | --- | --- |
| `directory.config` | `/etc/kitdev-sandboxes` | `absent` | `directory:uid=0:gid=0:mode=0755` |
| `directory.experiments` | `/var/lib/kitdev-sandboxes/experiments` | `absent` | `directory:uid=0:gid=0:mode=0700` |
| `directory.workspace` | `/var/lib/kitdev-sandboxes/experiments/ovh-lab` | `absent` | `directory:uid=0:gid=0:mode=0700` |
| `file.authorization` | `/etc/kitdev-sandboxes/disposable-ovh-lab` | `absent` | `file:uid=0:gid=0:mode=0600:nlink=1:sha256:<marker digest>:bundle_sha256=sha256:<64 lowercase hex>` |

Tests must assert the exact state-string and canonical-plan bytes. The state
and journal roots are excluded from this resource list because they are the
retained transaction substrate described below, not disposable authorization
resources.

## Secure path rules

Every observation and mutation uses directory descriptors from `/`; lexical
checks such as `test -e`, `realpath`, or a preflight `lstat` followed by a path
operation are insufficient.

1. Open each existing ancestor with `O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC` and
   `dir_fd`; validate it with `fstat` before opening the next component.
2. Require ancestors to be root-owned directories without group/other write
   bits. Require the fixed final resource owner and exact mode above.
3. Create directories with `mkdirat` against an already validated parent, then
   open and `fstat` the created entry. The reconciler sets `umask 077`, creates
   at `0700`, then may `fchmod` that just-created descriptor to the exact final
   `0755` where specified before its first directory `fsync`. Because execution
   is UID/GID 0, wrong ownership after create is a conflict; `fchown` is not a
   repair mechanism. `EEXIST` never means success until the exact admissible
   state is independently verified, and an existing entry is never chmodded.
4. Reject `.`/`..`, doubled separators, control or format characters, symlinks,
   non-directory ancestors, device/FIFO/socket targets, and path-component
   substitution races.
5. Require every managed descendant to remain on the expected parent device
   and mount. `st_dev` alone cannot detect a same-filesystem bind mount; compare
   mount IDs using `statx(STATX_MNT_ID)` or an equivalently bounded validated
   `/proc/self/mountinfo` collector. Record `(mount_id, st_dev, st_ino)` in the
   current locked session and confirm descriptor identity before any destructive
   action in that session.
6. Publish the marker through an exclusive root-owned `0600` temporary file:
   write all bytes, `fsync` the file, validate it, publish without replacement
   using the journal's hard-link pattern or equivalent no-replace primitive,
   `fsync` the directory, remove the temporary link, and `fsync` again.
   If the hard-link pattern is used, the only valid temporary name is
   `.disposable-ovh-lab.tmp.<32 lowercase hex>`. Recovery may remove one such
   residue only when its bytes and metadata match the expected marker and,
   when both names exist, both names identify the same inode. Marker absent
   plus one exact temp is discarded durably and republished; different inodes,
   multiple temps, or any noncanonical temp block.
7. Enumerate directories through their validated descriptors. An unknown entry
   or suspicious temporary artifact is a hard conflict and is never removed.
8. Before removing a file, require exact content, metadata, link count and
   descriptor identity. Remove directories deepest-first only when exact and
   empty, then `fsync` each changed parent.

Every Stage 05 `execute` or `rollback` holds the exclusive journal-root session
lock from its first resource observation through its final in-transaction
validation. The runner's later `after` and postcondition calls acquire fresh
shared nonblocking sessions or return `transaction_busy`; they must not report
an in-flight prefix as stable. All later mutable lab stages use the same
journal-root session lock before checking Stage 05 authorization or their own
state, preventing cross-stage resource and journal interleaving.

A read-only call never creates the state or journal root merely to obtain a
lock. When the roots are absent, it observes the deepest existing safe ancestor
twice and reports `absent` only if the classification is stable; otherwise it
returns `transaction_busy` or `unknown`.

For this disposable Ubuntu profile, the allowed extended-attribute set on the
two bootstrap roots and all four managed resources is exactly empty. The safe
ancestry check also rejects POSIX access/default ACL and `security.capability`
xattrs; inability to enumerate them is `unknown`. A host that requires an
additional security-label xattr needs an explicit contract revision rather
than a wildcard allowlist. Checks use descriptor-based `flistxattr` equivalents
before and after mutation.

All variable input and enumeration is bounded:

| Input | Maximum |
| --- | --- |
| Entries in any inspected directory | 4,096 |
| One UTF-8 entry name | 255 bytes |
| Total encoded entry-name bytes per directory | 1 MiB |
| Marker bytes | 4 KiB |
| Canonical plan bytes | 64 KiB |
| Journal bytes | Existing `JournalStore` 1 MiB limit |
| `/proc/self/mountinfo` fallback | 1 MiB, 8,192 lines, 16 KiB per line |
| One systemd classification response | 4 KiB |

Exceeding a cap is `unknown`/conflict, never truncation followed by mutation.

The allocator follows the same ancestry rules. If absent, it creates the state
root at `0700`, opens it, applies the just-created `fchmod(..., 0755)`, verifies
and `fsync`s it, then `fsync`s `/var/lib`. It creates the `journal` child at
`0700`, opens, verifies and `fsync`s it, then `fsync`s the state root. A retry
may adopt only these exact prefixes: state root absent; the exact empty `0700`
provisional state root described by the approved crash-prefix amendment; exact
empty final-mode state root; exact state root containing only an empty journal
root; or exact roots containing canonical residue for the same Stage 05 plan.
A state root that lacks the exact journal child but contains anything else, or
a journal root containing foreign state, fails closed.

Across a process crash, schema-v1 `JournalRecord` does not persist inode or
mount IDs and its resource list is immutable. Recovery therefore must not
claim that an inode survived the crash. Its ownership proof is instead the
combination required by ADR 0002: the exact canonical journal and install/plan
IDs, legal transition and operation prefix, fixed canonical paths, root-only
nonwritable ancestry, exact type/UID/GID/mode/content/entry state, and absence
of a mount boundary. Within one locked process, descriptor identities still
close path-swap races. A hostile root can forge either proof and is outside the
threat model; unprivileged substitution is prevented by the ancestry modes.

## Production refusal

`lab_refuse_production` must be upgraded to use the same no-follow collector
before Stage 05 can become executable. Stage 05 refuses if any of these is
present, is a symlink, has unsafe ancestry, cannot be classified, or changes
during the check:

- `/etc/kitdev-sandboxes/production`;
- `/var/lib/kitdev-sandboxes/install-manifest.json`;
- `/etc/kitdev-sandboxes/install-manifest.json`;
- `/opt/kitdev-sandboxes`;
- any known production API, client-proxy, or orchestrator service unit in
  `/etc/systemd/system`, `/usr/lib/systemd/system`, `/lib/systemd/system`, or
  `/etc/systemd/system/multi-user.target.wants`;
- any of those named units whose systemd `LoadState` is not `not-found`, even
  when no unit file is visible at the known paths.

An unavailable or malformed systemd query is `unknown` and blocks execution.
The refusal runs before state-root allocation and is repeated immediately
before `planned -> applying`. If production evidence appears after a partial
Stage 05 apply, both forward progress and automated rollback are forbidden;
the safe recovery is operator investigation followed by the already-required
OVH reinstall. No lab operation may remove production evidence.

The existing Ubuntu 26.04/x86-64/systemd/cgroup-v2 gate and exact operation,
target, SSH-config, and bundle-bound approval remain mandatory.

The verified `known_hosts` input must receive the same approval integrity as
the SSH configuration before any mutation becomes executable. The runner must
open it with `O_NOFOLLOW`, verify a bounded regular file, owner and nonwritable
group/other mode before and after open, read a stable snapshot, bind its SHA-256
into the approval and summary, and use one private `0600` snapshot for every
SSH phase. Reopening an operator pathname independently for `before`, execute,
after and postconditions is not acceptable because the approved target key
could change between phases.

The runner/common argument contract must remain explicit and covered by an
executed streamed-bundle test: the local runner validates the full
colon-delimited approval, while the remote bundle receives the fixed base
acknowledgement and separately verifies the exact bundle digest. Stage 05 must
not weaken either side or mistake the base acknowledgement for reusable
authorization of another target, operation, stage, SSH configuration or
bundle.

## State machine and operation order

### Initial admissible states

Only these initial states are admissible:

1. **Pristine:** state root, journal root, config directory, experiments
   directory, workspace, marker and journal are absent.
2. **Bootstrap residue:** `/var/lib/kitdev-sandboxes` is an empty root-owned
   `0755` directory, or contains only the exact root-owned `0700` journal child
   and canonical same-plan journal temporary/final residue accepted by
   `JournalStore`.
3. **Resumable transaction:** the exact journal exists and the resources match
   one recognized prefix state for its current transition.

If `/etc/kitdev-sandboxes`, either workspace ancestor, or the marker exists
without the exact journal relationship, the stage refuses to adopt it. If a
journal exists with a different plan, bundle hash, resource list, canonical
encoding, owner, mode, or state, the stage refuses. It never starts a second
install ID to bypass the conflict.

### Execute

The successful first-apply sequence is fixed:

1. Run approval, platform and production-refusal checks.
2. Build the expected canonical marker, resources, plan hash and planned journal
   record in memory from the approved bundle digest.
3. Allocate or structurally validate the state and journal roots and acquire
   the journal root's nonblocking exclusive lock.
4. Under that lock, compare any journal temp/final residue with the expected
   record, perform only the allowed same-plan cleanup, and collect exact initial
   resource states through safe descriptors.
5. When absent, exclusively create and `fsync` the `planned` journal; when
   present, exactly load the recognized retry state. The first durable planned
   record is the write-ahead point; no managed resource exists before it.
6. Re-run production refusal and re-observe that all managed resources still
   equal their journaled prior state.
7. Durably transition the journal to `applying`.
8. Create and `fsync` `/etc/kitdev-sandboxes`.
9. Create and `fsync` `/var/lib/kitdev-sandboxes/experiments`.
10. Create and `fsync` the empty workspace.
11. Publish and `fsync` the marker last. Until this step completes, later
    mutation stages have no authorization evidence.
12. Reconcile every resource against desired state and transition to `applied`.
13. Reopen all paths and the journal from `/`, repeat production refusal,
    verify marker/plan reconstruction and empty workspace, then transition to
    `validated`.
14. Reopen once more and emit bounded postcondition evidence.

A second `execute` against a `validated` exact journal is an idempotent
validation: it performs no write and succeeds only if all Stage 05-owned
objects still match. Initial entry-emptiness is not reasserted after a
downstream validated journal owns content. The generic `JournalStore.resume()`
rejects completed records, so the reconciler must explicitly load and validate
this state instead of trying an illegal transition.

### Rollback

Rollback is independently approved and may start from `planned`, `applying`,
`applied`, `validated`, or `failed`. A `planned` journal first takes its legal
`planned -> failed` transition because no managed resource should exist. The
fixed sequence is:

1. Load and exactly match the journal and plan; reconcile current resources.
2. Before changing the journal or marker, preflight that each possibly absent
   Stage 05 directory matches its recognized transaction prefix and that every
   present workspace, experiments or config directory contains only the exact
   Stage 05 entries and is otherwise empty. Any downstream or foreign resource
   blocks rollback without revoking authorization; downstream stages must roll
   back in reverse order first.
3. Transition to `rolling_back` where the journal state permits it.
4. If the exact marker is present, remove it first and `fsync` its parent. This
   immediately revokes authorization for later mutations.
5. Remove each present empty exact workspace and `experiments` directory in
   that order, with parent `fsync`s.
6. Remove the exact config directory if present and empty.
7. Verify all journaled resources equal prior state and transition to
   `rolled_back`.

The state root, journal root and journal remain root-owned and durable after
rollback. The defined rollback state is therefore "managed resources restored
to absent; retained transaction provenance present," not a byte-for-byte
pristine host. This residue cannot authorize later mutation because the marker
is absent and the journal is `rolled_back`. The authoritative removal of
retained lab provenance is the already-required OVH OS reinstall.

A second rollback against an exact `rolled_back` journal performs no write and
succeeds after validation. Rollback refuses to remove a nonempty directory,
foreign file, changed marker, mountpoint or hard-linked file. It also refuses
an in-session descriptor identity change; after process restart it applies the
durable ownership proof above rather than claiming a persisted inode identity.

## Crash, failure, and retry contract

| Interruption point | Durable observable state | Required retry behavior |
| --- | --- | --- |
| Before journal-root creation | Pristine | Start normally |
| After either bootstrap `mkdirat`, before journal publish | Exact empty state-root or journal-root prefix | Adopt exact residue and create journal, or leave it untouched |
| During journal create/replace | No record, a complete record, or exact canonical temp residue | Use `JournalStore` recovery; foreign/partial residue blocks |
| `planned` | No managed resources | Execute may revalidate and enter `applying`; rollback records failure/rollback without deleting foreign state |
| `applying`, before marker | Exact prefix of three directory creations | Resume only missing suffix after full reconciliation, or rollback exact prefix |
| During marker publication | Marker absent or complete due exclusive atomic publication | Reconcile, then finish forward or rollback |
| `applying`, marker complete | All desired resources, journal transition pending | Reconcile, record `applied`, validate |
| `applied` | All desired resources | Revalidate then record `validated`, or rollback |
| `validated` | Exact authorized state | Repeated execute is read-only success; drift blocks and revokes effective authorization |
| `failed` | One recognized stable resource prefix | Forward resume forbidden; exact rollback or reinstall only |
| `rolling_back` | Marker may still be exact, or resources form an exact reverse prefix | Remove the marker first if present, then continue the fixed reverse order |
| `rolled_back` | Managed resources absent; journal retained | Repeated rollback is read-only success; execute requires reinstall, not journal reuse |

The reconciler must recognize only prefixes of the fixed operation order, never
arbitrary subsets. On a caught error it reloads the journal because an `fsync`
or replace error can occur after publication. It records `failed` only through
a legal transition and only after obtaining stable reconciliation evidence.
It must not label an unknown state failed merely to make rollback available.

The local runner's separate `before`, `execute`, `after`, and postcondition SSH
calls are evidence orchestration, not transaction boundaries. A timeout or
lost SSH session can occur after any durable write. Recovery is entirely
determined by the host-local journal and exact resource state on the next
approved invocation.

## Later-stage authorization proof

The Stage 05 marker alone never authorizes a command. Every later mutable stage
must satisfy all of these gates, in this order, before its own first mutation:

1. Its manifest entry is reviewed as `kind=mutation,status=executable`, and the
   runner explicitly permits that exact stage.
2. The operator supplies that stage's target/operation/SSH-config/bundle-bound
   approval, also bound to the verified `known_hosts` snapshot digest. A Stage
   05 approval cannot be reused.
3. Platform and descriptor-based production-refusal gates pass.
4. A shared `lab_require_validated_stage05` helper safely opens the fixed
   marker and journal, validates types/owners/modes/link counts and directory
   identities, parses their exact canonical schemas, reconstructs the Stage 05
   plan from the recorded bundle digest, and matches the journal's install ID,
   plan ID, plan hash, resource list and terminal `validated` state.
5. The helper reconciles the current Stage 05-owned directory metadata and
   exact marker. It does not require the workspace to remain empty after Stage
   05 validation. Any downstream content used by the requesting stage must be
   absent or proven by that content's own exact validated journal; unknown
   content blocks. Drift in a Stage 05-owned object, a missing marker, rollback
   state or uncertainty also blocks.
6. The later stage creates its own write-ahead journal and validates its own
   prior state. Stage 05 provenance is a prerequisite, not a substitute for a
   per-stage transaction.

The helper returns a typed pass/fail result and the safe Stage 05 plan and
bundle hashes for evidence. It must not export marker content through the
shell, trust `test -f`, or treat a journal filename as proof.

Read-only and plan-only stages may report whether valid authorization exists,
but Stage 05 absence must not prevent genuinely read-only discovery. Stage 90
acceptance must require Stage 05 validation plus the validated journals and
postconditions for every actually executed mutation stage.

## Evidence contract

Stage output remains normalized, bounded and secret-free. It may contain:

- stage and requested operation;
- platform and production-refusal result;
- journal-root classification: `absent`, `bootstrap-residue`, or `exact`;
- journal state and transition count;
- abbreviated or full lowercase plan, bundle and marker SHA-256 values;
- per-resource state: `absent`, `exact`, `prefix`, `drift`, or `unknown`;
- fixed numeric UID/GID/mode expectations and pass/fail results;
- whether the workspace is empty and whether unexpected entries exist;
- operation result, retained-provenance status and next allowed action.

It must not print directory contents, raw metadata containing host identifiers,
timestamps, device/inode numbers, mount sources, environment, endpoint, SSH
alias/config, systemd unit content, journal/marker JSON, or exception strings
containing paths. Detailed local diagnostics map to fixed reason codes.

The off-host runner summary binds stage, operation, manifest status, kind,
bundle hash, SSH-config hash and `known_hosts` snapshot hash. It must
additionally record the Stage 05 plan hash returned by a successful
execute/rollback so later evidence can be correlated without exposing private
inventory.

Required reason codes include at least:

- `production_state_present`, `production_state_unknown`;
- `transaction_busy`;
- `journal_root_conflict`, `journal_conflict`, `journal_corrupt`;
- `unsafe_ancestry`, `symlink_or_type_conflict`, `mount_boundary`;
- `resource_prior_mismatch`, `resource_desired_mismatch`;
- `unexpected_entry`, `marker_content_mismatch`;
- `illegal_recovery_state`, `rollback_foreign_state`.

## Implementation and review status

The working implementation supplies the typed `JournalStore` reconciler, one
operation-wide lock, descriptor-relative allocation and mutation, exact
canonical encoders, embedded reviewed Python sources, approval-bound bundle
construction, and a manifest gate that enables only Stage 05 mutation. The
hermetic suite covers canonical bytes, pristine and idempotent apply/rollback,
every injected forward and rollback crash point, exact provisional and linked
publication residue, symlink/mount/ownership/mode/ACL/xattr/capability
conflicts, plan mismatch, unexpected content, process-lock contention, and
abrupt lock-holder exit. Read-only modes do not allocate or repair.

Independent code and safety review initially found seven recovery defects and
one terminal-residue ambiguity during re-review. All were corrected and the
same reviewer approved the local repository gate after 99 focused and 225 full
tests passed. Remote qualification still requires a separately approved
disposable-host apply/apply/rollback/rollback exercise with off-host evidence.
Local approval is not remote authorization and does not remove the final clean
reinstall qualification.

No test may use `/etc/kitdev-sandboxes` or `/var/lib/kitdev-sandboxes` on the
development PC. Hermetic tests use an explicitly injected temporary trusted
prefix; only the disposable-host integration test uses fixed production paths.

## Non-goals

- Stage 05 does not make the experimental harness production automation.
- It does not permit later stages that remain blocked in `stages.json`.
- It does not claim ownership of pre-existing similarly named resources.
- It does not provide general recursive deletion or purge.
- It does not remove its journal on rollback.
- It does not replace the final reinstall-and-clean-automation acceptance gate.

## Repository evidence reviewed

- `src/kitdev_sandboxes/journal.py`
- `src/kitdev_sandboxes/stage05.py`
- `tests/unit/test_journal.py`
- `tests/unit/test_stage05.py`
- `experiments/ovh-lab/lib/common.sh`
- `experiments/ovh-lab/run-stage.sh`
- `experiments/ovh-lab/stages.json`
- `experiments/ovh-lab/stages/05-lab-marker-workspace.sh`
- `tests/unit/test_ovh_lab_framework.py`
- `docs/adr/0002-project-owned-state-layout.md`
- `docs/research/ovh-disposable-lab-framework.md`

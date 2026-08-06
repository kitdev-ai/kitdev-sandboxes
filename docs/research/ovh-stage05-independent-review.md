# OVH Stage 05 independent code and safety review

Date: 2026-08-06
Reviewer: independent LUNA subagent `stage05_safety_review`
Verdict: **superseded by the dated re-review below; approved for the local
qualification gate, with remote execution still separately gated**

## Scope and method

This review compared the complete uncommitted Stage 05 change with
`docs/research/ovh-stage05-marker-contract.md`, including the approved
provisional-directory amendment. It inspected the full reconciler, the existing
`JournalStore` session extension, shell runner and embedded-source loader,
manifest gate, framework tests, Stage 05 tests, and related documentation.

The review was entirely local. It did not use SSH or any network operation, did
not contact or mutate the OVH host, and did not edit implementation, tests, or
other documentation.

## Findings

### High: retry can validate without completing an interrupted directory durability barrier

`SecureTree.create_directory()` performs the required directory and parent
`fsync`s only on the original create path
(`src/kitdev_sandboxes/stage05.py:435-464`). A crash after `mkdirat` or `fchmod`
but before either `fsync` leaves an exact-looking prefix. On retry,
`_prefix_length()` only validates that prefix (`stage05.py:1006-1039`), and the
execute loop creates only the missing suffix (`stage05.py:1169-1192`). It can
therefore publish `applied` and `validated` without reissuing the durability
barrier missed by the interrupted operation.

The same problem exists in bootstrap allocation. `_allocate_roots()` accepts an
already final-mode state or journal root without syncing that directory and its
parent (`stage05.py:950-985`). For example, after interruption immediately after
creating the final-mode workspace, retry never `fsync`s the workspace or its
experiments parent; after interruption immediately after creating the journal
root, retry writes and syncs inside the journal root but never completes the
missed state-root parent sync.

The crash tests exercise these process-interruption points but assert only the
eventual namespace and journal state (`tests/unit/test_stage05.py:424-480`). They
do not assert that retry reissues every incomplete durability barrier. A later
power loss can consequently leave a durable `validated` journal without all
directory entries it claims. Recovery must durably adopt each recognized exact
prefix by syncing the descriptor and parent before advancing the journal, with
fault tests that verify those retry barriers.

### High: rollback cannot recover legal marker-publication crash residues

Execute cleans canonical marker temp/link residue whenever the journal is
`applying` (`stage05.py:1141-1147`), but rollback does so only after the journal
has already reached `rolling_back` (`stage05.py:1211-1217`). A direct rollback
from the contractually allowed `applying` state calls strict prefix validation
with no marker-residue allowance, so the canonical temp is treated as foreign
state before rollback can transition.

Local reproductions interrupted execute at `after_write_marker`,
`after_file_fsync_marker`, `after_link_marker`, and
`after_publish_parent_fsync_marker`. Every subsequent direct rollback failed
with `rollback_foreign_state`. This contradicts the rollback start-state rule
and the marker-publication row of the crash table. Rollback must validate and
durably discard the exact same-plan residue before its foreign-state preflight,
while continuing to preserve suspicious residue.

### High: read-only observation can report an exact validated journal beside unvalidated residue

`_validate_existing_roots()` permits names matching the journal temp pattern,
but `_validate_journal_entries()` checks only file metadata, not canonical
content or its relationship to the published journal (`stage05.py:908-948`).
When the published journal has link count one, `JournalStore._load_inflight_at()`
does not enumerate or validate any temp (`src/kitdev_sandboxes/journal.py:532-538`).
`observe()` can therefore return `journal_root=exact`,
`journal_state=validated`, and `status=pass` while an arbitrary root-owned
`0600` file exists under a syntactically canonical journal temp name
(`stage05.py:1328-1347`).

This was reproduced locally by adding an invalid non-JSON canonical temp beside
a validated journal: `observe("after")` succeeded as validated and left the
temp untouched. A later execute correctly blocked in `JournalStore` cleanup,
which demonstrates that read-only evidence and mutation disagree about whether
the state is canonical. Observation must validate every allowed residue under
the shared lock and classify legal transition residue as in-flight; unexplained
or noncanonical residue must fail closed.

### Medium: observation bypasses the shared operation lock when no final journal exists

Once the journal root exists, `observe()` validates and returns the no-final
journal cases before opening a `JournalStore` shared session
(`stage05.py:1308-1328`). Holding an actual exclusive journal-root lock over an
empty root and calling `observe("after")` was reproduced to return a passing
`bootstrap-residue/absent` result instead of `transaction_busy`.

This violates the operation-lock contract and permits a race with journal create
or transition. Acquire the nonblocking shared session immediately after safely
establishing that the journal root exists, then enumerate, load, and classify all
journal states through that locked descriptor. The current lock test starts
from an already published journal (`tests/unit/test_stage05.py:398-422`) and
therefore misses this branch.

### Medium: marker-temp recovery accepts an unexplained second hard link

`_recover_marker_temp()` accepts temp link counts `{1, 2}` before it knows
whether the marker exists (`stage05.py:666-684`). If the marker is absent, it
does not require link count one. A local reproduction linked a canonical temp to
an outside name, leaving the marker absent and temp link count two; retry
unlinked the temp, published a new marker, reached `validated`, and left the
outside hard link behind.

The legal absent-marker prefix has exactly one temp link. Link count two is legal
only when the other name is the exact marker and both names resolve to the same
inode. Mutation recovery should apply the same branch-specific checks already
used by read-only `validate_marker_residue()` (`stage05.py:695-726`) and should
also compare the temp mount ID with its parent.

### Medium: journal transitions are not revalidated against the Stage 05 xattr policy before resource mutation

`_transition()` trusts the record returned by `JournalStore` and does not call
the Stage 05 journal metadata/xattr/mount validator (`stage05.py:1103-1112`).
After `planned -> applying`, resource creation begins immediately
(`stage05.py:1155-1182`). `JournalStore` validates type, owner, group, mode, link
count, stable bytes, and ancestry, but it does not enforce Stage 05's exact-empty
xattr set or compare the file mount ID (`journal.py:437-492`).

Consequently a policy-assigned xattr/capability on the replacement journal can
coexist with subsequent managed-resource mutation, even though an identical
xattr on an already existing journal would be rejected. Call
`_validate_journal_metadata()` after every transition, and add a stateful test
that makes the post-transition journal acquire a forbidden xattr before the
first resource mutation.

### Low: provisional-directory recovery does not retain the original descriptor identity through final revalidation

Recovery records the provisional descriptor identity and compares it with the
published name before `fchmod` (`stage05.py:504-519`), then calls
`validate_directory()` through a newly opened descriptor. It never requires
that final descriptor's `(st_dev, st_ino)` equal the original provisional
descriptor. The approved amendment explicitly requires the same identity
through validation, mutation, and revalidation. Preserve the initial identity
and assert it after `fchmod`/`fsync` as well as against the published name. This
race requires another privileged actor under the current modes, but it is still
an exact amendment-contract mismatch.

## Passing controls

The review found no defect in these reviewed areas:

- canonical marker, plan, and journal fixture bytes and fixed resource IDs;
- exact production host paths and production UID/GID/modes in the executable
  entrypoint;
- descriptor-relative no-follow traversal and fixed-path construction;
- two production-refusal calls before first managed mutation, fail-closed
  systemd classification, and rollback refusal after production evidence;
- current-bundle exact plan matching for both forward and rollback recovery;
- use of the existing `JournalStore` locked-session foundation rather than a
  second journal format/state machine;
- deterministic anonymous-pipe delivery of hash-checked embedded Python, with
  no remote source file or source-bearing argv/environment variable;
- target/config/known-hosts/bundle-bound approval and one immutable streamed
  bundle across runner phases;
- manifest gating: Stage 05 is the only executable mutation;
- bounded fixed-field reconciler evidence and fixed public reason codes;
- runner behavior after a simulated SSH operation interruption.

These passing controls do not offset the blocking recovery and evidence
findings above.

## Verification performed

All commands ran from `/Users/kit/projects/kitdev/sandboxes` with no SSH or
network access.

1. `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m unittest tests.unit.test_journal tests.unit.test_stage05 tests.unit.test_ovh_lab_framework -v`
   - Result: **84 tests passed**.
2. `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m unittest discover -s tests/unit -v`
   - Result: **210 tests passed**.
3. `bash -n experiments/ovh-lab/run-stage.sh experiments/ovh-lab/lib/common.sh experiments/ovh-lab/stages/*.sh`
   - Result: passed.
4. `git diff --check`
   - Result: passed.
5. Local `ast.parse` of the changed Python modules/tests and `json.loads` of
   `experiments/ovh-lab/stages.json` with bytecode disabled.
   - Result: passed.
6. Search for `__pycache__`, `*.pyc`, and `*.pyo` beneath `src`, `tests`,
   `experiments`, and `docs`.
   - Result: no artifacts found.
7. Four hermetic adversarial scripts using only temporary injected roots:
   - direct rollback after four marker publication crash points: all four
     reproduced `rollback_foreign_state`;
   - observation while an empty journal root was exclusively locked: reproduced
     a passing `bootstrap-residue/absent` result;
   - invalid canonical journal temp beside validated final journal: reproduced
     passing validated observation;
   - absent marker plus canonical temp with an unexplained second hard link:
     reproduced successful validation with the outside hard link retained.

`shellcheck` was not available in the local environment. Shell syntax and the
executed streamed-bundle tests passed, but a future review should also run the
repository's pinned shell linter when available.

## Original review gate (superseded)

Do not enable or remotely execute Stage 05 from this revision. Correct the high
and medium findings, add regression tests for each reproduced case and retry
durability barrier, rerun the focused and full suites, and request a fresh
independent LUNA review before generating a mutation approval.

This instruction records the original review state. The re-review below
supersedes it for the corrected local candidate.

## Re-review - 2026-08-06

Current verdict: **approved for the local repository qualification gate**.
There are no remaining blocking findings in the reviewed Stage 05 candidate.
This is not approval to mutate the OVH server: remote execution and the
Ubuntu 26.04 host qualification remain separately gated.

The re-review was again entirely local. It did not use SSH, make network calls,
contact the OVH host, or mutate any server. The reviewer changed only this
review document; implementation and test corrections were made by the owning
agents.

### Closure of original findings

1. **Retry durability barriers: closed.** `SecureTree.sync_directory()` and
   `sync_marker()` revalidate the descriptor, parent, identity, mode, mount,
   and security metadata while reissuing the required `fsync` barriers
   (`stage05.py:497-574`). `_sync_bootstrap_roots()` and `_sync_prefix()` apply
   those barriers to recovered bootstrap roots and every accepted prefix
   (`stage05.py:1094-1106`). Crash replay evidence passed at all six injected
   directory/barrier points, including a second crash during recovery.
2. **Direct rollback after marker publication interruption: closed.** Marker
   residue recovery validates the temp/final inode relationship and link count,
   repairs only recognized publication states, and preserves foreign states
   (`stage05.py:766-850`). Direct rollback passed at all four marker publication
   crash points. Direct rollback from the provisional config-directory state
   also passed.
3. **Observation beside journal residue: closed.** Observation validates the
   final journal together with canonical residue under the journal lock.
   `JournalSession.inspect_residue()`, `load_unpublished()`, and
   `load_inflight()` expose the store's descriptor-relative validation
   (`journal.py:873-912`), while Stage 05 rejects and preserves unexplained
   residue. A foreign temp beside a validated final journal was blocked and
   preserved.
4. **Observation without a final journal record: closed.** Once the journal
   root exists, observation acquires the shared nonblocking journal lock before
   classifying residue (`stage05.py:1505-1595`). An exclusive lock over an
   empty journal root now returns `transaction_busy` instead of a passing
   absent result.
5. **Absent marker plus two-link temp: closed.** Two links are accepted only
   when the final marker is the second link to the same exact inode; an outside
   hard link is rejected and preserved (`stage05.py:766-850`).
6. **Post-transition journal metadata: closed.** `_transition()` validates the
   new journal inode's complete metadata immediately after publication and
   before resource mutation (`stage05.py:1311-1321`). Injected xattr and mount
   identity changes were both blocked before config mutation.
7. **Provisional directory identity continuity: closed.** Recovery retains the
   initial descriptor identity through chmod, sync, and final validation
   (`stage05.py:576-624`). An injected dev/inode replacement was blocked.

### Additional re-review finding and correction

The first corrected candidate still allowed `execute` to silently replace an
exact `PLANNED` temp beside a terminal final journal and allowed rollback to
ignore related residue. This was a new blocking journal-state ambiguity found
during re-review.

The final candidate closes it in `_prepare_existing_record()`
(`stage05.py:1257-1309`). It inspects final/link/temp residue without mutation,
requires exact plan and metadata, accepts only the explicit legal outbound
transition for the existing final state, and cleans a temp only after that
validation. A two-link residue is accepted only for the initial `PLANNED`
hard-link publication state. Impossible residue beside `VALIDATED` or
`ROLLED_BACK` is blocked and preserved. Three legal cross-operation residues
(`PLANNED` to `APPLYING`, `APPLYING` to `APPLIED`, and `APPLIED` to
`VALIDATED`) were independently recovered by rollback to `ROLLED_BACK`.

### Re-review verification

All commands ran from `/Users/kit/projects/kitdev/sandboxes` with bytecode
generation disabled where Python executed.

1. `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m unittest tests.unit.test_journal tests.unit.test_stage05 tests.unit.test_ovh_lab_framework -v`
   - Result: **99 tests passed** in 17.031 seconds.
2. `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m unittest discover -s tests/unit -v`
   - Result: **225 tests passed** in 19.139 seconds.
3. `bash -n experiments/ovh-lab/run-stage.sh experiments/ovh-lab/lib/common.sh experiments/ovh-lab/stages/*.sh`
   - Result: passed.
4. Python AST parsing of the changed Python modules and tests, plus JSON parsing
   of `experiments/ovh-lab/stages.json`.
   - Result: passed.
5. `git diff --check`
   - Result: passed.
6. Search for `__pycache__`, `*.pyc`, and `*.pyo` beneath `src`, `tests`,
   `experiments`, and `docs`.
   - Result: no artifacts found.
7. Independent hermetic adversarial cases using only temporary injected roots:
   - marker-residue direct rollback: **4/4 passed**;
   - provisional-config direct rollback: passed;
   - invalid journal temp during observation: blocked and preserved;
   - empty journal root under an exclusive lock: blocked;
   - absent marker with an outside two-link temp: blocked and preserved;
   - post-transition journal xattr and mount substitution: both blocked before
     resource mutation;
   - durability replay followed by a second recovery crash: **6/6 passed**;
   - provisional directory dev/inode substitution: blocked;
   - impossible temp beside each of `VALIDATED` and `ROLLED_BACK`: **2/2
     blocked and preserved**;
   - legal cross-operation journal transition residue: **3/3 recovered to
     `ROLLED_BACK`**, with residue removed.

`shellcheck` remains unavailable locally. Shell syntax and streamed-bundle unit
coverage passed, but the repository's pinned shell lint should run in the
remote qualification environment.

### Residual risks and next gate

- Local macOS tests model Linux mount identity, ownership, xattrs, ACLs, and
  capabilities; they do not replace qualification on the target Ubuntu 26.04
  kernel and filesystems.
- The durability ordering is verified by fault hooks and replay, not by an
  actual power-loss test against the target NVMe/HDD stack.
- The embedded loader has local interpreter-substitution coverage, but the
  bundle has not yet run under the target host's `/usr/bin/python3`.
- A hostile process already holding root is outside this stage's threat model.

The next permitted step is the separately approved, read-only Ubuntu 26.04
qualification followed by an explicit mutation approval. This review does not
authorize either action by itself.

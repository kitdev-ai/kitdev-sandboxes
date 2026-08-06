# OVH Ubuntu 26.04 identity and recovery access plan

Date: 2026-08-06

Status: rejected for implementation; non-mutating review artifact requiring revision

## Review gate

The independent implementation review rejected this revision. It remains useful
as research, but it is not an apply plan and authorizes no host change. A revised
plan must, at minimum:

- create and fsync an exact write-ahead phase journal before the first mutation,
  with per-resource transitions and strict resume/rollback rules;
- move bootstrap into its own typed, reviewed action set with exact paths,
  modes, hashes, convergence, and rollback instead of treating it as an
  exception to the identity plan;
- plan exact deterministic UID/GID values, hash them, pass them explicitly, and
  recheck name and numeric collisions immediately before every action;
- obtain the expected SSH fingerprint through an authenticated independent
  channel such as OVH IPMI, not an assumed control-panel fingerprint field; and
- define pinned Ansible group semantics, no-follow path invariants, bounded
  rollback ownership scans, absolute packaged command paths, and the optional
  observer condition precisely.

The repository's current `identity-access` phase is dry-run only and keeps all
actions suppressed while these prerequisites remain unresolved.

## Decision

Prepare identities through repository-owned, idempotent Ansible automation only.
Do not hand-build the server over SSH. Manual SSH is limited to read-only
discovery, recovery-path verification, and invoking a reviewed repository
command. No command in this report is authorization to mutate the host.

The first identity phase should:

- preserve the human operator's SSH key access and `sudo` membership;
- remove the operator from `lxd` only after a bounded preflight proves that LXD
  is unused and the change has an exact rollback record;
- create fixed, non-login service identities for the API, proxy, worker, and
  optional observer;
- give only the worker the existing `kvm` supplementary group;
- give no service identity `sudo`, `lxd`, `docker`, `libvirt`, `adm`,
  `systemd-journal`, or other administrative group membership; and
- grant no TUN ownership, Linux capability, NBD, mount, network-namespace, or
  firewall authority in this phase.

The phase has no package, storage, network, firewall, kernel, SSH configuration,
service deployment, or project workload changes. It must not start a microVM.

## Observed state

The tracked evidence is redacted. Exact infrastructure and account identifiers
remain in the ignored operator inventory.

| Observation | Confidence | Consequence |
| --- | --- | --- |
| The host is bare-metal Ubuntu Server 26.04 LTS, x86-64, systemd, and cgroups v2. | Observed | Eligible platform for a production-target identity plan. |
| The SSH operator belongs to `sudo` and `lxd`, but not `kvm`. | Observed | The operator can administer through `sudo`; LXD socket access is a second root-equivalent path; direct KVM access is absent. |
| Non-interactive passwordless `sudo` succeeds for the operator. | Observed | Automation can acquire privilege without changing sudoers. This must be revalidated immediately before apply. |
| `/dev/kvm` is a character device, mode `0660`, owned by `root:kvm`. | Observed | Group access is already expressed narrowly; do not change the node mode or owner. |
| Root passes read/write permission tests for `/dev/kvm`. | Observed | The host/device permission boundary is usable by root. This was not yet a Firecracker or KVM ioctl proof. |
| The operator fails read/write permission tests for `/dev/kvm`. | Observed | This is expected because the operator is not in `kvm`; the operator does not need KVM access. |
| `/dev/net/tun` is a character device, mode `0666`, owned by `root:root`. | Observed | Opening the node is not equivalent to permission to create or attach interfaces. Do not create a `tun` group or change its mode. |
| The first-seen SSH host key is not yet verified against the provider control plane. | Observed blocker | No persistent change may run until exact out-of-band verification succeeds. |
| LXD use, local instances, storage pools, networks, and socket activity have not been qualified. | Unknown blocker for removal | Do not remove `lxd` membership until the dedicated read-only LXD discovery gate passes. |

## Evidence and security interpretation

Linux KVM begins with opening `/dev/kvm`; the resulting descriptor is used to
create VM and vCPU descriptors. Access to that node is therefore a real worker
privilege, but it does not imply general host administration. Firecracker's own
getting-started guidance notes that distributions commonly use the `kvm` group
or ACLs for the node. The delivered Ubuntu group boundary is preferable to a
world-writable mode or a per-login ACL.

The kernel TUN/TAP documentation explains why mode `0666` on `/dev/net/tun` is
not by itself a network-administration grant: `CAP_NET_ADMIN` is required to
create network devices or attach to devices the caller does not own. Therefore
there is no useful or justified `tun` group to add in this phase. The worker's
future network authority must be derived from a trace of the pinned E2B and
Firecracker workflow. It may ultimately require a narrow root-owned helper,
systemd capability assignment, or another design; current upstream evidence is
insufficient to select one safely.

Ubuntu's LXD documentation states that local Unix-socket access gives full LXD
control, including host device and filesystem attachment, and should be given
only to users trusted with root. The operator is already a deliberate `sudo`
administrator, so retaining `lxd` does not create the first route to root, but
it creates an unnecessary second daemon/socket attack surface. This project does
not use LXD. The desired state is removal after absence/non-use is proven. If
LXD is active, has resources, or cannot be inventoried safely, stop and leave
membership unchanged pending a separate LXD disposition plan.

Docker's documentation similarly says the `docker` group grants root-level
privileges. No human or kitdev service identity receives that group. Future
Compose lifecycle must be mediated by the privileged, audited installer or a
narrow purpose-built boundary rather than unrestricted socket membership.

Libvirt's official daemon documentation says a read-write connection to many
system-mode daemon sockets commonly implies root-equivalent privilege. This
project launches Firecracker directly rather than through libvirt, so neither
the operator nor a service identity should be added to a `libvirt` socket group.
The group is not a substitute for narrow access to `/dev/kvm`.

## Desired identity model

Names are stable resource identities; numeric UID/GID values are allocated from
the host's system range after collision checks and recorded in the installation
manifest. Fixed numeric IDs are not portable and must not be guessed.

| Identity | Login | Primary group | Supplementary groups | Intended access |
| --- | --- | --- | --- | --- |
| Explicit configured operator | Existing key-only account retained | Existing | Existing set minus `lxd`; retain `sudo` | Human administration and invocation of reviewed automation |
| `kitdev-e2b` | Locked, `/usr/sbin/nologin`, no home | `kitdev-e2b` | None | Future API control plane only |
| `kitdev-proxy` | Locked, `/usr/sbin/nologin`, no home | `kitdev-proxy` | None | Future authenticated sandbox proxy only |
| `kitdev-worker` | Locked, `/usr/sbin/nologin`, no home | `kitdev-worker` | `kvm` only | Future Firecracker worker; KVM node only in this phase |
| `kitdev-observe` | Locked, `/usr/sbin/nologin`, no home | `kitdev-observe` | None | Optional project-provided read-only metrics path; no journal-wide group |

No account receives an authorized-keys file, password, shell, home directory,
subordinate UID/GID range, Docker/LXD socket access, or blanket ownership of a
project top-level directory in this phase. Later directory roles must grant
each service only its own required subpaths.

The future worker systemd unit should combine `User=kitdev-worker` with an
explicit primary group and tested hardening. Candidate controls include
`DevicePolicy=closed`, `DeviceAllow=/dev/kvm rw`, `NoNewPrivileges=yes`, a
capability bounding set, filesystem protection, private temporary space, and
writable-path allowlists. These are not applied here: systemd documents that
device policy and supplementary groups affect the whole service execution
boundary, while the architecture requires compatibility to be demonstrated
before hardening flags are enabled. In particular, do not pre-grant
`CAP_NET_ADMIN` or infer a final unit from this identity plan.

## Reproducible automation boundary

### Repository is authoritative

The project architecture assigns accounts and host state to Ansible. The
preflight contract says dry-run combines typed observations with Ansible check
and diff output, and the dependency policy requires an exact, hash-complete
Ansible environment. The identity phase therefore cannot run until the missing
Milestone 1 bootstrap/apply foundation exists and passes review.

Proposed repository-owned files are:

```text
install.sh
requirements.in
requirements.lock
ansible/site.yaml
ansible/roles/host_identities/defaults/main.yaml
ansible/roles/host_identities/tasks/preflight.yaml
ansible/roles/host_identities/tasks/main.yaml
ansible/roles/host_identities/tasks/validate.yaml
ansible/roles/host_identities/tasks/rollback.yaml
src/kitdev_sandboxes/manifest.py
tests/unit/test_identity_plan.py
tests/integration/test_host_identities.py
```

The exact filenames may be adjusted during implementation to existing role
layout, but there must be one owned role, one typed plan/manifest path, and no
parallel shell implementation.

### Minimal bootstrap

The bootstrap is the only unavoidable pre-Ansible boundary. It must itself be
repository code, use `set -Eeuo pipefail`, validate Ubuntu release and
architecture without mutation, verify its own repository/release identity,
stage pinned wheels with reviewed hashes, and create a versioned virtual
environment only under `/opt/kitdev-sandboxes`. Installation must use
`pip --require-hashes --no-deps`; no downloaded shell script or floating package
selection is allowed. Bootstrap state and exact package hashes enter the
manifest.

Bootstrap dry-run must work before creating the virtual environment. It reports
the proposed directories, files, packages, and environment hash. If a
system-provided Python or package prerequisite is missing, it reports a blocked
plan; it does not run `apt` as an implicit convenience step. Any package
bootstrap is a separately reviewed package phase.

This plan proposes the following eventual CLI contract, to be implemented and
tested before use:

```bash
./kitdev install --phase identity-access --dry-run --format json
sudo ./kitdev install --phase identity-access
sudo ./kitdev uninstall --phase identity-access
```

These commands do not work in the current foundation and must not be attempted
until their implementation is reviewed. Direct `ansible-playbook`, `useradd`,
`usermod`, `gpasswd`, file-copy, or editor commands over SSH are not the normal
execution path.

### Exact Ansible resources

The role must use fully-qualified built-in modules and fixed values:

- `ansible.builtin.group` creates each matching primary system group only when
  absent and blocks on any foreign collision;
- `ansible.builtin.user` creates each system account with its matching primary
  group, `system: true`, `create_home: false`, `home: /nonexistent`,
  `shell: /usr/sbin/nologin`, and `password_lock: true`;
- only `kitdev-worker` has desired supplementary groups `[kvm]`;
- an explicitly guarded `ansible.builtin.command` invokes
  `/usr/bin/gpasswd --delete <configured-operator> lxd` only when preflight
  proved current membership, LXD absence/non-use, and exact operator identity;
- check mode predicts every action without running the guarded command;
- validation uses `getent`, `id`, account status, file/device stat, and a
  service-context KVM open probe without starting a VM; and
- handlers are absent: account/group work does not restart SSH, LXD, systemd,
  networking, or the host.

The operator name is an explicit validated configuration value. It must not be
inferred from `SUDO_USER`, `$USER`, a home path, UID 1000, or the SSH process.
The role rejects root, a missing account, a non-`sudo` account, or an account
without the already-authorized SSH key path.

## Machine-readable change table

The implementation should emit equivalent records in stable JSON. `condition`
is mandatory; a false or unknown condition produces `blocked` or `no_change`,
never an optimistic mutation.

| id | type | target | condition | desired | privilege | impact | rollback |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `identity.group.api` | `account_group` | `kitdev-e2b` group | name absent or exact manifest ownership | system group present | root | none | delete only if phase-created, unused, and manifest-owned |
| `identity.user.api` | `account` | `kitdev-e2b` | name absent or exact manifest ownership | locked non-login system user; no home; no supplementary groups | root | none | delete only after ownership/process/file checks |
| `identity.group.proxy` | `account_group` | `kitdev-proxy` group | name absent or exact manifest ownership | system group present | root | none | same guarded deletion |
| `identity.user.proxy` | `account` | `kitdev-proxy` | name absent or exact manifest ownership | locked non-login system user; no home; no supplementary groups | root | none | same guarded deletion |
| `identity.group.worker` | `account_group` | `kitdev-worker` group | name absent or exact manifest ownership | system group present | root | none | same guarded deletion |
| `identity.user.worker` | `account` | `kitdev-worker` | name absent or exact manifest ownership; `kvm` exact | locked non-login system user; supplementary group exactly `kvm` | root | new processes see group | remove `kvm`, then guarded account/group deletion |
| `identity.group.observe` | `account_group` | `kitdev-observe` group | name absent or exact manifest ownership | system group present | root | none | same guarded deletion |
| `identity.user.observe` | `account` | `kitdev-observe` | name absent or exact manifest ownership | locked non-login system user; no home; no supplementary groups | root | none | same guarded deletion |
| `identity.operator.lxd` | `group_membership` | configured operator | host key verified; sudo valid; LXD proven unused; second session and recovery ready | operator absent from `lxd`, retained in `sudo` | root | old sessions retain old groups until closed | re-add only when manifest says membership existed before apply |
| `identity.manifest` | `managed_record` | project manifest | all validations passed | atomic records for every prior/desired identity state and allocated UID/GID | root | none | preserve audit record; append rollback result |

## Preconditions and failure stops

All preconditions are evaluated before privilege-bearing mutation. Stop the
whole phase if any item fails; do not apply a partial subset.

1. The private SSH host fingerprint has an exact out-of-band match in the OVH
   control plane and is promoted from `UNVERIFIED` by the operator.
2. OVH IPMI access is opened and tested, and customer rescue mode is confirmed
   available without changing the persistent boot order. A maintenance reboot
   is not part of this test.
3. A fresh second SSH connection authenticates with the intended key and
   `sudo -n /usr/bin/true` succeeds in both the held and fresh sessions.
4. `sudo -n visudo -c` validates the complete sudoers policy. This phase does
   not create or edit a sudoers file.
5. The typed collector independently confirms Ubuntu 26.04 production mode,
   x86-64, systemd, cgroups v2, existing `kvm`, and `/dev/kvm` as character
   `0660 root:kvm`.
6. Each proposed service user/group name is absent or authenticated by the
   exact kitdev installation ID and manifest. Any foreign collision blocks.
7. The explicit operator is a local non-root account, retains `sudo`, has the
   approved SSH key path, and is the only operator account changed.
8. LXD packages/snaps, units, sockets, instances, projects, storage pools,
   networks, profiles, and current process/socket use are all absent or proven
   unused through a separately approved read-only inventory. Unknown means the
   `lxd` removal action is blocked and, for atomicity, the phase does not apply.
9. The repository release, dependency lock, staged hashes, Ansible version,
   inventory, configuration, plan hash, and allowed mutation set match the
   reviewed approval exactly.
10. Ansible check mode and the typed dry-run agree and propose only the ten
    records in the table. Any package, file outside the manifest/bootstrap
    paths, SSH, service, socket, device-mode, network, firewall, kernel, storage,
    reboot, or additional group change blocks apply.
11. The manifest path is a regular root-owned non-symlink on the expected
    filesystem, or its parent is safely creatable as an explicitly planned
    project directory. Mount/symlink/type ambiguity blocks.

## Apply and lockout guard

1. Keep session A open. Verify key authentication, the expected host key,
   `sudo -n /usr/bin/true`, `sudo -n visudo -c`, and provider console access.
2. Open independent session B without SSH connection multiplexing and repeat
   the same checks.
3. From the reviewed checkout in session B, run the exact dry-run command and
   compare its plan hash and allowed-resource list with approval.
4. Invoke the exact phase apply command once. The CLI acquires the installation
   lock, reruns all preconditions, invokes the pinned local Ansible environment,
   validates results, and atomically appends the manifest. It stops on the first
   unexpected result.
5. Open fresh session C. Confirm key login, `sudo -n /usr/bin/true`, complete
   sudoers validity, operator membership in `sudo` and absence from `lxd`, exact
   service identities/groups, and the worker-only KVM permission probe.
6. Old processes retain supplementary groups. After session C passes, close
   sessions A and B and any other pre-change operator sessions. Open session D
   and verify no remaining operator process carries the old `lxd` group before
   declaring the removal effective.
7. Rerun dry-run. It must report zero identity changes. A second apply must be
   convergent and leave the manifest resource hashes unchanged except for an
   append-only validation event.

There is no SSH daemon edit or restart, so an identity failure must not be
"fixed" by changing `sshd_config`, enabling root SSH, adding passwords, or
weakening authentication. Ubuntu warns that bad SSH configuration can lock out
remote access and recommends `sshd -t` before a restart; this phase avoids that
risk entirely.

## Validation evidence

Before and after snapshots are normalized, bounded, and redacted. They include:

- `getent passwd` and `getent group` records for only the five affected
  identities/groups plus `kvm`, `sudo`, and `lxd`;
- numeric UID/GID, primary/supplementary groups, shell, home field, and locked
  password status without shadow hashes;
- `/dev/kvm` type, numeric mode, owner/group, and a bounded open test executed
  as root, operator, worker, API, proxy, and observer;
- complete sudoers validation result and non-interactive sudo outcome, without
  printing policy contents;
- normalized LXD absence/non-use conclusions, not raw instance configuration;
- running processes owned by proposed service identities;
- project manifest resource IDs, desired-state hashes, prior-state booleans,
  allocated IDs, phase, installation ID, and automation release; and
- SSH service active/listening status only, with no endpoint or address.

Expected after-state:

- operator: key login passes, `sudo` passes, no `lxd`, no `kvm`;
- worker: login denied, password locked, only primary group plus `kvm`, KVM open
  passes, no TUN administration or general Linux capabilities;
- API/proxy/observer: login denied, password locked, no supplementary groups,
  KVM open fails; and
- no service restart, reboot request, new listener, device metadata change, or
  non-project file change beyond the system account/group databases and atomic
  manifest/bootstrap-owned files declared in the plan.

## Rollback

Rollback is another manifest-checked Ansible mode, not a manual SSH recipe.
The exact eventual invocation is:

```bash
sudo ./kitdev uninstall --phase identity-access
```

The implementation performs this order:

1. Acquire the same installation lock and validate installation ID, manifest
   integrity, current resource ownership, and recovery access.
2. Stop if any affected service identity owns a running process, unexpected
   file, persistent credential, additional group, or resource not recorded by
   this phase. This phase starts no services, so any such state is foreign.
3. Remove `kitdev-worker` from `kvm` only when its current identity and manifest
   match.
4. Delete phase-created service users, then their same-name groups, only when
   unused and still exact. Pre-existing authenticated resources are restored to
   recorded prior state rather than deleted.
5. Re-add the operator to `lxd` only if the pre-change manifest records that
   membership. This restores access risk as well as state, so record it visibly.
6. Validate SSH key login, `sudo -n`, `visudo -c`, group/account state, unchanged
   `/dev/kvm`, and zero remaining phase-owned resources.
7. Append the rollback result to the manifest/audit record; do not erase the
   evidence that the phase ran.

If normal SSH is unavailable, use the already-verified OVH IPMI console. If the
installed OS cannot boot or authenticate, schedule provider customer rescue,
mount the known RAID root according to the separately reviewed recovery
runbook, and invoke a reviewed recovery procedure against that root. Rescue
mode and any reboot are disruptive and require separate approval. OVH warns not
to alter the platform's persistent boot order because doing so can disable
provider rescue behavior.

## Test gate

Automation is not eligible for server use until these tests pass:

- unit fixtures for absent, exact-owned, foreign-collision, malformed, and
  partially applied identities;
- Ubuntu 26.04 production integration tests for check mode, first apply, second
  convergent apply, rollback, and second convergent rollback;
- negative tests proving API/proxy/observer/operator cannot open KVM and worker
  can, without changing `/dev/kvm`;
- negative tests proving no identity receives `lxd`, `docker`, `libvirt`,
  `sudo`, TUN ownership, capabilities, subordinate IDs, home, key, or password;
- LXD unknown/active/resource-present fixtures that block before mutation;
- invalid sudo, missing second session/recovery proof, unverified host key,
  manifest mismatch, UID/GID collision, symlink, mount, and unexpected-diff
  failures;
- before/after host-fact hashing that permits only the approved account/group,
  manifest, and bootstrap resources;
- simulated interruption after every task, followed by safe resume or exact
  rollback; and
- an SSH survival test using independent sessions plus proof that old sessions
  retain removed supplementary groups until closed.

## Reboot and service impact

No reboot is required or allowed. No systemd unit is started, stopped, enabled,
disabled, reloaded, or restarted. SSH and LXD are not restarted. Account/group
database updates affect only newly created processes; existing sessions retain
their supplementary group set until exit. The future worker service must be
started only in a later reviewed phase.

## Explicit exclusions

- package installation or repository configuration;
- raw disk partitioning, RAID changes, filesystem creation, mounts, or storage
  ownership;
- network links, bridges, TAP devices, namespaces, routes, DNS, sysctls,
  forwarding, firewall/UFW/nftables, ports, or public exposure;
- KVM/TUN/NBD mode or ownership changes, module loading, huge pages, kernel
  command line, CPU/SMT policy, or reboot;
- Docker, LXD, Compose, Firecracker, E2B, systemd service, template, or project
  workload deployment;
- SSH daemon configuration, new human accounts/keys/passwords, root login, or
  sudoers changes; and
- granting worker capabilities before pinned-upstream tracing demonstrates an
  exact need and negative tests validate a narrower boundary.

## Approval checklist

- [ ] Private SSH host fingerprint matches the OVH control plane exactly.
- [ ] IPMI and customer rescue availability are verified out of band.
- [ ] The explicit operator identity and retained SSH key are approved.
- [ ] Read-only LXD inventory proves absence/non-use; otherwise the phase stops.
- [ ] Service names and `kvm`-only worker membership are approved.
- [ ] Bootstrap and Ansible locks are implemented, hash-complete, and reviewed.
- [ ] Typed dry-run, Ansible check/diff, and machine-readable plan agree.
- [ ] Plan contains no mutation outside the approved table and bootstrap paths.
- [ ] Manifest ownership, atomicity, interruption recovery, and rollback tests pass.
- [ ] Ubuntu 26.04 apply/apply/rollback/rollback integration tests pass.
- [ ] Two independent SSH sessions and OVH console recovery are ready for apply.
- [ ] A fresh post-change session retains `sudo` and has no `lxd` or `kvm`.
- [ ] No old operator process retains the pre-change `lxd` group at gate close.
- [ ] A final dry-run reports zero identity changes and all exclusions remain true.

## Sources

Primary/official sources retrieved on 2026-08-06:

- [Ubuntu Server user management](https://documentation.ubuntu.com/server/how-to/security/user-management/)
- [Ubuntu Server OpenSSH configuration and lockout warning](https://documentation.ubuntu.com/server/how-to/security/openssh-server/)
- [Ubuntu 26.04 `sysusers.d` manual](https://manpages.ubuntu.com/manpages/resolute/en/man5/sysusers.d.5.html)
- [Ubuntu 26.04 `adduser` system-account behavior](https://manpages.ubuntu.com/manpages/resolute/en/man8/adduser.8.html)
- [Ubuntu 26.04 `useradd` system-account behavior](https://manpages.ubuntu.com/manpages/resolute/en/man8/useradd.8.html)
- [Ubuntu 26.04 `visudo` validation behavior](https://manpages.ubuntu.com/manpages/resolute/en/man8/visudo-rs.8.html)
- [Linux KVM API](https://docs.kernel.org/virt/kvm/api.html)
- [Linux TUN/TAP driver permissions](https://www.kernel.org/doc/html/v5.9/networking/tuntap.html)
- [Firecracker KVM access guidance](https://github.com/firecracker-microvm/firecracker/blob/main/docs/getting-started.md)
- [Firecracker security design](https://github.com/firecracker-microvm/firecracker/blob/main/docs/design.md)
- [systemd execution identity and hardening options](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html)
- [systemd device access controls](https://www.freedesktop.org/software/systemd/man/latest/systemd.resource-control.html)
- [Ubuntu LXD access security](https://documentation.ubuntu.com/lxd/latest/explanation/security/)
- [Ubuntu LXD installation and local group warning](https://documentation.ubuntu.com/lxd/latest/installing/)
- [Docker group root-level privilege warning](https://docs.docker.com/engine/install/linux-postinstall/)
- [Libvirt system-daemon socket privilege boundary](https://libvirt.org/daemons.html)
- [E2B orchestrator role](https://e2b.dev/docs/byoc)
- [OVHcloud dedicated-server boot model](https://help.ovhcloud.com/csm/en-dedicated-servers-boot-process?id=kb_article_view&sysparm_article=KB0074824)
- [OVHcloud dedicated-server IPMI](https://docs.ovhcloud.com/en/guides/bare-metal-cloud/dedicated-servers/ipmi)
- [OVHcloud customer rescue mode](https://docs.ovhcloud.com/en/guides/bare-metal-cloud/dedicated-servers/rescue-mode)

## Residual unknowns

- The pinned E2B/Firecracker worker has not been traced on this host, so network,
  namespace, cgroup, mount, NBD, and capability requirements are unresolved.
- LXD non-use has not yet been established. Removal is the desired policy, not
  an authorized current-host action.
- The full production collector, port policy, CPU/SMT disposition, firewall
  evidence, and storage plan remain separate blockers for platform install.
- The current repository does not yet implement the pinned Ansible bootstrap,
  identity role, apply, manifest, or rollback commands described here.

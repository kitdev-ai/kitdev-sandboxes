# Fresh-host Ansible prerequisite contract

Date: 2026-08-07

Status: implemented and locally syntax/unit tested; intentionally not applied to
the legacy OVH lab. Qualification requires a clean Ubuntu reinstall.

## Scope and decision

The fresh-host prerequisite layer uses a pinned, repository-local
`ansible-core` controller and only `ansible.builtin` modules. The role split is:

1. `preflight`: fail-closed OS/lifecycle, systemd, cgroups v2, KVM/TUN, CPU,
   APT trust, identity collision, NBD and hugepage-capacity checks;
2. `host_packages`: immutable prior-state capture followed by the smallest
   package set implied by concrete host capabilities;
3. `host_identity`: fixed high-range non-login identities, with only
   `kitdev-worker` in `kvm`;
4. `host_kernel`: project-owned module, NBD and sysctl drop-ins plus live
   convergence;
5. `host_manifest`: exact installed package versions and managed-file hashes;
6. `host_remove`: guarded prior-state restoration.

No role edits SSH configuration, unattended/security-update policy, Docker,
firewall state, storage or unrelated services. Ubuntu 26.04 accepts production,
development and migration. Ubuntu 25.04 accepts only explicit development or
migration. Ubuntu 24.04 is outside the implementation.

This is a fresh dedicated-host policy: every enabled APT source must be an
explicitly accepted Ubuntu archive/mirror using the Ubuntu archive keyring.
Any third-party repository, alternate signing key, insecure option or foreign
suite blocks convergence. A later project-owned Docker repository role must
extend this validator with its exact key/source manifest rather than weakening
the prerequisite gate.

## Dependency decision

`ansible-core==2.21.2` is locked with every transitive distribution hash in
`requirements.lock`. The lock also pins the final controller `pip` version.
The entrypoint verifies the complete lock digest before creating or using the
controller environment and verifies the resulting Ansible version before a
playbook can run. No Galaxy collection or floating system Ansible package is
used.

Ansible's upstream support matrix lists the 2.21 controller and target Python
ranges as compatible with Python 3.12 through 3.14 on the controller and Python
3.9 through 3.14 on targets. This covers the Python generations expected on the
two selected Ubuntu releases. The upstream installation guide documents both
the minimal `ansible-core` package and isolated Python installation. The Ubuntu
26.04 archive independently carries `ansible-core` 2.20.1, but the project does
not depend on that mutable distro package.

## Kernel persistence decision

The role writes one named file in each of `/etc/modules-load.d`,
`/etc/modprobe.d` and `/etc/sysctl.d`. Systemd documents that local
administrator drop-ins in `/etc` override vendor configuration and that files
are ordered lexically. The project therefore owns only its `90-kitdev-sandboxes`
files and refuses to overwrite non-regular path collisions.

Linux documents `nbds_max` as the number of NBD devices and `max_part` as the
number of partitions per device. Both values are bounded operator inputs. If
NBD is already loaded below the requested capacity, convergence stops before
mutation instead of trying to unload an in-use block-device module.

Linux exposes persistent HugeTLB pool size through `vm.nr_hugepages`. The role
derives the page count from the declared maximum sandbox memory, concurrent
hugepage-backed sandbox count, and build/snapshot overlap allowance. The
initial profile is two 8 GiB live-sandbox slots plus one 8 GiB transient
mapping, producing a 24 GiB pool (`12288` 2 MiB pages). This covers either two
live guests plus one snapshot mapping, or one live guest plus a build requiring
two guest-sized mappings; it does not cover two live guests and that build at
the same time. The pool must not exceed 50% of detected RAM. A second
pre-mutation gate calculates only the additional pages above the current pool
against `MemAvailable` and requires 16 GiB of normal memory to remain. The
derived inputs and result are recorded in the ownership manifest.

This reservation does not enforce a runtime concurrency limit. Admission
control and sustained workload qualification remain separate work; this gate
only prevents the prerequisite role from converging a host that cannot meet its
declared memory policy.

## Rollback contract

Before the first package, user or kernel mutation, the playbook records:

- prior exact package versions and whether each package existed;
- whether each service user and group existed;
- prior contents, numeric ownership and mode for every managed file;
- prior live forwarding and hugepage values;
- prior loaded-module state; and
- the exact platform/lifecycle tuple.

The final ownership manifest records desired identities, final package
versions, managed-file SHA-256 values and kernel inputs. Removal authenticates
both root-owned mode-0600 records, refuses locally edited managed files and
active service identities, restores prior files/sysctls, and removes only
accounts/packages absent before installation. Modules newly loaded during
installation are not forcibly unloaded because KVM/TUN/NBD may be in use;
removing persistence followed by a controlled reboot returns that part of live
state to the recorded boot-time baseline. Removal writes the affected module
names to the root-only ephemeral
`/run/kitdev-sandboxes/host-prerequisites-reboot-required` marker. Removal also
requires the live forwarding and hugepage values to still equal the final
ownership manifest before it restores their prior values.

## Verification completed

- hash-complete install from an empty Python virtual environment;
- `ansible-playbook [core 2.21.2]` verification;
- syntax check of apply and removal playbooks;
- eleven unit/structural tests for platform, role, identity, lock, APT and
  kernel-capacity contracts;
- Bash syntax, Python compile and Git whitespace checks.

Apply/apply, reboot, removal and clean-host tests remain intentionally pending
for Ubuntu 26.04 production and Ubuntu 25.04 development/migration fixtures.

## Primary sources

- [Ansible installation guide](https://docs.ansible.com/projects/ansible-core/devel/installation_guide/)
- [Ansible release and Python support matrix](https://docs.ansible.com/projects/ansible-core/devel/reference_appendices/release_and_maintenance.html)
- [Ubuntu 26.04 ansible-core package](https://packages.ubuntu.com/resolute/admin/ansible-core)
- [Linux NBD parameters](https://docs.kernel.org/admin-guide/blockdev/nbd.html)
- [Linux HugeTLB pages](https://docs.kernel.org/admin-guide/mm/hugetlbpage.html)
- [Ubuntu 26.04 sysctl.d manual](https://manpages.ubuntu.com/manpages/resolute/man5/sysctl.d.5.html)
- [systemd modules-load.d manual](https://www.freedesktop.org/software/systemd/man/latest/modules-load.d.html)

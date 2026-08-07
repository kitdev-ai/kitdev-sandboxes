# Ansible ownership

Local Ansible now converges the first Milestone 1 host-prerequisite slice. The
localhost-only `site.yaml` gates the complete platform and identity tuple before
mutation, then applies narrow package, identity, kernel and ownership-manifest
roles. `remove-host-prerequisites.yaml` restores recorded prior state and
refuses modified managed files or active service identities.

Roles are separated by ownership and rollback behavior. Each future role must
declare inputs, preconditions, exact managed resources, handlers, check-mode
support, idempotency tests, and removal behavior. Roles must not use broad shell
commands when an idempotent module or structured parser is available.

The pinned controller lives in the repository-local `.venv-ansible` directory.
Use `scripts/host-prerequisites.sh`; do not invoke a system Ansible version.
The bootstrap is the only non-Ansible package edge: when `venv` is missing it
installs only Ubuntu's `python3-venv`, then installs the hash-complete Python
lock. It does not alter SSH, security-update policy, Docker, firewall or disks.

```console
sudo ./scripts/host-prerequisites.sh bootstrap production
sudo ./scripts/host-prerequisites.sh check production
sudo ./scripts/host-prerequisites.sh apply production
sudo ./scripts/host-prerequisites.sh remove-check production
sudo ./scripts/host-prerequisites.sh remove production
```

Ubuntu 25.04 requires `development` or `migration`; production is rejected by
the shell gate and the Ansible gate before mutation. Ubuntu 24.04 is not
supported.

The default workload profile provides two 8 GiB live-sandbox slots plus one
8 GiB transient-mapping allowance. The resulting 24 GiB pool covers either two
live guests plus one snapshot mapping, or one live guest plus a build requiring
two guest-sized mappings. It does not cover two live guests and such a build at
the same time. Preflight derives `12288` 2 MiB pages, refuses a pool above 50%
of total RAM, and requires at least 16 GiB of normal memory to remain available
after any additional allocation. The five policy inputs live in
`roles/preflight/defaults/main.yaml`; derived values are recorded in the
root-owned host prerequisite manifest. These are host-reservation guards, not
a runtime admission controller.

The disposable OVH development lab predates this ownership contract. Do not
weaken the fresh-host roles to accommodate it. The separately named
`legacy-capacity-migration.yaml` playbook can adopt only that lab's exact
root-owned 2,048-page sysctl drop-in after proving idle SDK/runtime/build state
and six expected services. Use only its locked wrapper:

```console
sudo ./scripts/legacy-capacity-migration.sh check
sudo ./scripts/legacy-capacity-migration.sh apply
sudo ./scripts/legacy-capacity-migration.sh remove-check
sudo ./scripts/legacy-capacity-migration.sh remove
```

The path is intentionally Ubuntu 26.04 development-only and records reversible
prior state before changing the legacy file or live pool. It does not adopt
legacy identities, modules, containers, storage, firewall state, or services.

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

# Dependency management

## Artifact classes

The project locks Python packages, system packages/repositories, upstream Git
sources, OCI images, release archives/binaries, Firecracker, guest kernels and
root filesystems, and template toolchains. A human-readable version is not an
immutable identity by itself.

Every fetched artifact record includes origin, exact version or full commit,
cryptographic digest, platform, retrieval time, license/provenance where
available, and the component/template compatibility set. Release automation
must operate from lock data and refuse floating branches, mutable tags, missing
digests, and unverified downloads outside an explicit development mode.

## Python layers

`pyproject.toml` defines package metadata and the supported interpreter range.
`requirements.in` contains direct deployment/bootstrap requirements.
`requirements.lock` is generated, reviewed output with exact versions and hashes
for all transitive dependencies. Installation uses `pip --require-hashes
--no-deps` in a project-owned virtual environment after bootstrap tooling is
verified.

Milestone 0 has no runnable Python dependency and therefore an intentionally
empty deployment lock. Before Milestone 1 adds Ansible or a CLI dependency, it
must:

1. select and record the lock generator and pip versions;
2. generate under the lowest supported interpreter and resolve any Python 3.14
   markers separately;
3. test the result on Ubuntu 25.04 development/migration and Ubuntu 26.04 LTS
   production x86-64 fixtures;
4. check that every distribution has a permitted source/wheel and hash;
5. archive the input and generated lock diff in CI; and
6. install from an empty cache with index access disabled after artifacts are
   staged, proving completeness.

Development tools use a separate generated lock derived from
`requirements-dev.in`; they never become implicit production dependencies.
Build-system requirements in `pyproject.toml` must also be represented in the
staged, hashed toolchain before package build/install is part of a milestone.

## Operating-system packages

Ubuntu 25.04 and 26.04 may require release-specific package versions. Ubuntu
25.04 artifacts exist only for development/migration compatibility and do not
make an EOL host production-eligible; Ubuntu 26.04 LTS is the production lock
target. Ansible uses exact accepted versions where the archive provides stable
version retention, records installed versions in the manifest, and fails
clearly when a required version is unavailable. Third-party APT repositories
require a pinned key fingerprint, a dedicated keyring and source file, TLS
origin, supported release mapping, and an explicit rollback/removal path.

Security updates are not disabled. Where exact pins would prevent security
maintenance, the release/update policy must define an approved compatibility
range plus a resolved installation lock and retest process rather than silently
following the archive.

## Upstream and OCI inputs

Git inputs use full commit SHAs and retain repository URLs. Submodules and Git
LFS objects are recursively recorded. OCI inputs use content digests, not tags;
multi-architecture indexes are resolved to the x86-64 manifest digest. Binary
archives and kernel/rootfs inputs use publisher checksums plus a project-recorded
strong digest, and signatures are verified when upstream provides them.

`versions.lock.yaml` is owned by upstream discovery. No placeholder commit or
checksum is valid, and absence of an accepted pin blocks the consuming
milestone.

## Updates

Automated dependency proposals build artifacts in isolation, run compatibility,
security, SDK, and coexistence tests, and produce a provenance diff. Promotion
is manual. Existing releases and templates remain addressable for rollback;
mutable artifacts are never replaced in place.

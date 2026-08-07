# OVH Docker 29 containerd-root audit

Date: 2026-08-07

Status: live read-only audit. No storage migration was performed while the SDK
compatibility run was active.

## Observation

The OVH data disk has about 3.6 TB total capacity and about 9.4 GB physically
used. Project runtime data accounts for about 4.6 GB and build cache data for
about 4.1 GB. Docker-mounted views add about 1.6 GB of logical usage; these
views overlap underlying data and must not be summed as independent physical
consumption.

The root NVMe filesystem has about 410 GB total and 24 GB used. Despite
Docker's reported `DockerRootDir` being on the data disk, Docker 29 stores
containerd content under `/var/lib/containerd` on the root filesystem. That
directory uses about 21 GB: approximately 20 GB of overlay snapshots and 1.3
GB of blobs. Pulling the pinned Node SDK image therefore grows root-disk usage,
not the intended project data disk.

## Reproducibility gap

Fresh-host automation does not yet configure and verify containerd's root on
the data disk before Docker image pulls. Moving it during a live sandbox or
image operation would risk partial state and was intentionally deferred.

The remediation must be a separately reviewed maintenance step that stops all
Docker/containerd consumers, verifies no Firecracker or build process is
active, copies content with ownership and filesystem metadata preserved,
configures containerd's root explicitly on the data disk, restarts services,
and verifies both image availability and zero new writes to the old root. It
also needs rollback instructions and free-space gates on both filesystems.

# Cloud and hosted bare-metal options

Status: Milestone 0 follow-up research; no deployment approval

Retrieved: 2026-08-06

## Question and scope

This report answers two different questions that otherwise produce conflicting
recommendations:

1. Where is the pinned upstream E2B infrastructure easiest to deploy mostly as
   written?
2. Where is this project's intended single-host Ubuntu 26.04 systemd/Compose
   port easiest to deploy?

It also assesses whether the complete single-host port can run inside an Ubuntu
26.04 KVM/libvirt virtual machine on the existing `kit@pc` host. The research
used current first-party documentation and the project's pinned E2B source at
`882a3b4786755db9e94be3297de6827f9100ce5e`. Provider price and capacity are
region- and account-dependent, so cost findings are qualitative rather than a
quote.

## Recommendation

### Short answer

- **For upstream E2B's Terraform/Nomad topology: choose GCP.** Upstream marks
  GCP supported and AWS beta. GCP is the longer-established path in the pinned
  repository, including image creation with nested virtualization, managed
  ingress, certificates, instance groups, storage, and service discovery.
- **For kitdev's one-host Ubuntu 26.04 topology: choose a Hetzner dedicated root
  server for the first remote qualification.** Hetzner's current first-party
  catalog offers Ubuntu 26.04 LTS directly on dedicated AMD systems whose
  published specifications include AMD-V. This most closely matches the host
  contract without an extra hypervisor or an upstream cloud cluster.
- **For local development: an Ubuntu 26.04 KVM/libvirt VM on `kit@pc` is
  technically viable, subject to a nested-KVM proof.** It is a good way to
  contain installation experiments. It is not a production answer: the outer
  Ubuntu 25.04 host is end-of-life, remains the root of trust, and controls the
  networking, storage, and availability of the 26.04 guest.

Do not interpret the GCP answer as Ubuntu 26.04 qualification. The pinned E2B
GCP and AWS Packer recipes both select Ubuntu 24.04. Deploying the upstream
stack "as-is" therefore violates this project's 25.04/26.04 host policy.
Changing the Packer source to 26.04 is plausible, but it is a port that must
pass package, kernel, KVM, NBD, huge-page, userfaultfd, Docker, and full E2B
runtime tests.

## Decision matrix

| Option | Upstream E2B as written | Single-host kitdev | Ubuntu 26.04 path | Firecracker/KVM path | Relative cost and operations | Verdict |
|---|---|---|---|---|---|---|
| GCP Compute Engine | **Best fit**; upstream-supported Terraform path | Possible only as a nested-virtualization VM or a separate port | Canonical publishes active Ubuntu images, but pinned E2B Packer selects 24.04 | Supported on eligible Intel L1 VMs; not E2, AMD/Arm, memory-optimized, or H4D | Large multi-service footprint; quotas and cloud operations required | Choose for upstream topology, not the first one-host 26.04 qualification |
| AWS EC2 | Supported upstream but explicitly **beta** | Possible on a nested M/C/R Intel instance or EC2 bare metal | Official Canonical 26.04 AMIs exist; pinned E2B Packer selects 24.04 | KVM nesting supported on listed Intel families; upstream defaults to M8i | Similar multi-node footprint; account quotas, ALB, S3/ECR, Secrets Manager, EBS | Second choice for upstream; attractive when AWS is already the operating standard |
| AWS EC2 bare metal | Not the current upstream default | Good technical fit for one host | Official 26.04 AMI lookup path | Direct hardware virtualization; no nested layer | High hourly cost and large fixed instance shapes; strong cloud integration | Technically clean but usually poor value for the first qualification host |
| Hetzner dedicated | No upstream Terraform provider | **Best fit** for one host | Current catalog offers 26.04 LTS | Direct AMD-V on published AX hardware | Predictable dedicated-server bill; operator owns all services and recovery | Recommended first remote one-host target |
| OVHcloud dedicated | No upstream Terraform provider | Credible alternative | Use catalog if present or official BYOLinux with a Canonical 26.04 image; exact model must be validated | OVH documents KVM on its AMD dedicated hardware | Predictable monthly server; more image preparation if 26.04 is not in the selected region/model catalog | Strong alternative, but less direct evidence than Hetzner for one-click 26.04 |
| Equinix Metal | None | None | Not applicable | Not applicable | Service ended | **Do not use**; shut down on 2026-06-30 |
| Ubuntu 26.04 VM on `kit@pc` | Not upstream-supported production topology | Good development approximation | Install official 26.04 guest image | Requires nested KVM and `/dev/kvm` in the L1 guest | No new hosting bill, but consumes and complicates the existing workstation | Use for development only after a disposable proof |

The cost column is an inference from each deployment's resource count and
billing model, not a price quotation. Run provider calculators with the exact
region, storage, egress, public IPv4, backup, and capacity-reservation choices
before purchase.

## A. Upstream E2B topology

### Why GCP is the default recommendation

The pinned E2B [repository README](https://github.com/e2b-dev/infra/blob/882a3b4786755db9e94be3297de6827f9100ce5e/README.md)
marks GCP supported, AWS beta, and a general Linux machine unsupported. Its
[self-host guide](https://github.com/e2b-dev/infra/blob/882a3b4786755db9e94be3297de6827f9100ce5e/self-host.md)
describes Terraform and Nomad/Consul deployments for both clouds, not one VM.

The GCP guide requires at least 24 CPU quota and 2,500 GB of Persistent Disk
SSD quota, plus Cloudflare, PostgreSQL, Packer, Terraform, Docker/Buildx, Go,
NPM, several Google APIs, secrets, artifact uploads, two Terraform apply
stages, database migration, and cluster seeding. This is not a small install,
but it is the upstream path with the clearest support status.

The pinned GCP
[Packer recipe](https://github.com/e2b-dev/infra/blob/882a3b4786755db9e94be3297de6827f9100ce5e/iac/provider-gcp/nomad-cluster-disk-image/main.pkr.hcl)
adds Google's `enable-vmx` image license, and the worker instance groups consume
that image. Google now recommends enabling nested virtualization directly on
the VM, but continues to support the image-license method used by E2B. Google
requires an Intel Haswell-or-newer platform and documents the following
restrictions:

- Linux KVM is the only supported L1 hypervisor.
- E2, memory-optimized, AMD, Arm, and H4D VMs cannot be L1 nested-virtualization
  hosts.
- Nested workloads can lose 10% or more performance for CPU-bound work and
  potentially more for I/O-bound work.

Sources: Google Cloud's [nested virtualization overview](https://docs.cloud.google.com/compute/docs/instances/nested-virtualization/overview)
and [enablement guide](https://docs.cloud.google.com/compute/docs/instances/nested-virtualization/enabling).

There is one immediate project-policy blocker: the pinned GCP
[image variable](https://github.com/e2b-dev/infra/blob/882a3b4786755db9e94be3297de6827f9100ce5e/iac/provider-gcp/nomad-cluster-disk-image/variables.pkr.hcl)
is `ubuntu-2404-noble-amd64-v20260517`. Canonical documents how to discover
official Ubuntu images on GCE with `gcloud compute images list --filter
ubuntu-os`, but the exact 26.04 image must replace the pin and be tested.
Ubuntu 26.04 cloud images use AMD64v3 by default; Canonical specifically notes
that N1 Sandy Bridge and Ivy Bridge platforms are no longer supported. E2B's
minimum Haswell requirement is new enough for AMD64v3, but that establishes
only CPU-instruction compatibility, not E2B qualification.

Sources: Canonical's [GCE image lookup guide](https://documentation.ubuntu.com/gcp/google-how-to/gce/find-ubuntu-images/),
[26.04 release notes](https://documentation.ubuntu.com/release-notes/26.04/),
and [architecture-variant policy](https://documentation.ubuntu.com/public-cloud/all-clouds-explanation/architecture-variants/).

### When AWS is easier

AWS can be easier organizationally when the team already operates AWS IAM,
VPCs, ECR, S3, Secrets Manager, ACM, and load balancers. It is not the lowest
technical-risk upstream choice because E2B still labels it beta.

The pinned E2B AWS Terraform now defaults Firecracker client nodes to
`m8i.4xlarge` and build nodes to `m8i.2xlarge`, with nested virtualization
enabled through EC2 CPU options. It also creates three `t3.medium` control
servers by default and separate API, ClickHouse, build, and client pools. See
the pinned [AWS variables](https://github.com/e2b-dev/infra/blob/882a3b4786755db9e94be3297de6827f9100ce5e/iac/provider-aws/variables.tf)
and [client launch template](https://github.com/e2b-dev/infra/blob/882a3b4786755db9e94be3297de6827f9100ce5e/iac/provider-aws/modules/nodepool-client/main.tf).

AWS launched nested virtualization on virtual EC2 instances in February 2026.
The current EC2 guide supports KVM and lists C8i, M8i, R8i and related variants,
plus selected C7i, M7i, R7i, and I7i types. Availability and quota remain
region-specific. AWS recommends bare metal for performance-sensitive or
strict-latency workloads. There is no extra feature charge for nested
virtualization, but ordinary instance, storage, IPv4, load balancer, logging,
and data-transfer charges still apply.

Sources: AWS's [nested virtualization guide](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/amazon-ec2-nested-virtualization.html),
[launch announcement](https://aws.amazon.com/about-aws/whats-new/2026/02/amazon-ec2-nested-virtualization-on-virtual/),
and [EC2 On-Demand pricing model](https://aws.amazon.com/ec2/pricing/on-demand/).

Canonical publishes official Ubuntu 26.04 images through AWS Systems Manager;
the documented AMD64 lookup is:

```text
/aws/service/canonical/ubuntu/server/26.04/stable/current/amd64/hvm/ebs-gp3/ami-id
```

However, E2B's pinned AWS
[Packer variables](https://github.com/e2b-dev/infra/blob/882a3b4786755db9e94be3297de6827f9100ce5e/iac/provider-aws/nomad-cluster-disk-image/variables.pkr.hcl)
filter for `ubuntu-noble-24.04`. Replacing that source is required before AWS
can satisfy kitdev's OS policy. Source: Canonical's [AWS image lookup guide](https://documentation.ubuntu.com/aws/en/latest/aws-how-to/instances/find-ubuntu-images/).

### Network and wildcard DNS

Both upstream providers automate a public load balancer, certificate, and
wildcard routing. The pinned Terraform assumes a domain managed in Cloudflare.
It creates `*.domain` DNS and certificates, routes `api.domain` to the API, and
routes wildcard sandbox hostnames to the client proxy. See the pinned
[GCP network module](https://github.com/e2b-dev/infra/blob/882a3b4786755db9e94be3297de6827f9100ce5e/iac/provider-gcp/nomad-cluster/network/main.tf)
and [AWS domain module](https://github.com/e2b-dev/infra/blob/882a3b4786755db9e94be3297de6827f9100ce5e/iac/provider-aws/domain.tf).

This is required by the SDK hostname contract, not provider preference. A
single-host deployment still needs:

- `api.<domain>` and `*.<sandbox-domain>` resolving to its ingress address;
- a certificate covering the API name and wildcard sandbox name;
- HTTP upgrade/WebSocket support and appropriate idle timeouts;
- only `80/443` publicly exposed, with API, proxy, datastores, orchestrator,
  guest bridge, VNC, and noVNC listeners kept private as designed.

Cloudflare supports wildcard A/AAAA/CNAME records on all plans. Wildcard
certificate issuance needs DNS validation; do not assume that merely creating
a wildcard DNS record provisions the origin certificate. Sources: Cloudflare's
[wildcard DNS documentation](https://developers.cloudflare.com/dns/manage-dns-records/reference/wildcard-dns-records/)
and Google's [load-balancer WebSocket documentation](https://docs.cloud.google.com/load-balancing/docs/https#websocket_support).

## B. Single-host Ubuntu 26.04 topology

### Why hosted bare metal is simpler

The project's topology expects direct control of KVM, `/dev/kvm`, cgroups v2,
NBD, HugeTLB, userfaultfd, TAP/veth devices, namespaces, routing/firewall rules,
local storage, and systemd. A dedicated server exposes those primitives without
an L0 cloud hypervisor or a nested-virtualization support matrix. It also makes
performance and kernel failures easier to attribute.

This does **not** make the application simple. The operator still owns the
entire database, cache, observability, ingress, TLS, backup, patching, and
disaster-recovery lifecycle. One host also creates one failure domain and no
horizontal capacity. The recommendation is about the closest host substrate,
not production high availability.

### Hetzner dedicated: recommended first target

Hetzner's current AX dedicated-server specifications explicitly publish AMD-V
on its AMD Ryzen and EPYC systems, 64 GB through 512 GB RAM options, NVMe
storage, and Ubuntu 26.04 LTS as a preinstalled operating-system choice. The
provider's Robot documentation supports automatic OS installation, rescue
boot, reset, networking, and KVM virtualization. Its virtualization guide
documents routed, NAT, and bridged modes and additional-IP behavior for Linux
KVM.

Sources: Hetzner's [AX server matrix](https://www.hetzner.com/dedicated-rootserver/matrix-ax/),
[Robot overview](https://docs.hetzner.com/robot/general/overview/), and
[virtualization networking guide](https://docs.hetzner.com/robot/dedicated-server/virtualization/general/).

Why it is the easiest first remote target:

- the requested OS is a current catalog option rather than a custom-image
  project;
- published AMD-V hardware avoids nested virtualization;
- a single public IP plus wildcard DNS is enough for initial ingress;
- NVMe and 128 GB-class options are available for templates, snapshots, and
  local caches;
- its fixed dedicated-server model is operationally and financially easier to
  reason about than the full upstream multi-node cloud footprint.

Limitations: the current AX catalog places these servers in Germany or Finland,
so latency from India and data-location requirements must be accepted. One
server has no provider-managed failover. Extra IPv4, remote-console access,
backup capacity, and stock availability must be checked at order time. Hetzner
is not an E2B-supported provider, so this remains kitdev's port.

### OVHcloud dedicated: credible alternative

OVHcloud documents dedicated bare-metal hosts running KVM/QEMU on AMD EPYC and
provides network boot, rescue, custom iPXE, and custom Linux deployment. Its
current BYOLinux flow accepts a QCOW2 URL through the control panel or reinstall
API; this can consume Canonical's official Ubuntu 26.04 cloud image after the
image is prepared to meet OVHcloud's single-partition/filesystem constraints.

Sources: OVHcloud's [KVM/SEV dedicated-server guide](https://help.ovhcloud.com/csm/en-ca-dedicated-servers-amd-sme-sev?id=kb_article_view&sysparm_article=KB0044013),
[BYOLinux guide](https://help.ovhcloud.com/csm/es-dedicated-servers-bring-your-own-linux?id=kb_article_view&sysparm_article=KB0061614),
[BYOI/BYOLinux comparison](https://help.ovhcloud.com/csm/en-ca-dedicated-servers-bring-your-own-image-versus-bring-your-own-linux?id=kb_article_view&sysparm_article=KB0061593),
and Canonical's [released 26.04 cloud images](https://cloud-images.ubuntu.com/releases/server/26.04/release/).

OVHcloud is not the first recommendation because this research did not establish
that Ubuntu 26.04 is a one-click catalog image for every relevant dedicated
server/region. The custom-image route is credible, but it adds image conversion,
boot, partitioning, and network validation before kitdev testing begins.

### AWS EC2 bare metal: technically clean, costly

AWS Nitro bare-metal instances expose low-level hardware features such as Intel
VT and are designed for workloads that need full hardware access. Canonical's
official 26.04 AMIs remove the custom-image issue, and ordinary VPC, EBS, Elastic
IP, ACM, and load-balancer services remain available. This is a clean cloud
substrate for direct Firecracker.

Sources: AWS's [Nitro bare-metal description](https://docs.aws.amazon.com/ec2/latest/instancetypes/ec2-nitro-instances.html)
and Canonical's [AWS image lookup guide](https://documentation.ubuntu.com/aws/en/latest/aws-how-to/instances/find-ubuntu-images/).

It is not the default recommendation because bare-metal EC2 shapes are large,
hourly-priced resources and capacity can require a specific zone or
reservation. For one qualification host, the cloud integration usually does
not offset the cost difference from a conventional dedicated server. This cost
conclusion is an inference; obtain an AWS calculator quote for the exact metal
type and region.

### Equinix Metal: unavailable

Equinix Metal was shut down on 2026-06-30, all resources were removed, and new
provisioning is unavailable. It is not a candidate. Source: the official
[Equinix Metal end-of-life notice](https://docs.equinix.com/metal/).

## Why the one-host port is difficult

The difficulty is not simply "install Firecracker." The upstream system
separates control, build, worker, routing, state, artifact, and observability
roles across Nomad jobs and cloud resources. The one-host project must replace
or adapt those assumptions while preserving the SDK contract.

The hard parts are:

1. **Privileged worker integration.** The orchestrator creates KVM guests,
   cgroups, namespaces, TAP/veth links, NBD copy-on-write disks, HugeTLB-backed
   memory, and firewall/routing state. A mistake can affect the host, not only a
   container.
2. **Host coexistence.** The same machine already has ports, Docker networks,
   firewall rules, GDM, NetworkManager, and services that kitdev must not damage.
   Ownership, idempotency, rollback, and scoped cleanup are core functionality.
3. **Collapsed failure domains.** PostgreSQL, Redis, artifacts, API, proxy,
   builder, and workers share CPU, RAM, disks, kernel, power, and network. One
   exhaustion event or reboot affects everything.
4. **Hostile-code containment.** Firecracker is only one isolation layer. The
   jailer, cgroups, network egress policy, storage paths, credentials, metadata
   access, and privileged service boundaries must all be correct.
5. **Public routing contract.** Arbitrary `<port>-<sandbox-id>` hostnames,
   WebSockets, VNC/noVNC streams, and long-lived sessions must route through one
   wildcard TLS ingress without exposing internal services.
6. **Artifact and state lifecycle.** Template builds, snapshots, caches,
   persistent volumes, backups, migrations, upgrades, and rollback all compete
   for local storage and need explicit version compatibility.
7. **Unsupported topology.** Upstream marks a general Linux host unsupported.
   Every replacement for Terraform/Nomad/cloud discovery becomes kitdev-owned
   behavior and requires contract tests.

Direct bare metal removes the nested-hypervisor variables. It does not remove
these seven application and operations problems.

## Can everything run in one Ubuntu 26.04 VM on `kit@pc`?

### Feasibility

**Yes for development, if a proof confirms nested KVM.** The topology would be:

```text
L0: kit@pc, Ubuntu 25.04, physical AMD-V/KVM host
  L1: Ubuntu 26.04 KVM/libvirt VM running kitdev systemd + Compose
    L2: Firecracker microVM sandboxes
```

Ubuntu documents x86 nested virtualization, including checking the
`kvm_amd`/`kvm_intel` `nested` module parameter and using libvirt CPU mode
`host-model` or `host-passthrough` so the L1 guest receives `svm` or `vmx`.
Firecracker requires read/write access to `/dev/kvm`. Firecracker's own
development setup documents running it in a nested GCE VM, which confirms that
a nested KVM environment is a supported development technique, though not a
production-performance guarantee.

Sources: Canonical's [nested virtualization guide](https://documentation.ubuntu.com/server/how-to/virtualisation/enable-nested-virtualisation/),
Firecracker's [getting-started prerequisites](https://github.com/firecracker-microvm/firecracker/blob/main/docs/getting-started.md),
and Firecracker's [development VM setup](https://github.com/firecracker-microvm/firecracker/blob/master/docs/dev-machine-setup.md).

The existing discovery report establishes 16 physical/32 logical AMD CPUs,
60 GiB RAM, substantial free NVMe space, KVM at the physical-host level,
cgroups v2, and TAP prerequisites. It did **not** establish that nested KVM is
enabled or that a libvirt guest can successfully run the pinned E2B
Firecracker/UFFD path. Those are required proof steps.

### What the VM helps

- Kitdev's packages, systemd units, Docker networks, NBD devices, firewall
  rules, and storage paths are mostly contained inside a disposable guest.
- Ubuntu 26.04 can be tested without in-place upgrading the workstation.
- Reverting the L1 disk image is useful during installer development.
- The outer host can keep existing desktop and development workloads running.

### What the VM does not solve

- The Ubuntu 25.04 outer host is end-of-life and remains the root of trust. A
  patched 26.04 guest cannot protect itself from a compromised L0 kernel or
  hypervisor and cannot make the system production-supported.
- Nested KVM adds CPU and I/O overhead and another kernel/QEMU/libvirt layer.
  Canonical warns that not all KVM features are available to nested guests and
  the L1 cannot be saved or migrated while nested guests run.
- Networking becomes two levels deep: physical host to L1 bridge/NAT, then L1
  namespaces/TAP/veth to Firecracker. Wildcard ingress and outbound filtering
  become harder to debug.
- The L1 needs enough fixed RAM, CPU, huge pages, and storage for databases,
  builds, snapshots, and L2 guests. Reserving that capacity competes directly
  with current workstation services.
- L1 snapshots are not a consistent backup while PostgreSQL, Redis,
  ClickHouse, NBD devices, or Firecracker guests are active.

### Required development proof before relying on it

Do not install the full system first. Build a disposable Ubuntu 26.04 L1 and
prove, in order:

1. `svm`/`vmx` is visible and `/dev/kvm` is read/write in the guest.
2. The pinned Firecracker binary boots and stops a minimal microVM repeatedly.
3. NBD allocation, userfaultfd, cgroups v2, namespaces, TAP/veth, and HugeTLB
   behavior work inside the L1.
4. A sandbox has controlled outbound networking and no route to L0 host-only
   services or metadata-like endpoints.
5. Host-to-L1 wildcard ingress preserves the Host header and WebSocket upgrades.
6. Concurrent template build and sandbox restore remain within an explicit L1
   resource budget without destabilizing `kit@pc`.

If any KVM/UFFD/snapshot feature fails under nesting, use the local physical
host only for narrow development and move full runtime qualification directly
to Ubuntu 26.04 bare metal.

## Purchase and qualification gates

Before ordering or deploying, record the following as a dated decision:

- target geography, latency, data residency, and provider account constraints;
- exact CPU model and confirmed VT-x/AMD-V exposure;
- an explicit RAM/CPU budget derived from the intended sandbox and concurrent
  build profile, with headroom reserved for the control and state services;
- NVMe capacity, endurance, RAID/failure behavior, inode headroom, and backup
  target;
- public IPv4/IPv6, reverse DNS, wildcard DNS, certificate automation, and
  WebSocket timeout behavior;
- rescue console, reinstall path, remote power control, hardware replacement
  SLA, and recovery ownership;
- Ubuntu 26.04 exact image serial/checksum and post-boot kernel/package state;
- a disposable Firecracker smoke test before any production claim;
- monthly quote including compute, disks, snapshots/backups, public IP,
  traffic/egress, load balancer, DNS, logs, and taxes.

## Uncertainties

- Upstream's Packer and boot scripts have not been run on Ubuntu 26.04. Official
  image availability does not establish E2B runtime compatibility.
- GCP eligible machine-series details can change; validate the exact chosen
  type and zone against the current nested-virtualization API before purchase.
- AWS nested-virtualization type availability and quotas vary by region. The
  selected type must be queried in the deployment account.
- Hetzner hardware stock and prices change. Confirm the exact CPU exposes AMD-V
  and order enough RAM/storage; do not infer all dedicated models from the AX
  matrix.
- OVHcloud 26.04 catalog availability was not established for a specific model
  and region. Its BYOLinux fallback is documented but untested here.
- No performance, snapshot-restore, UFFD, or network-isolation benchmark has
  yet been run on GCP, AWS, hosted bare metal, or nested local KVM.

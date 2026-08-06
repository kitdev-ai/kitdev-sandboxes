# Project: kitdev-sandboxes

Build an open-source, self-contained, idempotent deployment system for running a complete E2B-compatible AI-agent sandbox platform on a single bare-metal Ubuntu server.

The repository name is:

`kitdev-sandboxes`

The development machine is a Mac mini. The actual test host is available over SSH as:

`kit@pc`

All Firecracker, KVM, networking, template-building and sandbox tests must run on `kit@pc`. Do not attempt to run Firecracker directly on macOS.

## Primary objective

A user should be able to clone this repository on a clean supported Ubuntu server and run:

```bash
sudo ./kitdev install
```

After installation, the server should provide:

* E2B-compatible sandbox API
* E2B JavaScript/TypeScript and Python SDK compatibility
* Firecracker microVM-based sandbox execution
* Code-execution sandboxes
* Browser sandboxes
* Full desktop/computer-use sandboxes
* Command and PTY execution
* File upload, download, read, write and watching
* Sandbox pause, resume and snapshot support
* Template building
* Authenticated sandbox port exposure
* Browser screen streaming
* Screenshots
* Mouse control
* Keyboard control
* Browser automation through Playwright/CDP
* Persistent workspace support
* Metrics, logs and health checks
* Secure defaults for executing untrusted AI-generated code

The complete installation must be manageable through one CLI:

```bash
kitdev install
kitdev configure
kitdev up
kitdev down
kitdev restart
kitdev status
kitdev doctor
kitdev logs
kitdev test
kitdev update
kitdev backup
kitdev restore
kitdev uninstall
```

## Scope of version 0.1

Support only:

```text
Production host OS: Ubuntu 26.04 LTS
Development/migration compatibility: Ubuntu 25.04 (EOL; not production-eligible)
Architecture: x86-64
Boot: systemd
Virtualization: Intel VT-x or AMD-V
Kernel virtualization: KVM
Control groups: cgroups v2
Deployment: single bare-metal server
```

Detect unsupported environments and exit clearly without making changes.

Recognize and test both Ubuntu 25.04 and 26.04. Because Ubuntu 25.04 is
end-of-life, allow it only in an explicit development or migration lifecycle
mode and refuse production mode without making changes. Ubuntu 26.04 LTS is the
production target.

Support both server and desktop editions when concrete capability and
coexistence checks pass. On desktop systems, inspect GDM or other display
managers, remote-desktop listeners, NetworkManager-owned interfaces and routes,
sleep policy, device access and resource headroom. Preserve desktop services;
the edition label or presence of GDM alone is not a reason to reject a host.

Do not claim support for:

* macOS
* WSL
* ARM64
* arbitrary Linux distributions
* machines without `/dev/kvm`
* cloud VMs without nested virtualization
* multi-node scheduling in version 0.1

Design the repository so multi-node workers can be added later.

## Installation design

Use a small Bash entrypoint that bootstraps a pinned Python virtual environment and runs local Ansible playbooks.

Suggested flow:

```text
install.sh
  → prerequisite validation
  → create Python virtual environment
  → install pinned ansible-core
  → run Ansible locally
  → install and configure services
  → run health checks
  → build templates
  → run integration tests
```

Do not implement the entire installer as one large Bash script.

Requirements:

* Bash must use `set -Eeuo pipefail`.
* Pin every external dependency.
* Verify checksums for downloaded binaries and archives.
* Never execute downloaded shell scripts directly from the internet.
* Never run an unpinned Git branch in production.
* Record exact upstream Git commits.
* Generate a machine-readable installation manifest.
* Make every operation safe to rerun.
* Re-running `kitdev install` must converge to the same state.
* Re-running it must not overwrite unchanged secrets.
* Failed installation phases must be resumable.

## Repository structure

Use approximately:

```text
kitdev-sandboxes/
├── README.md
├── LICENSE
├── CHANGELOG.md
├── SECURITY.md
├── CONTRIBUTING.md
├── install.sh
├── kitdev
├── pyproject.toml
├── requirements.lock
├── versions.lock.yaml
├── config/
│   ├── default.yaml
│   ├── schema.json
│   └── examples/
├── ansible/
│   ├── site.yaml
│   ├── inventory/
│   ├── roles/
│   │   ├── preflight/
│   │   ├── host_packages/
│   │   ├── host_kernel/
│   │   ├── docker/
│   │   ├── e2b_sources/
│   │   ├── e2b_datastores/
│   │   ├── e2b_api/
│   │   ├── e2b_proxy/
│   │   ├── e2b_orchestrator/
│   │   ├── e2b_templates/
│   │   ├── networking/
│   │   ├── firewall/
│   │   ├── observability/
│   │   ├── backup/
│   │   └── validation/
├── compose/
│   ├── compose.yaml
│   ├── compose.observability.yaml
│   └── env/
├── systemd/
│   ├── kitdev-e2b-api.service
│   ├── kitdev-e2b-client-proxy.service
│   ├── kitdev-e2b-orchestrator.service
│   └── kitdev-e2b-maintenance.timer
├── templates/
│   ├── base/
│   ├── coding/
│   ├── browser/
│   └── desktop/
├── networking/
│   ├── nftables.conf.j2
│   └── network-policy.yaml
├── scripts/
│   ├── preflight.sh
│   ├── build-template.sh
│   ├── backup.sh
│   └── migration.sh
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── security/
│   └── smoke/
├── docs/
│   ├── architecture.md
│   ├── installation.md
│   ├── configuration.md
│   ├── security-model.md
│   ├── networking.md
│   ├── templates.md
│   ├── upgrades.md
│   ├── disaster-recovery.md
│   └── multi-node-roadmap.md
└── examples/
    ├── typescript/
    └── python/
```

## Filesystem isolation

All project-owned state must live under dedicated paths:

```text
/opt/kitdev-sandboxes
/etc/kitdev-sandboxes
/var/lib/kitdev-sandboxes
/var/log/kitdev-sandboxes
/run/kitdev-sandboxes
```

Do not place runtime state in the Git checkout.

Suggested layout:

```text
/var/lib/kitdev-sandboxes/
├── postgres/
├── redis/
├── clickhouse/
├── object-storage/
├── registry/
├── templates/
├── snapshots/
├── build-cache/
├── sandbox-cache/
├── persistent-volumes/
└── backups/
```

Never delete or modify files outside project-owned paths unless the file is explicitly managed and backed up.

## Host-change policy

Some host changes are unavoidable because Firecracker requires KVM, NBD, huge pages, TAP devices, routing and cgroups.

Every host change must:

1. Use a project-specific configuration file.
2. Preserve the previous system state.
3. Be documented in the installation manifest.
4. Be reversible by `kitdev uninstall`.
5. Avoid replacing unrelated configuration.

Use dedicated files such as:

```text
/etc/modules-load.d/kitdev-sandboxes.conf
/etc/modprobe.d/kitdev-sandboxes-nbd.conf
/etc/sysctl.d/90-kitdev-sandboxes.conf
/etc/nftables.d/kitdev-sandboxes.nft
```

Do not:

* flush the host firewall
* replace `/etc/nftables.conf`
* overwrite `/etc/docker/daemon.json`
* disable AppArmor
* disable SELinux-like controls
* disable unattended security updates
* change SSH configuration
* reboot automatically
* stop unrelated Docker containers
* bind databases to public interfaces
* add the normal login user to privileged runtime groups unnecessarily

When modifying a shared JSON or configuration file, parse and merge it structurally. Never append blindly.

## Upstream source management

Vendor or fetch pinned versions of:

* `e2b-dev/infra`
* `e2b-dev/e2b`
* `e2b-dev/desktop`
* any required dashboard or supporting repositories
* Firecracker/kernel artifacts used by the selected E2B commit

Store versions in:

```yaml
e2b_infra:
  repository: https://github.com/e2b-dev/infra.git
  commit: "<full commit SHA>"

e2b_desktop:
  repository: https://github.com/e2b-dev/desktop.git
  commit: "<full commit SHA>"
```

The installer must refuse floating branches unless explicitly running in development mode.

Implement:

```bash
kitdev versions
kitdev update --check
kitdev update --to <release>
kitdev rollback
```

## Services

Run stateful support services through an isolated Docker Compose project:

```text
Project name: kitdev-sandboxes
Network prefix: kitdev_sandboxes_
Volume prefix: kitdev_sandboxes_
```

Core services:

* PostgreSQL
* Redis
* ClickHouse
* S3-compatible object storage if needed for production-like snapshot/template storage
* local container registry if needed for templates

Optional observability profile:

* Grafana
* Loki
* Tempo
* Mimir or Prometheus
* OpenTelemetry Collector
* Vector, if required by the pinned E2B version

Bind internal services only to loopback or the private Compose network.

Do not expose PostgreSQL, Redis, ClickHouse, object storage administration or observability services publicly by default.

Run host-integrated E2B components under systemd:

* E2B API
* E2B client proxy
* E2B orchestrator/template manager

The orchestrator may need root privileges for KVM, TAP, cgroups, mounts and NBD. Keep the API and client proxy unprivileged.

Create dedicated users:

```text
kitdev-e2b
kitdev-proxy
kitdev-observe
```

The orchestrator service must not inherit production application credentials.

## Configuration

Create:

```text
/etc/kitdev-sandboxes/config.yaml
/etc/kitdev-sandboxes/secrets.env
```

`config.yaml` contains non-secret configuration.

`secrets.env` must:

* be root-readable only
* have mode `0600`
* contain cryptographically random generated secrets
* never be committed
* never print secrets in logs
* never regenerate existing secrets during a normal rerun

Support noninteractive configuration:

```bash
sudo kitdev install \
  --domain sandboxes.example.com \
  --listen-address 127.0.0.1 \
  --profile full
```

Profiles:

```text
minimal:
  E2B core, base code template

standard:
  core, coding template, browser template, observability

full:
  core, coding, browser, desktop/computer-use, observability,
  persistence and backup support
```

## Sandbox templates

Create and test four templates.

### Base

Minimal Ubuntu guest containing:

* `envd`
* bash
* coreutils
* ca-certificates
* curl
* wget
* git
* process utilities
* non-root sandbox user

### Coding

Include:

* Git
* Node.js LTS
* npm
* pnpm
* Bun
* Python
* uv
* pip
* Go
* Rust toolchain
* common C/C++ build tools
* ripgrep
* jq
* unzip
* SSH client
* GitHub CLI where licensing and installation permit
* Playwright client libraries
* common agent CLI prerequisites

Do not embed API keys or credentials.

### Browser

Include:

* Chromium
* Firefox
* Playwright browsers and dependencies
* Xvfb where required
* CDP access
* screenshot tooling
* download directory
* browser-profile persistence option

Browser template tests must prove:

* Chromium launches
* Playwright connects
* page navigation works
* screenshot is returned
* download artifact can be collected
* browser process is killed when the sandbox is destroyed

### Desktop/computer-use

Base this on the E2B Desktop OSS template and SDK.

Include:

* lightweight XFCE desktop
* X server or Xvfb as required
* Chromium
* Firefox
* VNC/noVNC or E2B-supported screen streaming
* authenticated stream URLs
* screenshot support
* mouse movement and clicks
* scrolling
* keyboard typing and key combinations
* window enumeration
* window title retrieval
* launching applications
* opening files
* configurable display resolution and DPI

Add TypeScript and Python examples that:

1. Create a desktop sandbox.
2. Launch Chromium.
3. Start an authenticated desktop stream.
4. Take a screenshot.
5. Move the mouse.
6. Click the browser.
7. Type text.
8. Capture a second screenshot.
9. Stop the stream.
10. Destroy the sandbox.

## Public API exposure

Default installation must bind all E2B interfaces to loopback.

Public exposure must be an explicit configuration choice.

Support:

```text
api.<domain>
*.sandboxes.<domain>
```

Use a reverse proxy only when configured.

Requirements:

* wildcard routing for sandbox ports
* TLS support
* authenticated stream URLs
* request-size limits
* rate limits
* timeout limits
* WebSocket support
* no direct exposure of orchestrator ports
* no direct exposure of Redis or datastore ports

Do not automatically alter public DNS.

Generate the required DNS records and certificate instructions for the operator.

## Security model

Assume sandbox code is malicious.

Threats include:

* arbitrary native binaries
* fork bombs
* memory exhaustion
* disk exhaustion
* network scanning
* access to host services
* attempts to access Docker sockets
* cloud metadata access
* attempts to steal credentials
* malicious package-install scripts
* browser exploits
* persistent malware in workspaces
* hostile template content

### Host isolation

Each sandbox must run in its own Firecracker microVM.

Every sandbox must have:

* explicit vCPU limit
* explicit RAM limit
* execution timeout
* disk quota
* process/PID limits
* output-size limit
* network bandwidth policy where practical
* unique runtime identity
* isolated TAP/network namespace
* copy-on-write root filesystem
* automatic destruction after TTL

Never mount into a sandbox:

* `/var/run/docker.sock`
* host `/`
* `/opt/kitdev-sandboxes`
* `/etc/kitdev-sandboxes`
* host SSH keys
* host cloud credentials
* host package-manager credentials
* production application secrets

### Networking

Create a dedicated network namespace/bridge system owned only by this project.

Use a dedicated nftables table, for example:

```text
inet kitdev_sandboxes
```

Do not flush or replace unrelated nftables tables.

Default sandbox egress policy:

* allow DNS only through the configured resolver
* allow HTTP/HTTPS internet access according to profile
* deny access to the host
* deny access to Docker networks
* deny access to internal datastores
* deny RFC1918 networks unless allowlisted
* deny link-local ranges
* deny cloud metadata addresses
* deny multicast
* deny communication between sandboxes by default
* deny the management network
* deny the E2B orchestrator API
* deny Redis, PostgreSQL and ClickHouse

Cover both IPv4 and IPv6. Do not leave IPv6 unfiltered.

Add tests proving a sandbox cannot access:

```text
127.0.0.1 host services
host bridge address
Docker bridge gateways
PostgreSQL
Redis
ClickHouse
orchestrator ports
169.254.169.254
other running sandboxes
```

### Credentials

No long-lived third-party credentials may be injected into sandbox environment variables.

Provide an interface for short-lived credential proxying later, but do not implement a broad credential broker in milestone one.

Never log:

* API keys
* access tokens
* cookies
* authorization headers
* VNC authentication secrets
* signed sandbox URLs

### Service hardening

Apply systemd hardening where compatible:

* explicit users and groups
* restrictive `UMask`
* `NoNewPrivileges` where possible
* limited writable paths
* explicit device access
* explicit capability bounds
* restart policy
* resource limits
* dependency ordering
* watchdog or health check
* clean shutdown

Do not apply hardening flags blindly to the orchestrator. Test each option because the orchestrator needs KVM, NBD, mounts, TAP networking and cgroups.

## CLI behavior

Implement a user-friendly `kitdev` CLI.

Examples:

```bash
kitdev doctor
kitdev install --profile full
kitdev status
kitdev services
kitdev logs api
kitdev logs orchestrator
kitdev templates list
kitdev templates build desktop
kitdev sandbox create --template coding
kitdev sandbox exec <id> -- uname -a
kitdev sandbox delete <id>
kitdev test smoke
kitdev test security
kitdev backup create
kitdev update --check
kitdev uninstall
```

Commands must return useful nonzero exit codes on failure.

Support:

```text
--json
--verbose
--dry-run
--non-interactive
```

`--dry-run` must not change the host.

## Doctor command

`kitdev doctor` must check:

* supported Ubuntu release and lifecycle mode (Ubuntu 25.04 development or
  migration only; Ubuntu 26.04 LTS production)
* x86-64 architecture
* CPU virtualization flags
* `/dev/kvm`
* KVM module
* cgroups v2
* NBD module and configured device count
* huge-page availability
* free memory
* free disk space
* Docker version
* Docker Compose version
* required ports
* DNS configuration
* firewall state
* AppArmor status
* time synchronization
* existing conflicting services
* installed project version
* upstream component versions
* service health
* template availability
* ability to start a minimal Firecracker sandbox

Output a readable report and `--json` output.

## Idempotency requirements

The following sequence must succeed:

```bash
sudo ./install.sh
sudo ./install.sh
sudo kitdev configure --profile full
sudo kitdev configure --profile full
sudo kitdev restart
sudo kitdev test
```

The second run must:

* preserve API keys
* preserve databases
* preserve templates unless a rebuild is needed
* preserve persistent workspaces
* avoid duplicate firewall rules
* avoid duplicate users and groups
* avoid duplicate systemd configuration
* avoid redownloading unchanged artifacts
* report which resources changed

Use atomic writes for managed configuration files.

## Update and rollback

An update must:

1. Download and verify the requested release.
2. Back up configuration and databases.
3. Validate compatibility.
4. Build new binaries and templates separately.
5. Run preflight tests.
6. Stop affected services only.
7. apply migrations.
8. Start services.
9. Run health checks.
10. Roll back automatically when health checks fail.

Do not perform automatic updates by default.

## Uninstall behavior

`kitdev uninstall` must stop and remove only resources owned by this project.

Default uninstall must preserve:

* databases
* snapshots
* templates
* persistent workspaces
* backups
* generated secrets

A separate destructive command is required:

```bash
kitdev uninstall --purge-data
```

Before purging data:

* require an interactive confirmation
* require the installation ID
* print the exact paths that will be deleted
* refuse in noninteractive mode unless an additional explicit confirmation flag is supplied

Never remove Docker itself if Docker existed before installation.

Never stop or delete unrelated containers, networks, images or volumes.

## Backups

Back up:

* PostgreSQL
* ClickHouse metadata needed by E2B
* Redis only if persistence is required
* template metadata
* snapshots
* persistent workspace volumes
* configuration
* encrypted secrets
* installation manifest

Support local backup initially:

```text
/var/lib/kitdev-sandboxes/backups
```

Design the backend so S3-compatible remote backup can be added later.

Test restore into a clean installation.

## Observability

Provide:

* health endpoints
* structured JSON logs
* service status
* Firecracker VM count
* active sandbox count
* sandbox creation latency
* failed sandbox creations
* template-build duration
* disk usage
* memory usage
* huge-page usage
* NBD usage
* API request metrics
* proxy errors

Do not expose Grafana publicly by default.

## Acceptance tests

Create automated tests for the following.

### Installation

* install on clean Ubuntu 25.04 in explicit development/migration mode
* reject production mode on Ubuntu 25.04 without making changes
* install on clean Ubuntu 26.04 LTS in production mode
* install on a compatible Ubuntu desktop host without disrupting GDM,
  NetworkManager or unrelated desktop services
* rerun installation
* reboot host
* all services recover
* uninstall without purge
* reinstall with preserved state

### Core sandbox

* create sandbox
* run shell command
* stream command output
* write and read file
* upload and download file
* expose an HTTP port
* reconnect to sandbox
* pause sandbox
* resume sandbox
* snapshot sandbox
* delete sandbox
* TTL cleanup

### Browser

* launch Chromium
* connect with Playwright
* open a test page
* take screenshot
* download file
* collect artifact
* terminate cleanly

### Desktop

* create desktop sandbox
* start authenticated stream
* launch browser
* take screenshot
* move pointer
* click
* type
* scroll
* inspect active window
* stop stream
* destroy sandbox

### Security

* sandbox cannot access host services
* sandbox cannot access Docker socket
* sandbox cannot access private networks
* sandbox cannot access metadata IP
* sandbox cannot communicate with another sandbox
* fork bomb is constrained
* memory exhaustion is constrained
* disk exhaustion is constrained
* timeout kills the workload
* secrets do not appear in logs
* project firewall rules survive reruns without duplication

### Coexistence

Before installation, create an unrelated Docker container and network.

After install, update and uninstall:

* unrelated container remains running
* unrelated network remains unchanged
* unrelated Docker volumes remain
* unrelated firewall rules remain
* unrelated systemd services remain

## Development workflow

Work in milestones. Do not attempt the complete system in one unreviewable change.

### Milestone 0: discovery and architecture

* inspect `kit@pc`
* collect OS, kernel, CPU, RAM, disks, KVM, Docker and firewall details
* make no mutations
* write `docs/research/host-discovery.md`
* pin upstream E2B commits
* write architecture decision records
* create repository scaffold
* commit

### Milestone 1: preflight and host preparation

* implement `kitdev doctor`
* implement dry-run
* prepare KVM/NBD/huge pages idempotently
* create project users and directories
* test reboot persistence
* commit

### Milestone 2: E2B core

* install pinned E2B sources
* start Postgres, Redis and ClickHouse
* build API, client proxy, orchestrator and `envd`
* create systemd services
* build base template
* create and execute first sandbox
* commit

### Milestone 3: coding sandbox

* coding template
* TypeScript and Python SDK examples
* process and filesystem tests
* pause/resume/snapshot tests
* commit

### Milestone 4: browser sandbox

* Chromium
* Playwright/CDP
* screenshots
* downloads
* artifact collection
* commit

### Milestone 5: desktop/computer use

* E2B Desktop template
* XFCE
* authenticated stream
* screenshots
* mouse/keyboard/window APIs
* end-to-end computer-use test
* commit

### Milestone 6: security

* network isolation
* nftables
* resource limits
* systemd hardening
* security tests
* coexistence tests
* commit

### Milestone 7: operations

* backup and restore
* update and rollback
* uninstall
* observability
* documentation
* commit

## Rules while operating on kit@pc

Before changing anything:

```bash
ssh kit@pc
```

Collect and record:

```bash
cat /etc/os-release
uname -a
lscpu
free -h
lsblk
df -h
docker version
docker compose version
systemctl --version
stat -fc %T /sys/fs/cgroup
ls -l /dev/kvm
sudo nft list ruleset
sudo ss -tulpn
```

Do not:

* reboot without explicit approval
* modify SSH settings
* disable the firewall
* uninstall existing packages
* stop unrelated services
* delete Docker resources
* repartition disks
* format storage
* modify bootloader settings
* expose services publicly
* use destructive cleanup commands
* use `docker system prune`
* use broad `rm -rf` outside verified project paths

Make small commits after each working milestone.

Every commit must include:

* what changed
* how it was tested
* known limitations
* rollback instructions

## First action

Perform only Milestone 0 now.

Do not install or modify the host yet.

Inspect the upstream E2B repositories and `kit@pc`, create the repository scaffold, write the architecture documents, version lock file, preflight design and milestone plan, then report:

1. Host compatibility findings.
2. Port and service conflicts.
3. Required host changes.
4. Selected upstream commits.
5. Proposed architecture.
6. Risks or blockers.
7. The exact next milestone.

Stop after Milestone 0 and wait for review before applying host changes.

# OVH disposable lab manual Docker bootstrap

Date: 2026-08-06
Status: manually applied under explicit user approval; tracked replay added;
production promotion and rollback qualification not established

## Scope

The project lead manually completed the package/repository and Docker Engine
bootstrap on the disposable Ubuntu 26.04 OVH host. This was a mutation-first
lab exercise, not an invocation of the still-blocked Stage 50 runner and not
production automation. No SSH was used while converting the observed sequence
into the tracked replay artifact.

The tracked
[`bootstrap-docker-engine.sh`](../../experiments/ovh-lab/bootstrap-docker-engine.sh)
is a fail-closed replay of the observed end state. It remains outside
`stages.json`, refuses production state, holds and revalidates the exact Stage
05 authorization throughout apply, and does not change Stage 50 from
`blocked`.

The script must run from a root-owned checkout whose complete ancestry and six
reviewed component files are not group/other writable. It stable-reads and
frames the script, common shell library, runner, journal, Stage 05, and Stage 10
bytes into the approval digest, then imports only from that immutable checkout.
This is a trusted-checkout component-digest acknowledgement, not the staged
runner's anonymous snapshotted bundle. An operator-writable checkout is rejected.

## Before state

The host was Ubuntu 26.04 Resolute `amd64` with the validated disposable-lab
marker. Docker Engine packages, Docker repository files, containers, and images
were absent. The baseline package operation found every explicitly requested
package except `make` already installed. Apt marked these already-installed
packages manual because they were explicitly requested:

```text
ca-certificates curl gnupg kmod iproute2 iptables procps xz-utils
```

`make` was newly installed and therefore manual. The command did not report a
manual-mark change for `git`, `jq`, or `util-linux`.

## Recorded commands and exact versions

The initial baseline commands were:

```bash
apt-get update
apt-get install -y --no-install-recommends ca-certificates curl gnupg git jq make kmod iproute2 iptables util-linux procps xz-utils
```

The resulting explicitly requested baseline versions were:

| Package | Exact installed version |
| --- | --- |
| `ca-certificates` | `20260601~26.04.1` |
| `curl` | `8.18.0-1ubuntu2.3` |
| `gnupg` | `2.4.8-4ubuntu3` |
| `git` | `1:2.53.0-1ubuntu1` |
| `jq` | `1.8.1-4ubuntu2` |
| `make` | `4.4.1-3` |
| `kmod` | `34.2-2ubuntu2` |
| `iproute2` | `6.19.0-1ubuntu1.1` |
| `iptables` | `1.8.11-2ubuntu3` |
| `util-linux` | `2.41.3-3ubuntu2` |
| `procps` | `2:4.0.4-9ubuntu1` |
| `xz-utils` | `5.8.3-1` |

The operator fetched Docker's official Ubuntu key as a file, never piped
downloaded content to a shell, and verified all of these exact values before
publishing it at `/etc/apt/keyrings/docker.asc`:

| Key property | Exact value |
| --- | --- |
| ASCII-object SHA256 | `1500c1f56fa9e26b9b8f42452a553675796ade0807cdce11975eb98170b3a570` |
| Primary fingerprint | `9DC858229FC7DD38854AE2D88D81803C0EBFCD88` |
| Signing-subkey fingerprint | `D3306A018370199E527AE7997EA0A9C3F273FCD8` |

The canonical 155-byte `docker.sources` was written at
`/etc/apt/sources.list.d/docker.sources`, with SHA256
`47be0f749c19273936c7e56fff5a29b9108bcce8137ee677cc736523fb876e71`.
The effective repository definition was:

```text
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: resolute
Components: stable
Architectures: amd64
Signed-By: /etc/apt/keyrings/docker.asc
```

After another `apt-get update`, the exact requested Docker install set was:

```bash
apt-get install -y --no-install-recommends docker-ce=5:29.7.2-1~ubuntu.26.04~resolute docker-ce-cli=5:29.7.2-1~ubuntu.26.04~resolute containerd.io=2.3.3-1~ubuntu.26.04~resolute docker-buildx-plugin=0.36.1-1~ubuntu.26.04~resolute docker-compose-plugin=5.4.0-1~ubuntu.26.04~resolute
```

The tracked replay adds `--no-remove`, pins the observed baseline versions too,
checks every Docker candidate with an input-consuming parser, and verifies the
final manual marks. These are deliberate fail-closed improvements over the
interactive command sequence, not claims that the original baseline command
contained version pins.

The replay also clears caller-supplied `APT_CONFIG`, securely validates
no-follow repository parents, publishes absent files with Linux
`renameat2(RENAME_NOREPLACE)`, never replaces existing foreign bytes, and skips
the mutable key download when the exact approved key is already installed.
Exact key-only state is a supported resume checkpoint; source-without-key and
publication residue fail closed. After package installation it explicitly
converges Docker and containerd to enabled/active before verification.
It does not otherwise isolate or freeze system APT hooks, preferences, proxy
configuration, or the complete dependency closure; those remain explicit
manual-replay limitations and production blockers.

## Interrupted first attempt

The first Docker script attempt completed the baseline operation, key/source
publication, and repository `apt-get update`, then stopped before Docker
package installation. Its candidate-selection pipeline combined
`apt-cache policy` with an `awk` program that exited as soon as it found the
candidate. Under `set -o pipefail`, that early consumer exit could make
`apt-cache` receive `SIGPIPE`, so the otherwise successful lookup made the
script fail closed.

The operator inspected the boundary and resumed from the exact published
key/source state. The corrected parser consumes the complete `apt-cache`
stream and decides in its `END` block. The tracked replay tests retain this
property. The first attempt did not install any Docker package or start Docker;
the later exact install performed those mutations.

## After state

The requested Docker packages were installed at these exact versions:

| Package | Exact installed version |
| --- | --- |
| `docker-ce` | `5:29.7.2-1~ubuntu.26.04~resolute` |
| `docker-ce-cli` | `5:29.7.2-1~ubuntu.26.04~resolute` |
| `containerd.io` | `2.3.3-1~ubuntu.26.04~resolute` |
| `docker-buildx-plugin` | `0.36.1-1~ubuntu.26.04~resolute` |
| `docker-compose-plugin` | `5.4.0-1~ubuntu.26.04~resolute` |

`docker.service` and `containerd.service` were active and enabled. Docker
reported the `overlayfs` storage driver, `systemd` cgroup driver, and cgroup
version 2. The engine had zero containers and zero images.

## Mutation and rollback boundary

The manual operation changed host APT lists, package selections/manual marks,
installed `make`, wrote the Docker key and source, installed the exact requested
Docker set plus solver-selected dependencies, and allowed package lifecycle
scripts to enable/start Docker and containerd. Ordinary package logs and
service-runtime state are outside the five top-level version pins.

This run did not capture hashes for the complete Ubuntu/Docker dependency
closure, the original selection/auto/manual state of every dependency, or a
tested reverse solver transaction. Removing the five requested packages would
not by itself restore APT lists, manual marks, dependencies, service state, or
Docker data, and Docker documents that package removal does not remove
`/var/lib/docker`. Therefore the tracked script intentionally has no rollback
mode. The authoritative disposable-lab rollback remains an OVH operating-system
reinstall. Production promotion still requires the isolated artifact contract,
journaled apply/rollback, and clean Ubuntu 26.04 automation qualification.

# OVH Stage 10 official package and Docker repository prerequisites

Date: 2026-08-06

## Scope

This note defines the evidence and approval boundary for Stage 10 on a clean
Ubuntu Server 26.04 LTS (Resolute) `amd64` host. It uses only Ubuntu and Docker
primary sources. No SSH command or host mutation was performed for this
research.

Stage 10 is limited to:

- inventorying `ca-certificates`, `curl`, Docker-conflicting packages, apt
  sources, holds, and installed package state;
- resolving an exact bootstrap package transaction when a bootstrap package is
  absent;
- authenticating and freezing Docker's apt key and repository metadata; and
- proposing installation of exact, reviewed `docker.asc` and `docker.sources`
  bytes.

Stage 10 must not remove a conflicting package, install Docker Engine or its
plugins, start or enable Docker/containerd, create the `docker` group, or alter
daemon, network, firewall, or data-root state. Those are Stage 50 concerns.

## Conclusions

1. Docker officially supports Ubuntu Resolute 26.04 LTS and `amd64`. Its
   repository publishes a signed Resolute `stable/binary-amd64` index.
2. The Docker Ubuntu instructions require `ca-certificates` and `curl`, install
   the ASCII-armored repository key at `/etc/apt/keyrings/docker.asc`, and add
   a deb822 source at `/etc/apt/sources.list.d/docker.sources`.
3. The key fetched from Docker on 2026-08-06 has primary fingerprint
   `9DC858229FC7DD38854AE2D88D81803C0EBFCD88`. The Resolute `InRelease` was
   signed by its signing subkey
   `D3306A018370199E527AE7997EA0A9C3F273FCD8` and verifies back to that primary
   key.
4. Docker does not publish a SHA256 for the mutable `/linux/ubuntu/gpg` object
   in its Ubuntu installation instructions or beside the object. The observed
   SHA256 below is therefore a captured approval pin, not an independently
   published checksum. Fingerprint verification and signed `InRelease`
   verification are both mandatory.
5. `apt-get --simulate` is no-action, but it uses existing package lists and
   disables locking. A fresh plan can be produced without changing host apt or
   dpkg state by putting all acquired lists, sources, keys, and caches under a
   private evidence directory and preventing host apt configuration fragments
   and hooks from loading. That acquisition writes the private evidence
   directory and performs network reads; it is not a literally read-only
   process.
6. Repository contents are mutable. The versions observed in this note are not
   durable pins. An apply approval must bind the exact authenticated metadata,
   complete dependency closure, downloaded package hashes, key bytes, source
   bytes, pre-state, and rollback plan used by that run.

## Official facts

### Supported platform and official repository flow

Docker's current Ubuntu installation page lists Ubuntu Resolute 26.04 LTS,
supports `x86_64`/`amd64`, and documents these repository fields:

```text
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: ${UBUNTU_CODENAME:-$VERSION_CODENAME}
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
```

On the required host tuple, the resolved, canonical file bytes are exactly:

```text
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: resolute
Components: stable
Architectures: amd64
Signed-By: /etc/apt/keyrings/docker.asc
```

The file has one LF after every line, including the last line; it is 155 bytes
and has SHA256
`47be0f749c19273936c7e56fff5a29b9108bcce8137ee677cc736523fb876e71`.
This digest is derived from the resolved official template and is a proposed
project constant.

Docker tells the operator to create `/etc/apt/keyrings` mode `0755`, download
`https://download.docker.com/linux/ubuntu/gpg` as `docker.asc`, make it readable,
write the source, and then run `apt update`. Docker recommends its apt
repository for installation and recommends the convenience script only for
testing and development. The project should not use that script.

### Key evidence

The official Docker key object observed on 2026-08-06 was:

| Property | Observed value |
| --- | --- |
| URL | `https://download.docker.com/linux/ubuntu/gpg` |
| Length | `3817` bytes |
| ASCII-object SHA256 | `1500c1f56fa9e26b9b8f42452a553675796ade0807cdce11975eb98170b3a570` |
| Primary fingerprint | `9DC858229FC7DD38854AE2D88D81803C0EBFCD88` |
| Primary key | RSA 4096, created 2017-02-22, capabilities `SCEA` |
| UID | `Docker Release (CE deb) <docker@docker.com>` |
| Signing-subkey fingerprint | `D3306A018370199E527AE7997EA0A9C3F273FCD8` |

The fingerprint, UID, size, and digest were mechanically derived from the
official key bytes. Docker's page calls the object its official GPG key but
does not separately print its fingerprint or SHA256. Ubuntu's `apt-secure`
manual explains why a repository publisher should publish a fingerprint and
why securely acquiring the key is part of the trust chain. The absence of an
upstream SHA256 means approval must retain the reviewed bytes themselves and
their locally computed digest. A future byte change requires a new review even
if the primary fingerprint remains unchanged, because legitimate key
certification or subkey material can also change the object digest.

The repository `InRelease` observed at 2026-08-06 11:01:36 UTC declared
`Origin: Docker`, `Label: Docker CE`, `Suite: resolute`, the expected
architectures/components, and SHA256
`b1eb09d9a96f1e08f0fce06fd7cfab1d265fba3147d013c66016c6549d2c97b5`
for the 26,737-byte `stable/binary-amd64/Packages.gz`. `gpgv` validated the
`InRelease` signature under the signing subkey above and reported the expected
primary fingerprint. These are timestamped observations, not permanent pins.

### Bootstrap package observations

Ubuntu's official package pages showed these current Resolute `amd64` package
artifacts on 2026-08-06:

| Package | Version | Architecture | Artifact SHA256 |
| --- | --- | --- | --- |
| `ca-certificates` | `20260601~26.04.1` | `all` | `6077d27c6b6f8b23590cb01ff877ed8c804a67a5442cc32b5a33da10d2bd0e90` |
| `curl` | `8.18.0-1ubuntu2.3` | `amd64` | `f2a58bae98e7de882894357ca729339aa19e8a53f0fbd9614b7b4a556cd323d8` |

`ca-certificates` depends on `debconf` or the `debconf-2.0` virtual package and
on `openssl >= 1.1.1`. The `amd64` curl package depends on exactly the matching
`libcurl4t64` version, plus `libc6 >= 2.34` and `zlib1g >= 1:1.1.4`. Therefore
pinning only the two command-line package names is insufficient: the approved
plan must bind every installed, upgraded, downgraded, configured, or removed
package in the solver result.

Do not force an upgrade merely to match this table. An already-installed,
correctly configured package is a no-op input whose exact version is recorded.
If either package is absent, the candidate and dependency closure must be
resolved again from authenticated metadata immediately before approval.

### Docker conflicts and Stage 50 package set

Docker's official Ubuntu page requires removal of these distribution packages
before Docker Engine installation:

```text
docker.io
docker-compose
docker-compose-v2
docker-doc
docker-buildx
podman-docker
containerd
runc
```

Inventory all eight by package status, version, architecture, selection, and
hold state. Presence is a Stage 10 stop/report result, not permission to remove
anything. Docker also notes that removing its packages does not automatically
remove `/var/lib/docker` images, containers, volumes, or networks.

Docker documents the Stage 50 install set as `docker-ce`, `docker-ce-cli`,
`containerd.io`, `docker-buildx-plugin`, and `docker-compose-plugin`. On
2026-08-06, the signed Resolute/amd64 index exposed these newest stanzas:

| Package | Observed version | Artifact SHA256 |
| --- | --- | --- |
| `docker-ce` | `5:29.7.2-1~ubuntu.26.04~resolute` | `d12027201c0a10959a7146365423b8de0296686c82f3b5f8ebf29375a479b3e7` |
| `docker-ce-cli` | `5:29.7.2-1~ubuntu.26.04~resolute` | `5967eae97b2c9c4b90ef28c271cbcaf2f0ca64d53ef2a69645423fda45b75885` |
| `containerd.io` | `2.3.3-1~ubuntu.26.04~resolute` | `6bf5d8ce94adb0876403753281a293b5af7ebeca51d6fac147d016fa40c0ebe5` |
| `docker-buildx-plugin` | `0.36.1-1~ubuntu.26.04~resolute` | `27aeedeca57b58a5da1081a934ff03a44d6f00dc10d14f5c299d80596ab58c94` |
| `docker-compose-plugin` | `5.4.0-1~ubuntu.26.04~resolute` | `e7417bef65f7c76b3ebf0fc2ac5abacc9cb786c33102b8b23402297e9d0f8796` |

This table proves publication, not project approval or mutual compatibility.
Stage 50 must re-resolve and approve a coherent exact set. In particular,
Docker's example pins the Engine and CLI to one version string but leaves the
other three package names unversioned; that example is not strict enough for
this project's apply contract.

## Proposed Stage 10 design

Everything in this section is project policy derived from the official facts,
not an upstream Docker or Ubuntu procedure.

### 1. Stable read-only inventory

Under the operation-wide journal lock, collect and normalize:

- `/etc/os-release`: require `ID=ubuntu`, `VERSION_ID=26.04`, and
  `VERSION_CODENAME` or `UBUNTU_CODENAME` equal to `resolute`;
- `dpkg --print-architecture`: require exactly `amd64`;
- installed package status with a fixed `dpkg-query -W` format for
  `ca-certificates`, `curl`, the eight conflict names, `ubuntu-keyring`, and
  every later planned dependency;
- selections, holds, and manual/auto marks, including bounded canonical
  `apt-mark showhold`, `apt-mark showmanual`, and `apt-mark showauto` results,
  plus a stable read of `/var/lib/apt/extended_states` when present;
- existing `/etc/apt/keyrings`, `docker.asc`, `docker.sources`, legacy Docker
  `.list` files, and any Docker repository stanza anywhere under apt sources;
- ownership, mode, type, link count, device/inode, size, and content hash for
  every relevant source/key file, using no-follow descriptors and stable
  pre/post-read metadata; and
- installed curl ownership (`dpkg-query`/`dpkg -S`) and `curl --version` only
  after the package/file relationship is established.

Do not dump the complete apt configuration: proxy URLs may contain secrets.
Reject symlinks, hard links, wrong owners, writable parent paths, duplicate
Docker source definitions, foreign bytes at either final path, broken dpkg
state, or any installed/selected/held conflict. Exact pre-existing canonical
Docker files may be adopted as no-op state only after the same validation.

### 2. Isolated authenticated metadata acquisition

`apt-get update` writes package lists; running it normally changes
`/var/lib/apt/lists`. Instead create a random root-owned acquisition directory
on the same trusted filesystem and give APT a dedicated `APT_CONFIG`. Its
parents must be no-link fixed paths. During acquisition the root is mode
`0711`, non-secret source/key inputs are root-owned mode `0644`, and only the
required list/archive partial directories are writable by APT's `_apt` sandbox.
This satisfies the `sources.list(5)` requirement that a `Signed-By` key be
readable by `_apt` without falling back to unsandboxed root downloads. After
APT exits and descriptors are closed, make the evidence tree root-only mode
`0700` before retaining it.

The config is loaded first and must point `Dir::Etc::Parts` and
`Dir::Etc::Main` at nonexistent paths, which prevents host fragments and their
invoke hooks from loading. It must also set:

- `Dir::Etc::SourceList` to the one reviewed temporary `.sources` file;
- `Dir::Etc::SourceParts` and both preferences paths to nonexistent paths;
- `Dir::State::Lists` and both cache files/directories inside the private
  evidence directory; and
- `APT::Get::List-Cleanup "false"` so the captured indexes remain evidence.

With `$EVIDENCE` replaced by one validated absolute path before the file is
approved, the configuration shape is:

```text
Dir::Etc::Parts "/nonexistent/kitdev-apt-parts";
Dir::Etc::Main "/nonexistent/kitdev-apt-main";
Dir::Etc::SourceList "$EVIDENCE/input/isolated.sources";
Dir::Etc::SourceParts "/nonexistent/kitdev-source-parts";
Dir::Etc::Preferences "/nonexistent/kitdev-preferences";
Dir::Etc::PreferencesParts "/nonexistent/kitdev-preferences-parts";
Dir::State::Lists "$EVIDENCE/lists";
Dir::Cache::pkgcache "";
Dir::Cache::srcpkgcache "";
Dir::Cache::archives "$EVIDENCE/archives";
APT::Get::List-Cleanup "false";
APT::Get::AllowUnauthenticated "false";
Acquire::AllowInsecureRepositories "false";
Acquire::AllowDowngradeToInsecureRepositories "false";
```

The literal `$EVIDENCE` placeholder above is explanatory; APT receives an
absolute, shell-free path. With a minimal fixed environment and no unreviewed
proxy variables, the relevant commands are:

```text
APT_CONFIG=/absolute/evidence/input/apt.conf apt-get update
APT_CONFIG=/absolute/evidence/input/apt.conf apt-get indextargets
APT_CONFIG=/absolute/evidence/input/apt.conf apt-cache policy PACKAGE...
APT_CONFIG=/absolute/evidence/input/apt.conf apt-get --simulate \
  --no-install-recommends install PACKAGE:amd64=VERSION...
```

`ca-certificates` is architecture `all`, so omit `:amd64` for it. Capture
stdout/stderr and exit status separately with fixed locale and size bounds; do
not treat human-formatted solver output as the approval object. The
implementation must parse and re-encode the result into its own bounded typed
schema.

Use two acquisitions:

1. Official Ubuntu Resolute base, updates, and security `main` stanzas, limited
   to `amd64` and authenticated by the already installed
   `/usr/share/keyrings/ubuntu-archive-keyring.gpg`, resolve only the bootstrap
   transaction.
2. A combined Ubuntu plus Docker view resolves future Stage 50 candidates. Its
   temporary Docker stanza references the private reviewed key path rather
   than `/etc/apt/keyrings/docker.asc`; all other repository fields equal the
   final canonical source. This temporary source is evidence only.

Never set `Trusted: yes`, `allow-insecure=yes`,
`Acquire::AllowInsecureRepositories`, or
`Acquire::AllowDowngradeToInsecureRepositories`. APT's official security
manual says unsigned repositories are refused by default and describes the
signed `Release` -> `Packages` -> package-checksum chain. Preserve that chain.

If authenticated Ubuntu metadata cannot be refreshed because the trust
keyring or TLS bootstrap is unusable, stop. Do not solve a missing trust
bootstrap by disabling verification.

The defensible claim for this flow is **no semantic host apt/dpkg mutation**,
not byte-for-byte immobility of every inode: reads can update access times, and
network activity changes external counters. Before and after acquisition,
compare a bounded stable inventory of names, types, owners, modes, links,
sizes, mtimes, ctimes, and content hashes under `/etc/apt`,
`/var/lib/apt/lists`, `/var/cache/apt`, and `/var/log/apt`, plus
`/var/lib/dpkg/status` and `/var/lib/apt/extended_states`. Exclude atime from
the equality contract but record that reads occurred. Also prove that the
effective `APT_CONFIG`, source, key, lists, caches, archives, lock, and log
paths used by the isolated commands all resolve under the validated evidence
root, except the intentional read-only Ubuntu archive keyring and real dpkg
status/extended-state inputs. Any new name, content/size/mtime/ctime change, or
write outside the evidence root fails the resolution experiment.

### 3. Exact solver and artifact closure

The Ubuntu `apt-get` manual defines `--simulate`/`--no-act` as no action and
warns that simulation disables locking and can be distorted when a non-root
user cannot read configuration. Run it as root while holding the project lock,
against the isolated lists and the real read-only dpkg status.

For an absent bootstrap package:

1. Read its candidate with `apt-cache policy` against the isolated snapshot.
2. Simulate `apt-get --simulate --no-install-recommends install` with
   `ca-certificates=<exact>` and/or `curl=<exact>`.
3. Parse the machine-bounded solver result; reject every removal, downgrade,
   held-package change, architecture other than `amd64`/`all`, non-Ubuntu
   origin, recommendation-only addition, or unresolved package.
4. Expand every `Inst` action into `binary-package:architecture=version` and
   simulate again with the whole closure explicitly versioned. The two action
   sets must match exactly.
5. Download every closure `.deb` into the private evidence directory without
   installation. Match package, version, architecture, filename, length, and
   SHA256 to the authenticated `Packages` record; then stable-read and hash the
   local artifact.
6. Build a private, canonical proposed post-state from copies of the stable
   pre-state status and extended-state inputs plus the exact forward actions.
   Bind both its bytes and the deterministic transformation code. Point a
   separate isolated resolver at those private inputs and produce the reverse
   simulation. If it cannot restore package presence, version, selection,
   hold, and manual/auto marks without touching a foreign package, or if the
   proposed post-state cannot faithfully model dpkg/apt state, Stage 10 apply
   remains blocked.

Because simulation does not lock dpkg, re-read the package, selection, hold,
manual/auto mark, and stable status/extended-state fingerprints immediately
after simulation and immediately before apply. Any change invalidates the
plan.

### 4. Approval object

One immutable Stage 10 approval should bind:

- operation ID, host identity, lifecycle tuple, journal before-state, and
  implementation/bundle hash;
- canonical normalized installed-package, selection, hold, manual/auto mark,
  source, keyring, extended-state, and path-metadata pre-state hashes;
- SHA256 of the isolated APT config and every source definition;
- complete `InRelease` bytes/hash, signature status, primary and signing-subkey
  fingerprints, release date/suite/origin/label, and every acquired index
  filename, length, and authenticated digest;
- exact bootstrap no-op state or the complete forward and reverse solver action
  sets;
- exact private proposed post-state status and extended-state bytes/hashes,
  plus the identifier and hash of the deterministic pre-state-to-post-state
  transformation;
- package/version/architecture/origin/filename/length/repository SHA256 and
  downloaded-artifact SHA256 for every package in either transaction;
- the 3817 approved `docker.asc` bytes, its SHA256, fingerprint set, UID, and
  stable local artifact metadata;
- the exact 155 approved `docker.sources` bytes and SHA256;
- planned final owner/group/mode/link count (`root:root`, regular file,
  `0644`, one link) and exact parent directory contract; and
- every ordered write-ahead transition, durability barrier, postcondition,
  rollback action, and failure stop.

Approval is for bytes, not URLs or the word `latest`. Apply must consume the
approved local artifacts; it must not redownload the key, source, metadata, or
packages. A changed key object, repository signature, package index, solver
closure, host package state, conflict inventory, or path state requires a new
plan and approval.

### 5. Apply and rollback boundary

The safest first-host outcome is expected to be a bootstrap no-op because
Ubuntu Server normally already has the two packages. When no package mutation
is needed, Stage 10 can journal and atomically publish only absent canonical
key/source files through validated parent descriptors, fsync each file and
directory, and verify exact bytes and metadata. It should not run a normal host
`apt-get update`; the authenticated isolated snapshot is the evidence, and
Stage 50 will require its own fresh resolution and approval.

If Stage 10 creates `/etc/apt/keyrings`, it must hold a validated descriptor for
`/etc/apt`, fsync `/etc/apt/keyrings` after publishing the key, and fsync
`/etc/apt` after the directory entry is created. Publishing
`docker.sources` similarly requires fsync of `/etc/apt/sources.list.d`.

Rollback removes a Stage 10 file only when the journal proves Stage 10 created
it and its bytes, metadata, inode binding, and link count remain exact. Remove
`docker.sources` before `docker.asc`, fsync both parent directories, and remove
`/etc/apt/keyrings` only if Stage 10 created it and it is empty. After that
`rmdir`, fsync `/etc/apt` to durably commit removal of the directory entry.
Never overwrite or restore a foreign pre-existing file.

If bootstrap installation is required, treat it as a separately approved
sub-operation. Retain all forward/reverse `.deb` artifacts and hashes. Reverse
only the exact package closure introduced by the forward transaction and
restore recorded selections, holds, and auto/manual marks. Abort rollback if a
package is now used, upgraded, replaced, held, or otherwise diverged. Package
maintainer scripts cannot provide byte-for-byte filesystem rollback, so the
authoritative recovery for an ambiguous or failed package transaction remains
reinstalling the disposable OVH Ubuntu 26.04 host.

## Stop conditions

Stage 10 must remain blocked if any of these is true:

- the platform is not exactly Ubuntu 26.04 Resolute `amd64`;
- `ubuntu-keyring` or authenticated Ubuntu metadata is unavailable;
- an apt/dpkg transaction is incomplete, or package, selection, hold,
  manual/auto mark, status, or extended-state content changes during plan;
- an installed, selected, or held conflict package is found;
- Docker key fingerprint/UID, `InRelease` signature, suite, origin, component,
  architecture, or authenticated index digest is unexpected;
- an insecure/trusted override, third-party package origin, proxy secret in
  evidence, duplicate source, unsafe path, or foreign final file is detected;
- any forward or reverse package action is implicit, unversioned, removable,
  unavailable as a verified artifact, or broader than the approved closure;
- approval identifies a mutable URL rather than the exact consumed bytes; or
- the implementation would install/remove Docker packages or affect services,
  sockets, groups, containers, networking, firewall state, or Docker data.

## Primary sources

- [Docker Engine installation on Ubuntu](https://docs.docker.com/engine/install/ubuntu/)
- [Docker's official Ubuntu repository key](https://download.docker.com/linux/ubuntu/gpg)
- [Docker Resolute signed repository release](https://download.docker.com/linux/ubuntu/dists/resolute/InRelease)
- [Docker Resolute stable amd64 package index](https://download.docker.com/linux/ubuntu/dists/resolute/stable/binary-amd64/Packages.gz)
- [Ubuntu Resolute `ca-certificates` package](https://packages.ubuntu.com/resolute/ca-certificates)
- [Ubuntu Resolute `ca-certificates` amd64 download record](https://packages.ubuntu.com/resolute/amd64/ca-certificates/download)
- [Ubuntu Resolute `curl` package](https://packages.ubuntu.com/resolute/curl)
- [Ubuntu Resolute `curl` amd64 download record](https://packages.ubuntu.com/resolute/amd64/curl/download)
- [Ubuntu Resolute `apt-get(8)` manual](https://manpages.ubuntu.com/manpages/resolute/en/man8/apt-get.8.html)
- [Ubuntu Resolute `apt-cache(8)` manual](https://manpages.ubuntu.com/manpages/resolute/en/man8/apt-cache.8.html)
- [Ubuntu Resolute `apt.conf(5)` manual](https://manpages.ubuntu.com/manpages/resolute/en/man5/apt.conf.5.html)
- [Ubuntu Resolute `sources.list(5)` manual](https://manpages.ubuntu.com/manpages/resolute/en/man5/sources.list.5.html)
- [Ubuntu Resolute `apt-secure(8)` manual](https://manpages.ubuntu.com/manpages/resolute/en/man8/apt-secure.8.html)
- [Ubuntu Resolute `apt-mark(8)` manual](https://manpages.ubuntu.com/manpages/resolute/en/man8/apt-mark.8.html)
- [Ubuntu Resolute `dpkg-query(1)` manual](https://manpages.ubuntu.com/manpages/resolute/en/man1/dpkg-query.1.html)
- [Ubuntu Resolute archive release metadata](https://archive.ubuntu.com/ubuntu/dists/resolute/InRelease)

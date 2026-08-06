from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path

from kitdev_sandboxes.collectors import (
    AddressFamily,
    BindScope,
    CollectionStatus,
    FilesystemStat,
    Ownership,
    Probe,
    SystemdActiveState,
    SystemdUnitFileState,
    VerifiedInstallationOwnership,
    _command_text,
    _default_owned_read,
    _default_owned_stat,
    _default_read,
    _default_resolver_read,
    _systemd_active_state,
    _systemd_unit_file_state,
    _trusted_resolver_target,
    collect_linux_facts,
)
from kitdev_sandboxes.runner import CommandOutcome, CommandResult, StreamEvidence


def command_result(
    argv: tuple[str, ...],
    *,
    stdout: str = "",
    stderr: str = "",
    outcome: CommandOutcome = CommandOutcome.SUCCESS,
    stdout_truncated: bool = False,
    stderr_truncated: bool = False,
) -> CommandResult:
    return CommandResult(
        argv=argv,
        outcome=outcome,
        returncode=0 if outcome is CommandOutcome.SUCCESS else 1,
        termination_signal=None,
        timed_out=outcome is CommandOutcome.TIMEOUT,
        missing_executable=outcome is CommandOutcome.MISSING,
        permission_denied=outcome is CommandOutcome.PERMISSION_DENIED,
        stdout=StreamEvidence(stdout, len(stdout), 1 if stdout_truncated else 0, stdout_truncated),
        stderr=StreamEvidence(stderr, len(stderr), 1 if stderr_truncated else 0, stderr_truncated),
        duration_seconds=0.012,
    )


class FakeRunner:
    def __init__(self, overrides: dict[tuple[str, ...], CommandResult] | None = None) -> None:
        self.overrides = overrides or {}
        self.commands: list[object] = []

    def run(self, command: object) -> CommandResult:
        self.commands.append(command)
        argv = getattr(command, "argv")
        if argv in self.overrides:
            return self.overrides[argv]
        if argv[:2] == ("systemctl", "is-active"):
            active = argv[2] in {
                "docker.service",
                "display-manager.service",
                "gdm.service",
                "NetworkManager.service",
                "ufw.service",
                "apparmor.service",
                "kitdev-vllm.service",
            }
            return command_result(
                argv,
                stdout="active\n" if active else "inactive\n",
                outcome=CommandOutcome.SUCCESS if active else CommandOutcome.NONZERO,
            )
        if argv[:2] == ("systemctl", "is-enabled"):
            enabled = argv[2] in {
                "docker.service",
                "display-manager.service",
                "gdm.service",
                "NetworkManager.service",
                "ufw.service",
                "apparmor.service",
            }
            return command_result(
                argv,
                stdout="enabled\n" if enabled else "disabled\n",
                outcome=CommandOutcome.SUCCESS if enabled else CommandOutcome.NONZERO,
            )
        defaults = {
            ("docker", "version", "--format", "{{.Server.Version}}"): "29.2.1\n",
            ("docker", "compose", "version", "--short"): "5.0.2\n",
            ("ss", "-H", "-lntup", "-4"): (
                "tcp LISTEN 0 4096 *:3000 *:*\n"
                "tcp LISTEN 0 4096 127.0.0.1:5432 0.0.0.0:* "
                'users:(("postgres",pid=918,fd=7))\n'
                "udp UNCONN 0 0 192.0.2.20:5353 0.0.0.0:*\n"
            ),
            ("ss", "-H", "-lntup", "-6"): (
                "tcp LISTEN 0 4096 *:4000 [::]:*\n"
                "tcp LISTEN 0 4096 [::1]:5008 [::]:*\n"
            ),
            ("ip", "-details", "-j", "-4", "address", "show"): (
                '[{"ifname":"lo","flags":["UP"],"link_type":"loopback",'
                '"addr_info":[{"local":"127.0.0.1","prefixlen":8}]},'
                '{"ifname":"eth0","flags":["UP"],"link_type":"ether",'
                '"addr_info":[{"local":"192.0.2.20","prefixlen":24}]},'
                '{"ifname":"docker0","flags":["UP"],"link_type":"ether",'
                '"linkinfo":{"info_kind":"bridge"},'
                '"addr_info":[{"local":"172.18.0.1","prefixlen":16}]}]'
            ),
            ("ip", "-details", "-j", "-6", "address", "show"): (
                '[{"ifname":"lo","flags":["UP"],"link_type":"loopback",'
                '"addr_info":[{"local":"::1","prefixlen":128}]},'
                '{"ifname":"eth0","flags":["UP"],"link_type":"ether",'
                '"addr_info":[{"local":"2001:db8::20","prefixlen":64}]}]'
            ),
            ("ip", "-j", "-4", "route", "show", "table", "all"): (
                '[{"dst":"default","dev":"eth0"},{"dst":"172.18.0.0/16","dev":"docker0"}]'
            ),
            ("ip", "-j", "-6", "route", "show", "table", "all"): (
                '[{"dst":"default","dev":"eth0"},{"dst":"2001:db8::/64","dev":"eth0"}]'
            ),
            ("nft", "--version"): "nftables v1.1.1\n",
            ("nft", "--json", "list", "tables"): (
                '{"nftables":[{"table":{"family":"inet","name":"filter"}},'
                '{"table":{"family":"ip","name":"kitdev-vllm"}}]}'
            ),
            ("ufw", "--version"): "ufw 0.36.2\n",
            ("ufw", "status"): "Status: active\n",
            ("timedatectl", "show", "--property=NTPSynchronized", "--value"): "yes\n",
            ("findmnt", "--json", "--target", "/", "--output", "FSTYPE,OPTIONS"): (
                '{"filesystems":[{"fstype":"ext4","options":"rw,relatime"}]}'
            ),
        }
        if argv in defaults:
            return command_result(argv, stdout=defaults[argv])
        raise AssertionError(f"unexpected command: {argv!r}")


class FakeHost:
    def __init__(self) -> None:
        self.reads: dict[str, Probe[str]] = {
            "/proc/meminfo": Probe.ok(
                "MemTotal:       65536000 kB\n"
                "MemAvailable:   52428800 kB\n"
                "SwapTotal:             0 kB\n"
                "SwapFree:              0 kB\n"
                "HugePages_Total:       16\n"
                "HugePages_Free:        12\n"
                "HugePages_Rsvd:         2\n"
                "HugePages_Surp:         0\n"
                "Hugepagesize:        2048 kB\n",
                source="/proc/meminfo",
            ),
            "/proc/self/mounts": Probe.ok(
                "none /dev/hugepages hugetlbfs rw,relatime 0 0\n",
                source="/proc/self/mounts",
            ),
            "/proc/modules": Probe.ok(
                "kvm_amd 204800 0 - Live 0x0\nkvm 1409024 1 kvm_amd, Live 0x0\n"
                "nbd 65536 1 - Live 0x0\n",
                source="/proc/modules",
            ),
            "/sys/module/nbd/parameters/nbds_max": Probe.ok("64\n", source="nbds_max"),
            "/sys/module/nbd/parameters/max_part": Probe.ok("16\n", source="max_part"),
            "/sys/block/nbd0/pid": Probe.ok("4242\n", source="nbd0.pid"),
            "/sys/block/nbd1/pid": Probe.degraded(
                CollectionStatus.PERMISSION_DENIED, source="nbd1.pid"
            ),
            "/etc/resolv.conf": Probe.ok(
                "nameserver 127.0.0.53 # local stub\n"
                "nameserver 2001:4860:4860::8888\n"
                "search lab.example.invalid\n",
                source="/etc/resolv.conf",
            ),
            "/proc/sys/net/ipv4/ip_forward": Probe.ok("1\n", source="ipv4.forward"),
            "/proc/sys/net/ipv6/conf/all/forwarding": Probe.ok("0\n", source="ipv6.forward"),
            "/sys/module/apparmor/parameters/enabled": Probe.ok("Y\n", source="apparmor"),
            "/opt/kitdev-sandboxes/VERSION": Probe.ok("0.1.0\n", source="version"),
        }
        self.regular_paths = {
            "/",
            "/etc/kitdev-sandboxes/config.yaml",
            "/opt/kitdev-sandboxes/VERSION",
            "/repo/versions.lock.yaml",
            "/var/lib/kitdev-sandboxes/templates/base/manifest.json",
            "/var/lib/kitdev-sandboxes/templates/coding/manifest.json",
        }
        self.stat_calls: list[str] = []
        self.special_modes: dict[str, int] = {}

    def read(self, path: Path, _maximum_bytes: int) -> Probe[str]:
        return self.reads.get(
            str(path), Probe.degraded(CollectionStatus.ABSENT, source=str(path))
        )

    def stat(self, path: Path) -> Probe[os.stat_result]:
        path_text = str(path)
        self.stat_calls.append(path_text)
        if path_text in self.special_modes:
            return Probe.ok(
                os.stat_result(
                    (self.special_modes[path_text], 0, 0, 0, 0, 0, 0, 0, 0, 0)
                ),
                source=path_text,
            )
        if path_text == "/dev/net/tun":
            mode = stat.S_IFCHR | 0o666
            return Probe.ok(os.stat_result((mode, 0, 0, 0, 0, 0, 0, 0, 0, 0)), source=path_text)
        if path_text in self.regular_paths:
            mode = stat.S_IFDIR | 0o755 if path_text == "/" else stat.S_IFREG | 0o644
            return Probe.ok(os.stat_result((mode, 0, 0, 0, 0, 0, 0, 0, 0, 0)), source=path_text)
        return Probe.degraded(CollectionStatus.ABSENT, source=path_text)

    @staticmethod
    def filesystem(path: Path) -> Probe[FilesystemStat]:
        return Probe.ok(
            FilesystemStat(2_000_000_000_000, 800_000_000_000, 100_000_000, 90_000_000),
            source=str(path),
        )

    @staticmethod
    def glob(pattern: str) -> Probe[tuple[Path, ...]]:
        if pattern == "/sys/block/nbd*":
            return Probe.ok((Path("/sys/block/nbd0"), Path("/sys/block/nbd1")), source=pattern)
        raise AssertionError(pattern)


def collect_fixture(
    *,
    runner: FakeRunner | None = None,
    host: FakeHost | None = None,
    verified_ownership: VerifiedInstallationOwnership | None = None,
):
    selected_host = host or FakeHost()
    selected_runner = runner or FakeRunner()
    facts = collect_linux_facts(
        configured_paths=(
            Path("/opt/kitdev-sandboxes"),
            Path("/var/lib/kitdev-sandboxes"),
        ),
        runner=selected_runner,  # type: ignore[arg-type]
        project_root=Path("/repo"),
        read_text=selected_host.read,
        stat_path=selected_host.stat,
        stat_filesystem=selected_host.filesystem,
        glob_paths=selected_host.glob,
        verified_ownership=verified_ownership,
    )
    return facts, selected_runner, selected_host


class CollectorTests(unittest.TestCase):
    def test_shared_pc_fixture_normalizes_all_required_fact_groups(self) -> None:
        facts, runner, host = collect_fixture()

        self.assertEqual(facts.devices.kvm_modules.value, ("kvm", "kvm_amd"))
        self.assertTrue(facts.devices.nbd.module_loaded.value)
        self.assertEqual(facts.devices.nbd.max_devices.value, 64)
        self.assertEqual(facts.devices.nbd.max_partitions.value, 16)
        assert facts.devices.nbd.devices.value is not None
        self.assertTrue(facts.devices.nbd.devices.value[0].in_use.value)
        self.assertIs(
            facts.devices.nbd.devices.value[1].in_use.status,
            CollectionStatus.PERMISSION_DENIED,
        )
        self.assertEqual(facts.devices.huge_pages.size_kib.value, 2048)
        self.assertEqual(facts.devices.huge_pages.mounts.value, ("/dev/hugepages",))
        self.assertTrue(facts.devices.tun_exists.value)
        self.assertTrue(facts.devices.tun_is_character_device.value)

        self.assertEqual(facts.memory.total_bytes.value, 65_536_000 * 1024)
        self.assertEqual(facts.memory.swap_total_bytes.value, 0)
        self.assertEqual(len(facts.filesystems), 2)
        self.assertTrue(all(item.containing_path == "/" for item in facts.filesystems))
        self.assertTrue(all(item.filesystem_type.value == "ext4" for item in facts.filesystems))
        self.assertNotIn("/opt/kitdev-sandboxes", host.regular_paths)

        self.assertEqual(facts.docker.version.value, "29.2.1")
        self.assertTrue(facts.docker.active.value)
        self.assertEqual(facts.compose.version.value, "5.0.2")
        self.assertEqual(
            {listener.port for listener in facts.network.listeners.value or ()},
            {3000, 4000, 5008, 5432, 5353},
        )
        postgres = next(
            listener for listener in facts.network.listeners.value or () if listener.port == 5432
        )
        self.assertEqual(postgres.owner, "postgres")
        self.assertNotIn("918", repr(postgres))
        ipv6_wildcard = next(
            listener for listener in facts.network.listeners.value or () if listener.port == 4000
        )
        self.assertIs(ipv6_wildcard.family, AddressFamily.IPV6)
        self.assertIs(ipv6_wildcard.bind_scope, BindScope.WILDCARD)

        self.assertEqual(
            facts.network.dns.value.resolvers if facts.network.dns.value else (),
            ("127.0.0.53", "2001:4860:4860::8888"),
        )
        self.assertTrue(facts.network.ipv4_forwarding.value)
        self.assertFalse(facts.network.ipv6_forwarding.value)
        self.assertIn(
            "2001:db8::/64", {route.destination for route in facts.network.routes.value or ()}
        )
        self.assertIn(
            "192.0.2.0/24",
            {
                network
                for interface in facts.network.interfaces.value or ()
                for network in interface.networks
            },
        )
        docker_bridge = next(
            interface
            for interface in facts.network.interfaces.value or ()
            if interface.name == "docker0"
        )
        self.assertEqual(docker_bridge.kind, "bridge")

        self.assertTrue(facts.firewall.nftables.present.value)
        self.assertTrue(facts.firewall.nftables.active.value)
        self.assertEqual(
            facts.firewall.nftables_tables.value, ("inet:filter", "ip:kitdev-vllm")
        )
        self.assertTrue(facts.firewall.ufw.active.value)
        self.assertTrue(facts.security.apparmor_enabled.value)
        self.assertTrue(facts.security.time_synchronized.value)
        vllm = next(
            service
            for service in facts.conflicting_services
            if service.name == "kitdev-vllm.service"
        )
        self.assertIs(vllm.active.value, SystemdActiveState.ACTIVE)
        self.assertIs(vllm.ownership, Ownership.UNKNOWN)
        docker_service = next(
            service
            for service in facts.conflicting_services
            if service.name == "docker.service"
        )
        self.assertIs(docker_service.ownership, Ownership.SHARED)

        self.assertEqual(
            facts.installed.markers.value,
            ("/etc/kitdev-sandboxes/config.yaml", "/opt/kitdev-sandboxes/VERSION"),
        )
        self.assertEqual(facts.installed.installed_version.value, "0.1.0")
        self.assertTrue(facts.installed.upstream_lock_present.value)
        self.assertTrue(facts.installed.templates["base"].value)
        self.assertFalse(facts.installed.templates["desktop"].value)
        self.assertTrue(
            all(
                service.ownership is Ownership.UNKNOWN
                for service in facts.installed.owned_services
            )
        )
        self.assertTrue(
            all(getattr(command, "argv", (None,))[0] != "sudo" for command in runner.commands)
        )
        self.assertTrue(
            all(getattr(command, "cwd", None) == Path("/") for command in runner.commands)
        )

    def test_timeout_truncation_permission_and_absence_are_not_guessed(self) -> None:
        host = FakeHost()
        host.reads["/proc/meminfo"] = Probe.degraded(
            CollectionStatus.PERMISSION_DENIED, source="/proc/meminfo"
        )
        timeout_argv = ("ss", "-H", "-lntup", "-4")
        docker_argv = ("docker", "version", "--format", "{{.Server.Version}}")
        nft_argv = ("nft", "--json", "list", "tables")
        runner = FakeRunner(
            {
                timeout_argv: command_result(timeout_argv, outcome=CommandOutcome.TIMEOUT),
                docker_argv: command_result(
                    docker_argv, stdout="29.2", stdout_truncated=True
                ),
                nft_argv: command_result(
                    nft_argv, outcome=CommandOutcome.PERMISSION_DENIED
                ),
            }
        )

        facts, _runner, _host = collect_fixture(runner=runner, host=host)

        self.assertIs(facts.memory.total_bytes.status, CollectionStatus.PERMISSION_DENIED)
        self.assertIs(facts.network.listeners.status, CollectionStatus.TIMEOUT)
        self.assertIsNone(facts.network.listeners.value)
        self.assertIs(facts.docker.version.status, CollectionStatus.TRUNCATED)
        self.assertIsNone(facts.docker.present.value)
        self.assertIs(facts.firewall.nftables_tables.status, CollectionStatus.PERMISSION_DENIED)
        self.assertIs(facts.firewall.nftables.active.status, CollectionStatus.PERMISSION_DENIED)
        self.assertIsNone(facts.firewall.nftables.active.value)

    def test_missing_paths_use_existing_ancestor_without_creation(self) -> None:
        facts, _runner, host = collect_fixture()

        first = facts.filesystems[0]
        self.assertEqual(first.configured_path, "/opt/kitdev-sandboxes")
        self.assertEqual(first.containing_path, "/")
        start = host.stat_calls.index("/opt/kitdev-sandboxes")
        self.assertEqual(host.stat_calls[start : start + 3], ["/opt/kitdev-sandboxes", "/opt", "/"])

    def test_ipv4_or_ipv6_parse_failure_is_explicit(self) -> None:
        argv = ("ip", "-j", "-6", "route", "show", "table", "all")
        for output in ("not-json", '{"routes": []}'):
            with self.subTest(output=output):
                runner = FakeRunner({argv: command_result(argv, stdout=output)})
                facts, _runner, _host = collect_fixture(runner=runner)

                self.assertIs(facts.network.interfaces.status, CollectionStatus.ERROR)
                self.assertIs(facts.network.routes.status, CollectionStatus.ERROR)
                self.assertIsNone(facts.network.routes.value)

    def test_command_evidence_is_bounded_redacted_and_does_not_include_stderr(self) -> None:
        argv = ("example", "--fixed")
        runner = FakeRunner(
            {
                argv: command_result(
                    argv,
                    stdout="token=raw-secret /Users/operator/private\n",
                    stderr="password=stderr-secret argv --private-key raw\n",
                )
            }
        )

        result = _command_text(runner, argv, "example.fixed")

        self.assertIs(result.status, CollectionStatus.OK)
        self.assertNotIn("raw-secret", result.raw or "")
        self.assertNotIn("/Users/operator", result.raw or "")
        self.assertNotIn("stderr-secret", result.raw or "")
        self.assertEqual(result.source, "example.fixed")
        self.assertEqual(result.elapsed_ms, 12)

    def test_evidence_limit_is_utf8_bytes_not_characters(self) -> None:
        argv = ("example", "--unicode")
        runner = FakeRunner({argv: command_result(argv, stdout="e\u0301" * 600)})

        result = _command_text(runner, argv, "example.unicode")

        self.assertLessEqual(len((result.raw or "").encode("utf-8")), 512)

    def test_normalized_tool_versions_do_not_retain_secret_shaped_output(self) -> None:
        argv = ("docker", "version", "--format", "{{.Server.Version}}")
        runner = FakeRunner(
            {argv: command_result(argv, stdout="29.2 token=collector-secret\n")}
        )

        facts, _runner, _host = collect_fixture(runner=runner)

        self.assertNotIn("collector-secret", facts.docker.version.value or "")
        self.assertIn("[REDACTED]", facts.docker.version.value or "")

    def test_default_read_is_bounded_nonblocking_and_rejects_special_files(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temporary:
            root = Path(temporary)
            regular = root / "regular"
            regular.write_text("bounded", encoding="utf-8")
            self.assertEqual(_default_read(regular, 32).value, "bounded")

            oversized = root / "oversized"
            oversized.write_bytes(b"x" * 33)
            self.assertIs(_default_read(oversized, 32).status, CollectionStatus.TRUNCATED)

            fifo = root / "fifo"
            os.mkfifo(fifo)
            self.assertIs(_default_read(fifo, 32).status, CollectionStatus.ERROR)

            directory = root / "directory"
            directory.mkdir()
            self.assertIs(_default_read(directory, 32).status, CollectionStatus.ERROR)

            symlink = root / "symlink"
            symlink.symlink_to(regular)
            self.assertIs(_default_read(symlink, 32).status, CollectionStatus.ERROR)

    def test_owned_read_rejects_a_symlinked_parent(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temporary:
            root = Path(temporary)
            real = root / "real"
            real.mkdir()
            (real / "VERSION").write_text("0.1.0", encoding="utf-8")
            linked = root / "linked"
            linked.symlink_to(real, target_is_directory=True)

            result = _default_owned_read(linked / "VERSION", 4_096)
            stat_result = _default_owned_stat(linked / "VERSION")

            self.assertIs(result.status, CollectionStatus.ERROR)
            self.assertIsNone(result.value)
            self.assertIs(stat_result.status, CollectionStatus.ERROR)

    def test_resolver_symlink_policy_is_explicit_and_rejects_untrusted_targets(self) -> None:
        self.assertEqual(
            _trusted_resolver_target(
                Path("/etc/resolv.conf"), "../run/systemd/resolve/stub-resolv.conf"
            ),
            Path("/run/systemd/resolve/stub-resolv.conf"),
        )
        self.assertIsNone(
            _trusted_resolver_target(Path("/etc/resolv.conf"), "/tmp/operator-resolv.conf")
        )
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temporary:
            root = Path(temporary)
            target = root / "target"
            target.write_text("nameserver 192.0.2.53\n", encoding="utf-8")
            link = root / "resolv.conf"
            link.symlink_to(target)

            result = _default_resolver_read(link, 65_536)

            self.assertIs(result.status, CollectionStatus.ERROR)
            self.assertIsNone(result.value)

    def test_marker_template_and_lock_types_are_not_treated_as_present(self) -> None:
        host = FakeHost()
        host.special_modes["/etc/kitdev-sandboxes/config.yaml"] = stat.S_IFLNK | 0o777
        host.special_modes[
            "/var/lib/kitdev-sandboxes/templates/base/manifest.json"
        ] = stat.S_IFDIR | 0o755
        host.special_modes["/repo/versions.lock.yaml"] = stat.S_IFIFO | 0o600

        facts, _runner, _host = collect_fixture(host=host)

        self.assertIs(facts.installed.markers.status, CollectionStatus.ERROR)
        self.assertNotIn(
            "/etc/kitdev-sandboxes/config.yaml", facts.installed.markers.value or ()
        )
        self.assertIs(
            facts.installed.templates["base"].status, CollectionStatus.ERROR
        )
        self.assertIs(
            facts.installed.upstream_lock_present.status, CollectionStatus.ERROR
        )

    def test_unit_ownership_requires_verified_manifest_input(self) -> None:
        unit = "kitdev-e2b-api.service"

        unknown, _runner, _host = collect_fixture()
        verified, _runner, _host = collect_fixture(
            verified_ownership=VerifiedInstallationOwnership(
                "install-01234567",
                Path("/var/lib/kitdev-sandboxes/install-manifest.json"),
                frozenset({unit}),
            )
        )

        unknown_unit = next(
            service for service in unknown.installed.owned_services if service.name == unit
        )
        verified_unit = next(
            service for service in verified.installed.owned_services if service.name == unit
        )
        self.assertIs(unknown_unit.ownership, Ownership.UNKNOWN)
        self.assertIs(verified_unit.ownership, Ownership.PROJECT)

    def test_systemd_states_preserve_static_masked_not_found_and_failures(self) -> None:
        cases = {
            "static.service": ("static", CommandOutcome.SUCCESS, SystemdUnitFileState.STATIC),
            "masked.service": ("masked", CommandOutcome.NONZERO, SystemdUnitFileState.MASKED),
            "missing.service": (
                "not-found",
                CommandOutcome.NONZERO,
                SystemdUnitFileState.NOT_FOUND,
            ),
        }
        for unit, (stdout, outcome, expected) in cases.items():
            with self.subTest(unit=unit):
                argv = ("systemctl", "is-enabled", unit)
                runner = FakeRunner(
                    {argv: command_result(argv, stdout=f"{stdout}\n", outcome=outcome)}
                )
                result = _systemd_unit_file_state(runner, unit)
                self.assertIs(result.status, CollectionStatus.OK)
                self.assertIs(result.value, expected)

        argv = ("systemctl", "is-active", "timed-out.service")
        timeout_runner = FakeRunner(
            {
                argv: command_result(
                    argv, stdout="active\n", outcome=CommandOutcome.TIMEOUT
                )
            }
        )
        timed_out = _systemd_active_state(timeout_runner, "timed-out.service")
        self.assertIs(timed_out.status, CollectionStatus.TIMEOUT)
        self.assertIsNone(timed_out.value)

        truncated_runner = FakeRunner(
            {argv: command_result(argv, stdout="active\n", stdout_truncated=True)}
        )
        truncated = _systemd_active_state(truncated_runner, "timed-out.service")
        self.assertIs(truncated.status, CollectionStatus.TRUNCATED)
        self.assertIsNone(truncated.value)

        permission_runner = FakeRunner(
            {
                argv: command_result(
                    argv,
                    stdout="active\n",
                    outcome=CommandOutcome.PERMISSION_DENIED,
                )
            }
        )
        denied = _systemd_active_state(permission_runner, "timed-out.service")
        self.assertIs(denied.status, CollectionStatus.PERMISSION_DENIED)
        self.assertIsNone(denied.value)

    def test_malformed_ss_dns_and_nft_outputs_are_explicit_errors(self) -> None:
        ss_argv = ("ss", "-H", "-lntup", "-4")
        nft_argv = ("nft", "--json", "list", "tables")
        host = FakeHost()
        host.reads["/etc/resolv.conf"] = Probe.ok(
            "this is not resolver syntax\n", source="/etc/resolv.conf"
        )
        runner = FakeRunner(
            {
                ss_argv: command_result(ss_argv, stdout="not socket output\n"),
                nft_argv: command_result(nft_argv, stdout='{"wrong": []}'),
            }
        )

        facts, _runner, _host = collect_fixture(runner=runner, host=host)

        self.assertIs(facts.network.listeners.status, CollectionStatus.ERROR)
        self.assertIs(facts.network.dns.status, CollectionStatus.ERROR)
        self.assertIs(facts.firewall.nftables_tables.status, CollectionStatus.ERROR)
        self.assertIsNone(facts.firewall.nftables_tables.value)

    def test_mixed_valid_and_malformed_ss_output_is_rejected(self) -> None:
        argv = ("ss", "-H", "-lntup", "-4")
        runner = FakeRunner(
            {
                argv: command_result(
                    argv,
                    stdout="tcp LISTEN 0 4096 *:3000 *:*\nmalformed hidden listener\n",
                )
            }
        )

        facts, _runner, _host = collect_fixture(runner=runner)

        self.assertIs(facts.network.listeners.status, CollectionStatus.ERROR)
        self.assertIsNone(facts.network.listeners.value)

    def test_whitespace_only_module_lines_do_not_crash_nbd_collection(self) -> None:
        host = FakeHost()
        host.reads["/proc/modules"] = Probe.ok(
            "   \n\t\nkvm 1 0 - Live 0x0\n", source="/proc/modules"
        )

        facts, _runner, _host = collect_fixture(host=host)

        self.assertFalse(facts.devices.nbd.module_loaded.value)
        self.assertEqual(facts.devices.kvm_modules.value, ("kvm",))

    def test_null_findmnt_fields_are_errors_and_credentials_are_redacted(self) -> None:
        argv = ("findmnt", "--json", "--target", "/", "--output", "FSTYPE,OPTIONS")
        for field in ("fstype", "options"):
            with self.subTest(field=field):
                payload = (
                    '{"filesystems":[{"fstype":null,"options":"rw"}]}'
                    if field == "fstype"
                    else '{"filesystems":[{"fstype":"ext4","options":null}]}'
                )
                runner = FakeRunner({argv: command_result(argv, stdout=payload)})
                facts, _runner, _host = collect_fixture(runner=runner)
                self.assertIs(
                    facts.filesystems[0].filesystem_type.status, CollectionStatus.ERROR
                )
                self.assertIs(
                    facts.filesystems[0].mount_options.status, CollectionStatus.ERROR
                )

        secret = "cifs-password-value"
        private_path = "/private/operator/credentials"
        runner = FakeRunner(
            {
                argv: command_result(
                    argv,
                    stdout=(
                        '{"filesystems":[{"fstype":"cifs","options":'
                        f'"rw,password={secret},credentials={private_path},uid=501"}}]}}'
                    ),
                )
            }
        )
        facts, _runner, _host = collect_fixture(runner=runner)
        options = facts.filesystems[0].mount_options
        self.assertIs(options.status, CollectionStatus.OK)
        serialized = repr(options.value)
        self.assertNotIn(secret, serialized)
        self.assertNotIn(private_path, serialized)
        self.assertNotIn("501", serialized)
        self.assertIn("password=[REDACTED]", options.value or ())
        self.assertIn("credentials=[REDACTED]", options.value or ())

    def test_malformed_addr_info_entry_rejects_the_entire_interface_inventory(self) -> None:
        argv = ("ip", "-details", "-j", "-4", "address", "show")
        runner = FakeRunner(
            {
                argv: command_result(
                    argv,
                    stdout=(
                        '[{"ifname":"eth0","flags":["UP"],"addr_info":['
                        '{"local":"192.0.2.20","prefixlen":24},'
                        '{"local":null,"prefixlen":24}]}]'
                    ),
                )
            }
        )

        facts, _runner, _host = collect_fixture(runner=runner)

        self.assertIs(facts.network.interfaces.status, CollectionStatus.ERROR)
        self.assertIs(facts.network.routes.status, CollectionStatus.ERROR)

    def test_systemd_static_is_not_reported_as_boolean_enabled(self) -> None:
        argv = ("systemctl", "is-enabled", "kitdev-e2b-api.service")
        runner = FakeRunner(
            {argv: command_result(argv, stdout="static\n", outcome=CommandOutcome.SUCCESS)}
        )

        result = _systemd_unit_file_state(runner, "kitdev-e2b-api.service")

        self.assertIs(result.value, SystemdUnitFileState.STATIC)
        self.assertNotIsInstance(result.value, bool)

    def test_all_commands_are_fixed_argv_without_shell_metacharacters(self) -> None:
        _facts, runner, _host = collect_fixture()

        for command in runner.commands:
            argv = getattr(command, "argv")
            self.assertIsInstance(argv, tuple)
            self.assertNotIn("sh", argv[:1])
            self.assertNotIn("bash", argv[:1])
            self.assertFalse(any(";" in argument or "||" in argument for argument in argv))
            self.assertGreater(getattr(command, "timeout_seconds"), 0)
            self.assertGreater(getattr(command, "stdout_limit_bytes"), 0)


if __name__ == "__main__":
    unittest.main()

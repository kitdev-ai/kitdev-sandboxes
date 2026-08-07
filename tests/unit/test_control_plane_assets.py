from __future__ import annotations

import json
import os
import re
import subprocess
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
CONTROL_PLANE = ROOT / "compose" / "control-plane"
COMPOSE = CONTROL_PLANE / "compose.yaml"
IMAGE_LOCK = CONTROL_PLANE / "images.lock.json"
VERSIONS_LOCK = ROOT / "versions.lock.yaml"
SCRIPTS = ROOT / "scripts" / "control-plane"


def service_block(compose: str, name: str) -> str:
    match = re.search(
        rf"^  {re.escape(name)}:\n(.*?)(?=^  [a-z][a-z0-9-]*:\n|^networks:\n)",
        compose,
        re.MULTILINE | re.DOTALL,
    )
    if match is None:
        raise AssertionError(f"missing Compose service: {name}")
    return match.group(1)


class ControlPlaneAssetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.compose = COMPOSE.read_text(encoding="ascii")
        self.image_lock = json.loads(IMAGE_LOCK.read_text(encoding="ascii"))
        self.versions_lock = VERSIONS_LOCK.read_text(encoding="ascii")

    def test_runtime_images_are_exactly_digest_pinned(self) -> None:
        expected = {
            "postgres": (
                "docker.io/library/postgres:17.4@"
                "sha256:304ab813518754228f9f792f79d6da36359b82d8ecf418096c636725f8c930ad"
            ),
            "redis": (
                "docker.io/library/redis:7.4.6@"
                "sha256:a9cc41d6d01da2aa26c219e4f99ecbeead955a7b656c1c499cce8922311b2514"
            ),
            "clickhouse": (
                "docker.io/library/clickhouse:25.4.5.24@"
                "sha256:ad201eec325abb23e558e344d46d81bc9e2eba5a011fc02af440c124a27a1a61"
            ),
            "loki": (
                "docker.io/grafana/loki:3.4.1@"
                "sha256:1d0c5ddc7644b88956aa0bd775ad796d9635180258a225d6ab3552751d5e2a66"
            ),
        }
        locked = self.image_lock["runtime_images"]
        for service, reference in expected.items():
            with self.subTest(service=service):
                self.assertIn(f"image: {reference}", service_block(self.compose, service))
                self.assertEqual(locked[service]["reference"], reference)
                self.assertIn(reference.rsplit("@", maxsplit=1)[1], self.versions_lock)

    def test_locally_built_images_are_required_by_immutable_id(self) -> None:
        expected = {
            "api": "E2B_API_IMAGE_REF",
            "postgres-migrator": "E2B_DB_MIGRATOR_IMAGE_REF",
            "clickhouse-migrator": "E2B_CLICKHOUSE_MIGRATOR_IMAGE_REF",
            "client-proxy": "E2B_CLIENT_PROXY_IMAGE_REF",
        }
        for service, variable in expected.items():
            with self.subTest(service=service):
                block = service_block(self.compose, service)
                self.assertIn(f"image: ${{{variable}:?required}}", block)
                self.assertIn("pull_policy: never", block)
        for item in self.image_lock["source_images"].values():
            self.assertIsNone(item["content_digest"])
            self.assertEqual(item["status"], "required-before-apply")

    def test_only_the_reviewed_loopback_ports_are_published(self) -> None:
        expected = {
            "postgres": {(5432, 5432)},
            "clickhouse": {(8123, 8123), (9000, 9000)},
            "api": {(3000, 3000)},
            "client-proxy": {(3002, 3002), (3003, 3003)},
        }
        observed: dict[str, set[tuple[int, int]]] = {}
        for service in (
            "postgres",
            "redis",
            "clickhouse",
            "loki",
            "postgres-migrator",
            "clickhouse-migrator",
            "api",
            "client-proxy",
        ):
            block = service_block(self.compose, service)
            targets = [
                int(value) for value in re.findall(r"^        target: (\d+)$", block, re.MULTILINE)
            ]
            published = [
                int(value)
                for value in re.findall(r'^        published: "(\d+)"$', block, re.MULTILINE)
            ]
            self.assertEqual(len(targets), len(published))
            if targets:
                self.assertEqual(block.count("host_ip: 127.0.0.1"), len(targets))
                observed[service] = set(zip(targets, published, strict=True))
        self.assertEqual(observed, expected)

    def test_tracked_configuration_contains_no_secret_values(self) -> None:
        cluster = (CONTROL_PLANE / "clickhouse" / "cluster.xml").read_text(encoding="ascii")
        self.assertIn('<password from_env="CLICKHOUSE_PASSWORD"/>', cluster)
        self.assertNotRegex(cluster, r"<password>[^<]+</password>")
        combined = "\n".join(
            path.read_text(encoding="ascii")
            for path in sorted(CONTROL_PLANE.rglob("*"))
            if path.is_file()
        )
        self.assertNotIn("PRIVATE KEY", combined)
        self.assertNotRegex(combined, r"(?im)^(?:password|admin_token)=[^$\s].+$")
        self.assertNotIn("OTEL_COLLECTOR_GRPC_ENDPOINT", self.compose)

    def test_compose_topology_uses_generated_core_gateway(self) -> None:
        for service in ("api", "client-proxy"):
            block = service_block(self.compose, service)
            self.assertIn("host.docker.internal:${KITDEV_CORE_GATEWAY:?required}", block)
            self.assertNotIn("host-gateway", block)
        network = self.compose.split("\nnetworks:\n", maxsplit=1)[1]
        self.assertIn("name: kitdev-core", network)
        self.assertIn("external: true", network)
        self.assertNotIn("ipam:", network)

    def test_lifecycle_and_source_trust_fail_closed(self) -> None:
        common = (SCRIPTS / "common.sh").read_text(encoding="ascii")
        self.assertIn("production|development|migration", common)
        self.assertIn('values.get("VERSION_ID") not in {"25.04", "26.04"}', common)
        self.assertIn("ubuntu_25_04_not_production_eligible", common)
        self.assertNotIn("24.04", common)
        trusted_tree = common.split("<<'PY_TRUSTED_TREE'", maxsplit=1)[1]
        self.assertIn("stat.S_ISLNK", trusted_tree)
        self.assertNotIn("grep -q", trusted_tree)

    def test_worker_identity_and_datastore_ownership_are_collision_free(self) -> None:
        common = (SCRIPTS / "common.sh").read_text(encoding="ascii")
        layout = (SCRIPTS / "prepare-layout.sh").read_text(encoding="ascii")
        self.assertIn("require_worker_identity", layout)
        self.assertLess(layout.index("require_worker_identity"), layout.index("ensure_directory"))
        self.assertIn("uid >= 61000 && uid <= 61999", common)
        self.assertIn("gid >= 61000 && gid <= 61999", common)
        self.assertIn("kitdev_worker_container_identity_collision", common)
        self.assertIn("kitdev_worker_supplementary_groups_invalid", common)
        for expected in (
            'ensure_directory "$KITDEV_DATA_ROOT/postgres" 999 0 700',
            'ensure_directory "$KITDEV_DATA_ROOT/redis" 999 0 750',
            'ensure_directory "$KITDEV_DATA_ROOT/clickhouse" 101 101 750',
            'ensure_directory "$KITDEV_DATA_ROOT/loki" 10001 10001 750',
            'ensure_directory "$KITDEV_RUNTIME_ROOT/orchestrator/template-storage/templates" '
            "root kitdev 2700",
        ):
            self.assertIn(expected, layout)

        probe = f"""
source {SCRIPTS / "common.sh"}
getent() {{
  case "$1:$3" in
    passwd:kitdev-worker) printf '%s\\n' \
      'kitdev-worker:x:61000:61000::/var/lib/kitdev-worker:/usr/sbin/nologin' ;;
    group:kitdev-worker) printf '%s\\n' 'kitdev-worker:x:61000:' ;;
    group:kvm) printf '%s\\n' 'kvm:x:108:kitdev-worker' ;;
    *) return 2 ;;
  esac
}}
id() {{
  case "$1" in
    -G) printf '%s\\n' '61000 108' ;;
    -u) printf '%s\\n' '61000' ;;
    -g) printf '%s\\n' '61000' ;;
    *) return 2 ;;
  esac
}}
require_worker_identity
"""
        valid = subprocess.run(["bash", "-c", probe], capture_output=True, text=True)
        self.assertEqual(valid.returncode, 0, valid.stderr)
        collision = subprocess.run(
            ["bash", "-c", probe.replace(":61000:61000:", ":999:999:", 1)],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(collision.returncode, 0)
        self.assertIn("kitdev_worker_reserved_range_required", collision.stderr)

        gid_probe = f"""
source {SCRIPTS / "common.sh"}
getent() {{ printf '%s\\n' 'kitdev:x:61042:'; }}
[[ "$(identity_gid kitdev)" == 61042 ]]
"""
        gid_result = subprocess.run(["bash", "-c", gid_probe], capture_output=True, text=True)
        self.assertEqual(gid_result.returncode, 0, gid_result.stderr)

    def test_private_environment_is_nonrotating_and_parent_bound(self) -> None:
        source = (SCRIPTS / "private_env.py").read_text(encoding="ascii")
        self.assertLess(source.index("require_parent()"), source.index("os.lstat(ENV_PATH)"))
        source = source.replace("metadata.st_uid != 0", "metadata.st_uid != os.getuid()")
        source = source.replace("metadata.st_gid != 0", "metadata.st_gid != os.getgid()")
        source = source.replace("opened.st_uid != 0", "opened.st_uid != os.getuid()")
        source = source.replace("opened.st_gid != 0", "opened.st_gid != os.getgid()")
        namespace = {"__name__": "control_plane_private_env_test"}
        exec(compile(source, str(SCRIPTS / "private_env.py"), "exec"), namespace)
        with TemporaryDirectory(dir=ROOT) as directory:
            parent = Path(directory) / "etc"
            parent.mkdir(mode=0o700)
            env_path = parent / "control-plane.env"
            namespace["ENV_PATH"] = env_path
            with redirect_stdout(StringIO()):
                namespace["bootstrap"]()
            first = env_path.read_bytes()
            self.assertEqual(env_path.stat().st_mode & 0o777, 0o600)
            with redirect_stdout(StringIO()):
                namespace["bootstrap"]()
            self.assertEqual(env_path.read_bytes(), first)
            parent.chmod(0o755)
            with (
                redirect_stdout(StringIO()),
                redirect_stderr(StringIO()),
                self.assertRaises(SystemExit),
            ):
                namespace["read_document"]()

    def test_private_environment_writes_bounded_e2e_curl_configs(self) -> None:
        source = (SCRIPTS / "private_env.py").read_text(encoding="ascii")
        source = source.replace("metadata.st_uid != 0", "metadata.st_uid != os.getuid()")
        source = source.replace("metadata.st_gid != 0", "metadata.st_gid != os.getgid()")
        source = source.replace("opened.st_uid != 0", "opened.st_uid != os.getuid()")
        source = source.replace("opened.st_gid != 0", "opened.st_gid != os.getgid()")
        namespace = {"__name__": "control_plane_private_env_e2e_test"}
        exec(compile(source, str(SCRIPTS / "private_env.py"), "exec"), namespace)
        with TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            parent = root / "etc"
            parent.mkdir(mode=0o700)
            namespace["ENV_PATH"] = parent / "control-plane.env"
            with redirect_stdout(StringIO()):
                namespace["bootstrap"]()
            admin_token = namespace["read_document"]()["ADMIN_TOKEN"]
            api_key = "local_api_key_1234567890"
            api_key_path = root / "api-key"
            api_key_path.write_text(api_key + "\n", encoding="ascii")
            api_key_path.chmod(0o600)
            output = root / "configs"
            output.mkdir(mode=0o700)
            stdout = StringIO()
            with redirect_stdout(stdout):
                namespace["write_e2e_configs"](str(api_key_path), str(output))
            self.assertNotIn(api_key, stdout.getvalue())
            self.assertNotIn(admin_token, stdout.getvalue())
            self.assertEqual((output / "admin.curlrc").stat().st_mode & 0o777, 0o600)
            self.assertEqual((output / "api.curlrc").stat().st_mode & 0o777, 0o600)
            self.assertEqual(
                (output / "admin.curlrc").read_text(encoding="ascii"),
                f'header = "X-Admin-Token: {admin_token}"\n',
            )
            self.assertEqual(
                (output / "api.curlrc").read_text(encoding="ascii"),
                f'header = "X-API-Key: {api_key}"\n',
            )

            occupied = root / "occupied"
            occupied.mkdir(mode=0o700)
            (occupied / "admin.curlrc").write_text("foreign\n", encoding="ascii")
            with (
                redirect_stdout(StringIO()),
                redirect_stderr(StringIO()),
                self.assertRaises(SystemExit),
            ):
                namespace["write_e2e_configs"](str(api_key_path), str(occupied))
            self.assertEqual((occupied / "admin.curlrc").read_text(), "foreign\n")
            self.assertFalse((occupied / "api.curlrc").exists())

            api_occupied = root / "api-occupied"
            api_occupied.mkdir(mode=0o700)
            (api_occupied / "api.curlrc").write_text("foreign-api\n", encoding="ascii")
            with (
                redirect_stdout(StringIO()),
                redirect_stderr(StringIO()),
                self.assertRaises(SystemExit),
            ):
                namespace["write_e2e_configs"](str(api_key_path), str(api_occupied))
            self.assertFalse((api_occupied / "admin.curlrc").exists())
            self.assertEqual(
                (api_occupied / "api.curlrc").read_text(encoding="ascii"), "foreign-api\n"
            )

            invalid_keys = []
            wrong_mode = root / "wrong-mode"
            wrong_mode.write_text(api_key, encoding="ascii")
            wrong_mode.chmod(0o640)
            invalid_keys.append(wrong_mode)
            oversized = root / "oversized"
            oversized.write_bytes(b"a" * 514)
            oversized.chmod(0o600)
            invalid_keys.append(oversized)
            invalid_newline = root / "invalid-newline"
            invalid_newline.write_text(api_key + "\n\n", encoding="ascii")
            invalid_newline.chmod(0o600)
            invalid_keys.append(invalid_newline)
            symlink = root / "symlink-key"
            symlink.symlink_to(api_key_path.name)
            invalid_keys.append(symlink)
            hardlink = root / "hardlink-key"
            os.link(api_key_path, hardlink)
            invalid_keys.extend((api_key_path, hardlink))
            for index, invalid in enumerate(invalid_keys):
                rejected = root / f"rejected-{index}"
                rejected.mkdir(mode=0o700)
                with (
                    self.subTest(path=invalid.name),
                    redirect_stdout(StringIO()),
                    redirect_stderr(StringIO()),
                    self.assertRaises(SystemExit),
                ):
                    namespace["write_e2e_configs"](str(invalid), str(rejected))
                self.assertEqual(list(rejected.iterdir()), [])

    def test_api_proxy_e2e_runner_is_bounded_offline_and_credential_safe(self) -> None:
        runner = (SCRIPTS / "verify-api-proxy-e2e.sh").read_text(encoding="ascii")
        client = (SCRIPTS / "e2e-process-client" / "main.go").read_text(encoding="ascii")
        self.assertIn("umask 077", runner)
        self.assertIn("mktemp -d /run/kitdev-sandboxes/api-proxy-e2e.", runner)
        self.assertLess(runner.index("trap cleanup EXIT"), runner.index('stage="$(mktemp'))
        self.assertNotIn("curl --config", runner)
        self.assertEqual(runner.count("curl --disable"), 4)
        self.assertNotIn("--verbose", runner)
        self.assertNotIn("--trace", runner)
        self.assertIn("--max-filesize 1048576", runner)
        self.assertIn("--max-time 180", runner)
        self.assertIn("--pull never --platform linux/amd64 --network none", runner)
        self.assertIn('--volume "$stage/source:/src:ro"', runner)
        self.assertIn("./packages/shared/cmd/kitdev-e2e-process", runner)
        self.assertNotIn("./.kitdev-e2e", runner)
        self.assertIn(
            "2e1e9947a3d553b7e8f92b00304361503a220bbbda9b321c88e4e4886ed35f11",
            runner,
        )
        self.assertIn("readonly CLIENT_SIZE=10068094", runner)
        self.assertLess(runner.index("go mod download"), runner.index("--network none"))
        self.assertIn("require_clean_infra_checkout", runner)
        self.assertIn("flock --nonblock 9", runner)
        self.assertIn('node.get("id") == sys.argv[2]', runner)
        self.assertIn('node.get("status") == "ready"', runner)
        self.assertIn('item.get("buildID") == sys.argv[2]', runner)
        self.assertIn('item.get("diskSizeMB") != 3722', runner)
        self.assertIn('item.get("public") is not False', runner)
        self.assertIn('re.fullmatch(r"i[a-z0-9]{20}"', runner)
        self.assertIn("sandbox_list_state present", runner)
        self.assertIn("sandbox_list_is_empty", runner)
        self.assertIn("create_attempted=yes", runner)
        self.assertIn("recover_target_sandbox_id", runner)
        self.assertIn("terminal_state_ready", runner)
        self.assertIn("pgrep -x firecracker", runner)
        self.assertIn('redis-cli --raw --scan --pattern "*$sandbox_id*"', runner)
        self.assertIn("head -c 4097", runner)
        self.assertIn('[[ "$code" == 204 || "$code" == 404 ]]', runner)
        self.assertIn('[[ "$code" == 204 ]]', runner)
        self.assertNotIn("ADMIN_TOKEN", runner)
        self.assertNotIn("X-API-Key", runner)
        self.assertIn("`^i[a-z0-9]{20}$`", client)
        self.assertIn("const maxOutputBytes = 4096", client)
        self.assertIn("receivedBytes > maxOutputBytes", client)
        self.assertIn("defer stream.Close()", client)

    def test_api_proxy_e2e_sandbox_parsers_execute_exact_contracts(self) -> None:
        runner = (SCRIPTS / "verify-api-proxy-e2e.sh").read_text(encoding="ascii")

        def execute(marker: str, document: object, *arguments: str) -> subprocess.CompletedProcess:
            match = re.search(rf"<<'{marker}'\n(?P<code>.*?)\n{marker}", runner, re.DOTALL)
            self.assertIsNotNone(match)
            with TemporaryDirectory(dir=ROOT) as directory:
                path = Path(directory) / "response.json"
                path.write_text(json.dumps(document), encoding="ascii")
                return subprocess.run(
                    ["python3", "-I", "-B", "-S", "-", str(path), *arguments],
                    input=match.group("code"),
                    capture_output=True,
                    text=True,
                )

        sandbox_id = "i" + "a" * 20
        item = {
            "sandboxID": sandbox_id,
            "templateID": "2d9a8389-f5f5-4449-b0eb-e1d364ee98ae",
        }
        self.assertEqual(execute("PY_SANDBOX_LIST", [item], sandbox_id, "present").returncode, 0)
        self.assertEqual(execute("PY_SANDBOX_LIST", [], sandbox_id, "absent").returncode, 0)
        self.assertEqual(execute("PY_SANDBOX_LIST_EMPTY", []).returncode, 0)
        self.assertNotEqual(execute("PY_SANDBOX_LIST_EMPTY", [item]).returncode, 0)
        recovered = execute(
            "PY_RECOVER_TARGET",
            [item],
            "2d9a8389-f5f5-4449-b0eb-e1d364ee98ae",
        )
        self.assertEqual((recovered.returncode, recovered.stdout), (0, sandbox_id + "\n"))
        self.assertNotEqual(
            execute(
                "PY_RECOVER_TARGET",
                [item, {**item, "sandboxID": "i" + "b" * 20}],
                "2d9a8389-f5f5-4449-b0eb-e1d364ee98ae",
            ).returncode,
            0,
        )
        self.assertNotEqual(
            execute(
                "PY_RECOVER_TARGET",
                [{**item, "sandboxID": "invalid"}],
                "2d9a8389-f5f5-4449-b0eb-e1d364ee98ae",
            ).returncode,
            0,
        )
        self.assertNotEqual(execute("PY_SANDBOX_LIST", {}, sandbox_id, "absent").returncode, 0)

    def test_typescript_sdk_e2e_is_pinned_and_cleanup_safe(self) -> None:
        runner = (SCRIPTS / "verify-typescript-sdk-e2e.sh").read_text(encoding="ascii")
        client_dir = SCRIPTS / "e2e-typescript-sdk"
        package = json.loads((client_dir / "package.json").read_text(encoding="ascii"))
        lock = json.loads((client_dir / "package-lock.json").read_text(encoding="ascii"))
        smoke = (client_dir / "smoke.ts").read_text(encoding="ascii")
        commands = (client_dir / "commands.ts").read_text(encoding="ascii")
        files = (client_dir / "files.ts").read_text(encoding="ascii")
        pty = (client_dir / "pty.ts").read_text(encoding="ascii")
        lifecycle = (client_dir / "lifecycle.ts").read_text(encoding="ascii")
        pause = (client_dir / "pause.ts").read_text(encoding="ascii")
        snapshot = (client_dir / "snapshot.ts").read_text(encoding="ascii")
        cleanup = (client_dir / "cleanup.ts").read_text(encoding="ascii")

        self.assertEqual(package["engines"]["node"], "22.18.0")
        self.assertEqual(package["dependencies"]["e2b"], "2.38.0")
        self.assertEqual(lock["packages"]["node_modules/e2b"]["version"], "2.38.0")
        self.assertEqual(
            lock["packages"]["node_modules/e2b"]["integrity"],
            "sha512-l+3Quu3nI+BST9VVynFYiFhXowy7SxivyRKyvNAusrOnjgyTVKVGwi59PPntWLPg"
            "PSOkqEhWNM4vcLSg7E/s/A==",
        )
        self.assertIn(
            "sha256:752ea8a2f758c34002a0461bd9f1cee4f9a3c36d48494586f60ffce1fc708e0e", runner
        )
        self.assertIn("490c2920ffce8e59f8edd9e9d7951b0f13f93521a851355e7c72e99ad134766c", runner)
        self.assertIn("npm ci --ignore-scripts --no-audit --no-fund", runner)
        self.assertIn("--network none", runner)
        self.assertIn("--read-only", runner)
        self.assertIn("flock --nonblock 9", runner)
        self.assertIn("pgrep -x firecracker", runner)
        self.assertIn("redis-cli --raw --scan", runner)
        self.assertNotIn("E2B_DEBUG", smoke)
        self.assertNotIn("apiKey:", runner)
        self.assertIn('background: true, stdin: true', commands)
        self.assertIn("await stdin.closeStdin()", commands)
        self.assertIn("await detached.disconnect()", commands)
        self.assertIn("sandbox.commands.connect(detachedPid)", commands)
        self.assertIn("sandbox.commands.kill(sleepingPid)", commands)
        self.assertIn("run_sdk_group smoke.ts", runner)
        self.assertIn("run_sdk_group commands.ts", runner)
        self.assertIn("writeFiles", files)
        self.assertIn('format: "blob"', files)
        self.assertIn('format: "stream"', files)
        self.assertIn("watchDir", files)
        self.assertIn("includeEntry: true", files)
        self.assertIn("run_sdk_group files.ts", runner)
        self.assertIn("sandbox.pty.create", pty)
        self.assertIn("sandbox.pty.resize", pty)
        self.assertIn("sandbox.pty.sendInput", pty)
        self.assertIn("sandbox.pty.connect", pty)
        self.assertIn("sandbox.pty.kill", pty)
        self.assertIn("run_sdk_group pty.ts", runner)
        self.assertIn("sandbox.isRunning", lifecycle)
        self.assertIn("Sandbox.getInfo", lifecycle)
        self.assertIn("sandbox.setTimeout", lifecycle)
        self.assertIn("Sandbox.connect", lifecycle)
        self.assertIn("sandbox.getMetrics", lifecycle)
        self.assertIn("sandbox.getHost", lifecycle)
        self.assertIn("run_sdk_group lifecycle.ts", runner)
        self.assertIn("keepMemory: true", pause)
        self.assertIn("keepMemory: false", pause)
        self.assertIn("Sandbox.connect", pause)
        self.assertIn('query: { state: ["paused"] }', pause)
        self.assertIn("run_sdk_group pause.ts", runner)
        self.assertIn("source.createSnapshot", snapshot)
        self.assertIn("source.listSnapshots", snapshot)
        self.assertIn("Sandbox.listSnapshots", snapshot)
        self.assertIn("Sandbox.create(snapshotId", snapshot)
        self.assertIn("Sandbox.deleteSnapshot", snapshot)
        self.assertIn("Sandbox.deleteSnapshot", cleanup)
        self.assertIn("secondary-sandbox-id", runner)
        self.assertIn("run_sdk_group snapshot.ts", runner)
        self.assertIn('expected = "snapshot:last:" + sys.argv[2]', runner)
        self.assertIn("sdk_snapshot_audit_cleanup_invalid", runner)
        self.assertIn("cleanup_snapshot_audit_key || status=1", runner)
        self.assertLess(
            smoke.index('writeFile("/run/state/sandbox-id"'), smoke.index('pass("sandbox-create")')
        )
        self.assertLess(runner.index("trap cleanup EXIT"), runner.index('stage="$(mktemp'))
        group_runner = runner.split("run_sdk_group() {", maxsplit=1)[1].split(
            "\n}\n", maxsplit=1
        )[0]
        self.assertLess(
            group_runner.index('sandbox_id="$(read_sandbox_id)"'),
            group_runner.index("verify_terminal_state ||"),
        )
        self.assertLess(
            group_runner.index("verify_terminal_state ||"),
            group_runner.index('rm -f -- "$stage/state/sandbox-id"'),
        )

    def test_all_control_plane_shell_entrypoints_parse_and_are_strict(self) -> None:
        scripts = sorted(SCRIPTS.glob("*.sh"))
        self.assertTrue(scripts)
        for script in scripts:
            with self.subTest(script=script.name):
                text = script.read_text(encoding="ascii")
                self.assertIn("set -Eeuo pipefail", text)
                subprocess.run(["bash", "-n", str(script)], check=True, capture_output=True)

    def test_build_wrappers_keep_all_outputs_distinct_and_envd_reproducible(self) -> None:
        images = (SCRIPTS / "build-control-plane-images.sh").read_text(encoding="ascii")
        self.assertIn('for component in ("api", "db", "clickhouse", "client-proxy")', images)
        self.assertIn('"$KITDEV_INFRA_ROOT/packages/clickhouse"', images)
        self.assertIn("image_ids_not_distinct", images)
        self.assertEqual(images.count('--build-arg "COMMIT_SHA=$KITDEV_INFRA_SHORT_COMMIT"'), 2)
        self.assertNotIn('--build-arg "COMMIT_SHA=$KITDEV_INFRA_COMMIT"', images)
        self.assertEqual(images.count('$KITDEV_INFRA_SHORT_COMMIT"'), 10)
        self.assertIn('mapfile -t ids <<<"$manifest_output"', images)
        self.assertNotIn("mapfile -t ids < <(", images)
        self.assertIn('"$api_id" "$database_id" "$clickhouse_id" "$proxy_id"', images)

        envd = (SCRIPTS / "build-envd.sh").read_text(encoding="ascii")
        self.assertIn('git -C "$KITDEV_INFRA_ROOT" archive', envd)
        self.assertIn('--volume "$stage/source:/src"', envd)
        self.assertNotIn("$KITDEV_INFRA_ROOT:/src:ro", envd)
        self.assertIn(
            "530d84dfbfd82c05181e0dc61ca842f3caaa349b0cc2f3f52d2d8eb9478aa67e",
            envd,
        )
        self.assertIn("-trimpath -buildvcs=false -a", envd)
        self.assertIn("-buildid=", envd)

        tools = (SCRIPTS / "build-snapshot-tools.sh").read_text(encoding="ascii")
        self.assertIn("./packages/orchestrator/cmd/copy-build", tools)
        self.assertIn("./packages/orchestrator/cmd/resume-build", tools)
        self.assertIn("CGO_ENABLED=0 GOOS=linux GOARCH=amd64", tools)
        self.assertIn("CGO_ENABLED=1 GOOS=linux GOARCH=amd64", tools)
        self.assertIn("go mod download golang.org/x/term@v0.44.0", tools)
        self.assertIn("go mod download\n", tools)
        self.assertIn("--network none", tools)
        self.assertIn("--pull never", tools)
        self.assertNotIn("snapshot_tools_partial_state_conflict", tools)
        self.assertLess(
            tools.index("copy_build_runtime_conflict"),
            tools.index('publish_exact_file "$stage/out/copy-build"'),
        )
        self.assertLess(
            tools.index("resume_build_runtime_conflict"),
            tools.index('publish_exact_file "$stage/out/copy-build"'),
        )
        self.assertIn("-trimpath -buildvcs=false", tools)
        self.assertEqual(tools.count('-buildid="'), 2)
        for digest in (
            "aaf516f7157c70be3be35b552d94fdf1dbd3b9739a8d03a0c978f96d03c45406",
            "d294e961a478f3ffa84ab9d10b10bb8fed723f844c5c49e891e70b7019df2ca9",
        ):
            self.assertIn(digest, tools)
            self.assertIn(digest, self.versions_lock)

    def test_runtime_replay_verifies_migrations_containers_and_installed_bytes(self) -> None:
        replay = (SCRIPTS / "replay-compose.sh").read_text(encoding="ascii")
        self.assertIn("verify_migrations", replay)
        self.assertIn("'exited 0'", replay)
        self.assertIn("verify_runtime_contract", replay)
        self.assertIn('host.get("PortBindings")', replay)
        self.assertIn('container["Config"].get("Image")', replay)
        self.assertIn('host.get("ReadonlyRootfs")', replay)
        self.assertIn('host.get("PublishAllPorts")', replay)
        self.assertIn('host.get("CapDrop")', replay)
        self.assertIn('host.get("SecurityOpt")', replay)
        self.assertIn('environment_map(config.get("Env"))', replay)
        self.assertIn('container.get("Mounts", [])', replay)
        self.assertIn('config.get("User", "")', replay)
        self.assertIn('container["NetworkSettings"].get("Ports")', replay)
        self.assertIn('extra_hosts_map(host.get("ExtraHosts"))', replay)
        self.assertIn("--wait-timeout 300 api client-proxy", replay)

        preflight = (SCRIPTS / "preflight-orchestrator.sh").read_text(encoding="ascii")
        for expected in (
            "0:0:755:3566832:1",
            "0:0:644:43638104:1",
            "0:0:755:1210176:1",
            "0:$kitdev_gid:750:12927102:1",
        ):
            self.assertIn(expected, preflight)

        installer = (SCRIPTS / "install-orchestrator-service.sh").read_text(encoding="ascii")
        verify_branch = installer.split('if [[ "$mode" == verify ]]', maxsplit=1)[1]
        self.assertLess(
            verify_branch.index("require_exact_directory"),
            verify_branch.index("ensure_directory"),
        )
        self.assertEqual(installer.count("require_exact_file"), 8)
        self.assertIn("orchestrator.env.expected", installer)
        environment = (ROOT / "systemd" / "orchestrator.env.template").read_text(encoding="ascii")
        self.assertIn(
            "TEMPLATE_STORAGE_URL=file:///var/lib/kitdev-sandboxes/data/runtime/"
            "orchestrator/template-storage/templates",
            environment,
        )

    def test_firewall_audit_rejects_broad_project_interface_rules(self) -> None:
        firewall = (SCRIPTS / "configure-firewall.sh").read_text(encoding="ascii")
        self.assertIn('"port", "5007", "proto", "tcp"', firewall)
        self.assertIn("port 5007 proto tcp comment 'kitdev core to sandbox proxy'", firewall)
        verifier = firewall.split("<<'PY_VERIFY_UFW'\n", maxsplit=1)[1].split(
            "\nPY_VERIFY_UFW", maxsplit=1
        )[0]
        with TemporaryDirectory(dir=ROOT) as directory:
            verifier_path = Path(directory) / "verify_ufw.py"
            verifier_path.write_text(verifier, encoding="ascii")
            command = (
                'python3 -I -B -S "$1" 172.18.0.0/16 172.18.0.1 br-kitdev eth0 no subset 3<<<"$2"'
            )
            for rule in (
                "ufw allow in on veth+",
                "ufw allow in on br-kitdev from any to any",
                "ufw route allow in on veth+ out on eth0",
            ):
                with self.subTest(rule=rule):
                    result = subprocess.run(
                        ["bash", "-c", command, "_", str(verifier_path), rule],
                        capture_output=True,
                        text=True,
                    )
                    self.assertNotEqual(result.returncode, 0)
            unrelated = subprocess.run(
                ["bash", "-c", command, "_", str(verifier_path), "ufw allow 22/tcp"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(unrelated.returncode, 0, unrelated.stderr)

    @unittest.skipUnless(
        os.environ.get("KITDEV_VALIDATE_COMPOSE") == "1",
        "set KITDEV_VALIDATE_COMPOSE=1 where Docker Compose is installed",
    )
    def test_docker_compose_accepts_the_rendered_model(self) -> None:
        environment = os.environ.copy()
        environment.update(
            {
                "POSTGRES_PASSWORD": "a" * 64,
                "POSTGRES_CONNECTION_STRING": "postgres://generated",
                "CLICKHOUSE_PASSWORD": "b" * 64,
                "CLICKHOUSE_USER": "kitdev",
                "CLICKHOUSE_CONNECTION_STRING": "clickhouse://generated",
                "SANDBOX_ACCESS_TOKEN_HASH_SEED": "c" * 64,
                "ADMIN_TOKEN": "d" * 64,
                "E2B_API_IMAGE_REF": "sha256:" + "1" * 64,
                "E2B_DB_MIGRATOR_IMAGE_REF": "sha256:" + "2" * 64,
                "E2B_CLICKHOUSE_MIGRATOR_IMAGE_REF": "sha256:" + "3" * 64,
                "E2B_CLIENT_PROXY_IMAGE_REF": "sha256:" + "4" * 64,
                "KITDEV_CORE_SUBNET": "172.31.0.0/16",
                "KITDEV_CORE_GATEWAY": "172.31.0.1",
            }
        )
        subprocess.run(
            ["docker", "compose", "--file", str(COMPOSE), "config", "--quiet"],
            cwd=CONTROL_PLANE,
            env=environment,
            check=True,
            capture_output=True,
        )


if __name__ == "__main__":
    unittest.main()

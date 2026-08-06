from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / "experiments" / "ovh-lab"
SCRIPT = LAB / "bootstrap-docker-engine.sh"


class OvhDockerBootstrapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = SCRIPT.read_text(encoding="ascii")

    def test_script_is_executable_strict_and_parses(self) -> None:
        self.assertTrue(os.access(SCRIPT, os.X_OK))
        self.assertIn("set -Eeuo pipefail", self.text)
        subprocess.run(["bash", "-n", str(SCRIPT)], check=True, capture_output=True)

    def test_exact_package_versions_and_repository_constants_are_pinned(self) -> None:
        expected = {
            "ca-certificates=20260601~26.04.1",
            "curl=8.18.0-1ubuntu2.3",
            "gnupg=2.4.8-4ubuntu3",
            "git=1:2.53.0-1ubuntu1",
            "jq=1.8.1-4ubuntu2",
            "make=4.4.1-3",
            "kmod=34.2-2ubuntu2",
            "iproute2=6.19.0-1ubuntu1.1",
            "iptables=1.8.11-2ubuntu3",
            "util-linux=2.41.3-3ubuntu2",
            "procps=2:4.0.4-9ubuntu1",
            "xz-utils=5.8.3-1",
            "docker-ce=5:29.7.2-1~ubuntu.26.04~resolute",
            "docker-ce-cli=5:29.7.2-1~ubuntu.26.04~resolute",
            "containerd.io=2.3.3-1~ubuntu.26.04~resolute",
            "docker-buildx-plugin=0.36.1-1~ubuntu.26.04~resolute",
            "docker-compose-plugin=5.4.0-1~ubuntu.26.04~resolute",
        }
        observed = set(re.findall(r"^  '([^']+=[^']+)'$", self.text, re.MULTILINE))
        self.assertEqual(observed, expected)
        self.assertIn(
            'KEY_SHA256="1500c1f56fa9e26b9b8f42452a553675796ade0807cdce11975eb98170b3a570"',
            self.text,
        )
        self.assertIn(
            'SOURCE_SHA256="47be0f749c19273936c7e56fff5a29b9108bcce8137ee677cc736523fb876e71"',
            self.text,
        )
        self.assertIn(
            'KEY_PRIMARY_FINGERPRINT="9DC858229FC7DD38854AE2D88D81803C0EBFCD88"',
            self.text,
        )
        self.assertIn(
            'KEY_SIGNING_FINGERPRINT="D3306A018370199E527AE7997EA0A9C3F273FCD8"',
            self.text,
        )

    def test_source_fixture_is_the_canonical_155_bytes(self) -> None:
        match = re.search(
            r"<<'EOF_DOCKER_SOURCE'\n(.*?)EOF_DOCKER_SOURCE",
            self.text,
            re.DOTALL,
        )
        self.assertIsNotNone(match)
        source = match.group(1).encode("ascii")
        self.assertEqual(len(source), 155)
        self.assertEqual(
            hashlib.sha256(source).hexdigest(),
            "47be0f749c19273936c7e56fff5a29b9108bcce8137ee677cc736523fb876e71",
        )

    def test_mutation_remains_separately_gated_and_stage50_stays_blocked(self) -> None:
        manifest = json.loads((LAB / "stages.json").read_text(encoding="utf-8"))
        stage50 = next(stage for stage in manifest["stages"] if stage["id"] == "50")
        self.assertEqual((stage50["kind"], stage50["status"]), ("mutation", "blocked"))
        self.assertIn(
            'APPLY_ACK="DISPOSABLE_OVH_LAB:docker-bootstrap:$BUNDLE_SHA256"',
            self.text,
        )
        main = self.text.split("main() {", maxsplit=1)[1]
        self.assertLess(main.index("acknowledgement_required"), main.index("require_fixed_host"))
        self.assertLess(main.index("start_stage05_authorization"), main.index("apply_bootstrap"))
        self.assertLess(main.index("apply_bootstrap"), main.index("finish_stage05_authorization"))
        self.assertLess(main.index("finish_stage05_authorization"), main.index("verify_runtime"))
        self.assertIn("lab_refuse_production", self.text)
        self.assertIn("lab_require_supported_platform", self.text)
        self.assertIn("require_stage05_authorization", self.text)
        self.assertIn("stage05_authorization_invalid", self.text)
        self.assertIn("with resolver._authorization_session():", self.text)
        self.assertIn("sys.stdin.buffer.read(1)", self.text)
        for component in ("runner.py", "journal.py", "stage05.py", "stage10.py"):
            self.assertIn(component, self.text)
        self.assertIn("metadata.st_uid != 0", self.text)
        self.assertIn("stat.S_IMODE(metadata.st_mode) & 0o022", self.text)
        self.assertIn("for component in relative.parts[:-1]:", self.text)
        self.assertIn("require_trusted_directory(directory)", self.text)
        self.assertIn('struct.pack(">Q", len(label))', self.text)
        self.assertIn('struct.pack(">Q", len(content))', self.text)

    def test_relative_and_absolute_invocations_normalize_the_script_path(self) -> None:
        expected = f"SCRIPT_PATH={SCRIPT}"
        relative = SCRIPT.relative_to(ROOT)
        for invocation in (str(relative), str(SCRIPT)):
            with self.subTest(invocation=invocation):
                result = subprocess.run(
                    ["bash", "-x", invocation, "approval"],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 64)
                self.assertIn(expected, result.stderr)

    def test_bundle_rejects_a_writable_nested_component_parent(self) -> None:
        helper = re.search(
            r"<<'PY_BUNDLE'\n(.*?)\nPY_BUNDLE",
            self.text,
            re.DOTALL,
        )
        self.assertIsNotNone(helper)
        helper_code = helper.group(1).replace(
            "metadata.st_uid != 0", "metadata.st_uid not in (0, os.getuid())"
        ).replace(
            "opened.st_uid != 0", "opened.st_uid not in (0, os.getuid())"
        )
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            nested = root / "nested" / "component"
            nested.mkdir(parents=True)
            paths = []
            for index in range(6):
                path = nested / f"component-{index}"
                path.write_text(f"content-{index}", encoding="ascii")
                path.chmod(0o644)
                paths.append(path)

            trusted = subprocess.run(
                [sys.executable, "-", str(root), *(str(path) for path in paths)],
                input=helper_code,
                capture_output=True,
                text=True,
            )
            self.assertEqual(trusted.returncode, 0)
            self.assertRegex(trusted.stdout, r"^[0-9a-f]{64}\n$")

            nested.chmod(0o775)
            rejected = subprocess.run(
                [sys.executable, "-", str(root), *(str(path) for path in paths)],
                input=helper_code,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(rejected.returncode, 0)

    def test_key_and_candidate_parsers_consume_complete_input(self) -> None:
        self.assertNotIn("curl |", self.text)
        self.assertNotIn("get.docker.com", self.text)
        self.assertIn("--proto '=https' --tlsv1.2", self.text)
        candidate_parser = re.search(
            r"apt-cache policy -- \"\$package\" \|\n(.*?)\n    \)\"",
            self.text,
            re.DOTALL,
        )
        self.assertIsNotNone(candidate_parser)
        self.assertIn("END", candidate_parser.group(1))
        self.assertNotRegex(candidate_parser.group(1), r"exit\s+0")
        self.assertIn("unset APT_CONFIG", self.text)

    def test_verify_requires_exact_empty_runtime_state(self) -> None:
        for evidence in (
            "docker.service",
            "containerd.service",
            "{{.Driver}}",
            "overlayfs",
            "{{.CgroupDriver}}",
            "systemd",
            "{{.CgroupVersion}}",
            "docker container ls --all --quiet",
            "docker image ls --all --quiet",
        ):
            self.assertIn(evidence, self.text)
        self.assertIn("require_conflicts_absent", self.text)
        self.assertIn("require_no_foreign_docker_sources", self.text)
        self.assertIn("systemctl enable --now docker.service containerd.service", self.text)

    def test_live_service_report_retains_exact_public_digests(self) -> None:
        report = (ROOT / "docs/research/ovh-live-lab-services-first-run.md").read_text(
            encoding="utf-8"
        )
        for digest in (
            "304ab813518754228f9f792f79d6da36359b82d8ecf418096c636725f8c930ad",
            "a9cc41d6d01da2aa26c219e4f99ecbeead955a7b656c1c499cce8922311b2514",
            "ad201eec325abb23e558e344d46d81bc9e2eba5a011fc02af440c124a27a1a61",
        ):
            self.assertIn(digest, report)
        envd_digest = (
            "530d84dfbfd82c05181e0dc61ca842f3caaa349b0cc2f3f52d2d8eb9478aa67e"
        )
        self.assertIn(envd_digest, report)
        activity = (ROOT / "docs/research/activity-log.md").read_text(encoding="utf-8")
        self.assertIn(envd_digest, activity)
        self.assertIn("credentials, public/management endpoints", report)
        self.assertIn("intentionally omitted", report)

    def test_package_query_distinguishes_absent_broken_and_error_state(self) -> None:
        function = re.search(
            r"(package_version\(\) \{.*?\n\})\n\nrequire_conflicts_absent",
            self.text,
            re.DOTALL,
        )
        self.assertIsNotNone(function)
        program = """
set -Eeuo pipefail
DPKG_QUERY_FORMAT='${db:Status-Want}\\t${db:Status-Eflag}\\t'\\
'${db:Status-Status}\\t${Version}\\t${Architecture}\\n'
lab_die() { printf 'reason=%s\\n' "$1" >&2; exit "${2:-1}"; }
dpkg-query() {
  case "$MODE" in
    absent) return 1 ;;
    broken) return 2 ;;
    installed) printf 'install\\tok\\tinstalled\\t1.2.3\\tamd64\\n' ;;
    reinstreq) printf 'install\\treinstreq\\tinstalled\\t1.2.3\\tamd64\\n' ;;
  esac
}
""" + function.group(1) + "\npackage_version fixed-package\n"
        cases = {
            "absent": (0, "absent", ""),
            "broken": (65, "", "reason=package_inventory_unknown\n"),
            "installed": (0, "install\tok\tinstalled\t1.2.3\tamd64", ""),
            "reinstreq": (65, "", "reason=package_status_error\n"),
        }
        for mode, expected in cases.items():
            with self.subTest(mode=mode):
                result = subprocess.run(
                    ["bash", "-c", program],
                    env={"MODE": mode, "PATH": "/usr/bin:/bin"},
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(
                    (result.returncode, result.stdout, result.stderr), expected
                )

    def test_repository_check_rejects_aliases_and_binds_exact_file(self) -> None:
        helper = re.search(
            r"<<'PY_REPOSITORY_FILE'\n(.*?)\nPY_REPOSITORY_FILE",
            self.text,
            re.DOTALL,
        )
        self.assertIsNotNone(helper)
        helper_code = helper.group(1)
        if not hasattr(os, "listxattr"):
            helper_code = "import os\nos.listxattr = lambda _descriptor: []\n" + helper_code
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            root.chmod(0o755)
            target = root / "docker.asc"
            content = b"approved-key"
            digest = hashlib.sha256(content).hexdigest()
            target.write_bytes(content)
            target.chmod(0o644)

            exact = subprocess.run(
                [sys.executable, "-", "check", str(target), digest, "-"],
                input=helper_code,
                capture_output=True,
                text=True,
            )
            self.assertEqual((exact.returncode, exact.stdout), (0, "present\n"))

            alias = root / "alias"
            os.link(target, alias)
            linked = subprocess.run(
                [sys.executable, "-", "check", str(target), digest, "-"],
                input=helper_code,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(linked.returncode, 0)
            alias.unlink()

            missing = subprocess.run(
                [
                    sys.executable,
                    "-",
                    "check",
                    str(root / "missing-parent" / "docker.asc"),
                    digest,
                    "-",
                ],
                input=helper_code,
                capture_output=True,
                text=True,
            )
            self.assertEqual((missing.returncode, missing.stdout), (0, "absent\n"))

            real_parent = root / "real-parent"
            real_parent.mkdir(mode=0o755)
            linked_parent = root / "linked-parent"
            linked_parent.symlink_to(real_parent, target_is_directory=True)
            parent_alias = subprocess.run(
                [
                    sys.executable,
                    "-",
                    "check",
                    str(linked_parent / "docker.asc"),
                    digest,
                    "-",
                ],
                input=helper_code,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(parent_alias.returncode, 0)

    @unittest.skipUnless(sys.platform.startswith("linux"), "renameat2 is Linux-only")
    def test_repository_publish_is_no_clobber_and_idempotent(self) -> None:
        helper = re.search(
            r"<<'PY_REPOSITORY_FILE'\n(.*?)\nPY_REPOSITORY_FILE",
            self.text,
            re.DOTALL,
        )
        self.assertIsNotNone(helper)
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            root.chmod(0o755)
            source = root / "source"
            source.write_bytes(b"approved-source")
            source.chmod(0o600)
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            target = root / "new-parent" / "docker.sources"
            arguments = [
                sys.executable,
                "-",
                "publish",
                str(target),
                digest,
                str(source),
            ]
            first = subprocess.run(
                arguments, input=helper.group(1), capture_output=True, text=True
            )
            self.assertEqual((first.returncode, first.stdout), (0, "published\n"))
            second = subprocess.run(
                arguments, input=helper.group(1), capture_output=True, text=True
            )
            self.assertEqual((second.returncode, second.stdout), (0, "present\n"))

            foreign = root / "foreign"
            foreign.write_bytes(b"foreign")
            foreign.chmod(0o600)
            foreign_digest = hashlib.sha256(foreign.read_bytes()).hexdigest()
            rejected = subprocess.run(
                [
                    sys.executable,
                    "-",
                    "publish",
                    str(target),
                    foreign_digest,
                    str(foreign),
                ],
                input=helper.group(1),
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertEqual(target.read_bytes(), b"approved-source")


if __name__ == "__main__":
    unittest.main()

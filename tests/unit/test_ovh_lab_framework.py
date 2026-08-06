from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / "experiments" / "ovh-lab"


class OvhLabFrameworkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = json.loads((LAB / "stages.json").read_text(encoding="utf-8"))
        self.stage_files = sorted((LAB / "stages").glob("*.sh"))
        self.private_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.private_directory.cleanup)
        self.ssh_config = Path(self.private_directory.name) / "ssh-config"
        self.ssh_config.write_text("Host reviewed-alias\n  BatchMode yes\n", encoding="utf-8")
        self.ssh_config.chmod(0o600)
        self.known_hosts = Path(self.private_directory.name) / "known-hosts"
        self.known_hosts.write_text(
            "reviewed-alias ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestOnly\n",
            encoding="utf-8",
        )
        self.known_hosts.chmod(0o644)

    def approval_environment(
        self,
        config: Path | str | None = None,
        known_hosts: Path | str | None = None,
    ) -> dict[str, str]:
        return {
            "OVH_LAB_TARGET": "reviewed-alias",
            "OVH_LAB_SSH_CONFIG": str(self.ssh_config if config is None else config),
            "OVH_LAB_KNOWN_HOSTS": str(self.known_hosts if known_hosts is None else known_hosts),
            "PATH": "/usr/bin:/bin",
        }

    def test_manifest_has_the_fixed_complete_stage_sequence(self) -> None:
        stages = self.manifest["stages"]
        self.assertEqual(
            [stage["id"] for stage in stages],
            ["00", "05", "10", "20", "30", "40", "50", "60", "70", "80", "90"],
        )
        self.assertEqual(len(stages), len(self.stage_files))
        for stage in stages:
            script = LAB / stage["script"]
            self.assertTrue(script.is_file())
            self.assertTrue(os.access(script, os.X_OK))

        executable_mutations = [
            stage["id"]
            for stage in stages
            if stage["kind"] == "mutation" and stage["status"] == "executable"
        ]
        self.assertEqual(executable_mutations, ["05"])

    def test_all_shell_files_parse_and_use_strict_mode(self) -> None:
        shell_files = [LAB / "run-stage.sh", LAB / "lib" / "common.sh", *self.stage_files]
        for path in shell_files:
            with self.subTest(path=path.name):
                subprocess.run(["bash", "-n", str(path)], check=True, capture_output=True)
                text = path.read_text(encoding="utf-8")
                self.assertIn("set -Eeuo pipefail", text)

    def test_streamed_stage_bundles_parse(self) -> None:
        common = (LAB / "lib" / "common.sh").read_text(encoding="utf-8")
        for path in self.stage_files:
            text = path.read_text(encoding="utf-8")
            body = text.split("# OVH_LAB_STAGE_BODY", maxsplit=1)[1]
            with self.subTest(path=path.name):
                subprocess.run(
                    ["bash", "-n"],
                    input=common + body,
                    text=True,
                    check=True,
                    capture_output=True,
                )

    def test_storage_fixture_excludes_raid_parents_and_emits_one_anonymous_leaf(self) -> None:
        stage = (LAB / "stages" / "30-raw-data-storage-plan.sh").read_text(encoding="utf-8")
        parser = re.search(r"<<'PY_STORAGE'\n(.*?)\nPY_STORAGE", stage, re.DOTALL)
        self.assertIsNotNone(parser)
        fixture = ROOT / "tests" / "fixtures" / "ovh-lab" / "lsblk-raid-parents-and-raw-leaf.json"
        sysfs = Path(self.private_directory.name) / "sysfs"
        (sysfs / "disk_data_leaf" / "holders").mkdir(parents=True)
        (sysfs / "disk_data_leaf" / "slaves").mkdir()
        result = subprocess.run(
            ["python3", "-c", parser.group(1), str(sysfs)],
            input=fixture.read_bytes(),
            capture_output=True,
            check=True,
        )
        output = result.stdout.decode("ascii")
        self.assertEqual(
            output,
            "stage=30 disk_count=3 raw_unmounted_disk_count=1\n"
            "storage.raw_candidate_size_bytes=4000787030016\n"
            "storage.plan=discovery-only storage.format=forbidden storage.mount=forbidden\n",
        )
        self.assertNotIn("disk_raid", output)
        self.assertNotIn("disk_data_leaf", output)

    def test_storage_fixture_fails_closed_on_holder_or_md_membership(self) -> None:
        stage = (LAB / "stages" / "30-raw-data-storage-plan.sh").read_text(encoding="utf-8")
        parser = re.search(r"<<'PY_STORAGE'\n(.*?)\nPY_STORAGE", stage, re.DOTALL)
        self.assertIsNotNone(parser)
        fixture = ROOT / "tests" / "fixtures" / "ovh-lab" / "lsblk-raid-parents-and-raw-leaf.json"
        sysfs = Path(self.private_directory.name) / "sysfs"
        holders = sysfs / "disk_data_leaf" / "holders"
        slaves = sysfs / "disk_data_leaf" / "slaves"
        holders.mkdir(parents=True)
        slaves.mkdir()

        (holders / "foreign_holder").touch()
        held = subprocess.run(
            ["python3", "-c", parser.group(1), str(sysfs)],
            input=fixture.read_bytes(),
            capture_output=True,
        )
        self.assertEqual(held.returncode, 2)
        self.assertEqual(held.stdout, b"")
        (holders / "foreign_holder").unlink()

        (slaves / "foreign_slave").touch()
        has_slave = subprocess.run(
            ["python3", "-c", parser.group(1), str(sysfs)],
            input=fixture.read_bytes(),
            capture_output=True,
        )
        self.assertEqual(has_slave.returncode, 2)
        self.assertEqual(has_slave.stdout, b"")
        (slaves / "foreign_slave").unlink()

        (sysfs / "disk_data_leaf" / "md").mkdir()
        md_member = subprocess.run(
            ["python3", "-c", parser.group(1), str(sysfs)],
            input=fixture.read_bytes(),
            capture_output=True,
        )
        self.assertEqual(md_member.returncode, 2)
        self.assertEqual(md_member.stdout, b"")

    def test_storage_fixture_requires_leaf_without_fs_mount_or_partition_table(self) -> None:
        stage = (LAB / "stages" / "30-raw-data-storage-plan.sh").read_text(encoding="utf-8")
        parser = re.search(r"<<'PY_STORAGE'\n(.*?)\nPY_STORAGE", stage, re.DOTALL)
        self.assertIsNotNone(parser)
        fixture = ROOT / "tests" / "fixtures" / "ovh-lab" / "lsblk-raid-parents-and-raw-leaf.json"
        original = json.loads(fixture.read_text(encoding="utf-8"))
        sysfs = Path(self.private_directory.name) / "sysfs"
        (sysfs / "disk_data_leaf" / "holders").mkdir(parents=True)
        (sysfs / "disk_data_leaf" / "slaves").mkdir()

        mutations = {
            "child": ("children", [original["blockdevices"][0]["children"][0]]),
            "filesystem": ("fstype", "ext4"),
            "mount": ("mountpoints", ["/data"]),
            "partition_table": ("pttype", "gpt"),
        }
        for label, (field, value) in mutations.items():
            document = json.loads(json.dumps(original))
            document["blockdevices"][2][field] = value
            result = subprocess.run(
                ["python3", "-c", parser.group(1), str(sysfs)],
                input=json.dumps(document).encode("utf-8"),
                capture_output=True,
            )
            with self.subTest(label=label):
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b"")

    def test_storage_parser_rejects_dot_components_and_deep_topology(self) -> None:
        stage = (LAB / "stages" / "30-raw-data-storage-plan.sh").read_text(encoding="utf-8")
        parser = re.search(r"<<'PY_STORAGE'\n(.*?)\nPY_STORAGE", stage, re.DOTALL)
        self.assertIsNotNone(parser)
        fixture = ROOT / "tests" / "fixtures" / "ovh-lab" / "lsblk-raid-parents-and-raw-leaf.json"
        original = json.loads(fixture.read_text(encoding="utf-8"))
        sysfs = Path(self.private_directory.name) / "sysfs"
        sysfs.mkdir()

        for name in (".", ".."):
            document = {"blockdevices": [dict(original["blockdevices"][2], name=name)]}
            result = subprocess.run(
                ["python3", "-c", parser.group(1), str(sysfs)],
                input=json.dumps(document).encode("utf-8"),
                capture_output=True,
            )
            with self.subTest(name=name):
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b"")
                self.assertEqual(result.stderr, b"")

        leaf = dict(original["blockdevices"][2])
        for depth in range(70):
            leaf = dict(leaf, name=f"nested_{depth}", type="part", tran=None, children=[leaf])
        deep = subprocess.run(
            ["python3", "-c", parser.group(1), str(sysfs)],
            input=json.dumps({"blockdevices": [leaf]}).encode("utf-8"),
            capture_output=True,
        )
        self.assertEqual(deep.returncode, 2)
        self.assertEqual(deep.stdout, b"")
        self.assertEqual(deep.stderr, b"")

    def test_service_state_distinguishes_absent_inactive_active_and_errors(self) -> None:
        common = LAB / "lib" / "common.sh"
        probe = f"""
source {common!s}
systemctl() {{
  if [[ "$1" == show ]]; then
    case "$SERVICE_CASE" in
      absent) printf 'not-found\\n'; return 0 ;;
      active|inactive|active_error) printf 'loaded\\n'; return 0 ;;
      manager_error) return 1 ;;
      malformed) printf 'unexpected\\n'; return 0 ;;
    esac
  fi
  if [[ "$1" == is-active ]]; then
    case "$SERVICE_CASE" in
      active) printf 'active\\n'; return 0 ;;
      inactive) printf 'inactive\\n'; return 3 ;;
      active_error) return 1 ;;
    esac
  fi
  return 1
}}
lab_service_state docker.service
"""
        cases = {
            "absent": (0, "absent"),
            "active": (0, "active"),
            "inactive": (0, "inactive"),
            "active_error": (1, "error"),
            "manager_error": (1, "error"),
            "malformed": (1, "error"),
        }
        for service_case, expected in cases.items():
            result = subprocess.run(
                ["bash", "-c", probe],
                env={"SERVICE_CASE": service_case, "PATH": "/usr/bin:/bin"},
                capture_output=True,
                text=True,
            )
            with self.subTest(service_case=service_case):
                self.assertEqual((result.returncode, result.stdout), expected)

    def test_baseline_propagates_service_discovery_errors(self) -> None:
        stage = (LAB / "stages" / "00-read-only-baseline.sh").read_text(encoding="utf-8")
        self.assertIn(
            'docker_state="$(lab_service_state docker.service)" || lab_die service_discovery_failed 1',
            stage,
        )
        self.assertIn(
            'ufw_state="$(lab_service_state ufw.service)" || lab_die service_discovery_failed 1',
            stage,
        )

    def test_each_stage_has_remote_bundle_boundary_and_ack_gate(self) -> None:
        for path in self.stage_files:
            with self.subTest(path=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertEqual(text.count("# OVH_LAB_STAGE_BODY"), 1)
                self.assertIn('lab_require_ack "$@"', text)
                if path.name.startswith("05-"):
                    self.assertIn("Stage05Reconciler", (ROOT / "src" / "kitdev_sandboxes" / "stage05.py").read_text(encoding="utf-8"))
                    self.assertIn("refuse_production", (ROOT / "src" / "kitdev_sandboxes" / "stage05.py").read_text(encoding="utf-8"))
                else:
                    self.assertIn("lab_refuse_production", text)
                self.assertIn("lab_require_supported_platform", text)
                self.assertIn("postconditions", text)
                self.assertIn("rollback", text)

    def test_every_stage_rejects_missing_ack_before_other_work(self) -> None:
        for path in self.stage_files:
            with self.subTest(path=path.name):
                result = subprocess.run(["bash", str(path)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 64)
                self.assertIn("acknowledgement_required", result.stderr)

    def test_blocked_stages_have_no_guessed_mutation_commands(self) -> None:
        blocked = {stage["id"] for stage in self.manifest["stages"] if stage["status"] == "blocked"}
        forbidden = (
            "apt-get install",
            "useradd",
            "usermod",
            "mkfs",
            "mount ",
            "modprobe",
            "sysctl -w",
            "docker run",
            "docker compose up",
            "nft add",
            "git clone",
            "systemctl enable",
            "systemctl start",
        )
        for stage_id in blocked:
            path = next(path for path in self.stage_files if path.name.startswith(stage_id + "-"))
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                self.assertIn("lab_blocked", text)
                for token in forbidden:
                    self.assertNotIn(token, text)

    def test_runner_uses_verified_host_key_and_off_host_evidence(self) -> None:
        text = (LAB / "run-stage.sh").read_text(encoding="utf-8")
        self.assertIn("StrictHostKeyChecking=yes", text)
        self.assertIn("OVH_LAB_KNOWN_HOSTS", text)
        self.assertIn("OVH_LAB_SSH_CONFIG", text)
        self.assertIn('-F "$SSH_CONFIG_SNAPSHOT"', text)
        self.assertIn('-o "UserKnownHostsFile=$KNOWN_HOSTS_SNAPSHOT"', text)
        self.assertNotIn('-o "UserKnownHostsFile=$KNOWN_HOSTS"', text)
        self.assertIn("GlobalKnownHostsFile=/dev/null", text)
        self.assertIn("KnownHostsCommand=none", text)
        self.assertIn("UpdateHostKeys=no", text)
        self.assertIn("VerifyHostKeyDNS=no", text)
        self.assertIn('ARTIFACTS_ROOT="$SCRIPT_DIR/../../artifacts"', text)
        self.assertIn('RUN_ROOT="$ARTIFACTS_ROOT/ovh-lab"', text)
        self.assertIn("redact()", text)
        self.assertIn("signal.alarm", text)
        self.assertIn("/usr/bin/timeout", text)
        self.assertIn("EVIDENCE_MAX_BYTES=1048576", text)
        self.assertIn("[redacted-fingerprint]", text)
        self.assertIn("[redacted-host]", text)
        self.assertNotIn("StrictHostKeyChecking=no", text)
        self.assertNotIn("accept-new", text)

    def test_approval_is_manifest_selected_and_bundle_bound(self) -> None:
        result = subprocess.run(
            [str(LAB / "run-stage.sh"), "00", "approval"],
            env=self.approval_environment(),
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertRegex(
            result.stdout.strip(),
            r"^DISPOSABLE_OVH_LAB:00:execute:reviewed-alias:[0-9a-f]{64}:[0-9a-f]{64}:[0-9a-f]{64}$",
        )

    def test_stage05_bundle_is_deterministic_and_embeds_exact_reviewed_modules(self) -> None:
        from kitdev_sandboxes.stage05 import build_plan

        approvals = [
            subprocess.run(
                [str(LAB / "run-stage.sh"), "05", "approval"],
                env=self.approval_environment(),
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            for _ in range(2)
        ]
        self.assertEqual(approvals[0], approvals[1])
        bundle_digest = approvals[0].split(":")[4]
        plan_hash = build_plan(bundle_digest).plan_hash

        fake_bin = Path(self.private_directory.name) / "stage05-bin"
        fake_bin.mkdir()
        captured = Path(self.private_directory.name) / "stage05-bundle"
        fake_ssh = fake_bin / "ssh"
        fake_ssh.write_text(
            """#!/usr/bin/python3
import os
import sys

content = sys.stdin.buffer.read()
capture = os.environ["CAPTURED_BUNDLE"]
if os.path.exists(capture):
    if open(capture, "rb").read() != content:
        raise SystemExit(96)
else:
    with open(capture, "xb") as handle:
        handle.write(content)
print("stage=05 operation=fake status=pass plan_sha256=" + os.environ["EXPECTED_PLAN"])
""",
            encoding="utf-8",
        )
        fake_ssh.chmod(0o700)
        environment = self.approval_environment()
        environment.update(
            {
                "CAPTURED_BUNDLE": str(captured),
                "DISPOSABLE_OVH_LAB": approvals[0],
                "EXPECTED_PLAN": plan_hash,
                "PATH": f"{fake_bin}:/usr/bin:/bin",
            }
        )
        result = subprocess.run(
            [str(LAB / "run-stage.sh"), "05", "execute"],
            env=environment,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        bundle = captured.read_text(encoding="utf-8")
        for label, source in (
            ("JOURNAL", ROOT / "src" / "kitdev_sandboxes" / "journal.py"),
            ("RECONCILER", ROOT / "src" / "kitdev_sandboxes" / "stage05.py"),
        ):
            digest = re.search(rf"readonly STAGE05_{label}_SHA256='([0-9a-f]{{64}})'", bundle)
            payload = re.search(rf"readonly STAGE05_{label}_B64='([A-Za-z0-9+/=]+)'", bundle)
            self.assertIsNotNone(digest)
            self.assertIsNotNone(payload)
            decoded = base64.b64decode(payload.group(1), validate=True)
            self.assertEqual(decoded, source.read_bytes())
            self.assertEqual(hashlib.sha256(decoded).hexdigest(), digest.group(1))
        self.assertNotIn("__STAGE05_", bundle)

        loader_probe = bundle.rsplit('\nmain "$@"', maxsplit=1)[0]
        loader_probe = loader_probe.replace("/usr/bin/python3 -I", f"{sys.executable} -I", 1)
        loader_probe += f'\nstage05_python before WRONG {bundle_digest}\n'
        loaded = subprocess.run(
            ["bash", "-s"],
            input=loader_probe,
            env={"PATH": "/usr/bin:/bin"},
            capture_output=True,
            text=True,
        )
        self.assertEqual(loaded.returncode, 64)
        self.assertEqual(loaded.stdout, "")
        self.assertEqual(loaded.stderr, "status=error reason=acknowledgement_required\n")

        journal_digest = re.search(r"readonly STAGE05_JOURNAL_SHA256='([0-9a-f]{64})'", loader_probe)
        self.assertIsNotNone(journal_digest)
        corrupt_probe = loader_probe.replace(journal_digest.group(1), "0" * 64, 1)
        corrupt_probe += f'\nstage05_python before DISPOSABLE_OVH_LAB {bundle_digest}\n'
        corrupt = subprocess.run(
            ["bash", "-s"],
            input=corrupt_probe,
            env={"PATH": "/usr/bin:/bin"},
            capture_output=True,
            text=True,
        )
        self.assertEqual(corrupt.returncode, 70)
        self.assertEqual(corrupt.stdout, "")
        self.assertEqual(corrupt.stderr, "status=error reason=embedded_module_invalid\n")

        run_directories = {
            Path(line.rsplit(" ", 1)[-1])
            for line in result.stdout.splitlines()
            if line.startswith("ovh-lab: stage passed; redacted evidence: ")
        }
        self.assertEqual(len(run_directories), 1)
        run_directory = run_directories.pop()
        summary = (run_directory / "summary.txt").read_text(encoding="utf-8")
        self.assertIn(f"stage05_plan_sha256={plan_hash}\n", summary)
        shutil.rmtree(run_directory)

    def test_stage10_bundle_is_deterministic_plan_only_and_embeds_resolver(self) -> None:
        stage = next(item for item in self.manifest["stages"] if item["id"] == "10")
        self.assertEqual((stage["status"], stage["kind"]), ("executable", "plan-only"))
        approvals = [
            subprocess.run(
                [str(LAB / "run-stage.sh"), "10", "approval"],
                env=self.approval_environment(),
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            for _ in range(2)
        ]
        self.assertEqual(approvals[0], approvals[1])
        self.assertRegex(
            approvals[0],
            r"^DISPOSABLE_OVH_LAB:10:execute:reviewed-alias:[0-9a-f]{64}:"
            r"[0-9a-f]{64}:[0-9a-f]{64}$",
        )
        runner = (LAB / "run-stage.sh").read_text(encoding="utf-8")
        script = (LAB / stage["script"]).read_text(encoding="utf-8")
        resolver = (ROOT / "src/kitdev_sandboxes/stage10.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("STAGE10_RESOLVER_SOURCE", runner)
        self.assertIn("__STAGE10_RESOLVER_SHA256__", script)
        self.assertIn("kitdev_sandboxes.stage10", script)
        self.assertIn("apply_authorized=no", resolver)

        fake_bin = Path(self.private_directory.name) / "stage10-bin"
        fake_bin.mkdir()
        captured = Path(self.private_directory.name) / "stage10-bundle"
        fake_ssh = fake_bin / "ssh"
        fake_ssh.write_text(
            """#!/usr/bin/python3
import base64
import hashlib
import json
import os
import sys

content = sys.stdin.buffer.read()
capture = os.environ["CAPTURED_STAGE10_BUNDLE"]
if os.path.exists(capture):
    if open(capture, "rb").read() != content:
        raise SystemExit(96)
else:
    with open(capture, "xb") as handle:
        handle.write(content)
mode = sys.argv[-3]
bundle_digest = sys.argv[-1]
fault = os.environ.get("STAGE10_FAULT", "")
def package(name, status, version, architecture, candidate):
    return {
        "candidate_version": candidate,
        "error": "ok",
        "installed_architecture": architecture,
        "installed_version": version,
        "name": name,
        "selection": "install" if status == "installed" else "unknown",
        "status": status,
    }
document = {
    "actions": [],
    "apply_authorized": False,
    "architecture": "amd64",
    "apt_extended_states": "absent",
    "automatic": [],
    "candidate_scope": "host-cache-untrusted-for-apply",
    "conflicts": [
        package(name, "absent", None, None, None)
        for name in (
            "containerd", "docker-buildx", "docker-compose", "docker-compose-v2",
            "docker-doc", "docker.io", "podman-docker", "runc",
        )
    ],
    "docker_key": {
        "captured_sha256": "sha256:1500c1f56fa9e26b9b8f42452a553675796ade0807cdce11975eb98170b3a570",
        "primary_fingerprint": "9DC858229FC7DD38854AE2D88D81803C0EBFCD88",
        "signing_fingerprint": "D3306A018370199E527AE7997EA0A9C3F273FCD8",
        "state": "absent",
    },
    "docker_source": {
        "sha256": "sha256:47be0f749c19273936c7e56fff5a29b9108bcce8137ee677cc736523fb876e71",
        "state": "absent",
    },
    "dpkg_status": "absent",
    "foreign_docker_sources": [],
    "holds": [],
    "inventory_clean": False,
    "legacy_docker_keys": [],
    "manual": [],
    "operation": mode,
    "os_release_sha256": "sha256:" + "2" * 64,
    "packages": [
        package("ca-certificates", "installed", "1.0", "all", None),
        package("curl", "installed", "1.0", "amd64", "1.0"),
    ],
    "resolver_bundle_sha256": "sha256:" + bundle_digest,
    "schema_version": 1,
    "stage": "10-resolution",
    "stage05_bundle_sha256": "sha256:" + "1" * 64,
    "trust_packages": [
        package("ubuntu-keyring", "installed", "1.0", "all", None),
    ],
    "ubuntu_archive_keyring": "sha256:" + "3" * 64,
}
if fault == "extra-schema":
    document["unexpected"] = "must-not-cross-evidence-boundary"
elif fault == "bundle-mismatch":
    document["resolver_bundle_sha256"] = "sha256:" + "0" * 64
elif fault == "phase-divergence" and mode == "after":
    document["holds"] = ["changed-during-run"]
resolution = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\\n").encode("ascii")
digest = "sha256:" + hashlib.sha256(resolution).hexdigest()
encoded = base64.urlsafe_b64encode(resolution).decode("ascii")
print(
    "stage=10 operation=" + mode + " status=pass apply_authorized=no "
    "resolution_sha256=" + digest + " resolution_b64url=" + encoded
)
""",
            encoding="utf-8",
        )
        fake_ssh.chmod(0o700)
        environment = self.approval_environment()
        environment.update(
            {
                "CAPTURED_STAGE10_BUNDLE": str(captured),
                "DISPOSABLE_OVH_LAB": approvals[0],
                "PATH": f"{fake_bin}:/usr/bin:/bin",
            }
        )
        result = subprocess.run(
            [str(LAB / "run-stage.sh"), "10", "execute"],
            env=environment,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        bundle = captured.read_text(encoding="utf-8")
        for label, source in (
            ("RUNNER", ROOT / "src/kitdev_sandboxes/runner.py"),
            ("JOURNAL", ROOT / "src/kitdev_sandboxes/journal.py"),
            ("STAGE05", ROOT / "src/kitdev_sandboxes/stage05.py"),
            ("RESOLVER", ROOT / "src/kitdev_sandboxes/stage10.py"),
        ):
            digest = re.search(
                rf"readonly STAGE10_{label}_SHA256='([0-9a-f]{{64}})'", bundle
            )
            payload = re.search(
                rf"readonly STAGE10_{label}_B64='([A-Za-z0-9+/=]+)'", bundle
            )
            self.assertIsNotNone(digest)
            self.assertIsNotNone(payload)
            decoded = base64.b64decode(payload.group(1), validate=True)
            self.assertEqual(decoded, source.read_bytes())
            self.assertEqual(hashlib.sha256(decoded).hexdigest(), digest.group(1))
        self.assertNotIn("__STAGE10_", bundle)
        run_directory = next(
            Path(line.rsplit(" ", 1)[-1])
            for line in result.stdout.splitlines()
            if line.startswith("ovh-lab: stage passed; redacted evidence: ")
        )
        resolution = run_directory / "stage10-resolution.json"
        self.assertTrue(resolution.is_file())
        document = json.loads(resolution.read_text(encoding="ascii"))
        self.assertEqual(document["operation"], "execute")
        summary = (run_directory / "summary.txt").read_text(encoding="ascii")
        self.assertIn(
            "stage10_resolution_sha256=sha256:"
            + hashlib.sha256(resolution.read_bytes()).hexdigest()
            + "\n",
            summary,
        )
        shutil.rmtree(run_directory)

        rollback_approval = subprocess.run(
            [str(LAB / "run-stage.sh"), "10", "approval-rollback"],
            env=self.approval_environment(),
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        environment["DISPOSABLE_OVH_LAB"] = rollback_approval
        rollback = subprocess.run(
            [str(LAB / "run-stage.sh"), "10", "rollback"],
            env=environment,
            capture_output=True,
            text=True,
        )
        self.assertEqual(rollback.returncode, 0, rollback.stderr)
        rollback_directory = next(
            Path(line.rsplit(" ", 1)[-1])
            for line in rollback.stdout.splitlines()
            if line.startswith("ovh-lab: stage passed; redacted evidence: ")
        )
        rollback_document = json.loads(
            (rollback_directory / "stage10-resolution.json").read_text(encoding="ascii")
        )
        self.assertEqual(rollback_document["operation"], "rollback")
        shutil.rmtree(rollback_directory)

        environment["DISPOSABLE_OVH_LAB"] = approvals[0]
        for fault in ("extra-schema", "bundle-mismatch", "phase-divergence"):
            with self.subTest(fault=fault):
                environment["STAGE10_FAULT"] = fault
                rejected = subprocess.run(
                    [str(LAB / "run-stage.sh"), "10", "execute"],
                    env=environment,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(rejected.returncode, 74, rejected.stderr)
                self.assertIn("Stage 10 resolution evidence invalid", rejected.stderr)
                rejected_directory = Path(
                    rejected.stderr.rstrip().rsplit("redacted evidence: ", 1)[1]
                )
                self.assertFalse((rejected_directory / "stage10-resolution.json").exists())
                shutil.rmtree(rejected_directory)

    def test_stage05_execute_ssh_interruption_preserves_failure_and_collects_evidence(self) -> None:
        from kitdev_sandboxes.stage05 import build_plan

        approval = subprocess.run(
            [str(LAB / "run-stage.sh"), "05", "approval"],
            env=self.approval_environment(),
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        bundle_digest = approval.split(":")[4]
        plan_hash = build_plan(bundle_digest).plan_hash
        fake_bin = Path(self.private_directory.name) / "interrupt-bin"
        fake_bin.mkdir()
        record = Path(self.private_directory.name) / "interrupt-record"
        fake_ssh = fake_bin / "ssh"
        fake_ssh.write_text(
            """#!/usr/bin/python3
import os
import sys

sys.stdin.buffer.read()
mode = sys.argv[-3]
with open(os.environ["INTERRUPT_RECORD"], "a", encoding="ascii") as handle:
    handle.write(mode + "\\n")
if mode == "execute":
    raise SystemExit(143)
print("stage=05 operation=" + mode + " status=pass plan_sha256=" + os.environ["EXPECTED_PLAN"])
""",
            encoding="utf-8",
        )
        fake_ssh.chmod(0o700)
        environment = self.approval_environment()
        environment.update(
            {
                "DISPOSABLE_OVH_LAB": approval,
                "EXPECTED_PLAN": plan_hash,
                "INTERRUPT_RECORD": str(record),
                "PATH": f"{fake_bin}:/usr/bin:/bin",
            }
        )
        result = subprocess.run(
            [str(LAB / "run-stage.sh"), "05", "execute"],
            env=environment,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 143)
        self.assertEqual(record.read_text(encoding="ascii").splitlines(), ["before", "execute", "after", "postconditions"])
        run_directory = Path(result.stderr.rstrip().rsplit("evidence: ", 1)[1])
        summary = (run_directory / "summary.txt").read_text(encoding="utf-8")
        self.assertIn("operation_rc=143\nafter_rc=0\npostconditions_rc=0\n", summary)
        self.assertNotIn("stage05_plan_sha256=", summary)
        shutil.rmtree(run_directory)

    def test_ssh_config_rejects_relative_symlink_directory_open_mode_oversize_and_include(self) -> None:
        symlink = Path(self.private_directory.name) / "ssh-config-link"
        symlink.symlink_to(self.ssh_config)
        directory = Path(self.private_directory.name) / "config-directory"
        directory.mkdir()
        open_mode = Path(self.private_directory.name) / "ssh-config-open"
        open_mode.write_text("Host reviewed-alias\n", encoding="utf-8")
        open_mode.chmod(0o640)
        oversized = Path(self.private_directory.name) / "ssh-config-oversized"
        oversized.write_bytes(b"x" * 1048577)
        oversized.chmod(0o600)
        included = Path(self.private_directory.name) / "ssh-config-include"
        included.write_text("Include private-fragment\nHost reviewed-alias\n", encoding="utf-8")
        included.chmod(0o600)
        cases: tuple[Path | str, ...] = (
            self.ssh_config.name,
            symlink,
            directory,
            open_mode,
            oversized,
            included,
        )
        for config in cases:
            result = subprocess.run(
                [str(LAB / "run-stage.sh"), "00", "approval"],
                cwd=self.private_directory.name,
                env=self.approval_environment(config),
                capture_output=True,
                text=True,
            )
            with self.subTest(config=config):
                self.assertEqual(result.returncode, 64)
                self.assertIn("OVH_LAB_SSH_CONFIG must be a stable", result.stderr)
                self.assertEqual(result.stdout, "")

    def test_ssh_config_validation_checks_current_uid_and_no_follow_descriptor(self) -> None:
        text = (LAB / "run-stage.sh").read_text(encoding="utf-8")
        self.assertIn("metadata.st_uid != os.geteuid()", text)
        self.assertIn('hasattr(os, "O_NOFOLLOW")', text)
        self.assertIn("os.O_NOFOLLOW", text)
        self.assertIn('input_kind == "ssh_config"', text)
        self.assertIn("mode & 0o077", text)
        self.assertIn("1048577 - len(content)", text)
        self.assertIn('re.match(br"(?i:include)', text)

    def test_execution_uses_distinct_private_input_snapshots(self) -> None:
        text = (LAB / "run-stage.sh").read_text(encoding="utf-8")
        run_remote = text.split("run_remote()", maxsplit=1)[1]
        self.assertIn('SSH_CONFIG_SNAPSHOT="$RUN_DIR/private-ssh-config"', text)
        self.assertIn('KNOWN_HOSTS_SNAPSHOT="$RUN_DIR/private-known-hosts"', text)
        self.assertIn("os.O_EXCL | os.O_NOFOLLOW", text)
        self.assertIn("stat.S_IMODE(snapshot_stat.st_mode) != 0o600", text)
        self.assertIn("guarded input snapshots must be distinct files", text)
        self.assertIn('rm -f -- "$SSH_CONFIG_SNAPSHOT" "$KNOWN_HOSTS_SNAPSHOT"', text)
        self.assertIn('-F "$SSH_CONFIG_SNAPSHOT"', run_remote)
        self.assertIn('-o "UserKnownHostsFile=$KNOWN_HOSTS_SNAPSHOT"', run_remote)
        self.assertNotIn('-F "$SSH_CONFIG"', run_remote)
        self.assertNotIn('UserKnownHostsFile=$KNOWN_HOSTS"', run_remote)

    def test_known_hosts_is_required_and_rejects_unsafe_sources(self) -> None:
        symlink = Path(self.private_directory.name) / "known-hosts-link"
        symlink.symlink_to(self.known_hosts)
        directory = Path(self.private_directory.name) / "known-hosts-directory"
        directory.mkdir()
        group_writable = Path(self.private_directory.name) / "known-hosts-group-writable"
        group_writable.write_text("test host key\n", encoding="utf-8")
        group_writable.chmod(0o664)
        executable = Path(self.private_directory.name) / "known-hosts-executable"
        executable.write_text("test host key\n", encoding="utf-8")
        executable.chmod(0o744)
        special_mode = Path(self.private_directory.name) / "known-hosts-special-mode"
        special_mode.write_text("test host key\n", encoding="utf-8")
        special_mode.chmod(0o4644)
        oversized = Path(self.private_directory.name) / "known-hosts-oversized"
        oversized.write_bytes(b"x" * 1048577)
        oversized.chmod(0o600)
        cases: tuple[Path | str, ...] = (
            "",
            self.known_hosts.name,
            symlink,
            directory,
            group_writable,
            executable,
            special_mode,
            oversized,
        )
        for known_hosts in cases:
            result = subprocess.run(
                [str(LAB / "run-stage.sh"), "00", "approval"],
                cwd=self.private_directory.name,
                env=self.approval_environment(known_hosts=known_hosts),
                capture_output=True,
                text=True,
            )
            with self.subTest(known_hosts=known_hosts):
                self.assertEqual(result.returncode, 64)
                self.assertIn("OVH_LAB_KNOWN_HOSTS must be stable", result.stderr)
                self.assertEqual(result.stdout, "")

    def test_known_hosts_allows_read_only_group_and_other_permissions(self) -> None:
        for mode in (0o400, 0o440, 0o444, 0o600, 0o640, 0o644):
            self.known_hosts.chmod(mode)
            result = subprocess.run(
                [str(LAB / "run-stage.sh"), "00", "approval"],
                env=self.approval_environment(),
                capture_output=True,
                text=True,
            )
            with self.subTest(mode=oct(mode)):
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_config_and_known_hosts_sources_cannot_alias(self) -> None:
        result = subprocess.run(
            [str(LAB / "run-stage.sh"), "00", "approval"],
            env=self.approval_environment(known_hosts=self.ssh_config),
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 64)
        self.assertIn("SSH config and known_hosts must be distinct files", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_known_hosts_path_and_content_are_not_logged(self) -> None:
        result = subprocess.run(
            [str(LAB / "run-stage.sh"), "00", "approval"],
            env=self.approval_environment(),
            capture_output=True,
            text=True,
            check=True,
        )
        combined = result.stdout + result.stderr
        self.assertNotIn(str(self.known_hosts), combined)
        self.assertNotIn("AAAAC3NzaC1lZDI1NTE5AAAAITestOnly", combined)

    def test_execution_uses_stable_snapshots_and_cleans_them(self) -> None:
        fake_bin = Path(self.private_directory.name) / "bin"
        fake_bin.mkdir()
        record = Path(self.private_directory.name) / "ssh-record"
        fake_ssh = fake_bin / "ssh"
        fake_ssh.write_text(
            """#!/usr/bin/python3
import os
import stat
import sys

config = None
known_hosts = None
options = set()
arguments = iter(sys.argv[1:])
for argument in arguments:
    if argument == "-F":
        config = next(arguments, None)
    elif argument == "-o":
        option = next(arguments, "")
        options.add(option)
        if option.startswith("UserKnownHostsFile="):
            known_hosts = option.split("=", 1)[1]
if not config or not known_hosts or config == known_hosts:
    raise SystemExit(91)
required_options = {
    "GlobalKnownHostsFile=/dev/null",
    "KnownHostsCommand=none",
    "StrictHostKeyChecking=yes",
    "UpdateHostKeys=no",
    "VerifyHostKeyDNS=no",
}
if not required_options.issubset(options):
    raise SystemExit(95)
for path in (config, known_hosts):
    metadata = os.stat(path, follow_symlinks=False)
    if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600:
        raise SystemExit(92)
if open(config, "rb").read() != os.environ["EXPECTED_CONFIG"].encode():
    raise SystemExit(93)
if open(known_hosts, "rb").read() != os.environ["EXPECTED_KNOWN_HOSTS"].encode():
    raise SystemExit(94)
with open(os.environ["SSH_RECORD"], "a", encoding="utf-8") as handle:
    handle.write(config + "\\t" + known_hosts + "\\n")
sys.stdin.buffer.read()
""",
            encoding="utf-8",
        )
        fake_ssh.chmod(0o700)
        approval = subprocess.run(
            [str(LAB / "run-stage.sh"), "00", "approval"],
            env=self.approval_environment(),
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        environment = self.approval_environment()
        environment.update(
            {
                "DISPOSABLE_OVH_LAB": approval,
                "EXPECTED_CONFIG": self.ssh_config.read_text(encoding="utf-8"),
                "EXPECTED_KNOWN_HOSTS": self.known_hosts.read_text(encoding="utf-8"),
                "PATH": f"{fake_bin}:/usr/bin:/bin",
                "SSH_RECORD": str(record),
            }
        )
        result = subprocess.run(
            [str(LAB / "run-stage.sh"), "00", "execute"],
            env=environment,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        invocations = [line.split("\t") for line in record.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(invocations), 4)
        self.assertTrue(all(paths == invocations[0] for paths in invocations))
        config_snapshot, known_hosts_snapshot = map(Path, invocations[0])
        self.assertNotEqual(config_snapshot, known_hosts_snapshot)
        self.assertFalse(config_snapshot.exists())
        self.assertFalse(known_hosts_snapshot.exists())
        run_directory = config_snapshot.parent
        self.assertEqual(stat.S_IMODE(run_directory.stat().st_mode), 0o700)
        summary = (run_directory / "summary.txt").read_text(encoding="utf-8")
        evidence = (run_directory / "evidence.log").read_text(encoding="utf-8")
        expected_known_hosts_hash = hashlib.sha256(self.known_hosts.read_bytes()).hexdigest()
        self.assertIn(f"known_hosts_sha256={expected_known_hosts_hash}\n", summary)
        retained_output = summary + evidence
        self.assertNotIn(str(self.ssh_config), retained_output)
        self.assertNotIn(str(self.known_hosts), retained_output)
        self.assertNotIn("AAAAC3NzaC1lZDI1NTE5AAAAITestOnly", retained_output)
        shutil.rmtree(run_directory)

    def test_ssh_config_change_invalidates_prior_approval(self) -> None:
        first = subprocess.run(
            [str(LAB / "run-stage.sh"), "00", "approval"],
            env=self.approval_environment(),
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        self.ssh_config.write_text("Host reviewed-alias\n  BatchMode yes\n  Compression no\n", encoding="utf-8")
        self.ssh_config.chmod(0o600)
        second = subprocess.run(
            [str(LAB / "run-stage.sh"), "00", "approval"],
            env=self.approval_environment(),
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        self.assertNotEqual(first, second)

    def test_known_hosts_change_invalidates_prior_approval(self) -> None:
        first = subprocess.run(
            [str(LAB / "run-stage.sh"), "00", "approval"],
            env=self.approval_environment(),
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        self.known_hosts.write_text(
            "reviewed-alias ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIChanged\n",
            encoding="utf-8",
        )
        self.known_hosts.chmod(0o644)
        second = subprocess.run(
            [str(LAB / "run-stage.sh"), "00", "approval"],
            env=self.approval_environment(),
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        self.assertNotEqual(first, second)

    def test_manifest_blocked_stage_cannot_generate_approval(self) -> None:
        for stage in self.manifest["stages"]:
            if stage["status"] != "blocked":
                continue
            result = subprocess.run(
                [str(LAB / "run-stage.sh"), stage["id"], "approval"],
                env=self.approval_environment(),
                capture_output=True,
                text=True,
            )
            with self.subTest(stage=stage["id"]):
                self.assertEqual(result.returncode, 20)
                self.assertIn("stage is blocked", result.stderr)
                self.assertEqual(result.stdout, "")

    def test_remote_phases_stream_one_immutable_approved_bundle(self) -> None:
        text = (LAB / "run-stage.sh").read_text(encoding="utf-8")
        run_remote = text.split("run_remote()", maxsplit=1)[1]
        self.assertIn("readonly BUNDLE_CONTENT", text)
        self.assertIn("BUNDLE_CONTENT", run_remote)
        self.assertNotIn("bundle_stage |", run_remote)

    def test_framework_contains_no_literal_endpoint_or_private_key(self) -> None:
        ipv4 = re.compile(r"(?<![0-9])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9])")
        private_key = "-----BEGIN " + "PRIVATE KEY-----"
        for path in [LAB / "README.md", LAB / "stages.json", LAB / "run-stage.sh", LAB / "lib" / "common.sh", *self.stage_files]:
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                self.assertIsNone(ipv4.search(text))
                self.assertNotIn(private_key, text)


if __name__ == "__main__":
    unittest.main()

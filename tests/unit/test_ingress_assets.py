from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
NGINX = ROOT / "config" / "ingress" / "nginx.conf"
COMPOSE = ROOT / "compose" / "ingress" / "compose.yaml"
SCRIPTS = ROOT / "scripts" / "ingress"


class IngressAssetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.nginx = NGINX.read_text(encoding="ascii")
        self.compose = COMPOSE.read_text(encoding="ascii")

    def test_image_is_exact_linux_amd64_manifest(self) -> None:
        digest = "08fe94b0d1e72fc687840f5696f6e107a85c327b1bcb8a7acc22f8c100227c67"
        self.assertIn(f"nginx:1.29.6-alpine3.23@sha256:{digest}", self.compose)
        self.assertIn(digest, (ROOT / "versions.lock.yaml").read_text(encoding="ascii"))
        self.assertIn("pull_policy: never", self.compose)

    def test_container_is_minimal_and_uses_only_expected_host_mounts(self) -> None:
        for value in (
            "network_mode: host",
            "- ALL",
            "- NET_BIND_SERVICE",
            "- no-new-privileges:true",
            "read_only: true",
            "/etc/kitdev-sandboxes/ingress/nginx.conf:/etc/nginx/nginx.conf:ro",
            "/etc/kitdev-sandboxes/ingress/tls:/run/kitdev-tls:ro",
        ):
            self.assertIn(value, self.compose)
        self.assertNotIn("docker.sock", self.compose)

    def test_routes_only_api_shared_and_strict_sandbox_hosts(self) -> None:
        self.assertEqual(self.nginx.count("api.sandbox.kitdev.ai"), 2)
        self.assertEqual(self.nginx.count("sandbox.sandbox.kitdev.ai"), 2)
        pattern_text = re.findall(r'"~(\^.*?\\\.ai\$)"', self.nginx)
        self.assertEqual(len(pattern_text), 2)
        pattern = re.compile(pattern_text[0].replace(r"\.", "."))
        sandbox_id = "i" + "a" * 20
        for port in (1, 80, 443, 49983, 65535):
            self.assertRegex(f"{port}-{sandbox_id}.sandbox.kitdev.ai", pattern)
        for host in (
            f"0-{sandbox_id}.sandbox.kitdev.ai",
            f"65536-{sandbox_id}.sandbox.kitdev.ai",
            f"49983-{sandbox_id.upper()}.sandbox.kitdev.ai",
            f"49983-{sandbox_id}.evil.sandbox.kitdev.ai",
            "api.evil.example",
        ):
            self.assertNotRegex(host, pattern)
        self.assertIn("return 444;", self.nginx)
        self.assertIn("ssl_reject_handshake on;", self.nginx)

    def test_proxy_contract_preserves_streaming_and_routing(self) -> None:
        self.assertIn("proxy_pass http://127.0.0.1:3000", self.nginx)
        self.assertIn("proxy_pass http://127.0.0.1:3002", self.nginx)
        self.assertEqual(self.nginx.count("proxy_set_header Host $host"), 2)
        self.assertIn("proxy_set_header Upgrade $http_upgrade", self.nginx)
        self.assertIn("proxy_set_header E2b-Sandbox-Id $http_e2b_sandbox_id", self.nginx)
        self.assertIn("proxy_set_header E2b-Sandbox-Port $http_e2b_sandbox_port", self.nginx)
        self.assertEqual(self.nginx.count("proxy_request_buffering off"), 2)
        self.assertEqual(self.nginx.count("proxy_buffering off"), 2)
        self.assertIn("proxy_read_timeout 24h", self.nginx)
        self.assertIn("client_max_body_size 1g", self.nginx)

    def test_limits_headers_and_logs_are_credential_safe(self) -> None:
        for value in (
            "limit_req zone=kitdev_api_rate",
            "limit_req zone=kitdev_sandbox_rate",
            "limit_conn kitdev_per_ip",
            'Strict-Transport-Security "max-age=31536000" always',
            "X-Content-Type-Options nosniff always",
            "Referrer-Policy no-referrer always",
        ):
            self.assertIn(value, self.nginx)
        log_format = self.nginx.split("log_format kitdev", 1)[1].split(";", 1)[0]
        for forbidden in ("$request_uri", "$uri", "$args", "$http_"):
            self.assertNotIn(forbidden, log_format)
        self.assertIsNone(re.search(r"\$request(?:[^_a-z]|$)", log_format))

    def test_certificate_runner_does_not_source_credentials(self) -> None:
        runner = (SCRIPTS / "run_lego.py").read_text(encoding="ascii")
        manager = (SCRIPTS / "manage-certificate.sh").read_text(encoding="ascii")
        self.assertIn("O_NOFOLLOW", runner)
        self.assertIn("metadata.st_nlink != 1", runner)
        self.assertIn('f"*.{domain}"', runner)
        self.assertNotIn("shell=True", runner)
        self.assertNotIn("source $", manager)
        self.assertIn("issued_certificate_invalid", manager)
        self.assertLess(manager.index("issued_certificate_invalid"), manager.index("mv -f"))
        self.assertIn("docker kill --signal HUP", manager)
        for unit in (
            "kitdev-e2b-ingress.service",
            "kitdev-e2b-ingress-renew.service",
        ):
            self.assertIn(
                "Environment=KITDEV_LIFECYCLE=production",
                (ROOT / "systemd" / unit).read_text(encoding="ascii"),
            )

    def test_firewall_owns_rules_by_exact_comment_and_preserves_foreign_rules(self) -> None:
        firewall = (SCRIPTS / "configure-firewall.sh").read_text(encoding="ascii")
        self.assertIn('"kitdev public ingress http"', firewall)
        self.assertIn('"kitdev public ingress https"', firewall)
        self.assertIn("observed == expected", firewall)
        self.assertIn("elif verify_rules absent", firewall)
        self.assertIn("delete allow 80/tcp comment 'kitdev public ingress http'", firewall)
        self.assertIn("delete allow 443/tcp comment 'kitdev public ingress https'", firewall)
        self.assertIn("public_internal_listener_detected", firewall)

    def test_firewall_verifier_rejects_foreign_and_duplicate_ingress_rules(self) -> None:
        firewall = (SCRIPTS / "configure-firewall.sh").read_text(encoding="ascii")
        verifier = firewall.split("<<'PY_VERIFY_INGRESS_UFW'\n", 1)[1].split(
            "\nPY_VERIFY_INGRESS_UFW", 1
        )[0]
        http = "ufw allow 80/tcp comment 'kitdev public ingress http'"
        https = "ufw allow 443/tcp comment 'kitdev public ingress https'"

        def run(policy: str, rules: str) -> subprocess.CompletedProcess[str]:
            with TemporaryDirectory(dir=ROOT) as directory:
                verifier_path = Path(directory) / "verify.py"
                verifier_path.write_text(verifier, encoding="ascii")
                return subprocess.run(
                    [
                        "bash",
                        "-c",
                        'python3 -I -B -S "$1" "$2" 3<<<"$3"',
                        "_",
                        str(verifier_path),
                        policy,
                        rules,
                    ],
                    capture_output=True,
                    text=True,
                )

        self.assertEqual(run("absent", "ufw allow 22/tcp").returncode, 0)
        self.assertEqual(run("exact", f"{http}\n{https}\nufw allow 22/tcp").returncode, 0)
        self.assertNotEqual(run("absent", "ufw allow 80/tcp").returncode, 0)
        self.assertNotEqual(run("exact", f"{http}\n{http}\n{https}").returncode, 0)
        self.assertNotEqual(run("absent", "ufw allow 3000/tcp").returncode, 0)
        self.assertEqual(
            run(
                "absent",
                "ufw allow in on veth+ from 10.11.0.0/16 to any port 5007 proto tcp",
            ).returncode,
            0,
        )

    def test_all_ingress_shell_scripts_parse_and_are_strict(self) -> None:
        scripts = sorted(SCRIPTS.glob("*.sh"))
        self.assertTrue(scripts)
        for script in scripts:
            with self.subTest(script=script.name):
                text = script.read_text(encoding="ascii")
                self.assertIn("set -Eeuo pipefail", text)
                subprocess.run(["bash", "-n", str(script)], check=True, capture_output=True)


if __name__ == "__main__":
    unittest.main()

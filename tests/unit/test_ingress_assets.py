from __future__ import annotations

import json
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
        # Exactly the capabilities nginx needs for root-bind plus unprivileged
        # workers. Anything beyond this list is a hardening regression.
        granted = re.findall(r"^      - ([A-Z_]+)$", self.compose, re.MULTILINE)
        self.assertEqual(
            sorted(name for name in granted if name != "ALL"),
            ["CHOWN", "KILL", "NET_BIND_SERVICE", "SETGID", "SETUID"],
        )
        for forbidden in ("SYS_ADMIN", "DAC_OVERRIDE", "NET_ADMIN", "SYS_PTRACE"):
            self.assertNotIn(forbidden, self.compose)

    def test_routes_only_api_shared_and_strict_sandbox_hosts(self) -> None:
        self.assertEqual(self.nginx.count("api.sandbox.kitdev.ai"), 1)
        self.assertEqual(self.nginx.count("sandbox.sandbox.kitdev.ai"), 1)
        pattern_text = re.findall(r'"~(\^.*?\\\.ai\$)"', self.nginx)
        self.assertEqual(len(pattern_text), 1)
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
        self.assertIn("ssl_reject_handshake on;", self.nginx)
        self.assertNotIn("listen 80", self.nginx)
        self.assertEqual(self.nginx.count("listen [::]:443 ssl"), 3)

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

    def test_unmanaged_control_plane_firewall_is_development_only(self) -> None:
        firewall = (SCRIPTS / "configure-firewall.sh").read_text(encoding="ascii")
        body = firewall.split("verify_control_plane_firewall() {", 1)[1].split("\n}", 1)[0]
        self.assertIn('"${KITDEV_UNMANAGED_CONTROL_PLANE_FIREWALL:-}" == acknowledged', body)
        # The concession must refuse outside development and must announce itself.
        self.assertIn('[[ "${KITDEV_LIFECYCLE:-}" == development ]] || return 1', body)
        self.assertIn("warning=unmanaged_control_plane_firewall", body)
        self.assertLess(
            body.index("KITDEV_LIFECYCLE"),
            body.index('[[ ! -L "$CONTROL_PLANE_FIREWALL"'),
        )
        # It must not disable any substantive check.
        for required in (
            "verify_ufw_defaults || control_plane_die ufw_default_policy_mismatch 65",
            "verify_ufw_ipv6 || control_plane_die ufw_ipv6_required 65",
            "verify_listeners || control_plane_die public_internal_listener_detected 65",
            "verify_docker_publications || control_plane_die public_docker_ingress_detected 65",
        ):
            self.assertIn(required, firewall)

    def test_docker_templates_are_not_backslash_escaped(self) -> None:
        # Inside a single-quoted shell string a backslash is literal, so
        # {{index .Config.Labels \"...\"}} reaches Go as an invalid template.
        # docker inspect then fails, which silently disabled the certificate
        # reload because that call is guarded by `|| true`.
        for name in ("install-ingress.sh", "manage-certificate.sh"):
            text = (SCRIPTS / name).read_text(encoding="ascii")
            for line in text.splitlines():
                if "docker inspect" in line or "{{" in line:
                    self.assertNotIn('\\"', line, f"{name}: escaped quote in Go template")

    def test_unmanaged_firewall_dropin_is_converged_both_ways(self) -> None:
        installer = (SCRIPTS / "install-ingress.sh").read_text(encoding="ascii")
        dropin = (
            ROOT / "systemd" / "kitdev-e2b-ingress.service.d" / "kitdev-unmanaged-firewall.conf"
        ).read_text(encoding="ascii")
        self.assertIn("Environment=KITDEV_LIFECYCLE=development", dropin)
        self.assertIn(
            "Environment=KITDEV_UNMANAGED_CONTROL_PLANE_FIREWALL=acknowledged", dropin
        )
        guard = installer.split("unmanaged_firewall_acknowledged() {", 1)[1].split("\n}", 1)[0]
        self.assertIn('"${KITDEV_LIFECYCLE:-}" == development', guard)
        # Absent acknowledgement the drop-in must be removed, not merely skipped,
        # so an unacknowledged run converges a host back to the strict unit.
        converge = installer.split("converge_dropin() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("update_exact_file", converge)
        self.assertIn("remove_exact_file", converge)
        self.assertIn("unmanaged_firewall_dropin_present", installer)
        self.assertIn('remove_exact_file "$DROPIN_SOURCE"', installer)

    def test_installer_validates_the_installed_file_not_the_release_tree(self) -> None:
        installer = (SCRIPTS / "install-ingress.sh").read_text(encoding="ascii")
        # require_exact_file stats its first argument. Passing the release tree
        # first validated the checkout's mode and made verify and remove fail
        # on the mode-0600 operator examples.
        self.assertIn(
            'require_exact_file "$INSTALLED_DIR/$name" "$SCRIPT_DIR/$name" root root 755',
            installer,
        )
        self.assertIn(
            'require_exact_file "$INGRESS_ETC/ingress.env.example" \\\n'
            '    "$REPO_ROOT/config/ingress/ingress.env.template" root root 600',
            installer,
        )
        self.assertNotIn(
            'require_exact_file "$SCRIPT_DIR/$name" "$INSTALLED_DIR/$name"',
            installer,
        )
        remove = installer.split("remove_exact_file() {", 1)[1].split("}", 1)[0]
        self.assertIn('require_exact_file "$target" "$source"', remove)

    def test_installer_can_converge_a_changed_release_asset(self) -> None:
        installer = (SCRIPTS / "install-ingress.sh").read_text(encoding="ascii")
        self.assertIn("case \"$mode\" in stage|update|apply|verify|remove", installer)
        update = installer.split("update_exact_file() {", 1)[1].split("\n}", 1)[0]
        # Ownership must be proved before the installed bytes are replaced.
        self.assertLess(update.index("file_metadata_conflict"), update.index("mv -f"))
        self.assertIn("stat -c '%u:%g:%a:%h'", update)
        self.assertLess(update.index("mv -f"), update.index("require_exact_file"))

    def test_certificate_runner_does_not_source_credentials(self) -> None:
        acquisition = (SCRIPTS / "acquire-artifacts.sh").read_text(encoding="ascii")
        runner = (SCRIPTS / "run_lego.py").read_text(encoding="ascii")
        manager = (SCRIPTS / "manage-certificate.sh").read_text(encoding="ascii")
        self.assertIn("O_NOFOLLOW", runner)
        self.assertIn("metadata.st_nlink != 1", runner)
        self.assertIn('f"*.{domain}"', runner)
        self.assertNotIn("shell=True", runner)
        # The pinned lego 5.x CLI takes these as `run` subcommand flags and has
        # no `renew` command. The 4.x layout silently fails at issuance time.
        self.assertLess(runner.index('"run",'), runner.index('"--accept-tos",'))
        self.assertIn('"--renew-days", "30", "--no-random-sleep"', runner)
        self.assertNotIn('"--days"', runner)
        self.assertNotIn("source $", manager)
        self.assertIn("issued_certificate_invalid", manager)
        self.assertLess(manager.index("issued_certificate_invalid"), manager.index("mv -f"))
        self.assertIn("docker kill --signal HUP", manager)
        self.assertLess(acquisition.index("lego_archive_hash_mismatch"), acquisition.index("chmod 0755"))
        self.assertLess(acquisition.index("chmod 0755"), acquisition.index('"$stage/lego" --version'))
        self.assertIn('$KITDEV_OPT_ROOT/bin/.ingress-artifacts.', acquisition)
        self.assertNotIn("/run/kitdev-sandboxes/ingress-artifacts", acquisition)
        self.assertIn("-depth -delete", acquisition)
        self.assertNotIn("${stage:-}", acquisition)
        provider_example = (ROOT / "config" / "ingress" / "acme-provider.env.example").read_text(
            encoding="ascii"
        )
        self.assertIn(
            "CLOUDFLARE_DNS_API_TOKEN_FILE="
            "/etc/kitdev-sandboxes/ingress/cloudflare-dns-api-token",
            provider_example,
        )
        self.assertNotIn("CF_DNS_API_TOKEN=", provider_example)
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
        self.assertIn("kitdev restricted ingress https", firewall)
        self.assertIn("kitdev public ingress https explicit", firewall)
        self.assertIn("KITDEV-INGRESS", firewall)
        self.assertIn("observed == expected", firewall)
        self.assertIn("source_firewall_transaction_failed", firewall)
        self.assertIn("source_firewall_rollback_failed", firewall)
        self.assertIn("--ctorigdstport", firewall)
        self.assertIn("docker http deny", firewall)
        self.assertIn("ufw_default_policy_mismatch", firewall)
        self.assertIn("public_internal_listener_detected", firewall)
        self.assertIn("public_docker_ingress_detected", firewall)
        self.assertNotIn("ufw allow 80", firewall)
        self.assertIn('candidate-mode "$mode"', firewall)
        self.assertIn("control_plane_firewall_mismatch", firewall)
        self.assertIn('"$CONTROL_PLANE_FIREWALL" verify', firewall)
        listener_verifier = firewall.split("<<'PY_VERIFY_INGRESS_LISTENERS'\n", 1)[1].split(
            "\nPY_VERIFY_INGRESS_LISTENERS", 1
        )[0]
        self.assertNotIn("5007", listener_verifier)
        self.assertNotIn("5008", listener_verifier)
        self.assertNotIn("5010", listener_verifier)

    def test_firewall_lock_and_list_are_fail_closed(self) -> None:
        firewall = (SCRIPTS / "configure-firewall.sh").read_text(encoding="ascii")
        lock = firewall.split("open_firewall_lock() {", 1)[1].split(
            "\n}\n\nguard_required", 1
        )[0]
        self.assertIn("os.O_EXCL", lock)
        self.assertIn("os.O_NOFOLLOW", lock)
        self.assertIn("metadata.st_nlink != 1", lock)
        self.assertIn("metadata.st_size != 0", lock)
        self.assertIn("/proc/self/fd/9", lock)
        source_list = firewall.split('if [[ "$mode" == source-list ]]', 1)[1].split(
            'if [[ "$mode" == source-add ]]', 1
        )[0]
        self.assertLess(
            source_list.index("verify_system_rules"),
            source_list.index('"$SOURCE_STATE" list'),
        )

    def test_firewall_transaction_commits_manifest_last_and_has_rollback(self) -> None:
        firewall = (SCRIPTS / "configure-firewall.sh").read_text(encoding="ascii")
        transaction = firewall.split("mutate_sources() {", 1)[1].split("\n}\n\nmain()", 1)[0]
        self.assertLess(
            transaction.index('transition_system_rules "$old_file" "$new_file"'),
            transaction.index('install-file "$new_file"'),
        )
        self.assertIn('transition_system_rules "$new_file" "$old_file"', transaction)
        self.assertIn("source_manifest_commit_failed", transaction)
        transition = firewall.split("transition_system_rules() {", 1)[1].split(
            "\n}\n\nmutate_sources", 1
        )[0]
        self.assertIn('cleanup_candidate_rules "$new_file"', transition)
        self.assertIn('add_system_rules "$old_file"', transition)
        failed_remove = transition.split('&& ! remove_system_rules "$old_file"', 1)[1].split(
            "\n  fi", 1
        )[0]
        self.assertIn('cleanup_candidate_rules "$old_file"', failed_remove)
        self.assertIn('add_system_rules "$old_file"', failed_remove)

    def test_firewall_verifier_rejects_foreign_and_duplicate_ingress_rules(self) -> None:
        firewall = (SCRIPTS / "configure-firewall.sh").read_text(encoding="ascii")
        verifier = firewall.split("<<'PY_VERIFY_INGRESS_UFW'\n", 1)[1].split(
            "\nPY_VERIFY_INGRESS_UFW", 1
        )[0]
        https = (
            "ufw allow proto tcp from 8.8.8.8/32 to any port 443 "
            "comment 'kitdev restricted ingress https'"
        )

        def run(
            policy: str,
            rules: str,
            sources: tuple[str, ...] = (),
        ) -> subprocess.CompletedProcess[str]:
            with TemporaryDirectory(dir=ROOT) as directory:
                verifier_path = Path(directory) / "verify.py"
                verifier_path.write_text(verifier, encoding="ascii")
                source_path = Path(directory) / "sources.json"
                source_path.write_text(
                    json.dumps(
                        {
                            "schema_version": 1,
                            "sources": [
                                {
                                    "cidr": source,
                                    "non_public_override": False,
                                    "broad_range_override": False,
                                }
                                for source in sources
                            ],
                        }
                    ),
                    encoding="ascii",
                )
                return subprocess.run(
                    [
                        "bash",
                        "-c",
                        'python3 -I -B -S "$1" "$2" "$3" '
                        '"kitdev restricted ingress https" "22" 3<<<"$4"',
                        "_",
                        str(verifier_path),
                        policy,
                        str(source_path),
                        rules,
                    ],
                    capture_output=True,
                    text=True,
                )

        self.assertEqual(run("absent", "ufw allow 22/tcp").returncode, 0)
        self.assertEqual(
            run("restricted", f"{https}\nufw allow 22/tcp", ("8.8.8.8/32",)).returncode,
            0,
        )
        self.assertNotEqual(run("absent", "ufw allow 80/tcp").returncode, 0)
        self.assertNotEqual(
            run("restricted", f"{https}\n{https}\nufw allow 22/tcp", ("8.8.8.8/32",)).returncode,
            0,
        )
        public = "ufw allow 443/tcp comment 'kitdev public ingress https explicit'"
        self.assertEqual(run("public", f"{public}\nufw allow 22/tcp").returncode, 0)
        self.assertNotEqual(run("absent", "ufw allow 3000/tcp").returncode, 0)
        self.assertEqual(
            run(
                "absent",
                "ufw allow 22/tcp\n"
                "ufw allow in on veth+ from 10.11.0.0/16 to any port 5007 proto tcp",
            ).returncode,
            0,
        )
        self.assertNotEqual(run("absent", "ufw deny 22/tcp").returncode, 0)

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

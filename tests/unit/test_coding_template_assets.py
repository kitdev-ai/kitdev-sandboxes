from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "control-plane" / "verify-typescript-sdk-coding-template.sh"
CLIENT = ROOT / "scripts" / "control-plane" / "e2e-typescript-sdk" / "coding-template.ts"


class CodingTemplateAssetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = RUNNER.read_text(encoding="ascii")
        self.client = CLIENT.read_text(encoding="ascii")

    def test_toolchain_inputs_and_resources_are_exact(self) -> None:
        self.assertIn(
            "e2bdev/base@sha256:"
            "4a369f01a820fe5e65f53c2c5727a78899daf86f0541b721097f289559c8b73f",
            self.client,
        )
        self.assertIn("node-v22.18.0-linux-x64.tar.xz", self.client)
        self.assertIn(
            "c1bfeecf1d7404fa74728f9db72e697decbd8119ccc6f5a294d795756dfcfca7",
            self.client,
        )
        self.assertIn("sha256sum -c -", self.client)
        self.assertIn("cpuCount: 2", self.client)
        self.assertIn("memoryMB: 2048", self.client)

    def test_template_is_nonroot_workspace_ready(self) -> None:
        self.assertIn('.setWorkdir("/home/user/workspace")', self.client)
        self.assertIn('.setUser("user")', self.client)
        self.assertIn("kitdev-coding-ready", self.client)
        self.assertIn("pgrep -u user -x sleep", self.client)
        self.assertIn("user:user:755", self.client)
        self.assertIn("id -u", self.client)

    def test_live_client_covers_coding_surface(self) -> None:
        for expected in (
            "Template.build(",
            "Sandbox.create(",
            "sandbox.files.write(",
            "sandbox.files.read(",
            "node main.ts",
            "sandbox.commands.run(",
            "sandbox.pty.create(",
            "sandbox.pty.sendInput(",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, self.client)
        error_block = self.client.split("main().catch", maxsplit=1)[1]
        self.assertNotIn("error.message", error_block)

    def test_runner_is_locked_credential_safe_and_rerunnable(self) -> None:
        self.assertIn("typescript-sdk-e2e.lock", self.runner)
        self.assertIn("sdk_preexisting_firecracker", self.runner)
        self.assertIn("e2e_not_for_production", self.runner)
        self.assertIn('template_name="kitdev-coding-template-${uuid:0:12}"', self.runner)
        self.assertLess(
            self.runner.index('>"$stage/config/e2b-template-name"'),
            self.runner.index("node coding-template.ts"),
        )
        self.assertIn('--request DELETE -- "$API_ROOT/sandboxes/$sandbox_id"', self.runner)
        self.assertIn('--request DELETE -- "$API_ROOT/templates/$template_id"', self.runner)
        self.assertIn('--request DELETE -- "$API_ROOT/templates/$template_name"', self.runner)
        self.assertIn("sandbox_absent", self.runner)
        self.assertIn("template_absent", self.runner)


if __name__ == "__main__":
    unittest.main()

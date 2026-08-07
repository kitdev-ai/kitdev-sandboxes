from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "control-plane" / "verify-typescript-sdk-template-build.sh"
CLIENT = ROOT / "scripts" / "control-plane" / "e2e-typescript-sdk" / "template-build.ts"


class TypeScriptSDKTemplateBuildAssetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = RUNNER.read_text(encoding="ascii")
        self.client = CLIENT.read_text(encoding="ascii")

    def test_runner_is_pinned_locked_and_nonproduction(self) -> None:
        self.assertIn("node:22.18.0-bookworm-slim@sha256:", self.runner)
        self.assertIn("LOCK_SHA256=", self.runner)
        self.assertIn("typescript-sdk-e2e.lock", self.runner)
        self.assertIn("e2e_not_for_production", self.runner)
        self.assertIn("sdk_preexisting_firecracker", self.runner)

    def test_runner_uses_unique_pre_recorded_name_and_cleanup_fallback(self) -> None:
        self.assertIn('template_name="kitdev-sdk-template-${uuid:0:12}"', self.runner)
        self.assertLess(
            self.runner.index('>"$stage/config/e2b-template-name"'),
            self.runner.index("node template-build.ts"),
        )
        self.assertIn('--request DELETE -- "$API_ROOT/templates/$template_id"', self.runner)
        self.assertIn('--request DELETE -- "$API_ROOT/templates/$template_name"', self.runner)
        self.assertIn('--request DELETE -- "$API_ROOT/sandboxes/$sandbox_id"', self.runner)

    def test_client_covers_official_template_surface_without_logging_errors(self) -> None:
        for operation in (
            "Template.buildInBackground(",
            "Template.getBuildStatus(",
            "Template.exists(",
            "Template.getTags(",
            "Template.assignTags(",
            "Template.removeTags(",
            "Template.build(",
            "Sandbox.create(",
        ):
            with self.subTest(operation=operation):
                self.assertIn(operation, self.client)
        self.assertEqual(len(re.findall(r'user: "root"', self.client)), 2)
        error_block = self.client.split("main().catch", maxsplit=1)[1]
        self.assertNotIn("error.message", error_block)
        self.assertNotIn("console.error(status.reason", self.client)


if __name__ == "__main__":
    unittest.main()

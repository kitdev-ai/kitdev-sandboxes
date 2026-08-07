from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
STATE_PATH = ROOT / "scripts" / "control-plane" / "template-publication-state.py"
RUNNER = (ROOT / "scripts" / "control-plane" / "publish-stable-template.sh").read_text()
CODING = (ROOT / "scripts" / "control-plane" / "e2e-typescript-sdk" / "coding-template.ts").read_text()
BROWSER = (ROOT / "scripts" / "control-plane" / "e2e-typescript-sdk" / "browser-template.ts").read_text()


def load_state_module():
    spec = importlib.util.spec_from_file_location("template_publication_state", STATE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TemplatePublicationTests(unittest.TestCase):
    def test_journal_schema_accepts_owned_published_record(self) -> None:
        module = load_state_module()
        record = {
            "schema_version": 1,
            "product": "coding",
            "alias": "kitdev-coding",
            "version": "v1",
            "state": "published",
            "definition_sha256": "a" * 64,
            "created_at": "2026-08-07T00:00:00Z",
            "template_id": "a" * 20,
            "build_id": "12345678-1234-1234-1234-123456789abc",
            "published_at": "2026-08-07T00:01:00Z",
        }
        self.assertEqual(module.validate_record(record), record)

    def test_journal_schema_rejects_alias_substitution(self) -> None:
        module = load_state_module()
        record = {
            "schema_version": 1,
            "product": "coding",
            "alias": "someone-elses-template",
            "version": "v1",
            "state": "reserved",
            "definition_sha256": "a" * 64,
            "created_at": "2026-08-07T00:00:00Z",
        }
        with self.assertRaisesRegex(module.StateError, "record_product_invalid"):
            module.validate_record(record)

    def test_runner_serializes_and_deletes_only_recorded_private_candidate(self) -> None:
        lifecycle = RUNNER.index('exec 8<>"$LIFECYCLE_LOCK"')
        sdk = RUNNER.index('exec 9<>"$SDK_LOCK"')
        self.assertLess(lifecycle, sdk)
        self.assertIn('"$API_ROOT/templates/$template_id"', RUNNER)
        self.assertNotIn('DELETE -- "$API_ROOT/templates/$alias"', RUNNER)
        self.assertIn('--data-binary \'{"public":true}\' -- "$API_ROOT/templates/$alias"', RUNNER)
        self.assertIn("publication_alias_not_owned", RUNNER)
        self.assertIn("publication_firecracker_running", RUNNER)
        self.assertIn("publication_build_running", RUNNER)

    def test_builders_use_version_and_stable_tags_only_in_publication_mode(self) -> None:
        for source, alias in ((CODING, "kitdev-coding"), (BROWSER, "kitdev-browser-heavy")):
            self.assertIn(f'alias: "{alias}"', source)
            self.assertIn('{ tags: ["stable"] }', source)
            self.assertIn('[publication.version, "stable"]', source)
            self.assertIn("publication?.version", source)


if __name__ == "__main__":
    unittest.main()

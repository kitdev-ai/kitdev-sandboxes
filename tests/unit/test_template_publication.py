from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
STATE_PATH = ROOT / "scripts" / "control-plane" / "template-publication-state.py"
RUNNER = (ROOT / "scripts" / "control-plane" / "publish-stable-template.sh").read_text()
CODING = (ROOT / "scripts" / "control-plane" / "e2e-typescript-sdk" / "coding-template.ts").read_text()
BROWSER = (ROOT / "scripts" / "control-plane" / "e2e-typescript-sdk" / "browser-template.ts").read_text()
CONSUMER = (ROOT / "scripts" / "control-plane" / "e2e-typescript-sdk" / "stable-template-consumer.ts").read_text()


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

    def test_failed_build_debris_does_not_block_a_later_publish(self) -> None:
        # A build that fails leaves its env and alias rows behind. Requiring the
        # alias not to exist at all therefore let one failed build block every
        # later publish of that product permanently, recoverable only by
        # deleting rows by hand. The alias is reclaimable only when it is
        # private, owned by this API key's own team, and carries no build that
        # did not fail -- so a foreign or already-published alias still refuses.
        self.assertIn("publication_alias_not_owned", RUNNER)
        guard = RUNNER.split("SELECT count(*) FROM public.env_aliases a", 1)[1].split(";\")", 1)[0]
        self.assertIn("e.public = false", guard)
        self.assertIn("k.api_key_hash='$key_hash'", guard)
        self.assertIn("b.status <> 'failed'", guard)
        self.assertIn("NOT EXISTS", guard)
        # The team must come from the key being used, never a caller argument.
        self.assertIn('key_hash="$(api_key_hash "$api_key_file")"', RUNNER)
        self.assertIn("publication_api_key_hash_invalid", RUNNER)
        # The guard compares against a scalar subquery, so an unresolvable key
        # would make NOT(...) NULL, drop the row, and read as 0 -- permitting
        # exactly what it exists to refuse. Resolve the team first, fail closed.
        self.assertIn("publication_api_key_team_unresolved", RUNNER)
        self.assertLess(
            RUNNER.index("publication_api_key_team_unresolved"),
            RUNNER.index("publication_alias_not_owned"),
        )
        # Debris is deleted, not tolerated: the client asserts the template does
        # not exist, so leaving it would swap one refusal for another. Deleting
        # through the API keeps its ownership rules in force, and the removal is
        # confirmed rather than assumed.
        # By template id, never by alias: an alias can be repointed between the
        # read and the delete, which is why the rollback path deletes by id too.
        self.assertIn('--request DELETE -- "$API_ROOT/templates/$debris_id"', RUNNER)
        self.assertIn("publication_debris_id_invalid", RUNNER)
        self.assertIn("publication_debris_delete_rejected", RUNNER)
        self.assertIn("publication_debris_delete_incomplete", RUNNER)
        self.assertLess(
            RUNNER.index("publication_alias_not_owned"),
            RUNNER.index("--request DELETE"),
        )
        self.assertIn("Template.exists(templateName, connection), false", CODING)

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
        self.assertIn("| awk '{print $1}' | sha256sum", RUNNER)

    def test_builders_use_version_and_stable_tags_only_in_publication_mode(self) -> None:
        for source, alias in ((CODING, "kitdev-coding"), (BROWSER, "kitdev-browser-heavy")):
            self.assertIn(f'alias: "{alias}"', source)
            self.assertIn('{ tags: ["stable"] }', source)
            self.assertIn('[publication.version, "stable"]', source)
            self.assertIn("publication?.version", source)

    def test_consumer_uses_only_stable_alias_and_sdk_sandbox_operations(self) -> None:
        self.assertIn("Sandbox.create(`${config.alias}:stable`", CONSUMER)
        self.assertIn("await sandbox.commands.run(", CONSUMER)
        self.assertIn("await sandbox.kill()", CONSUMER)
        self.assertNotIn("Template.", CONSUMER)


if __name__ == "__main__":
    unittest.main()

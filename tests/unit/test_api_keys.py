from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kitdev_sandboxes import api_keys
from kitdev_sandboxes.api_keys import (
    ApiKeyManager,
    ApiKeyOperationError,
    ApiKeyResult,
    HttpResponse,
)
from kitdev_sandboxes.cli import main

TEAM_ID = "11111111-1111-4111-8111-111111111111"
KEY_ID = "22222222-2222-4222-8222-222222222222"
RAW_KEY = "e2b_" + "a" * 40
ADMIN_TOKEN = "b" * 64
MASK = {
    "prefix": "e2b_",
    "valueLength": 44,
    "maskedValuePrefix": "e2b_aa",
    "maskedValueSuffix": "aaaa",
}


def record(*, key_id: str = KEY_ID, name: str = "app--kitdev-abcdef123456") -> dict[str, object]:
    return {
        "id": key_id,
        "name": name,
        "mask": MASK,
        "createdAt": "2026-08-07T00:00:00Z",
        "lastUsed": None,
    }


class FakeTransport:
    def __init__(self, responses: list[HttpResponse]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, dict[str, str], bytes | None]] = []

    def request(self, method, path, headers, body=None):
        self.calls.append((method, path, dict(headers), body))
        if not self.responses:
            raise AssertionError("unexpected API request")
        return self.responses.pop(0)


def response(status: int, value: object | None = None) -> HttpResponse:
    body = b"" if value is None else json.dumps(value).encode("ascii")
    return HttpResponse(status, body)


def write_secure(path: Path, payload: str) -> None:
    path.write_text(payload, encoding="ascii")
    path.chmod(0o600)


class ApiKeyManagerTests(unittest.TestCase):
    def manager(self, responses: list[HttpResponse]) -> tuple[ApiKeyManager, FakeTransport]:
        transport = FakeTransport(responses)
        return ApiKeyManager(
            transport, root_uid=os.getuid(), root_gid=os.getgid()
        ), transport

    def test_list_validates_response_and_never_returns_raw_key(self) -> None:
        manager, transport = self.manager([response(200, [record()])])

        keys = manager.list(admin_token=ADMIN_TOKEN, team_id=TEAM_ID)

        self.assertEqual(keys[0]["id"], KEY_ID)
        self.assertNotIn("key", keys[0])
        self.assertEqual(transport.calls[0][2]["X-Admin-Token"], ADMIN_TOKEN)

    def test_verify_reads_secure_key_and_authenticates(self) -> None:
        manager, transport = self.manager([response(200, [])])
        with tempfile.TemporaryDirectory() as directory:
            key_file = Path(directory) / "app.key"
            write_secure(key_file, RAW_KEY + "\n")
            manager.verify(key_file=key_file, uid=os.getuid(), gid=os.getgid())

        self.assertEqual(transport.calls[0][1], "/sandboxes?limit=1")
        self.assertEqual(transport.calls[0][2]["X-API-Key"], RAW_KEY)

    def test_create_publishes_0600_files_and_rerun_is_idempotent(self) -> None:
        created = record()
        manager, transport = self.manager(
            [
                response(200, []),
                response(201, {**created, "key": RAW_KEY}),
                response(200, [created]),
                response(200, []),
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            output = root / "app.key"
            metadata = root / "app.metadata.json"
            generated = mock.Mock(hex="abcdef1234567890")
            with mock.patch.object(api_keys.uuid, "uuid4", return_value=generated):
                first = manager.create(
                    admin_token=ADMIN_TOKEN,
                    team_id=TEAM_ID,
                    label="app",
                    output=output,
                    metadata_file=metadata,
                    owner_uid=os.getuid(),
                    owner_gid=os.getgid(),
                )
            second = manager.create(
                admin_token=ADMIN_TOKEN,
                team_id=TEAM_ID,
                label="app",
                output=output,
                metadata_file=metadata,
                owner_uid=os.getuid(),
                owner_gid=os.getgid(),
            )
            document = json.loads(metadata.read_text(encoding="ascii"))

            self.assertEqual(first.outcome, "created")
            self.assertEqual(second.outcome, "existing")
            self.assertEqual(output.read_text(encoding="ascii"), RAW_KEY + "\n")
            self.assertEqual(os.stat(output).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(metadata).st_mode & 0o777, 0o600)
            self.assertEqual(document["state"], "active")
            self.assertNotIn(RAW_KEY, metadata.read_text(encoding="ascii"))
            self.assertEqual(sum(call[0] == "POST" for call in transport.calls), 1)

    def test_create_recovers_after_key_file_was_published(self) -> None:
        remote_name = "app--kitdev-abcdef123456"
        remote = record(name=remote_name)
        manager, _transport = self.manager([response(200, [remote]), response(200, [])])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            output = root / "app.key"
            metadata = root / "app.metadata.json"
            write_secure(output, RAW_KEY + "\n")
            api_keys._publish_metadata(
                metadata,
                {
                    "schema_version": 1,
                    "state": "creating",
                    "operation_id": "abcdef123456",
                    "team_id": TEAM_ID,
                    "label": "app",
                    "remote_name": remote_name,
                    "key_file": str(output),
                    "key_owner_uid": os.getuid(),
                    "key_owner_gid": os.getgid(),
                },
                root_uid=os.getuid(),
                root_gid=os.getgid(),
                replace=False,
            )

            result = manager.create(
                admin_token=ADMIN_TOKEN,
                team_id=TEAM_ID,
                label="app",
                output=output,
                metadata_file=metadata,
                owner_uid=os.getuid(),
                owner_gid=os.getgid(),
            )

            self.assertEqual(result.outcome, "recovered")
            self.assertEqual(json.loads(metadata.read_text())["state"], "active")

    def test_create_revokes_remote_orphan_before_recreating(self) -> None:
        remote_name = "app--kitdev-abcdef123456"
        old = record(name=remote_name)
        new = record(key_id="33333333-3333-4333-8333-333333333333", name=remote_name)
        manager, transport = self.manager(
            [
                response(200, [old]),
                response(204),
                response(200, []),
                response(201, {**new, "key": RAW_KEY}),
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            output = root / "app.key"
            metadata = root / "app.metadata.json"
            api_keys._publish_metadata(
                metadata,
                {
                    "schema_version": 1,
                    "state": "creating",
                    "operation_id": "abcdef123456",
                    "team_id": TEAM_ID,
                    "label": "app",
                    "remote_name": remote_name,
                    "key_file": str(output),
                    "key_owner_uid": os.getuid(),
                    "key_owner_gid": os.getgid(),
                },
                root_uid=os.getuid(),
                root_gid=os.getgid(),
                replace=False,
            )
            result = manager.create(
                admin_token=ADMIN_TOKEN,
                team_id=TEAM_ID,
                label="app",
                output=output,
                metadata_file=metadata,
                owner_uid=os.getuid(),
                owner_gid=os.getgid(),
            )

        self.assertEqual(result.key_id, new["id"])
        self.assertEqual([call[0] for call in transport.calls], ["GET", "DELETE", "GET", "POST"])

    def test_auth_failures_have_stable_nonsecret_error(self) -> None:
        manager, _transport = self.manager([response(401, {"message": RAW_KEY})])
        with self.assertRaisesRegex(ApiKeyOperationError, "admin_authentication_failed") as caught:
            manager.list(admin_token=ADMIN_TOKEN, team_id=TEAM_ID)
        self.assertEqual(caught.exception.exit_code, 77)
        self.assertNotIn(RAW_KEY, str(caught.exception))


class ApiKeyCliTests(unittest.TestCase):
    def test_create_dispatches_defaults_without_collecting_host_facts(self) -> None:
        calls: list[tuple[str, dict[str, object]]] = []

        def runner(action, configuration, **kwargs):
            calls.append((action, kwargs))
            return ApiKeyResult(action, "created", TEAM_ID, KEY_ID)

        def forbidden():
            raise AssertionError("host collector must not run")

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(
                ["api-key", "create", "--name", "my-app", "--output", "/tmp/my-app.key"],
                fact_collector=forbidden,
                api_key_runner=runner,
            )

        self.assertEqual(code, 0)
        self.assertEqual(calls[0][0], "create")
        self.assertIsNone(calls[0][1]["team_id"])
        self.assertEqual(calls[0][1]["private_env_file"], api_keys.DEFAULT_PRIVATE_ENV)
        self.assertNotIn(RAW_KEY, output.getvalue())

    def test_json_error_contains_only_stable_reason(self) -> None:
        def runner(*_args, **_kwargs):
            raise ApiKeyOperationError("admin_authentication_failed", 77)

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["api-key", "list", "--json"], api_key_runner=runner)

        self.assertEqual(code, 77)
        self.assertEqual(
            json.loads(output.getvalue())["error"]["code"],
            "admin_authentication_failed",
        )

    def test_dry_run_does_not_read_secrets_or_call_runner(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(
                ["api-key", "list", "--dry-run", "--json"],
                api_key_runner=lambda *_args, **_kwargs: self.fail("runner must not run"),
            )

        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "planned")

    def test_admin_token_sources_are_mutually_exclusive(self) -> None:
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            code = main(
                [
                    "api-key",
                    "list",
                    "--admin-token-file",
                    "/a",
                    "--private-env-file",
                    "/b",
                ]
            )
        self.assertEqual(code, 2)

    def test_teams_command_returns_nonsecret_discovery_fields(self) -> None:
        def runner(action, _configuration, **_kwargs):
            return ApiKeyResult(
                action,
                "listed",
                teams=({"id": TEAM_ID, "slug": "default", "name": "Default"},),
            )

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["api-key", "teams", "--json"], api_key_runner=runner)

        payload = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["teams"][0], {"id": TEAM_ID, "slug": "default", "name": "Default"})

    def test_team_id_and_slug_are_mutually_exclusive(self) -> None:
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            code = main(
                [
                    "api-key",
                    "list",
                    "--team-id",
                    TEAM_ID,
                    "--team-slug",
                    "default",
                ]
            )
        self.assertEqual(code, 2)


class ApiKeyFileTests(unittest.TestCase):
    def test_admin_token_source_formats_are_not_auto_detected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            raw = root / "raw"
            private = root / "private.env"
            write_secure(raw, ADMIN_TOKEN + "\n")
            write_secure(private, "ADMIN_TOKEN=" + ADMIN_TOKEN + "\n")

            self.assertEqual(
                api_keys._admin_token(
                    token_file=raw,
                    private_env_file=private,
                    root_uid=os.getuid(),
                    root_gid=os.getgid(),
                ),
                ADMIN_TOKEN,
            )
            self.assertEqual(
                api_keys._admin_token(
                    token_file=None,
                    private_env_file=private,
                    root_uid=os.getuid(),
                    root_gid=os.getgid(),
                ),
                ADMIN_TOKEN,
            )
            with self.assertRaises(ApiKeyOperationError):
                api_keys._admin_token(
                    token_file=private,
                    private_env_file=raw,
                    root_uid=os.getuid(),
                    root_gid=os.getgid(),
                )

    def test_secret_parent_must_be_root_owned_even_for_service_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory).resolve() / "service.key"
            with self.assertRaisesRegex(ApiKeyOperationError, "output_parent_untrusted"):
                api_keys._secure_parent(output, root_uid=os.getuid() + 1)

    def test_metadata_bound_key_deletion_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            key_file = Path(directory).resolve() / "app.key"
            write_secure(key_file, RAW_KEY + "\n")

            self.assertTrue(
                api_keys._delete_bound_key_file(
                    key_file,
                    uid=os.getuid(),
                    gid=os.getgid(),
                    root_uid=os.getuid(),
                )
            )
            self.assertFalse(
                api_keys._delete_bound_key_file(
                    key_file,
                    uid=os.getuid(),
                    gid=os.getgid(),
                    root_uid=os.getuid(),
                )
            )

    def test_team_resolution_requires_exactly_one_eligible_team(self) -> None:
        container = mock.Mock(returncode=0, stdout="a" * 12 + "\n")
        ambiguous = mock.Mock(
            returncode=0,
            stdout=(
                TEAM_ID + "\x1fdefault\x1fDefault\n"
                + KEY_ID + "\x1fheavy\x1fHeavy\n"
            ),
        )
        with mock.patch.object(api_keys.subprocess, "run", side_effect=[container, ambiguous]):
            teams = api_keys._list_local_teams()
        with self.assertRaisesRegex(ApiKeyOperationError, "team_id_or_slug_required"):
            api_keys._resolve_team(None, teams)

    def test_team_resolution_accepts_one_exact_slug(self) -> None:
        teams = (
            {"id": TEAM_ID, "slug": "default", "name": "Default"},
            {"id": KEY_ID, "slug": "kitdev-browser-heavy-team", "name": "Heavy"},
        )

        self.assertEqual(
            api_keys._resolve_team("kitdev-browser-heavy-team", teams), KEY_ID
        )
        with self.assertRaisesRegex(ApiKeyOperationError, "team_slug_not_found"):
            api_keys._resolve_team("KITDEV-browser-heavy-team", teams)

    def test_revoke_journals_before_deleting_local_key(self) -> None:
        metadata = {
            "schema_version": 1,
            "state": "active",
            "operation_id": "abcdef123456",
            "team_id": TEAM_ID,
            "label": "app",
            "remote_name": "app--kitdev-abcdef123456",
            "key_file": "/etc/kitdev-sandboxes/secrets/app.key",
            "key_owner_uid": 1000,
            "key_owner_gid": 1000,
            "key_id": KEY_ID,
            "mask": MASK,
            "created_at": "2026-08-07T00:00:00Z",
        }
        events: list[str] = []
        transport = FakeTransport([response(204)])
        configuration = mock.Mock()

        @contextlib.contextmanager
        def unlocked():
            yield

        def publish(*_args, **_kwargs):
            events.append("journal")

        def delete(*_args, **_kwargs):
            events.append("delete")
            return True

        with (
            mock.patch.object(api_keys, "_require_host"),
            mock.patch.object(api_keys, "_lifecycle_lock", unlocked),
            mock.patch.object(api_keys, "_admin_token", return_value=ADMIN_TOKEN),
            mock.patch.object(api_keys, "_read_metadata", return_value=metadata),
            mock.patch.object(api_keys, "_publish_metadata", side_effect=publish),
            mock.patch.object(api_keys, "_delete_bound_key_file", side_effect=delete),
        ):
            result = api_keys.run_api_key(
                "revoke",
                configuration,
                team_id=TEAM_ID,
                key_id=KEY_ID,
                confirm_key_id=KEY_ID,
                metadata_file=Path("/etc/kitdev-sandboxes/secrets/app.key.metadata.json"),
                delete_key_file=True,
                transport=transport,
            )

        self.assertEqual(result.outcome, "revoked")
        self.assertEqual(events, ["journal", "delete"])


if __name__ == "__main__":
    unittest.main()

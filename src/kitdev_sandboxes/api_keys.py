"""Credential-safe lifecycle operations for self-hosted E2B project API keys."""

from __future__ import annotations

import contextlib
import fcntl
import grp
import http.client
import json
import os
import platform
import pwd
import re
import stat
import subprocess
import tempfile
import uuid
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast
from urllib.parse import quote

from kitdev_sandboxes.config import Configuration

API_HOST = "127.0.0.1"
API_PORT = 3000
DEFAULT_PRIVATE_ENV = Path("/etc/kitdev-sandboxes/control-plane.env")
LIFECYCLE_LOCK = Path("/run/kitdev-sandboxes/control-plane-lifecycle.lock")
MAX_RESPONSE_BYTES = 1_048_576
MAX_SECRET_FILE_BYTES = 65_536
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
API_KEY_RE = re.compile(r"e2b_[0-9a-f]{40}")
ADMIN_TOKEN_RE = re.compile(r"[0-9a-f]{64}")
LABEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,47}")
REMOTE_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,47}--kitdev-[0-9a-f]{12}")


class ApiKeyOperationError(RuntimeError):
    """Bounded operator-safe failure with a stable reason code."""

    def __init__(self, reason: str, exit_code: int = 65) -> None:
        super().__init__(reason)
        self.reason = reason
        self.exit_code = exit_code


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes


class ApiTransport(Protocol):
    def request(
        self,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: bytes | None = None,
    ) -> HttpResponse: ...


class DirectApiTransport:
    """Fixed loopback HTTP transport that ignores proxy environment variables."""

    def request(
        self,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: bytes | None = None,
    ) -> HttpResponse:
        if method not in {"GET", "POST", "DELETE"} or not path.startswith("/"):
            raise ApiKeyOperationError("api_request_invalid")
        connection = http.client.HTTPConnection(API_HOST, API_PORT, timeout=10)
        try:
            connection.request(method, path, body=body, headers=dict(headers))
            response = connection.getresponse()
            length = response.getheader("Content-Length")
            if length is not None:
                try:
                    if int(length) > MAX_RESPONSE_BYTES:
                        raise ApiKeyOperationError("api_response_too_large", 69)
                except ValueError as error:
                    raise ApiKeyOperationError("api_response_invalid", 69) from error
            payload = response.read(MAX_RESPONSE_BYTES + 1)
            if len(payload) > MAX_RESPONSE_BYTES:
                raise ApiKeyOperationError("api_response_too_large", 69)
            return HttpResponse(status=response.status, body=payload)
        except ApiKeyOperationError:
            raise
        except (OSError, http.client.HTTPException) as error:
            raise ApiKeyOperationError("api_unreachable", 69) from error
        finally:
            connection.close()


@dataclass(frozen=True)
class ApiKeyResult:
    action: str
    outcome: str
    team_id: str | None = None
    key_id: str | None = None
    keys: tuple[dict[str, object], ...] = ()
    teams: tuple[dict[str, str], ...] = ()

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": 1,
            "command": f"api-key {self.action}",
            "status": "pass",
            "outcome": self.outcome,
        }
        if self.team_id is not None:
            payload["team_id"] = self.team_id
        if self.key_id is not None:
            payload["key_id"] = self.key_id
        if self.keys:
            payload["keys"] = list(self.keys)
        elif self.action == "list":
            payload["keys"] = []
        if self.teams:
            payload["teams"] = list(self.teams)
        elif self.action == "teams":
            payload["teams"] = []
        return payload

    def render_text(self) -> str:
        fields = ["command=api-key-" + self.action, "status=pass", "outcome=" + self.outcome]
        if self.team_id is not None:
            fields.append("team_id=" + self.team_id)
        if self.key_id is not None:
            fields.append("key_id=" + self.key_id)
        lines = [" ".join(fields)]
        for item in self.keys:
            lines.append(
                "key_id="
                + str(item["id"])
                + " name="
                + json.dumps(item["name"], ensure_ascii=True)
                + " created_at="
                + str(item["created_at"])
                + " last_used="
                + (str(item["last_used"]) if item["last_used"] is not None else "never")
            )
        for team in self.teams:
            lines.append(
                "team_id="
                + team["id"]
                + " slug="
                + json.dumps(team["slug"], ensure_ascii=True)
                + " name="
                + json.dumps(team["name"], ensure_ascii=True)
            )
        return "\n".join(lines)


def _uuid(value: str, reason: str, exit_code: int = 64) -> str:
    normalized = value.lower()
    if UUID_RE.fullmatch(normalized) is None:
        raise ApiKeyOperationError(reason, exit_code)
    try:
        if str(uuid.UUID(normalized)) != normalized:
            raise ValueError
    except ValueError as error:
        raise ApiKeyOperationError(reason, exit_code) from error
    return normalized


def _absolute_path(path: Path, reason: str) -> Path:
    value = str(path)
    if not path.is_absolute() or not value.isascii() or any(ord(char) < 32 for char in value):
        raise ApiKeyOperationError(reason, 64)
    return path


def _secure_read(path: Path, *, uid: int, gid: int, maximum: int, reason: str) -> bytes:
    _absolute_path(path, reason)
    try:
        before = os.lstat(path)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
    except OSError as error:
        raise ApiKeyOperationError(reason) from error
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or stat.S_ISLNK(before.st_mode)
            or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
            or opened.st_uid != uid
            or opened.st_gid != gid
            or stat.S_IMODE(opened.st_mode) != 0o600
            or opened.st_nlink != 1
            or opened.st_size > maximum
        ):
            raise ApiKeyOperationError(reason)
        payload = os.read(descriptor, maximum + 1)
        if len(payload) > maximum or os.read(descriptor, 1):
            raise ApiKeyOperationError(reason)
        return payload
    finally:
        os.close(descriptor)


def _admin_token(
    *, token_file: Path | None, private_env_file: Path, root_uid: int, root_gid: int
) -> str:
    path = token_file if token_file is not None else private_env_file
    payload = _secure_read(
        path,
        uid=root_uid,
        gid=root_gid,
        maximum=MAX_SECRET_FILE_BYTES,
        reason="admin_token_file_invalid",
    )
    try:
        text = payload.decode("ascii")
    except UnicodeDecodeError as error:
        raise ApiKeyOperationError("admin_token_file_invalid") from error
    if token_file is not None:
        token = text.rstrip("\n")
        if ADMIN_TOKEN_RE.fullmatch(token) is None or text not in {token, token + "\n"}:
            raise ApiKeyOperationError("admin_token_file_invalid")
        return token

    values: dict[str, str] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ApiKeyOperationError("private_env_invalid")
        key, value = line.split("=", 1)
        if re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", key) is None or key in values:
            raise ApiKeyOperationError("private_env_invalid")
        values[key] = value
    token = values.get("ADMIN_TOKEN", "")
    if ADMIN_TOKEN_RE.fullmatch(token) is None:
        raise ApiKeyOperationError("private_env_admin_token_invalid")
    return token


def _resolve_identity(owner: str, group: str | None) -> tuple[int, int]:
    if re.fullmatch(r"[a-z_][a-z0-9_-]{0,30}", owner) is None:
        raise ApiKeyOperationError("key_owner_invalid", 64)
    try:
        user = pwd.getpwnam(owner)
    except KeyError as error:
        raise ApiKeyOperationError("key_owner_unknown", 64) from error
    if group is None:
        return user.pw_uid, user.pw_gid
    if re.fullmatch(r"[a-z_][a-z0-9_-]{0,30}", group) is None:
        raise ApiKeyOperationError("key_group_invalid", 64)
    try:
        return user.pw_uid, grp.getgrnam(group).gr_gid
    except KeyError as error:
        raise ApiKeyOperationError("key_group_unknown", 64) from error


def _secure_parent(path: Path, *, root_uid: int) -> Path:
    path = _absolute_path(path, "output_path_invalid")
    parent = path.parent
    try:
        if parent.resolve(strict=True) != parent:
            raise ApiKeyOperationError("output_parent_untrusted")
        metadata = os.lstat(parent)
    except OSError as error:
        raise ApiKeyOperationError("output_parent_untrusted") from error
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != root_uid
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise ApiKeyOperationError("output_parent_untrusted")
    return parent


def _publish_exclusive(path: Path, payload: bytes, *, uid: int, gid: int, root_uid: int) -> None:
    parent = _secure_parent(path, root_uid=root_uid)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".kitdev-api-key.", dir=parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        os.fchown(descriptor, uid, gid)
        _write_all(descriptor, payload)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError as error:
            raise ApiKeyOperationError("key_output_exists") from error
        temporary.unlink()
        directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def _publish_metadata(
    path: Path,
    document: Mapping[str, object],
    *,
    root_uid: int,
    root_gid: int,
    replace: bool,
) -> None:
    parent = _secure_parent(path, root_uid=root_uid)
    if path.exists() or path.is_symlink():
        if not replace:
            raise ApiKeyOperationError("metadata_file_exists")
        _secure_read(
            path,
            uid=root_uid,
            gid=root_gid,
            maximum=MAX_SECRET_FILE_BYTES,
            reason="metadata_file_invalid",
        )
    payload = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")
    descriptor, temporary_name = tempfile.mkstemp(prefix=".kitdev-api-key-metadata.", dir=parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        os.fchown(descriptor, root_uid, root_gid)
        _write_all(descriptor, payload)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        if replace:
            os.replace(temporary, path)
        else:
            try:
                os.link(temporary, path, follow_symlinks=False)
            except FileExistsError as error:
                raise ApiKeyOperationError("metadata_file_exists") from error
            temporary.unlink()
        directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def _read_metadata(path: Path, *, root_uid: int, root_gid: int) -> dict[str, object]:
    payload = _secure_read(
        path,
        uid=root_uid,
        gid=root_gid,
        maximum=MAX_SECRET_FILE_BYTES,
        reason="metadata_file_invalid",
    )
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ApiKeyOperationError("metadata_file_invalid") from error
    if not isinstance(document, dict):
        raise ApiKeyOperationError("metadata_file_invalid")
    return document


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            raise ApiKeyOperationError("secure_file_write_failed", 74)
        offset += written


def _delete_bound_key_file(
    path: Path, *, uid: int, gid: int, root_uid: int
) -> bool:
    path = _absolute_path(path, "api_key_file_invalid")
    parent = _secure_parent(path, root_uid=root_uid)
    try:
        before = os.lstat(path)
    except FileNotFoundError:
        return False
    payload = _secure_read(
        path, uid=uid, gid=gid, maximum=45, reason="api_key_file_invalid"
    )
    try:
        value = payload.decode("ascii").rstrip("\n")
    except UnicodeDecodeError as error:
        raise ApiKeyOperationError("api_key_file_invalid") from error
    if API_KEY_RE.fullmatch(value) is None or payload not in {
        value.encode("ascii"),
        (value + "\n").encode("ascii"),
    }:
        raise ApiKeyOperationError("api_key_file_invalid")
    try:
        after = os.lstat(path)
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise ApiKeyOperationError("api_key_file_changed")
        path.unlink()
        directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError as error:
        raise ApiKeyOperationError("api_key_file_delete_failed", 74) from error
    return True


def _json(response: HttpResponse, reason: str) -> object:
    try:
        return json.loads(response.body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ApiKeyOperationError(reason, 69) from error


def _mask(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != {
        "prefix",
        "valueLength",
        "maskedValuePrefix",
        "maskedValueSuffix",
    }:
        raise ApiKeyOperationError("api_response_invalid", 69)
    result: dict[str, object] = {}
    for key in ("prefix", "maskedValuePrefix", "maskedValueSuffix"):
        item = value[key]
        if not isinstance(item, str) or not item.isascii() or len(item) > 64:
            raise ApiKeyOperationError("api_response_invalid", 69)
        result[key] = item
    length = value["valueLength"]
    if not isinstance(length, int) or isinstance(length, bool) or not 1 <= length <= 512:
        raise ApiKeyOperationError("api_response_invalid", 69)
    result["valueLength"] = length
    return result


def _key_record(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ApiKeyOperationError("api_response_invalid", 69)
    key_id = value.get("id")
    name = value.get("name")
    created = value.get("createdAt")
    last_used = value.get("lastUsed")
    if (
        not isinstance(key_id, str)
        or not isinstance(name, str)
        or not isinstance(created, str)
        or (last_used is not None and not isinstance(last_used, str))
        or not name.isascii()
        or any(ord(char) < 32 for char in name)
        or len(name) > 256
        or len(created) > 64
        or (isinstance(last_used, str) and len(last_used) > 64)
    ):
        raise ApiKeyOperationError("api_response_invalid", 69)
    return {
        "id": _uuid(key_id, "api_response_invalid", 69),
        "name": name,
        "mask": _mask(value.get("mask")),
        "created_at": created,
        "last_used": last_used,
    }


class ApiKeyManager:
    def __init__(self, transport: ApiTransport, *, root_uid: int = 0, root_gid: int = 0) -> None:
        self.transport = transport
        self.root_uid = root_uid
        self.root_gid = root_gid

    @staticmethod
    def _admin_headers(admin_token: str, team_id: str | None = None) -> dict[str, str]:
        headers = {"Accept": "application/json", "X-Admin-Token": admin_token}
        if team_id is not None:
            headers["X-Team-ID"] = team_id
        return headers

    def list(self, *, admin_token: str, team_id: str) -> tuple[dict[str, object], ...]:
        response = self.transport.request(
            "GET", "/api-keys", self._admin_headers(admin_token, team_id)
        )
        if response.status == 401:
            raise ApiKeyOperationError("admin_authentication_failed", 77)
        if response.status != 200:
            raise ApiKeyOperationError("api_key_list_failed", 69)
        document = _json(response, "api_response_invalid")
        if not isinstance(document, list) or len(document) > 10_000:
            raise ApiKeyOperationError("api_response_invalid", 69)
        return tuple(_key_record(item) for item in document)

    def verify(self, *, key_file: Path, uid: int, gid: int) -> None:
        payload = _secure_read(
            key_file,
            uid=uid,
            gid=gid,
            maximum=45,
            reason="api_key_file_invalid",
        )
        try:
            api_key = payload.decode("ascii").rstrip("\n")
        except UnicodeDecodeError as error:
            raise ApiKeyOperationError("api_key_file_invalid") from error
        if API_KEY_RE.fullmatch(api_key) is None or payload not in {
            api_key.encode("ascii"),
            (api_key + "\n").encode("ascii"),
        }:
            raise ApiKeyOperationError("api_key_file_invalid")
        response = self.transport.request(
            "GET", "/sandboxes?limit=1", {"Accept": "application/json", "X-API-Key": api_key}
        )
        if response.status == 401:
            raise ApiKeyOperationError("api_key_authentication_failed", 77)
        if response.status != 200:
            raise ApiKeyOperationError("api_key_verification_failed", 69)
        document = _json(response, "api_response_invalid")
        if not isinstance(document, list):
            raise ApiKeyOperationError("api_response_invalid", 69)

    def revoke(self, *, admin_token: str, team_id: str, key_id: str) -> bool:
        response = self.transport.request(
            "DELETE",
            f"/admin/teams/{quote(team_id, safe='')}/api-keys/{quote(key_id, safe='')}",
            self._admin_headers(admin_token),
        )
        if response.status == 401:
            raise ApiKeyOperationError("admin_authentication_failed", 77)
        if response.status == 204 and not response.body:
            return True
        if response.status == 404:
            return False
        raise ApiKeyOperationError("api_key_revoke_failed", 69)

    def _create_remote(
        self, *, admin_token: str, team_id: str, remote_name: str
    ) -> tuple[str, str, dict[str, object]]:
        body = json.dumps({"name": remote_name}, separators=(",", ":")).encode("ascii")
        response = self.transport.request(
            "POST",
            f"/admin/teams/{quote(team_id, safe='')}/api-keys",
            {
                **self._admin_headers(admin_token),
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
            },
            body,
        )
        if response.status == 401:
            raise ApiKeyOperationError("admin_authentication_failed", 77)
        if response.status in {403, 404}:
            raise ApiKeyOperationError("team_not_eligible", 66)
        if response.status != 201:
            raise ApiKeyOperationError("api_key_create_failed", 69)
        document = _json(response, "api_response_invalid")
        record = _key_record(document)
        if not isinstance(document, dict) or document.get("name") != remote_name:
            raise ApiKeyOperationError("api_response_invalid", 69)
        raw_key = document.get("key")
        if not isinstance(raw_key, str) or API_KEY_RE.fullmatch(raw_key) is None:
            raise ApiKeyOperationError("api_response_invalid", 69)
        return str(record["id"]), raw_key, record

    def create(
        self,
        *,
        admin_token: str,
        team_id: str,
        label: str,
        output: Path,
        metadata_file: Path,
        owner_uid: int,
        owner_gid: int,
    ) -> ApiKeyResult:
        if LABEL_RE.fullmatch(label) is None:
            raise ApiKeyOperationError("api_key_name_invalid", 64)
        output = _absolute_path(output, "output_path_invalid")
        metadata_file = _absolute_path(metadata_file, "metadata_path_invalid")
        if output == metadata_file:
            raise ApiKeyOperationError("metadata_path_conflicts", 64)

        output_exists = output.exists() or output.is_symlink()
        metadata_exists = metadata_file.exists() or metadata_file.is_symlink()
        if output_exists and not metadata_exists:
            raise ApiKeyOperationError("api_key_state_incomplete")

        intent: dict[str, object]
        if metadata_exists:
            intent = _validated_intent(
                _read_metadata(
                    metadata_file, root_uid=self.root_uid, root_gid=self.root_gid
                ),
                expected_output=output,
                expected_team=team_id,
                expected_label=label,
                expected_uid=owner_uid,
                expected_gid=owner_gid,
            )
            state = str(intent["state"])
            remote_name = str(intent["remote_name"])
            remote = [
                item
                for item in self.list(admin_token=admin_token, team_id=team_id)
                if item["name"] == remote_name
            ]
            if len(remote) > 1:
                raise ApiKeyOperationError("api_key_remote_name_conflict")
            if state != "creating":
                metadata = _validated_metadata(intent)
                if state != "active":
                    raise ApiKeyOperationError("api_key_metadata_revoked")
                if len(remote) != 1 or remote[0]["id"] != metadata["key_id"]:
                    raise ApiKeyOperationError("api_key_metadata_remote_mismatch")
                self.verify(key_file=output, uid=owner_uid, gid=owner_gid)
                return ApiKeyResult("create", "existing", team_id, str(metadata["key_id"]))

            if output_exists:
                if len(remote) != 1:
                    raise ApiKeyOperationError("api_key_recovery_remote_missing")
                self.verify(key_file=output, uid=owner_uid, gid=owner_gid)
                complete = _complete_metadata(intent, remote[0])
                _publish_metadata(
                    metadata_file,
                    complete,
                    root_uid=self.root_uid,
                    root_gid=self.root_gid,
                    replace=True,
                )
                return ApiKeyResult("create", "recovered", team_id, str(remote[0]["id"]))

            # A raw key can only be obtained from POST. Remove an orphaned remote
            # key before repeating the create with the journaled unique name.
            if remote:
                self.revoke(
                    admin_token=admin_token,
                    team_id=team_id,
                    key_id=str(remote[0]["id"]),
                )
        else:
            operation_id = uuid.uuid4().hex[:12]
            remote_name = f"{label}--kitdev-{operation_id}"
            intent = {
                "schema_version": 1,
                "state": "creating",
                "operation_id": operation_id,
                "team_id": team_id,
                "label": label,
                "remote_name": remote_name,
                "key_file": str(output),
                "key_owner_uid": owner_uid,
                "key_owner_gid": owner_gid,
            }
            _publish_metadata(
                metadata_file,
                intent,
                root_uid=self.root_uid,
                root_gid=self.root_gid,
                replace=False,
            )

        remote_name = str(intent["remote_name"])
        created_key_id: str | None = None
        key_published = False
        try:
            existing = [
                item
                for item in self.list(admin_token=admin_token, team_id=team_id)
                if item["name"] == remote_name
            ]
            if existing:
                raise ApiKeyOperationError("api_key_remote_name_conflict")
            created_key_id, raw_key, record = self._create_remote(
                admin_token=admin_token, team_id=team_id, remote_name=remote_name
            )
            _publish_exclusive(
                output,
                (raw_key + "\n").encode("ascii"),
                uid=owner_uid,
                gid=owner_gid,
                root_uid=self.root_uid,
            )
            key_published = True
            complete = _complete_metadata(intent, record)
            _publish_metadata(
                metadata_file,
                complete,
                root_uid=self.root_uid,
                root_gid=self.root_gid,
                replace=True,
            )
        except BaseException:
            if created_key_id is not None and not key_published:
                with contextlib.suppress(ApiKeyOperationError):
                    self.revoke(
                        admin_token=admin_token,
                        team_id=team_id,
                        key_id=created_key_id,
                    )
            raise
        return ApiKeyResult("create", "created", team_id, created_key_id)


def _complete_metadata(
    intent: Mapping[str, object], record: Mapping[str, object]
) -> dict[str, object]:
    return {
        **intent,
        "state": "active",
        "key_id": record["id"],
        "mask": record["mask"],
        "created_at": record["created_at"],
    }


def _validated_intent(
    document: Mapping[str, object],
    *,
    expected_output: Path | None = None,
    expected_team: str | None = None,
    expected_label: str | None = None,
    expected_uid: int | None = None,
    expected_gid: int | None = None,
) -> dict[str, object]:
    base = {
        "schema_version",
        "state",
        "operation_id",
        "team_id",
        "label",
        "remote_name",
        "key_file",
        "key_owner_uid",
        "key_owner_gid",
    }
    complete = {"key_id", "mask", "created_at"}
    state = document.get("state")
    if (
        document.get("schema_version") != 1
        or state not in {"creating", "active", "revoked"}
        or set(document) != (base if state == "creating" else base | complete)
    ):
        raise ApiKeyOperationError("metadata_file_invalid")
    operation_id = document.get("operation_id")
    label = document.get("label")
    remote_name = document.get("remote_name")
    key_file = document.get("key_file")
    owner_uid = document.get("key_owner_uid")
    owner_gid = document.get("key_owner_gid")
    team_id = document.get("team_id")
    if (
        not isinstance(operation_id, str)
        or re.fullmatch(r"[0-9a-f]{12}", operation_id) is None
        or not isinstance(label, str)
        or LABEL_RE.fullmatch(label) is None
        or not isinstance(remote_name, str)
        or REMOTE_NAME_RE.fullmatch(remote_name) is None
        or remote_name != f"{label}--kitdev-{operation_id}"
        or not isinstance(key_file, str)
        or not isinstance(owner_uid, int)
        or isinstance(owner_uid, bool)
        or not isinstance(owner_gid, int)
        or isinstance(owner_gid, bool)
        or not isinstance(team_id, str)
    ):
        raise ApiKeyOperationError("metadata_file_invalid")
    _uuid(team_id, "metadata_file_invalid", 65)
    expected = (
        (expected_output is None or key_file == str(expected_output))
        and (expected_team is None or team_id == expected_team)
        and (expected_label is None or label == expected_label)
        and (expected_uid is None or owner_uid == expected_uid)
        and (expected_gid is None or owner_gid == expected_gid)
    )
    if not expected:
        raise ApiKeyOperationError("api_key_metadata_mismatch")
    return dict(document)


def _validated_metadata(
    document: Mapping[str, object],
    *,
    expected_output: Path | None = None,
    expected_team: str | None = None,
    expected_label: str | None = None,
    expected_uid: int | None = None,
    expected_gid: int | None = None,
) -> dict[str, object]:
    result = _validated_intent(
        document,
        expected_output=expected_output,
        expected_team=expected_team,
        expected_label=expected_label,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
    )
    created_at = document.get("created_at")
    if not isinstance(created_at, str) or len(created_at) > 64:
        raise ApiKeyOperationError("metadata_file_invalid")
    team_id = document.get("team_id")
    key_id = document.get("key_id")
    if not isinstance(team_id, str) or not isinstance(key_id, str):
        raise ApiKeyOperationError("metadata_file_invalid")
    _uuid(team_id, "metadata_file_invalid", 65)
    _uuid(key_id, "metadata_file_invalid", 65)
    try:
        _mask(document.get("mask"))
    except ApiKeyOperationError as error:
        raise ApiKeyOperationError("metadata_file_invalid") from error
    return result


def _list_local_teams() -> tuple[dict[str, str], ...]:
    try:
        completed = subprocess.run(
            (
                "/usr/bin/docker",
                "ps",
                "--quiet",
                "--filter",
                "label=com.docker.compose.project=kitdev-control-plane",
                "--filter",
                "label=com.docker.compose.service=postgres",
            ),
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ApiKeyOperationError("team_resolution_failed", 69) from error
    containers = completed.stdout.splitlines() if completed.returncode == 0 else []
    if len(containers) != 1 or re.fullmatch(r"[0-9a-f]{12}|[0-9a-f]{64}", containers[0]) is None:
        raise ApiKeyOperationError("team_resolution_failed", 69)
    separator = "\x1f"
    query = (
        "SELECT id, slug, name FROM public.teams "
        "WHERE is_blocked = FALSE AND is_banned = FALSE ORDER BY slug, id;"
    )
    try:
        completed = subprocess.run(
            (
                "/usr/bin/docker",
                "exec",
                containers[0],
                "psql",
                "--no-psqlrc",
                "--tuples-only",
                "--no-align",
                "--field-separator",
                separator,
                "--username",
                "kitdev",
                "--dbname",
                "kitdev",
                "--command",
                query,
            ),
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ApiKeyOperationError("team_resolution_failed", 69) from error
    if len(completed.stdout.encode("utf-8")) > 65_536:
        raise ApiKeyOperationError("team_resolution_failed", 69)
    lines = completed.stdout.splitlines() if completed.returncode == 0 else []
    if len(lines) > 10_000:
        raise ApiKeyOperationError("team_resolution_failed", 69)
    teams: list[dict[str, str]] = []
    for line in lines:
        fields = line.split(separator)
        if len(fields) != 3:
            raise ApiKeyOperationError("team_resolution_failed", 69)
        team_id, slug, name = fields
        if (
            not slug.isascii()
            or not slug
            or len(slug) > 256
            or any(ord(char) < 32 or ord(char) == 127 for char in slug)
            or not name
            or len(name) > 256
            or any(ord(char) < 32 or ord(char) == 127 for char in name)
        ):
            raise ApiKeyOperationError("team_resolution_failed", 69)
        teams.append(
            {
                "id": _uuid(team_id, "team_resolution_failed", 69),
                "slug": slug,
                "name": name,
            }
        )
    return tuple(teams)


def _resolve_team(team_slug: str | None, teams: tuple[dict[str, str], ...]) -> str:
    if team_slug is None:
        if len(teams) != 1:
            raise ApiKeyOperationError("team_id_or_slug_required", 64)
        return teams[0]["id"]
    if (
        not team_slug.isascii()
        or not team_slug
        or len(team_slug) > 256
        or any(ord(char) < 32 or ord(char) == 127 for char in team_slug)
    ):
        raise ApiKeyOperationError("team_slug_invalid", 64)
    matches = [team for team in teams if team["slug"] == team_slug]
    if len(matches) != 1:
        raise ApiKeyOperationError("team_slug_not_found", 66)
    return matches[0]["id"]


def _require_host(configuration: Configuration) -> None:
    if os.geteuid() != 0:
        raise ApiKeyOperationError("root_required", 77)
    try:
        values = {}
        for line in Path("/etc/os-release").read_text(encoding="ascii").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                values[key] = value.strip().strip('"')
    except OSError as error:
        raise ApiKeyOperationError("host_platform_unavailable", 68) from error
    version = values.get("VERSION_ID")
    lifecycle = configuration.deployment.lifecycle_mode.value
    if (
        values.get("ID") != "ubuntu"
        or version not in {"25.04", "26.04"}
        or platform.machine() != "x86_64"
        or (version == "25.04" and lifecycle == "production")
    ):
        raise ApiKeyOperationError("unsupported_host_platform", 68)


@contextlib.contextmanager
def _lifecycle_lock(path: Path = LIFECYCLE_LOCK, *, root_uid: int = 0) -> Iterator[None]:
    parent = path.parent
    try:
        parent.mkdir(mode=0o700, exist_ok=True)
        parent_metadata = os.lstat(parent)
        if (
            not stat.S_ISDIR(parent_metadata.st_mode)
            or stat.S_ISLNK(parent_metadata.st_mode)
            or parent_metadata.st_uid != root_uid
            or parent_metadata.st_gid != root_uid
            or stat.S_IMODE(parent_metadata.st_mode) != 0o700
        ):
            raise ApiKeyOperationError("lifecycle_lock_invalid")
        descriptor = os.open(
            path,
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
    except OSError as error:
        raise ApiKeyOperationError("lifecycle_lock_invalid") from error
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != root_uid
            or metadata.st_gid != root_uid
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
            or metadata.st_size != 0
        ):
            raise ApiKeyOperationError("lifecycle_lock_invalid")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ApiKeyOperationError("lifecycle_operation_running", 75) from error
        yield
    finally:
        os.close(descriptor)


def run_api_key(
    action: str,
    configuration: Configuration,
    *,
    team_id: str | None = None,
    team_slug: str | None = None,
    name: str | None = None,
    output: Path | None = None,
    metadata_file: Path | None = None,
    owner: str = "root",
    group: str | None = None,
    admin_token_file: Path | None = None,
    private_env_file: Path = DEFAULT_PRIVATE_ENV,
    key_file: Path | None = None,
    key_id: str | None = None,
    confirm_key_id: str | None = None,
    delete_key_file: bool = False,
    transport: ApiTransport | None = None,
    team_lister: Callable[[], tuple[dict[str, str], ...]] = _list_local_teams,
) -> ApiKeyResult:
    """Run one API-key action without returning or printing raw credential material."""

    if action not in {"create", "list", "verify", "revoke", "teams"}:
        raise ApiKeyOperationError("api_key_action_invalid", 64)
    _require_host(configuration)
    manager = ApiKeyManager(transport or DirectApiTransport())
    with _lifecycle_lock():
        if action == "teams":
            return ApiKeyResult("teams", "listed", teams=team_lister())
        if action == "verify":
            if key_file is None:
                raise ApiKeyOperationError("api_key_file_required", 64)
            if metadata_file is not None:
                metadata = _validated_metadata(
                    _read_metadata(metadata_file, root_uid=0, root_gid=0)
                )
                uid = cast(int, metadata["key_owner_uid"])
                gid = cast(int, metadata["key_owner_gid"])
                if str(key_file) != metadata["key_file"]:
                    raise ApiKeyOperationError("api_key_metadata_mismatch")
                verified_id = str(metadata["key_id"])
                verified_team = str(metadata["team_id"])
            else:
                metadata = None
                file_stat = os.stat(key_file, follow_symlinks=False)
                uid, gid = file_stat.st_uid, file_stat.st_gid
                verified_id = None
                verified_team = None
            manager.verify(key_file=key_file, uid=uid, gid=gid)
            return ApiKeyResult("verify", "authenticated", verified_team, verified_id)

        if team_id is not None and team_slug is not None:
            raise ApiKeyOperationError("team_selector_conflict", 64)
        resolved_team = (
            _uuid(team_id, "team_id_invalid")
            if team_id is not None
            else _resolve_team(team_slug, team_lister())
        )
        admin_token = _admin_token(
            token_file=admin_token_file,
            private_env_file=private_env_file,
            root_uid=0,
            root_gid=0,
        )
        if action == "list":
            keys = manager.list(admin_token=admin_token, team_id=resolved_team)
            return ApiKeyResult("list", "listed", resolved_team, keys=keys)
        if action == "create":
            if name is None or output is None:
                raise ApiKeyOperationError("api_key_create_arguments_required", 64)
            uid, gid = _resolve_identity(owner, group)
            create_metadata = metadata_file or Path(str(output) + ".metadata.json")
            return manager.create(
                admin_token=admin_token,
                team_id=resolved_team,
                label=name,
                output=output,
                metadata_file=create_metadata,
                owner_uid=uid,
                owner_gid=gid,
            )

        normalized_key = _uuid(key_id or "", "key_id_invalid")
        normalized_confirmation = _uuid(confirm_key_id or "", "confirmation_key_id_invalid")
        if normalized_key != normalized_confirmation:
            raise ApiKeyOperationError("revoke_confirmation_mismatch", 64)
        metadata: dict[str, object] | None = None
        if delete_key_file and metadata_file is None:
            raise ApiKeyOperationError("metadata_file_required_for_key_deletion", 64)
        if metadata_file is not None:
            metadata = _validated_metadata(
                _read_metadata(metadata_file, root_uid=0, root_gid=0),
                expected_team=resolved_team,
            )
            if metadata["key_id"] != normalized_key:
                raise ApiKeyOperationError("api_key_metadata_mismatch")
        already_revoked = metadata is not None and metadata["state"] == "revoked"
        revoked = False
        if not already_revoked:
            revoked = manager.revoke(
                admin_token=admin_token,
                team_id=resolved_team,
                key_id=normalized_key,
            )
            if metadata_file is not None and metadata is not None:
                metadata = {**metadata, "state": "revoked"}
                _publish_metadata(
                    metadata_file, metadata, root_uid=0, root_gid=0, replace=True
                )
        if delete_key_file and metadata is not None:
            _delete_bound_key_file(
                Path(str(metadata["key_file"])),
                uid=cast(int, metadata["key_owner_uid"]),
                gid=cast(int, metadata["key_owner_gid"]),
                root_uid=0,
            )
        outcome = "already-revoked" if already_revoked else (
            "revoked" if revoked else "already-absent"
        )
        return ApiKeyResult(
            "revoke", outcome, resolved_team, normalized_key
        )

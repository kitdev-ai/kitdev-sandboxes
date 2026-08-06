"""Typed, dependency-free configuration loading for kitdev-sandboxes."""

from __future__ import annotations

import ast
import copy
import ipaddress
import json
import os
import re
import stat
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, cast


class ConfigurationError(ValueError):
    """Raised when configuration cannot be parsed or validated."""


class LifecycleMode(StrEnum):
    PRODUCTION = "production"
    DEVELOPMENT = "development"
    MIGRATION = "migration"


class DeploymentProfile(StrEnum):
    MINIMAL = "minimal"
    STANDARD = "standard"
    FULL = "full"


@dataclass(frozen=True)
class DeploymentConfig:
    profile: DeploymentProfile
    lifecycle_mode: LifecycleMode
    listen_address: str
    public_exposure: bool
    domain: str | None


@dataclass(frozen=True)
class PathsConfig:
    install: str
    config: str
    state: str
    logs: str
    runtime: str


@dataclass(frozen=True)
class SandboxConfig:
    default_template: str
    default_vcpus: int
    default_memory_mib: int
    default_disk_mib: int
    default_timeout_seconds: int
    default_ttl_seconds: int
    max_processes: int
    max_output_bytes: int


@dataclass(frozen=True)
class NetworkConfig:
    ipv4_cidr: str
    ipv6_enabled: bool
    dns_resolvers: tuple[str, ...]
    allow_http_egress: bool
    allow_private_egress: bool
    private_egress_allowlist: tuple[str, ...]
    sandbox_to_sandbox: bool


@dataclass(frozen=True)
class FeaturesConfig:
    observability: bool
    persistence: bool
    backups: bool
    browser_template: bool
    desktop_template: bool


@dataclass(frozen=True)
class Configuration:
    schema_version: int
    deployment: DeploymentConfig
    paths: PathsConfig
    sandbox: SandboxConfig
    network: NetworkConfig
    features: FeaturesConfig


@dataclass(frozen=True)
class LoadedConfiguration:
    configuration: Configuration
    merged: dict[str, Any]
    sources: tuple[str, ...]


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = _PROJECT_ROOT / "config" / "default.yaml"
INSTALLED_CONFIG_PATH = Path("/etc/kitdev-sandboxes/config.yaml")
_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}\.?$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.?$"
)
_MAX_CONFIG_BYTES = 1_048_576
_MAX_CONFIG_LINES = 10_000
_MAX_NESTING_DEPTH = 32
_PATH_OWNERSHIP_ROOTS = {
    "install": PurePosixPath("/opt/kitdev-sandboxes"),
    "config": PurePosixPath("/etc/kitdev-sandboxes"),
    "state": PurePosixPath("/var/lib/kitdev-sandboxes"),
    "logs": PurePosixPath("/var/log/kitdev-sandboxes"),
    "runtime": PurePosixPath("/run/kitdev-sandboxes"),
}
_OWNED_PATH_PART_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _strip_comment(line: str) -> str:
    quote: str | None = None
    escaped = False
    for index, character in enumerate(line):
        if escaped:
            escaped = False
            continue
        if character == "\\" and quote == '"':
            escaped = True
            continue
        if character in {"'", '"'}:
            if quote is None:
                quote = character
            elif quote == character:
                quote = None
        elif character == "#" and quote is None:
            return line[:index]
    if quote is not None:
        raise ConfigurationError("unterminated quoted scalar")
    return line


def _split_mapping_entry(content: str, line_number: int) -> tuple[str, str]:
    quote: str | None = None
    escaped = False
    for index, character in enumerate(content):
        if escaped:
            escaped = False
            continue
        if character == "\\" and quote == '"':
            escaped = True
            continue
        if character in {"'", '"'}:
            if quote is None:
                quote = character
            elif quote == character:
                quote = None
        elif character == ":" and quote is None:
            key = content[:index].strip()
            if not _KEY_RE.fullmatch(key):
                raise ConfigurationError(f"line {line_number}: invalid mapping key {key!r}")
            return key, content[index + 1 :].strip()
    raise ConfigurationError(f"line {line_number}: expected a mapping entry")


def _parse_scalar(value: str, line_number: int) -> Any:
    if not value:
        raise ConfigurationError(f"line {line_number}: missing scalar value")
    if value in {"null", "~"}:
        return None
    if value == "true":
        return True
    if value == "false":
        return False
    if value in {"[]", "{}"}:
        return json.loads(value)
    if value[0] in {"'", '"'}:
        try:
            parsed = ast.literal_eval(value)
        except (SyntaxError, ValueError) as error:
            raise ConfigurationError(f"line {line_number}: invalid quoted scalar") from error
        if not isinstance(parsed, str):
            raise ConfigurationError(f"line {line_number}: quoted value must be a string")
        return parsed
    if re.fullmatch(r"-?(0|[1-9][0-9]*)", value):
        return int(value)
    if value.startswith(("[", "{", "&", "*", "!", "|", ">")):
        raise ConfigurationError(
            f"line {line_number}: unsupported YAML construct; use block lists and mappings"
        )
    return value


def parse_yaml(text: str, *, source: str = "configuration") -> dict[str, Any]:
    """Parse the deliberately small YAML subset accepted by project configuration."""

    if len(text.encode("utf-8")) > _MAX_CONFIG_BYTES:
        raise ConfigurationError(f"{source}: configuration exceeds {_MAX_CONFIG_BYTES} bytes")
    lines = text.splitlines()
    if len(lines) > _MAX_CONFIG_LINES:
        raise ConfigurationError(f"{source}: configuration exceeds {_MAX_CONFIG_LINES} lines")
    tokens: list[tuple[int, str, int]] = []
    for line_number, original in enumerate(lines, start=1):
        if "\t" in original[: len(original) - len(original.lstrip())]:
            raise ConfigurationError(f"{source}: line {line_number}: tabs are not allowed")
        try:
            content = _strip_comment(original).rstrip()
        except ConfigurationError as error:
            raise ConfigurationError(f"{source}: line {line_number}: {error}") from error
        if not content.strip():
            continue
        indent = len(content) - len(content.lstrip(" "))
        if indent % 2:
            raise ConfigurationError(f"{source}: line {line_number}: indentation must use 2 spaces")
        if indent // 2 > _MAX_NESTING_DEPTH:
            raise ConfigurationError(
                f"{source}: line {line_number}: nesting exceeds {_MAX_NESTING_DEPTH} levels"
            )
        tokens.append((indent, content.strip(), line_number))

    if not tokens:
        raise ConfigurationError(f"{source}: configuration is empty")

    def parse_block(index: int, indent: int) -> tuple[Any, int]:
        if tokens[index][0] != indent:
            raise ConfigurationError(f"{source}: line {tokens[index][2]}: invalid indentation")
        is_list = tokens[index][1].startswith("-")
        result: list[Any] | dict[str, Any] = [] if is_list else {}
        while index < len(tokens):
            current_indent, content, line_number = tokens[index]
            if current_indent < indent:
                break
            if current_indent > indent:
                raise ConfigurationError(f"{source}: line {line_number}: unexpected indentation")
            if is_list:
                if not content.startswith("-"):
                    raise ConfigurationError(f"{source}: line {line_number}: mixed list and mapping")
                scalar = content[1:].strip()
                if not scalar:
                    raise ConfigurationError(
                        f"{source}: line {line_number}: nested list items are not supported"
                    )
                cast(list[Any], result).append(_parse_scalar(scalar, line_number))
                index += 1
                continue
            if content.startswith("-"):
                raise ConfigurationError(f"{source}: line {line_number}: mixed mapping and list")
            key, scalar = _split_mapping_entry(content, line_number)
            mapping = cast(dict[str, Any], result)
            if key in mapping:
                raise ConfigurationError(f"{source}: line {line_number}: duplicate key {key!r}")
            index += 1
            if scalar:
                mapping[key] = _parse_scalar(scalar, line_number)
            else:
                if index >= len(tokens) or tokens[index][0] <= indent:
                    raise ConfigurationError(
                        f"{source}: line {line_number}: mapping key {key!r} has no value"
                    )
                mapping[key], index = parse_block(index, tokens[index][0])
        return result, index

    if tokens[0][0] != 0:
        raise ConfigurationError(f"{source}: first content must not be indented")
    parsed, final_index = parse_block(0, 0)
    if final_index != len(tokens) or not isinstance(parsed, dict):
        raise ConfigurationError(f"{source}: top-level configuration must be a mapping")
    return parsed


def _load_yaml_file(path: Path, *, required: bool) -> dict[str, Any] | None:
    flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ConfigurationError(f"configuration path is not a regular file: {path}")
            raw = stream.read(_MAX_CONFIG_BYTES + 1)
    except FileNotFoundError:
        if required:
            raise ConfigurationError(f"configuration file does not exist: {path}") from None
        return None
    except OSError as error:
        raise ConfigurationError(f"cannot read configuration file {path}: {error}") from error
    if len(raw) > _MAX_CONFIG_BYTES:
        raise ConfigurationError(f"{path}: configuration exceeds {_MAX_CONFIG_BYTES} bytes")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ConfigurationError(f"{path}: configuration is not valid UTF-8") from error
    return parse_yaml(text, source=str(path))


def merge_mappings(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge mappings; scalar and list values replace earlier values."""

    merged = copy.deepcopy(base)
    for key, value in override.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = merge_mappings(current, value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _mapping(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ConfigurationError(f"{path} must be a mapping")
    return cast(dict[str, Any], value)


def _keys(mapping: dict[str, Any], required: set[str], path: str) -> None:
    missing = sorted(required - mapping.keys())
    unknown = sorted(mapping.keys() - required)
    if missing:
        raise ConfigurationError(f"{path} is missing required keys: {', '.join(missing)}")
    if unknown:
        raise ConfigurationError(f"{path} contains unknown keys: {', '.join(unknown)}")


def _string(value: Any, path: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value:
        raise ConfigurationError(f"{path} must be a non-empty string")
    return value


def _boolean(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigurationError(f"{path} must be a boolean")
    return value


def _integer(value: Any, path: str, minimum: int, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigurationError(f"{path} must be an integer")
    if value < minimum or (maximum is not None and value > maximum):
        limit = f" between {minimum} and {maximum}" if maximum is not None else f" >= {minimum}"
        raise ConfigurationError(f"{path} must be{limit}")
    return value


def _string_list(value: Any, path: str, *, unique: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ConfigurationError(f"{path} must be a list")
    if not all(isinstance(item, str) and item for item in value):
        raise ConfigurationError(f"{path} entries must be non-empty strings")
    result = tuple(cast(list[str], value))
    if unique and len(set(result)) != len(result):
        raise ConfigurationError(f"{path} entries must be unique")
    return result


def validate_configuration(data: dict[str, Any]) -> Configuration:
    """Validate merged data against the version 1 configuration contract."""

    top = _mapping(data, "configuration")
    _keys(top, {"schema_version", "deployment", "paths", "sandbox", "network", "features"}, "configuration")
    if top["schema_version"] != 1 or isinstance(top["schema_version"], bool):
        raise ConfigurationError("schema_version must be 1")

    deployment = _mapping(top["deployment"], "deployment")
    _keys(deployment, {"profile", "lifecycle_mode", "listen_address", "public_exposure", "domain"}, "deployment")
    try:
        profile = DeploymentProfile(deployment["profile"])
    except (TypeError, ValueError) as error:
        raise ConfigurationError("deployment.profile must be minimal, standard, or full") from error
    try:
        lifecycle_mode = LifecycleMode(deployment["lifecycle_mode"])
    except (TypeError, ValueError) as error:
        raise ConfigurationError(
            "deployment.lifecycle_mode must be production, development, or migration"
        ) from error
    public_exposure = _boolean(deployment["public_exposure"], "deployment.public_exposure")
    domain = _string(deployment["domain"], "deployment.domain", nullable=True)
    if public_exposure and domain is None:
        raise ConfigurationError("deployment.domain is required when public_exposure is true")

    paths = _mapping(top["paths"], "paths")
    path_keys = {"install", "config", "state", "logs", "runtime"}
    _keys(paths, path_keys, "paths")
    normalized_paths: dict[str, str] = {}
    for key in path_keys:
        value = cast(str, _string(paths[key], f"paths.{key}"))
        path = PurePosixPath(value)
        if (
            not value.startswith("/")
            or value == "/"
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
            or any(part in {".", ".."} for part in value.split("/"))
            or str(path) != value
        ):
            raise ConfigurationError(f"paths.{key} must be a normalized absolute path")
        try:
            relative = path.relative_to(_PATH_OWNERSHIP_ROOTS[key])
        except ValueError as error:
            raise ConfigurationError(
                f"paths.{key} must be within {_PATH_OWNERSHIP_ROOTS[key]}"
            ) from error
        if any(not _OWNED_PATH_PART_RE.fullmatch(part) for part in relative.parts):
            raise ConfigurationError(
                f"paths.{key} descendants may contain only ASCII letters, digits, dot, underscore, and dash"
            )
        normalized_paths[key] = value
    for first_key, first_path in normalized_paths.items():
        for second_key, second_path in normalized_paths.items():
            if first_key >= second_key:
                continue
            first = PurePosixPath(first_path)
            second = PurePosixPath(second_path)
            if first == second or first in second.parents or second in first.parents:
                raise ConfigurationError(f"paths.{first_key} and paths.{second_key} must not overlap")

    sandbox = _mapping(top["sandbox"], "sandbox")
    sandbox_keys = {
        "default_template", "default_vcpus", "default_memory_mib", "default_disk_mib",
        "default_timeout_seconds", "default_ttl_seconds", "max_processes", "max_output_bytes",
    }
    _keys(sandbox, sandbox_keys, "sandbox")
    template = _string(sandbox["default_template"], "sandbox.default_template")
    if template not in {"base", "coding", "browser", "desktop"}:
        raise ConfigurationError("sandbox.default_template is not supported")

    network = _mapping(top["network"], "network")
    network_keys = {
        "ipv4_cidr", "ipv6_enabled", "dns_resolvers", "allow_http_egress",
        "allow_private_egress", "private_egress_allowlist", "sandbox_to_sandbox",
    }
    _keys(network, network_keys, "network")
    sandbox_to_sandbox = _boolean(network["sandbox_to_sandbox"], "network.sandbox_to_sandbox")
    if sandbox_to_sandbox:
        raise ConfigurationError("network.sandbox_to_sandbox must remain false in schema version 1")
    listen_address = cast(str, _string(deployment["listen_address"], "deployment.listen_address"))
    try:
        parsed_listen_address = ipaddress.ip_address(listen_address)
    except ValueError as error:
        raise ConfigurationError("deployment.listen_address must be an IPv4 or IPv6 address") from error
    if not public_exposure and not parsed_listen_address.is_loopback:
        raise ConfigurationError(
            "deployment.listen_address must be loopback when public_exposure is false"
        )
    if domain is not None and not _DOMAIN_RE.fullmatch(domain):
        raise ConfigurationError("deployment.domain must be a fully qualified DNS name")
    ipv4_cidr = cast(str, _string(network["ipv4_cidr"], "network.ipv4_cidr"))
    try:
        parsed_network = ipaddress.ip_network(ipv4_cidr, strict=True)
    except ValueError as error:
        raise ConfigurationError("network.ipv4_cidr must be a canonical IPv4 CIDR") from error
    if parsed_network.version != 4:
        raise ConfigurationError("network.ipv4_cidr must be IPv4")
    dns_resolvers = _string_list(network["dns_resolvers"], "network.dns_resolvers")
    for resolver in dns_resolvers:
        try:
            ipaddress.ip_address(resolver)
        except ValueError as error:
            raise ConfigurationError("network.dns_resolvers entries must be IP addresses") from error
    private_egress_allowlist = _string_list(
        network["private_egress_allowlist"], "network.private_egress_allowlist", unique=True
    )
    for allowed_network in private_egress_allowlist:
        try:
            ipaddress.ip_network(allowed_network, strict=True)
        except ValueError as error:
            raise ConfigurationError(
                "network.private_egress_allowlist entries must be canonical CIDRs"
            ) from error

    features = _mapping(top["features"], "features")
    feature_keys = {"observability", "persistence", "backups", "browser_template", "desktop_template"}
    _keys(features, feature_keys, "features")

    return Configuration(
        schema_version=1,
        deployment=DeploymentConfig(
            profile=profile,
            lifecycle_mode=lifecycle_mode,
            listen_address=listen_address,
            public_exposure=public_exposure,
            domain=domain,
        ),
        paths=PathsConfig(**normalized_paths),
        sandbox=SandboxConfig(
            default_template=cast(str, template),
            default_vcpus=_integer(sandbox["default_vcpus"], "sandbox.default_vcpus", 1, 64),
            default_memory_mib=_integer(sandbox["default_memory_mib"], "sandbox.default_memory_mib", 256),
            default_disk_mib=_integer(sandbox["default_disk_mib"], "sandbox.default_disk_mib", 1024),
            default_timeout_seconds=_integer(sandbox["default_timeout_seconds"], "sandbox.default_timeout_seconds", 1),
            default_ttl_seconds=_integer(sandbox["default_ttl_seconds"], "sandbox.default_ttl_seconds", 60),
            max_processes=_integer(sandbox["max_processes"], "sandbox.max_processes", 16),
            max_output_bytes=_integer(sandbox["max_output_bytes"], "sandbox.max_output_bytes", 1024),
        ),
        network=NetworkConfig(
            ipv4_cidr=ipv4_cidr,
            ipv6_enabled=_boolean(network["ipv6_enabled"], "network.ipv6_enabled"),
            dns_resolvers=dns_resolvers,
            allow_http_egress=_boolean(network["allow_http_egress"], "network.allow_http_egress"),
            allow_private_egress=_boolean(network["allow_private_egress"], "network.allow_private_egress"),
            private_egress_allowlist=private_egress_allowlist,
            sandbox_to_sandbox=sandbox_to_sandbox,
        ),
        features=FeaturesConfig(**{
            key: _boolean(features[key], f"features.{key}") for key in feature_keys
        }),
    )


def load_configuration(
    *,
    config_path: Path | None = None,
    default_path: Path = DEFAULT_CONFIG_PATH,
    installed_path: Path = INSTALLED_CONFIG_PATH,
    cli_overrides: dict[str, Any] | None = None,
) -> LoadedConfiguration:
    """Load defaults, an installed/explicit operator file, then explicit overrides."""

    defaults = _load_yaml_file(default_path, required=True)
    assert defaults is not None
    merged = defaults
    sources = [str(default_path)]
    operator_path = config_path if config_path is not None else installed_path
    operator = _load_yaml_file(operator_path, required=config_path is not None)
    if operator is not None:
        merged = merge_mappings(merged, operator)
        sources.append(str(operator_path))
    if cli_overrides:
        merged = merge_mappings(merged, cli_overrides)
        sources.append("command-line")
    return LoadedConfiguration(validate_configuration(merged), merged, tuple(sources))

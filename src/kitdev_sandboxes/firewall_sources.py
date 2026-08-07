"""Core CLI backend for the SDK HTTPS source allowlist."""

from __future__ import annotations

import ipaddress
import json
import os
import selectors
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

DEFAULT_BACKEND = Path("/opt/kitdev-sandboxes/libexec/ingress/configure-firewall.sh")
MAXIMUM_OUTPUT_BYTES = 65_536
BACKEND_TIMEOUT_SECONDS = 30


class FirewallSourceOperationError(RuntimeError):
    """Bounded operator failure with a stable reason code."""

    def __init__(self, reason: str, exit_code: int = 65) -> None:
        super().__init__(reason)
        self.reason = reason
        self.exit_code = exit_code


def normalize_source_cidr(
    value: str,
    *,
    allow_non_public: bool = False,
    allow_broad_range: bool = False,
) -> str:
    try:
        network = ipaddress.ip_network(value, strict=True)
    except ValueError as error:
        raise FirewallSourceOperationError("source_cidr_invalid", 64) from error
    canonical = str(network)
    if value != canonical or network.prefixlen == 0:
        raise FirewallSourceOperationError("source_cidr_not_canonical", 64)
    non_public = not network.is_global or any(
        (
            network.is_private,
            network.is_loopback,
            network.is_link_local,
            network.is_multicast,
            network.is_reserved,
            network.is_unspecified,
        )
    )
    if non_public and not allow_non_public:
        raise FirewallSourceOperationError("source_cidr_non_public_requires_override", 64)
    if network.prefixlen < (24 if network.version == 4 else 64) and not allow_broad_range:
        raise FirewallSourceOperationError("source_cidr_broad_range_requires_override", 64)
    return canonical


@dataclass(frozen=True)
class FirewallSourceResult:
    action: str
    sources: tuple[dict[str, object], ...]
    mode: str = "closed"
    outcome: str = "converged"

    def as_dict(self) -> dict[str, object]:
        command = (
            f"firewall mode {self.mode}"
            if self.action.startswith("mode-")
            else f"firewall source {self.action}"
        )
        return {
            "schema_version": 1,
            "command": command,
            "status": "pass",
            "outcome": self.outcome,
            "mode": self.mode,
            "sources": list(self.sources),
            **(
                {"warnings": ["TCP 443 is open to every IPv4 and IPv6 source"]}
                if self.mode == "public"
                else {}
            ),
        }

    def render_text(self) -> str:
        command = (
            f"firewall-mode-{self.mode}"
            if self.action.startswith("mode-")
            else f"firewall-source-{self.action}"
        )
        lines = [
            f"command={command} status=pass outcome={self.outcome} mode={self.mode}"
        ]
        if self.mode == "public":
            lines.append("warning=TCP-443-is-open-to-all-IPv4-and-IPv6-sources")
        for source in self.sources:
            lines.append(
                f"cidr={source['cidr']} non_public_override="
                f"{str(source['non_public_override']).lower()}"
            )
        return "\n".join(lines)


Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def _default_runner(arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
    environment = {
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
    }
    try:
        process = subprocess.Popen(
            arguments,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            close_fds=True,
        )
    except OSError as error:
        raise FirewallSourceOperationError("firewall_source_backend_unavailable", 69) from error
    assert process.stdout is not None
    assert process.stderr is not None
    stdout_fd = process.stdout.fileno()
    stderr_fd = process.stderr.fileno()
    streams = {stdout_fd: bytearray(), stderr_fd: bytearray()}
    selector = selectors.DefaultSelector()
    for descriptor in streams:
        os.set_blocking(descriptor, False)
        selector.register(descriptor, selectors.EVENT_READ)
    deadline = time.monotonic() + BACKEND_TIMEOUT_SECONDS
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise FirewallSourceOperationError("firewall_source_backend_timeout", 69)
            for key, _ in selector.select(remaining):
                chunk = os.read(key.fd, 8192)
                if not chunk:
                    selector.unregister(key.fd)
                    continue
                target = streams[key.fd]
                target.extend(chunk)
                if len(target) > MAXIMUM_OUTPUT_BYTES:
                    raise FirewallSourceOperationError(
                        "firewall_source_backend_output_too_large", 69
                    )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise FirewallSourceOperationError("firewall_source_backend_timeout", 69)
        returncode = process.wait(timeout=remaining)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        raise FirewallSourceOperationError("firewall_source_backend_timeout", 69) from None
    except FirewallSourceOperationError:
        process.kill()
        process.wait()
        raise
    finally:
        selector.close()
        process.stdout.close()
        process.stderr.close()
    try:
        stdout = bytes(streams[stdout_fd]).decode("ascii")
        stderr = bytes(streams[stderr_fd]).decode("ascii")
    except UnicodeDecodeError as error:
        raise FirewallSourceOperationError("firewall_source_backend_invalid", 69) from error
    return subprocess.CompletedProcess(list(arguments), returncode, stdout, stderr)


def _run_backend(
    arguments: Sequence[str], action: str, runner: Runner
) -> FirewallSourceResult:
    completed = runner(arguments)
    if completed.returncode != 0:
        reason = "firewall_source_backend_failed"
        for line in completed.stderr.splitlines():
            if line.startswith("status=error reason="):
                reason = line.split("=", 2)[2]
                break
        raise FirewallSourceOperationError(reason, completed.returncode)
    try:
        document = json.loads(completed.stdout)
        if (
            not isinstance(document, dict)
            or set(document) != {"schema_version", "command", "status", "mode", "sources"}
            or document["schema_version"] != 1
            or document["command"] != "firewall source list"
            or document["status"] != "pass"
        ):
            raise TypeError
        raw_sources = document["sources"]
        mode = document["mode"]
        if not isinstance(raw_sources, list) or mode not in {"closed", "public", "restricted"}:
            raise TypeError
        checked: list[dict[str, object]] = []
        networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
        for source in raw_sources:
            if not isinstance(source, dict) or set(source) != {
                "cidr",
                "non_public_override",
                "broad_range_override",
            }:
                raise TypeError
            cidr_value = source["cidr"]
            non_public_override = source["non_public_override"]
            broad_range_override = source["broad_range_override"]
            if (
                not isinstance(cidr_value, str)
                or not isinstance(non_public_override, bool)
                or not isinstance(broad_range_override, bool)
            ):
                raise TypeError
            canonical = normalize_source_cidr(
                cidr_value,
                allow_non_public=non_public_override,
                allow_broad_range=broad_range_override,
            )
            network = ipaddress.ip_network(canonical)
            non_public = not network.is_global or any(
                (
                    network.is_private,
                    network.is_loopback,
                    network.is_link_local,
                    network.is_multicast,
                    network.is_reserved,
                    network.is_unspecified,
                )
            )
            if non_public_override != non_public or broad_range_override != (
                network.prefixlen < (24 if network.version == 4 else 64)
            ):
                raise TypeError
            if any(network.overlaps(existing) for existing in networks):
                raise TypeError
            networks.append(network)
            checked.append(source)
        keys = [
            (network.version, int(network.network_address), network.prefixlen)
            for network in networks
        ]
        if keys != sorted(keys):
            raise TypeError
        sources = tuple(checked)
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise FirewallSourceOperationError("firewall_source_backend_invalid", 69) from error
    return FirewallSourceResult(action=action, sources=sources, mode=mode)


def run_firewall_source_operation(
    action: str,
    *,
    cidr: str | None = None,
    allow_non_public: bool = False,
    allow_broad_range: bool = False,
    backend: Path = DEFAULT_BACKEND,
    runner: Runner = _default_runner,
) -> FirewallSourceResult:
    if action not in {"add", "list", "remove"}:
        raise FirewallSourceOperationError("firewall_source_action_invalid", 64)
    arguments = [str(backend), f"source-{action}"]
    if action == "list":
        if cidr is not None or allow_non_public or allow_broad_range:
            raise FirewallSourceOperationError("firewall_source_arguments_invalid", 64)
    else:
        if cidr is None:
            raise FirewallSourceOperationError("source_cidr_required", 64)
        canonical = normalize_source_cidr(
            cidr,
            allow_non_public=allow_non_public if action == "add" else True,
            allow_broad_range=allow_broad_range if action == "add" else True,
        )
        arguments.extend(["--cidr", canonical])
        if action == "add" and allow_non_public:
            arguments.append("--allow-non-public")
        if action == "add" and allow_broad_range:
            arguments.append("--allow-broad-range")
    return _run_backend(arguments, action, runner)


def run_firewall_mode_operation(
    mode: str,
    *,
    backend: Path = DEFAULT_BACKEND,
    runner: Runner = _default_runner,
) -> FirewallSourceResult:
    if mode not in {"closed", "public", "restricted"}:
        raise FirewallSourceOperationError("firewall_mode_invalid", 64)
    return _run_backend([str(backend), "mode", mode], f"mode-{mode}", runner)

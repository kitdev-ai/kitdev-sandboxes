"""Bounded subprocess execution for trusted, allowlisted, read-only collectors.

The runner contains the original process group and kills that group before it
returns. It is not a sandbox for hostile executables: a descendant can escape
the group with ``setsid()`` unless a higher layer supplies cgroup containment.
Only reviewed collector commands may be passed here.

Command results are internal normalized parser input. They are deliberately not
credential-redacted because redaction can corrupt structured facts. Consumers
must extract allowlisted facts, redact report evidence, and never serialize a
raw ``CommandResult``.
"""

from __future__ import annotations

from contextlib import suppress
import errno
import math
import os
import selectors
import signal
import stat
import subprocess
import time
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import BinaryIO


DEFAULT_STREAM_LIMIT_BYTES = 262_144
MAX_STREAM_LIMIT_BYTES = 4_194_304
MAX_ARG_COUNT = 64
MAX_ARG_BYTES = 4_096
MAX_ARGV_BYTES = 16_384
MAX_TIMEOUT_SECONDS = 300.0
MAX_TERMINATION_GRACE_SECONDS = 5.0
MAX_NORMALIZED_EVIDENCE_BYTES = 1_048_576
MAX_NORMALIZED_ARG_BYTES = 4_096
MAX_NORMALIZED_ARGV_BYTES = 16_384
_READ_SIZE = 65_536
_SELECT_INTERVAL_SECONDS = 0.05
_KILL_REAP_SECONDS = 1.0

MINIMAL_ENVIRONMENT = MappingProxyType(
    {
        "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
        "LANG": "C",
        "LANGUAGE": "C",
        "LC_ALL": "C",
        "TZ": "UTC",
        "HOME": "/dev/null",
        "TMPDIR": "/dev/null",
        "TERM": "dumb",
        "NO_COLOR": "1",
        "PAGER": "cat",
        "GIT_PAGER": "cat",
        "SYSTEMD_PAGER": "cat",
        "SYSTEMD_COLORS": "0",
    }
)


class CommandOutcome(StrEnum):
    """Stable classification of a command invocation."""

    SUCCESS = "success"
    NONZERO = "nonzero"
    SIGNALED = "signaled"
    TIMEOUT = "timeout"
    MISSING = "missing"
    PERMISSION_DENIED = "permission_denied"
    SPAWN_ERROR = "spawn_error"
    IO_ERROR = "io_error"
    CLEANUP_ERROR = "cleanup_error"


@dataclass(frozen=True)
class Command:
    """A fully bounded command policy; environment and stdin are not caller-controlled."""

    argv: tuple[str, ...]
    cwd: Path = Path("/")
    timeout_seconds: float = 5.0
    termination_grace_seconds: float = 0.25
    stdout_limit_bytes: int = DEFAULT_STREAM_LIMIT_BYTES
    stderr_limit_bytes: int = DEFAULT_STREAM_LIMIT_BYTES

    def __post_init__(self) -> None:
        if self.argv.__class__ is not tuple:
            raise TypeError("argv must be an exact tuple")
        if not self.argv:
            raise ValueError("argv must not be empty")
        if not self.argv[0]:
            raise ValueError("argv[0] must not be empty")
        if len(self.argv) > MAX_ARG_COUNT:
            raise ValueError(f"argv may contain at most {MAX_ARG_COUNT} elements")

        total_bytes = 0
        for argument in self.argv:
            if argument.__class__ is not str:
                raise TypeError("every argv element must be a string")
            if "\x00" in argument:
                raise ValueError("argv elements must not contain NUL bytes")
            try:
                encoded_argument = argument.encode("utf-8", errors="strict")
            except UnicodeEncodeError as error:
                raise ValueError("argv elements must be valid UTF-8 text") from error
            argument_bytes = len(encoded_argument)
            if argument_bytes > MAX_ARG_BYTES:
                raise ValueError(f"an argv element may contain at most {MAX_ARG_BYTES} bytes")
            total_bytes += argument_bytes
        if total_bytes > MAX_ARGV_BYTES:
            raise ValueError(f"argv may contain at most {MAX_ARGV_BYTES} bytes")

        if not isinstance(self.cwd, Path):
            raise TypeError("cwd must be a pathlib.Path")
        if not self.cwd.is_absolute():
            raise ValueError("cwd must be absolute")
        if any(character == "\x00" or _is_control(character) for character in str(self.cwd)):
            raise ValueError("cwd must not contain control characters")
        try:
            os.fsencode(self.cwd).decode("utf-8", errors="strict")
        except (UnicodeDecodeError, UnicodeEncodeError) as error:
            raise ValueError("cwd must be valid UTF-8 text") from error

        _validate_duration("timeout_seconds", self.timeout_seconds, MAX_TIMEOUT_SECONDS)
        _validate_duration(
            "termination_grace_seconds",
            self.termination_grace_seconds,
            MAX_TERMINATION_GRACE_SECONDS,
        )
        _validate_stream_limit("stdout_limit_bytes", self.stdout_limit_bytes)
        _validate_stream_limit("stderr_limit_bytes", self.stderr_limit_bytes)


@dataclass(frozen=True, repr=False)
class StreamEvidence:
    """Bounded normalized parser input from one subprocess stream.

    ``text`` has terminal controls escaped but contains unredacted command data.
    It must not be serialized or logged directly.
    """

    text: str
    bytes_captured: int
    bytes_discarded: int
    truncated: bool
    normalized_bytes: int = 0
    normalized_truncated: bool = False
    read_error: bool = False


@dataclass(frozen=True, repr=False)
class CommandResult:
    """Internal raw-normalized result; consumers must extract and redact facts."""

    argv: tuple[str, ...]
    outcome: CommandOutcome
    returncode: int | None
    termination_signal: int | None
    timed_out: bool
    missing_executable: bool
    permission_denied: bool
    stdout: StreamEvidence
    stderr: StreamEvidence
    duration_seconds: float
    error_message: str | None = None
    io_error: bool = False
    cleanup_error: bool = False
    argv_truncated: bool = False

    @property
    def succeeded(self) -> bool:
        return self.outcome is CommandOutcome.SUCCESS

    @property
    def output_truncated(self) -> bool:
        return self.stdout.truncated or self.stderr.truncated


class _StreamAccumulator:
    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._data = bytearray()
        self._discarded = 0
        self._read_error = False

    def add(self, chunk: bytes) -> None:
        available = self._limit - len(self._data)
        if available > 0:
            self._data.extend(chunk[:available])
        self._discarded += max(0, len(chunk) - max(0, available))

    def mark_read_error(self) -> None:
        self._read_error = True

    def evidence(self) -> StreamEvidence:
        normalized = _normalize_terminal_text(
            bytes(self._data),
            maximum_bytes=MAX_NORMALIZED_EVIDENCE_BYTES,
            preserve_newlines=True,
        )
        return StreamEvidence(
            text=normalized.text,
            bytes_captured=len(self._data),
            bytes_discarded=self._discarded,
            truncated=self._discarded > 0 or normalized.truncated or self._read_error,
            normalized_bytes=normalized.size_bytes,
            normalized_truncated=normalized.truncated,
            read_error=self._read_error,
        )


@dataclass(frozen=True)
class _NormalizedText:
    text: str
    size_bytes: int
    truncated: bool


class CommandRunner:
    """Execute commands without a shell using fixed process and evidence bounds."""

    def run(self, command: Command) -> CommandResult:
        started = time.monotonic()
        safe_argv, argv_truncated = _normalize_argv(command.argv)
        try:
            cwd_stat = command.cwd.stat()
        except FileNotFoundError:
            return _spawn_failure(
                safe_argv,
                CommandOutcome.SPAWN_ERROR,
                started,
                argv_truncated=argv_truncated,
                error_message="working directory was not found",
            )
        except PermissionError:
            return _spawn_failure(
                safe_argv,
                CommandOutcome.PERMISSION_DENIED,
                started,
                permission_denied=True,
                argv_truncated=argv_truncated,
                error_message="permission denied while accessing working directory",
            )
        except OSError:
            return _spawn_failure(
                safe_argv,
                CommandOutcome.SPAWN_ERROR,
                started,
                argv_truncated=argv_truncated,
                error_message="working directory could not be inspected",
            )
        if not stat.S_ISDIR(cwd_stat.st_mode):
            return _spawn_failure(
                safe_argv,
                CommandOutcome.SPAWN_ERROR,
                started,
                argv_truncated=argv_truncated,
                error_message="working directory is not a directory",
            )

        try:
            # Collector policy supplies argv; this layer never invokes a shell.
            process = subprocess.Popen(
                command.argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=command.cwd,
                env=dict(MINIMAL_ENVIRONMENT),
                shell=False,
                start_new_session=True,
                close_fds=True,
                bufsize=0,
            )
        except FileNotFoundError:
            return _spawn_failure(
                safe_argv,
                CommandOutcome.MISSING,
                started,
                missing_executable=True,
                argv_truncated=argv_truncated,
                error_message="executable was not found",
            )
        except PermissionError:
            return _spawn_failure(
                safe_argv,
                CommandOutcome.PERMISSION_DENIED,
                started,
                permission_denied=True,
                argv_truncated=argv_truncated,
                error_message="permission denied while starting executable",
            )
        except OSError:
            return _spawn_failure(
                safe_argv,
                CommandOutcome.SPAWN_ERROR,
                started,
                argv_truncated=argv_truncated,
                error_message="operating system rejected command startup",
            )

        assert process.stdout is not None
        assert process.stderr is not None
        stdout = _StreamAccumulator(command.stdout_limit_bytes)
        stderr = _StreamAccumulator(command.stderr_limit_bytes)
        streams = {
            process.stdout.fileno(): (process.stdout, stdout),
            process.stderr.fileno(): (process.stderr, stderr),
        }
        selector = selectors.DefaultSelector()
        timed_out = False
        term_sent_at: float | None = None
        kill_sent_at: float | None = None
        deadline = started + command.timeout_seconds
        completed = False
        cleanup_error = False
        group_finalized = False

        try:
            for file_descriptor in streams:
                try:
                    os.set_blocking(file_descriptor, False)
                    selector.register(file_descriptor, selectors.EVENT_READ)
                except OSError:
                    _close_stream_after_error(
                        selector,
                        file_descriptor,
                        streams[file_descriptor],
                    )
            if not selector.get_map():
                cleanup_error |= not _signal_process_group(process.pid, signal.SIGKILL)
                group_finalized = True

            while selector.get_map() or process.poll() is None:
                now = time.monotonic()
                if not timed_out and now >= deadline:
                    timed_out = True
                    term_sent_at = now
                    cleanup_error |= not _signal_process_group(process.pid, signal.SIGTERM)
                elif (
                    timed_out
                    and kill_sent_at is None
                    and term_sent_at is not None
                    and now >= term_sent_at + command.termination_grace_seconds
                ):
                    kill_sent_at = now
                    cleanup_error |= not _signal_process_group(process.pid, signal.SIGKILL)
                    group_finalized = True
                elif (
                    kill_sent_at is not None
                    and now >= kill_sent_at + _KILL_REAP_SECONDS
                    and (selector.get_map() or process.poll() is None)
                ):
                    # SIGKILL normally closes these immediately. This hard bound prevents an
                    # uninterruptible process or broken platform pipe from hanging doctor.
                    for key in tuple(selector.get_map().values()):
                        with suppress(KeyError, OSError):
                            selector.unregister(key.fd)
                    break

                wake_at = deadline
                if term_sent_at is not None and kill_sent_at is None:
                    wake_at = term_sent_at + command.termination_grace_seconds
                elif kill_sent_at is not None:
                    wake_at = kill_sent_at + _KILL_REAP_SECONDS
                wait_seconds = max(0.0, min(_SELECT_INTERVAL_SECONDS, wake_at - now))
                try:
                    ready = selector.select(wait_seconds)
                except OSError as error:
                    if error.errno == errno.EINTR:
                        continue
                    for key in tuple(selector.get_map().values()):
                        _close_stream_after_error(selector, key.fd, streams[key.fd])
                    if not selector.get_map() and not group_finalized:
                        cleanup_error |= not _signal_process_group(
                            process.pid, signal.SIGKILL
                        )
                        group_finalized = True
                    continue
                for key, _event_mask in ready:
                    stream, accumulator = streams[key.fd]
                    try:
                        chunk = _read_pipe(key.fd)
                    except BlockingIOError:
                        continue
                    except OSError as error:
                        if error.errno in {errno.EAGAIN, errno.EWOULDBLOCK}:
                            continue
                        _close_stream_after_error(selector, key.fd, (stream, accumulator))
                        continue
                    if chunk:
                        accumulator.add(chunk)
                    else:
                        try:
                            selector.unregister(key.fd)
                        except (KeyError, OSError):
                            accumulator.mark_read_error()
                        try:
                            stream.close()
                        except OSError:
                            accumulator.mark_read_error()
                        if not selector.get_map() and not group_finalized:
                            cleanup_error |= not _signal_process_group(
                                process.pid,
                                signal.SIGKILL,
                                allow_empty_group_permission=True,
                            )
                            group_finalized = True

            # A collector may fork a background process which closes its inherited pipes.
            # The dedicated process group is never allowed to outlive this invocation.
            if not group_finalized:
                cleanup_error |= not _signal_process_group(
                    process.pid,
                    signal.SIGKILL,
                    allow_empty_group_permission=True,
                )
            returncode, reap_error = _reap_process(process)
            cleanup_error |= reap_error
            completed = True
        finally:
            if not completed:
                _signal_process_group(process.pid, signal.SIGKILL)
                _reap_process(process)
            with suppress(OSError):
                selector.close()
            for stream, _accumulator in streams.values():
                if not stream.closed:
                    with suppress(OSError):
                        stream.close()

        stdout_evidence = stdout.evidence()
        stderr_evidence = stderr.evidence()
        io_error = stdout_evidence.read_error or stderr_evidence.read_error
        outcome = _classify_outcome(returncode, timed_out, io_error, cleanup_error)
        termination_signal = -returncode if returncode is not None and returncode < 0 else None
        return CommandResult(
            argv=safe_argv,
            outcome=outcome,
            returncode=returncode,
            termination_signal=termination_signal,
            timed_out=timed_out,
            missing_executable=False,
            permission_denied=False,
            io_error=io_error,
            cleanup_error=cleanup_error,
            argv_truncated=argv_truncated,
            stdout=stdout_evidence,
            stderr=stderr_evidence,
            duration_seconds=max(0.0, time.monotonic() - started),
            error_message=_runtime_error_message(outcome),
        )


def _validate_duration(name: str, value: float, maximum: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number")
    if not math.isfinite(value) or not 0 < value <= maximum:
        raise ValueError(f"{name} must be finite, greater than zero, and at most {maximum}")


def _validate_stream_limit(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if not 0 <= value <= MAX_STREAM_LIMIT_BYTES:
        raise ValueError(f"{name} must be between 0 and {MAX_STREAM_LIMIT_BYTES}")


def _is_control(character: str) -> bool:
    codepoint = ord(character)
    if codepoint < 160:
        return codepoint < 32 or codepoint >= 127
    return unicodedata.category(character) in {
        "Cc",
        "Cf",
        "Cs",
        "Zl",
        "Zp",
    }


def _escaped_control(character: str) -> str:
    codepoint = ord(character)
    if codepoint <= 0xFF:
        return f"\\x{codepoint:02x}"
    if codepoint <= 0xFFFF:
        return f"\\u{codepoint:04x}"
    return f"\\U{codepoint:08x}"


def _normalize_terminal_text(
    data: bytes,
    *,
    maximum_bytes: int,
    preserve_newlines: bool,
) -> _NormalizedText:
    """Decode and neutralize into one bounded byte buffer with limited amplification."""

    decoded = data.decode("utf-8", errors="replace")
    normalized = bytearray()
    truncated = False
    for character in decoded:
        if character == "\n" and preserve_newlines:
            piece = b"\n"
        elif _is_control(character):
            piece = _escaped_control(character).encode("ascii")
        else:
            piece = character.encode("utf-8")
        if len(normalized) + len(piece) > maximum_bytes:
            truncated = True
            break
        normalized.extend(piece)
    return _NormalizedText(normalized.decode("utf-8"), len(normalized), truncated)


def _normalize_argv(argv: tuple[str, ...]) -> tuple[tuple[str, ...], bool]:
    normalized: list[str] = []
    remaining = MAX_NORMALIZED_ARGV_BYTES
    truncated = False
    for argument in argv:
        result = _normalize_terminal_text(
            argument.encode("utf-8"),
            maximum_bytes=min(MAX_NORMALIZED_ARG_BYTES, remaining),
            preserve_newlines=False,
        )
        normalized.append(result.text)
        remaining -= result.size_bytes
        truncated |= result.truncated
    return tuple(normalized), truncated


def _close_stream_after_error(
    selector: selectors.BaseSelector,
    file_descriptor: int,
    stream_entry: tuple[BinaryIO, _StreamAccumulator],
) -> None:
    stream, accumulator = stream_entry
    accumulator.mark_read_error()
    with suppress(KeyError, OSError):
        selector.unregister(file_descriptor)
    with suppress(OSError):
        stream.close()


def _read_pipe(file_descriptor: int) -> bytes:
    return os.read(file_descriptor, _READ_SIZE)


def _signal_process_group(
    process_group: int,
    signal_number: signal.Signals,
    *,
    allow_empty_group_permission: bool = False,
) -> bool:
    try:
        os.killpg(process_group, signal_number)
    except ProcessLookupError:
        return True
    except PermissionError:
        # Darwin can report EPERM for a group containing only an exited,
        # still-unreaped leader. Only normal EOF cleanup treats that as empty.
        return allow_empty_group_permission
    except OSError:
        return False
    return True


def _reap_process(process: subprocess.Popen[bytes]) -> tuple[int | None, bool]:
    try:
        returncode = process.poll()
    except OSError:
        return None, True
    if returncode is not None:
        return returncode, False
    cleanup_error = not _signal_process_group(process.pid, signal.SIGKILL)
    try:
        return process.wait(timeout=_KILL_REAP_SECONDS), cleanup_error
    except subprocess.TimeoutExpired:
        try:
            return process.poll(), True
        except OSError:
            return None, True
    except OSError:
        return None, True


def _classify_outcome(
    returncode: int | None,
    timed_out: bool,
    io_error: bool,
    cleanup_error: bool,
) -> CommandOutcome:
    if timed_out:
        return CommandOutcome.TIMEOUT
    if io_error:
        return CommandOutcome.IO_ERROR
    if cleanup_error:
        return CommandOutcome.CLEANUP_ERROR
    if returncode is None:
        return CommandOutcome.SPAWN_ERROR
    if returncode == 0:
        return CommandOutcome.SUCCESS
    if returncode < 0:
        return CommandOutcome.SIGNALED
    return CommandOutcome.NONZERO


def _runtime_error_message(outcome: CommandOutcome) -> str | None:
    if outcome is CommandOutcome.IO_ERROR:
        return "subprocess stream read failed"
    if outcome is CommandOutcome.CLEANUP_ERROR:
        return "process-group cleanup could not be confirmed"
    return None


def _spawn_failure(
    argv: tuple[str, ...],
    outcome: CommandOutcome,
    started: float,
    *,
    missing_executable: bool = False,
    permission_denied: bool = False,
    argv_truncated: bool = False,
    error_message: str,
) -> CommandResult:
    empty = StreamEvidence("", 0, 0, False)
    return CommandResult(
        argv=argv,
        outcome=outcome,
        returncode=None,
        termination_signal=None,
        timed_out=False,
        missing_executable=missing_executable,
        permission_denied=permission_denied,
        io_error=False,
        cleanup_error=False,
        argv_truncated=argv_truncated,
        stdout=empty,
        stderr=empty,
        duration_seconds=max(0.0, time.monotonic() - started),
        error_message=error_message,
    )

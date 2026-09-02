"""Crash-safe runtime primitives shared by the tinyTouch CLI and helper."""

from __future__ import annotations

import fcntl
import json
import os
import random
import secrets
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable


LEASE_SCHEMA = 1
MAX_LEASE_SECONDS = 6 * 60 * 60
MAX_DIAGNOSTIC_BYTES = 2 * 1024 * 1024
_DIAGNOSTIC_LOCK = threading.Lock()


class LeaseBusyError(RuntimeError):
    """Another foreground command currently owns the device lease."""


class LeaseProtocolError(RuntimeError):
    """The helper did not acknowledge a valid foreground lease."""


def atomic_write_bytes(path: Path, payload: bytes, *, mode: int = 0o600) -> None:
    """Replace a file durably without exposing partial content."""
    path.parent.mkdir(parents=True, exist_ok=True)
    directory_fd = os.open(path.parent, os.O_RDONLY)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=f".{path.name}.", dir=path.parent, delete=False
        ) as temporary:
            temporary_name = temporary.name
            os.fchmod(temporary.fileno(), mode)
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
        temporary_name = None
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass


def atomic_write_json(path: Path, value: object, *, mode: int = 0o600) -> None:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    atomic_write_bytes(path, payload, mode=mode)


def read_json_object(path: Path) -> dict[str, object] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def process_is_alive(pid: int) -> bool:
    if pid <= 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@dataclass(frozen=True)
class LeaseRecord:
    pid: int
    nonce: str
    acquired_at: float

    @classmethod
    def parse(cls, value: dict[str, object] | None) -> "LeaseRecord | None":
        if value is None or value.get("schema") != LEASE_SCHEMA:
            return None
        pid = value.get("pid")
        nonce = value.get("nonce")
        acquired_at = value.get("acquired_at")
        if (
            not isinstance(pid, int)
            or isinstance(pid, bool)
            or not isinstance(nonce, str)
            or len(nonce) != 32
            or any(character not in "0123456789abcdef" for character in nonce)
            or not isinstance(acquired_at, (int, float))
            or isinstance(acquired_at, bool)
        ):
            return None
        return cls(pid=pid, nonce=nonce, acquired_at=float(acquired_at))

    def is_live(self, *, now: float | None = None) -> bool:
        age = (time.time() if now is None else now) - self.acquired_at
        return -60 <= age <= MAX_LEASE_SECONDS and process_is_alive(self.pid)

    def as_json(self) -> dict[str, object]:
        return {
            "schema": LEASE_SCHEMA,
            "pid": self.pid,
            "nonce": self.nonce,
            "acquired_at": self.acquired_at,
        }


class ForegroundLease:
    """Hold exclusive foreground ownership until a CLI command completes."""

    def __init__(self, path: Path, acknowledgement_path: Path):
        self.path = path
        self.acknowledgement_path = acknowledgement_path
        self.lock_path = path.with_suffix(path.suffix + ".lock")
        self.record: LeaseRecord | None = None
        self._lock: BinaryIO | None = None

    def acquire(self, *, wait_for_ack: bool, timeout: float = 8.0) -> "ForegroundLease":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.lock_path.open("a+b")
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            lock.close()
            raise LeaseBusyError("Another tinyTouch command is using the device.") from exc
        self._lock = lock
        self.record = LeaseRecord(os.getpid(), secrets.token_hex(16), time.time())
        self.acknowledgement_path.unlink(missing_ok=True)
        atomic_write_json(self.path, self.record.as_json())
        if wait_for_ack:
            self._wait_for_ack(timeout)
        return self

    def _wait_for_ack(self, timeout: float) -> None:
        assert self.record is not None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            acknowledgement = read_json_object(self.acknowledgement_path)
            if acknowledgement and acknowledgement.get("nonce") == self.record.nonce:
                return
            time.sleep(0.05)
        self.release()
        raise LeaseProtocolError(
            "The HID background service did not release the serial device in time."
        )

    def release(self) -> None:
        if self.record is not None:
            current = LeaseRecord.parse(read_json_object(self.path))
            if current == self.record:
                self.path.unlink(missing_ok=True)
            acknowledgement = read_json_object(self.acknowledgement_path)
            if acknowledgement and acknowledgement.get("nonce") == self.record.nonce:
                self.acknowledgement_path.unlink(missing_ok=True)
        if self._lock is not None:
            fcntl.flock(self._lock.fileno(), fcntl.LOCK_UN)
            self._lock.close()
        self.record = None
        self._lock = None

    def __enter__(self) -> "ForegroundLease":
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        self.release()


class LeaseObserver:
    """Validate foreground leases and publish matching helper acknowledgements."""

    def __init__(self, path: Path, acknowledgement_path: Path):
        self.path = path
        self.acknowledgement_path = acknowledgement_path

    def active(self) -> LeaseRecord | None:
        record = LeaseRecord.parse(read_json_object(self.path))
        if record is not None and record.is_live():
            return record
        self.path.unlink(missing_ok=True)
        self.acknowledgement_path.unlink(missing_ok=True)
        return None

    def acknowledge(self, record: LeaseRecord) -> None:
        atomic_write_json(
            self.acknowledgement_path,
            {"schema": LEASE_SCHEMA, "pid": os.getpid(), "nonce": record.nonce},
        )


@dataclass(frozen=True)
class BackoffPolicy:
    initial: float = 0.25
    maximum: float = 30.0
    multiplier: float = 2.0
    jitter: float = 0.2

    def delay(self, failures: int, *, random_value: Callable[[], float] = random.random) -> float:
        base = min(self.maximum, self.initial * self.multiplier ** max(0, failures - 1))
        spread = base * self.jitter
        return max(0.0, base - spread + (2 * spread * random_value()))


class SerialFrameDecoder:
    """Decode newline frames and quarantine an oversized frame through its delimiter."""

    def __init__(self, maximum: int):
        self.maximum = maximum
        self.buffer = bytearray()
        self.discarding = False

    def feed(self, chunk: bytes) -> list[bytes]:
        frames: list[bytes] = []
        for value in chunk:
            if self.discarding:
                if value == 0x0A:
                    self.discarding = False
                continue
            if value == 0x0A:
                frames.append(bytes(self.buffer))
                self.buffer.clear()
                continue
            self.buffer.append(value)
            if len(self.buffer) > self.maximum:
                self.buffer.clear()
                self.discarding = True
        return frames

    def discard_partial(self) -> bool:
        had_partial = bool(self.buffer) or self.discarding
        self.buffer.clear()
        self.discarding = False
        return had_partial


def diagnostic(event: str, *, level: str = "info", **fields: object) -> None:
    """Write one bounded JSON diagnostic record without secret values."""
    record = {
        "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "level": level,
        "event": event,
        **fields,
    }
    line = json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
    configured_path = os.environ.get("TINYTOUCH_DIAGNOSTIC_LOG")
    if not configured_path:
        print(line, end="", flush=True)
        return
    path = Path(configured_path)
    with _DIAGNOSTIC_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if path.stat().st_size + len(line.encode("utf-8")) > MAX_DIAGNOSTIC_BYTES:
                older = path.with_suffix(path.suffix + ".2")
                previous = path.with_suffix(path.suffix + ".1")
                older.unlink(missing_ok=True)
                if previous.exists():
                    os.replace(previous, older)
                os.replace(path, previous)
        except FileNotFoundError:
            pass
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(descriptor, line.encode("utf-8"))
        finally:
            os.close(descriptor)

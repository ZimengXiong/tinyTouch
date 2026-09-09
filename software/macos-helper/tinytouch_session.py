"""Switch helpers for a beta session and restore the prior production state."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess

from tinytouch_channel import CHANNEL, PRODUCTION_LABEL
from tinytouch_runtime import atomic_write_json

BETA_LABEL = "com.tinytouch.beta.helper"


class SessionError(RuntimeError):
    """A helper switch failed and can be retried without losing its snapshot."""


def _launchctl(*arguments: str, missing_ok: bool = False) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(
            ["launchctl", *arguments], check=False, capture_output=True, text=True,
        )
    except OSError as exc:
        raise SessionError("Could not run launchctl. Retry from your macOS login session.") from exc
    if result.returncode and not (missing_ok and result.returncode == 113):
        detail = (result.stderr or result.stdout).strip()
        raise SessionError(
            f"Could not run launchctl {' '.join(arguments)}"
            f"{': ' + detail if detail else '.'} Retry the beta command or tinytouch-beta exit."
        )
    return result


def _target(label: str) -> str:
    return f"gui/{os.getuid()}/{label}"


def _loaded(label: str) -> bool:
    return _launchctl("print", _target(label), missing_ok=True).returncode == 0


def _disabled(label: str) -> bool:
    output = _launchctl("print-disabled", f"gui/{os.getuid()}").stdout
    # launchctl lists only explicit overrides; an omitted label is enabled.
    found = re.search(r'"' + re.escape(label) + r'"\s*=>\s*(true|false)', output)
    if found:
        return found.group(1) == "true"
    if "disabled services = {" not in output:
        raise SessionError("Could not read helper enablement from launchctl. No helper was changed.")
    return False


def _set_disabled(label: str, disabled: bool) -> None:
    _launchctl("disable" if disabled else "enable", _target(label))


def _stop(label: str) -> None:
    if _loaded(label):
        _launchctl("bootout", _target(label))


def _plist(label: str) -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"


def _start(label: str, *, required: bool) -> None:
    if _loaded(label):
        return
    path = _plist(label)
    if not path.is_file():
        if required:
            raise SessionError(
                f"The saved helper file is missing: {path}. Restore it and run tinytouch-beta exit again."
            )
        return
    _launchctl("bootstrap", f"gui/{os.getuid()}", str(path))


@contextmanager
def command_lock():
    """Reject overlapping beta commands, including activation and exit."""
    directory = Path.home() / "Library" / "Application Support" / "tinyTouch-beta"
    directory.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(directory / "helper-command.lock", os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(descriptor, "a+b") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SessionError("Another beta command is running. Wait for it to finish and retry.") from exc
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


@contextmanager
def _locked_session():
    directory = Path.home() / "Library" / "Application Support" / "tinyTouch-beta"
    directory.mkdir(parents=True, exist_ok=True)
    lock_path = directory / "helper-session.lock"
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(descriptor, "a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield directory / "helper-session.json"
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _read_snapshot(path: Path) -> dict | None:
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SessionError(
            f"Could not read the helper session at {path}. No helper was changed."
        ) from exc
    if (
        not isinstance(snapshot, dict) or set(snapshot) != {"schema", "uid", "loaded", "disabled"}
        or type(snapshot["schema"]) is not int or snapshot["schema"] != 1
        or type(snapshot["uid"]) is not int or snapshot["uid"] != os.getuid()
        or type(snapshot["loaded"]) is not bool or type(snapshot["disabled"]) is not bool
    ):
        raise SessionError(f"The helper session at {path} is invalid. No helper was changed.")
    return snapshot


def activate_beta() -> None:
    """Stop production and enable beta, preserving the first production snapshot."""
    if not CHANNEL.beta:
        raise SessionError("Helper switching is available only through tinytouch-beta.")
    with _locked_session() as path:
        snapshot = _read_snapshot(path)
        if snapshot is None:
            snapshot = {
                "schema": 1, "uid": os.getuid(),
                "loaded": _loaded(PRODUCTION_LABEL), "disabled": _disabled(PRODUCTION_LABEL),
            }
            atomic_write_json(path, snapshot)
        # Keep the snapshot on every failure so exit can restore the original state.
        _set_disabled(PRODUCTION_LABEL, True)
        _stop(PRODUCTION_LABEL)
        _set_disabled(BETA_LABEL, False)
        _start(BETA_LABEL, required=False)


def exit_beta() -> None:
    """Stop beta and restore the production state saved by the first activation."""
    if not CHANNEL.beta:
        raise SessionError("Helper switching is available only through tinytouch-beta.")
    with _locked_session() as path:
        snapshot = _read_snapshot(path)
        _set_disabled(BETA_LABEL, True)
        _stop(BETA_LABEL)
        if snapshot is None:
            return
        if snapshot["loaded"]:
            # A loaded service can also have a disabled launch override. Temporarily
            # enable it to bootstrap, then restore the saved override below.
            _set_disabled(PRODUCTION_LABEL, False)
            _start(PRODUCTION_LABEL, required=True)
        else:
            _stop(PRODUCTION_LABEL)
        _set_disabled(PRODUCTION_LABEL, snapshot["disabled"])
        path.unlink()

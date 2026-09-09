"""Select local identities and update versions from the bundled release channel."""

from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys

PRODUCTION_LABEL = "com.tinytouch.helper"
BETA_VERSION = r"[0-9]+\.[0-9]+\.[0-9]+-beta\.[0-9]+"
PRODUCTION_VERSION = r"[0-9]+\.[0-9]+\.[0-9]+-prod"


def bundled_version() -> str:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    path = root / "VERSION"
    return path.read_text(encoding="utf-8").strip() if path.exists() else "development"


class Channel:
    """Keep beta credentials, helper state, and releases separate from production."""

    def __init__(self, version: str):
        self.beta = re.fullmatch(BETA_VERSION, version) is not None
        self.command = "tinytouch-beta" if self.beta else "tinytouch"
        self.name = "tinyTouch-beta" if self.beta else "tinyTouch"
        self.label = "com.tinytouch.beta.helper" if self.beta else PRODUCTION_LABEL
        self.password_service = self.name
        self.pairing_service = f"{self.name}-pairing"

    def accepts(self, version: str) -> bool:
        pattern = BETA_VERSION if self.beta else PRODUCTION_VERSION
        return isinstance(version, str) and re.fullmatch(pattern, version) is not None

    def require_device_access(self) -> None:
        if not self.beta:
            return
        target = f"gui/{os.getuid()}/{PRODUCTION_LABEL}"
        try:
            loaded = subprocess.run(
                ["launchctl", "print", target], check=False,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            ).returncode == 0
        except OSError as exc:
            raise RuntimeError("Could not check whether the production helper is running.") from exc
        if loaded:
            raise RuntimeError(
                "The production tinyTouch helper is running. Stop it before using beta: "
                f"launchctl disable {target}, then launchctl bootout {target}. "
                "Do not run production commands while beta is using the device. "
                "When finished, stop the beta helper with "
                f"launchctl disable gui/{os.getuid()}/{self.label}, then "
                f"launchctl bootout gui/{os.getuid()}/{self.label}. Restore production with "
                f"launchctl enable {target}, then "
                f"launchctl bootstrap gui/{os.getuid()} "
                '"$HOME/Library/LaunchAgents/com.tinytouch.helper.plist". '
                "This does not remove either installation or its credentials."
            )


CHANNEL = Channel(bundled_version())

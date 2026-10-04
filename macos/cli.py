#!/usr/bin/env python3
"""Small, live-configuration CLI for protocol-6 tinyTouch firmware."""

from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import difflib
import getpass
import hashlib
import json
import os
import platform
import plistlib
import pty
import re
import select
import secrets
import shutil
import ssl
import subprocess
import sys
import threading
import textwrap
import tty
import time
import urllib.request
import warnings
from pathlib import Path
from urllib.parse import urlparse

import certifi

FROZEN = bool(getattr(sys, "frozen", False))
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ROOT = Path(sys.executable).resolve().parent if FROZEN else PROJECT_ROOT
BUNDLE_ROOT = Path(getattr(sys, "_MEIPASS", ROOT))
VERSION_FILE = BUNDLE_ROOT / "VERSION" if FROZEN else PROJECT_ROOT / "VERSION"
CLI_VERSION = VERSION_FILE.read_text(encoding="utf-8").strip() if VERSION_FILE.exists() else "development"
HELPER = PROJECT_ROOT / "macos" / "tinytouch_helper.py"
REQUIREMENTS = PROJECT_ROOT / "macos" / "requirements.txt"
VENV = ROOT / ".venv"
LAUNCH_AGENT = Path.home() / "Library" / "LaunchAgents" / "com.tinytouch.helper.plist"
SUPPORT_DIR = Path.home() / "Library" / "Application Support" / "tinyTouch"
LOG_DIR = Path.home() / "Library" / "Logs" / "tinyTouch"
PAIRING_SERVICE = "tinyTouch-pairing"
PASSWORD_SERVICE = "tinyTouch"
CURRENT_PROTOCOL = 6
OTA_CHUNK_SIZE = 3072
OTA_WRITE_WINDOW = 8
RELEASE_DOWNLOAD_URL = "https://github.com/ZimengXiong/tinyTouch/releases/download"
LATEST_RELEASE_URL = "https://github.com/ZimengXiong/tinyTouch/releases/latest/download"
FACTORY_FLASH_URL = "https://docs.tinytouch.dev/flash"
TLS = ssl.create_default_context(cafile=certifi.where())
VERBOSE = False
_sound_process = None

HELPER_MODULE_DIR = BUNDLE_ROOT if FROZEN else PROJECT_ROOT / "macos"
if str(HELPER_MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(HELPER_MODULE_DIR))
from tinytouch_runtime import (  # type: ignore  # noqa: E402
    ForegroundLease, LeaseBusyError, LeaseProtocolError, atomic_write_bytes,
    atomic_write_json,
)
from tinytouch_menu import select_menu, supports_arrows  # type: ignore  # noqa: E402

HELPER_SUSPEND = SUPPORT_DIR / "helper-suspend"
HELPER_SUSPEND_ACK = SUPPORT_DIR / "helper-suspend-ack"
_sudo_session_ready = False
_setup_password: bytearray | None = None
_helper_suppressed = False
_active_serial = None


class ToolError(RuntimeError):
    """An actionable error that can be shown directly to the user."""


class HelperCredentialAccessError(ToolError):
    """The replacement helper needs permission to read saved credentials."""


class HidSetupIncompleteError(ToolError):
    """HID mode needs a usable local password and registered pairing key."""


class SerialTimeout(ToolError):
    """The device did not return a terminal response."""


LED_COLORS = {
    "off": 0,
    "blue": 1,
    "green": 2,
    "cyan": 3,
    "red": 4,
    "purple": 5,
    "yellow": 6,
    "white": 7,
}
LED_EFFECTS = {"breathe": 1, "flash": 2, "steady": 3, "fade-in": 5, "fade-out": 6}
MODE_OPTIONS = (
    ("hid", "HID — types your password; works with most apps"),
    ("piv", "PIV — smart card; PIN login for supported Mac prompts"),
)
COMMAND_TITLES = {
    "menu": "tinyTouch",
    "setup": "Setup",
    "mode": "Mode",
    "piv-touch": "Touch-activated PIV",
    "led": "Lighting",
    "config": "Settings",
    "settings": "Settings",
    "enroll": "Enroll",
    "enroll-demo": "Fingerprint enrollment demo",
    "fingers": "Fingerprints",
    "delete": "Delete a fingerprint",
    "computers": "Registered computers",
    "factory-reset": "Factory reset",
    "update": "Update",
    "uninstall": "Uninstall service",
    "rom": "ROM bootloader",
    "bootloader": "ROM bootloader",
    "status": "Status",
    "logs": "Logs",
    "test": "Connection test",
    "keys": "PIV identity",
    "pair": "PIV pairing",
    "hid-smoke": "HID helper test",
    "ports": "USB serial devices",
    "help": "Command help",
}


class SettingSpec:
    def __init__(
        self,
        wire: str,
        label: str,
        description: str,
        default: str,
        minimum: int = 0,
        maximum: int = 0,
        *,
        choices: dict[str, int] | None = None,
        capability: str | None = None,
    ):
        self.wire, self.label, self.description, self.default = wire, label, description, default
        self.minimum, self.maximum = minimum, maximum
        self.choices, self.capability = choices, capability

    def allowed(self) -> str:
        return ", ".join(self.choices) if self.choices else f"{self.minimum}–{self.maximum}"

    def decode(self, value: str) -> str:
        if self.choices:
            if value in self.choices:
                return value
            return next((name for name, code in self.choices.items() if str(code) == value), value)
        return value


# One catalog drives validation, command help, current values and menu prompts.
SETTINGS = {
    "mode": SettingSpec("MODE", "Device mode", "Select HID password entry or PIV smart card authentication. Reconnect the device after changing the mode.", "piv", choices={"piv": 0, "hid": 1}),
    "led": SettingSpec("LED", "Sensor lighting", "Enable all sensor lighting, disable it, or show authentication results only.", "on", choices={"off": 0, "on": 1, "only-auth": 2}),
    "piv_delay_ms": SettingSpec("PIV_DELAY", "PIV PIN delay (ms)", "Delay before automatic PIN entry after the smart card is ready.", "50", 0, 5000),
    "typing_delay_ms": SettingSpec("TYPE_DELAY", "Typing delay (ms)", "Set the delay after each HID key press and release.", "1", 1, 100),
    "submit_enter": SettingSpec("SUBMIT_ENTER", "Submit Enter", "Press Enter after typing the password or the automatic PIV PIN.", "on", choices={"off": 0, "on": 1}),
    "touch_cooldown_ms": SettingSpec("COOLDOWN", "Touch cooldown (ms)", "Minimum interval between touch actions.", "800", 100, 5000),
    "led_idle_color": SettingSpec("LED_IDLE_COLOR", "Idle color", "Set the sensor ring color for idle operation and enrollment.", "blue", choices=LED_COLORS, capability="custom_config"),
    "led_success_color": SettingSpec("LED_SUCCESS_COLOR", "Success color", "Set the sensor ring color after a fingerprint match.", "green", choices=LED_COLORS, capability="custom_config"),
    "led_failure_color": SettingSpec("LED_FAILURE_COLOR", "Failure color", "Set the sensor ring color after a failed fingerprint match.", "red", choices=LED_COLORS, capability="custom_config"),
    "led_idle_end_color": SettingSpec("LED_IDLE_END_COLOR", "Breathing end color", "Set the end color for the breathing effect. Other effects use the idle color.", "blue", choices=LED_COLORS, capability="custom_config"),
    "led_idle_effect": SettingSpec("LED_IDLE_EFFECT", "Idle effect", "Set the animation while the idle sensor ring is enabled.", "steady", choices=LED_EFFECTS, capability="custom_config"),
    "led_idle_cycles": SettingSpec("LED_IDLE_CYCLES", "Animation repeats", "Set 0 for continuous animation or 1–255 for a limited number of repeats. Steady lighting ignores this setting.", "0", 0, 255, capability="custom_config"),
    "led_feedback_ms": SettingSpec("LED_FEEDBACK_MS", "Result feedback (ms)", "Set the duration of success and failure feedback. Authentication continues while feedback is displayed.", "350", 50, 2000, capability="custom_config"),
    "piv_auto_type": SettingSpec("PIV_AUTO_TYPE", "Automatic PIV PIN entry", "Type the PIV PIN after a fingerprint match. When off, a match still grants smart card presence.", "on", choices={"off": 0, "on": 1}, capability="custom_config"),
}

LED_PRESETS = {
    "default": ("blue", "blue", "green", "red", "steady"),
    "ocean": ("blue", "cyan", "cyan", "red", "breathe"),
    "neon": ("purple", "cyan", "green", "red", "breathe"),
    "sunset": ("red", "yellow", "yellow", "purple", "breathe"),
}


def setting_name(value: str) -> str:
    name = value.lower().replace("-", "_")
    aliases = {"type_delay": "typing_delay_ms", "cooldown": "touch_cooldown_ms", "led_mode": "led"}
    name = aliases.get(name, name)
    if name not in SETTINGS:
        match = difflib.get_close_matches(name, SETTINGS, n=1)
        suggestion = f" Did you mean '{match[0]}'?" if match else ""
        raise ToolError(f"Unknown setting '{value}'.{suggestion} Run 'tinytouch config list' to list valid settings.")
    return name


def setting_value(name: str, value: str) -> str:
    spec = SETTINGS[name]
    text = value.strip().lower()
    if name == "piv_delay_ms" and (not text.isascii() or not text.isdecimal() or not 0 <= int(text) <= 5000):
        raise ToolError("piv_delay_ms must be an integer from 0 to 5000 milliseconds.")
    if spec.choices:
        aliases = {"true": "on", "false": "off", "yes": "on", "no": "off", "magenta": "purple", "breathing": "breathe"}
        text = aliases.get(text, text)
        if text in spec.choices:
            return str(spec.choices[text])
        if text in {str(code) for code in spec.choices.values()}:
            return text
    else:
        try:
            number = int(text)
            if spec.minimum <= number <= spec.maximum:
                return str(number)
        except ValueError:
            pass
    raise ToolError(f"Invalid value '{value}' for {name}. Use {spec.allowed()}. Example: tinytouch config {name} {spec.default}")


def say(message: str = "") -> None:
    print(message, flush=True)


def terminal_style(text: str, code: str) -> str:
    """Apply restrained terminal styling when the terminal supports it."""
    if (
        not sys.stdout.isatty()
        or os.environ.get("NO_COLOR") is not None
        or os.environ.get("TERM") == "dumb"
    ):
        return text
    return f"\033[{code}m{text}\033[0m"


def panel_width() -> int:
    return max(24, min(64, shutil.get_terminal_size((80, 24)).columns))


def show_section(title: str) -> None:
    """Distinguish command results from menu choices without borders."""
    say(terminal_style(title, "1;32"))


def show_fields(rows: list[tuple[str, str]]) -> None:
    """Align readable labels and wrap values within the terminal width."""
    label_width = max((len(label) for label, _value in rows), default=0)
    for label, value in rows:
        prefix = f"  {label + ':':<{label_width + 2}}"
        say(
            textwrap.fill(
                value,
                width=max(panel_width(), len(prefix) + 12),
                initial_indent=prefix,
                subsequent_indent=" " * len(prefix),
            )
        )


ENROLLMENT_VIEWS = (
    ("left", "left edge", "36"),
    ("right", "right edge", "35"),
    ("top", "top", "33"),
    ("center", "center", "32"),
)


def fingerprint_oval(active: str | None, tap_index: int = 0) -> str:
    """Draw one filled Braille oval with one region highlighted."""
    rows = (
        "        ttttttttt",
        "      ttttttttttttt",
        "    ttttttttttttttttt",
        "  ttttttttttttttttttttt",
        " llllllllcccccccrrrrrrrr",
        "lllllllllcccccccrrrrrrrrr",
        "lllllllllcccccccrrrrrrrrr",
        "lllllllllcccccccrrrrrrrrr",
        "lllllllllcccccccrrrrrrrrr",
        "lllllllllcccccccrrrrrrrrr",
        "lllllllllcccccccrrrrrrrrr",
        " llllllllcccccccrrrrrrrr",
        "  lllllllcccccccrrrrrrr",
        "    lllllcccccccrrrrr",
        "      lllcccccccrrr",
        "        lcccccccr",
    )
    zone_for_marker = {"l": "left", "r": "right", "t": "top", "c": "center"}
    color_enabled = (
        sys.stdout.isatty()
        and os.environ.get("NO_COLOR") is None
        and os.environ.get("TERM") != "dumb"
    )
    highlight_colors = ("36", "32")
    highlight_glyphs = ("▓", "▒")
    rendered = []
    for row in rows:
        line = []
        for character in row:
            zone = zone_for_marker.get(character)
            if zone is None:
                line.append(character)
            elif zone == active:
                if color_enabled:
                    line.append(terminal_style("⣿", f"1;{highlight_colors[tap_index]}"))
                else:
                    line.append(highlight_glyphs[tap_index])
            else:
                line.append("⣿")
        rendered.append("".join(line))
    return "\n".join(rendered)


def introduce_enrollment() -> None:
    """Explain fingerprint enrollment before opening its full-screen view."""
    say("")
    say("Enroll different views of the same fingerprint.")
    say("Follow the instructions shown on the next screen.")
    ask("Press Enter to continue.")


def show_enrollment_view(
    view_index: int, tap_index: int, active: bool = True, demo: bool = False,
) -> None:
    """Render one frame of fingerprint enrollment."""
    zone, label, _color = ENROLLMENT_VIEWS[view_index]
    sys.stdout.write("\033[2J\033[H")
    say(terminal_style("Fingerprint enrollment", "1"))
    say(
        f'View {view_index + 1} of {len(ENROLLMENT_VIEWS)} · Tap {tap_index + 1} of 2'
    )
    say("")
    say(fingerprint_oval(zone if active else None, tap_index))
    say("")
    if not active:
        say("Lift your finger away from the sensor.")
    elif tap_index == 0:
        say(
            f"Tap the sensor with the {terminal_style(label, '1')} of your finger. Then lift your finger."
        )
    else:
        say(
            f"Tap the sensor again with the {terminal_style(label, '1')} of the same finger."
        )
    if demo:
        say(terminal_style("Tab: simulate tap    q: exit", "2"))


def show_enrollment_event(view_index: int, event: str) -> None:
    """Map firmware enrollment events to their matching visual state."""
    states = {
        "EVENT TOUCH": (0, True),
        "EVENT LIFT": (0, False),
        "EVENT TOUCH_AGAIN": (1, True),
    }
    state = states.get(event)
    if state is not None:
        show_enrollment_view(view_index, *state)


def command_enroll_demo(_args: argparse.Namespace) -> None:
    """Preview fingerprint enrollment without opening a device."""
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ToolError("The enrollment demo requires an interactive terminal.")
    introduce_enrollment()
    view_index = 0
    tap_index = 0
    previous = tty.tcgetattr(sys.stdin.fileno()) if hasattr(tty, "tcgetattr") else None
    try:
        tty.setcbreak(sys.stdin.fileno())
        while True:
            show_enrollment_view(view_index, tap_index, demo=True)
            key = os.read(sys.stdin.fileno(), 1)
            if key in {b"q", b"Q", b"\x03", b"\x1b", b"\r", b"\n"}:
                break
            if key == b"\t":
                if tap_index == 0:
                    show_enrollment_view(view_index, tap_index, active=False, demo=True)
                    time.sleep(0.35)
                    tap_index = 1
                else:
                    tap_index = 0
                    view_index = (view_index + 1) % len(ENROLLMENT_VIEWS)
    finally:
        if previous is not None:
            tty.tcsetattr(sys.stdin.fileno(), tty.TCSADRAIN, previous)
        sys.stdout.write("\033[2J\033[H")
        sys.stdout.flush()


def show_startup_mark(command: str) -> None:
    """Show the original Alpaca mark and one command section divider."""
    if not sys.stdout.isatty():
        return
    mark = (
        "⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣰⣷⣼⣇⡀⠀",
        "⠀⠀⠀⠀⢀⣀⣀⡀⠀⠀⣿⣿⡟⠟⢡⡄",
        "⠀⠀⠀⣤⣿⣯⣽⢿⣤⠀⣿⣿⣿⡟⠋⠀",
        "⠀⣰⣟⡛⢛⡛⢛⣛⢛⣻⣿⣿⣿⡇⠀⠀",
        "⠸⣿⣿⣷⣿⣿⣾⣿⣾⣿⣿⣿⣿⡇⠀⠀",
        "⠀⢸⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡿⠃⠀⠀",
        "⠀⢸⣿⣿⣿⡿⠉⠉⣿⣿⡏⣿⠁⠀⠀⠀",
        "⠀⠀⢸⡇⣿⡇⠀⠀⢸⣿⢸⡇⠀⠀⠀⠀",
    )
    for line in mark:
        say(terminal_style(line, "36"))
    say(f"          tinyTouch {terminal_style(CLI_VERSION, '2')}")
    if command != "menu":
        say("")
        title = COMMAND_TITLES.get(command, command.replace("-", " ").capitalize())
        show_section(title)


def verbose(message: str) -> None:
    if VERBOSE:
        say(f"[verbose] {message}")


def run(command: list[str], **kwargs):
    verbose("run: " + " ".join(command))
    try:
        return subprocess.run(command, check=True, **kwargs)
    except FileNotFoundError as exc:
        raise ToolError(f"Required command is unavailable: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        detail = ""
        for output in (exc.stderr, exc.stdout):
            if isinstance(output, str) and output.strip():
                detail = output.strip()
                break
        raise ToolError(detail or f"Command failed: {command[0]}") from exc


def authorize_macos() -> None:
    """Authorize sudo through its native terminal prompt."""
    global _sudo_session_ready
    if _sudo_session_ready:
        return
    cached = subprocess.run(
        ["sudo", "-n", "-v"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if cached.returncode:
        say("Authorize macOS in this terminal.")
        validated = subprocess.run(["sudo", "-v"], check=False)
        if validated.returncode:
            raise ToolError("macOS authorization failed.")
    _sudo_session_ready = True


def prepare_hid_password() -> None:
    """Capture the HID password and unlock Keychain without requiring sudo."""
    global _setup_password
    entered = bytearray(getpass.getpass("Mac password: ").encode("utf-8"))
    if not entered:
        raise ToolError("HID mode requires the password for this Mac.")
    try:
        try:
            _keychain().unlock_default_keychain(entered)
            _keychain().disable_user_interaction()
        except _keychain().KeychainError as exc:
            raise ToolError(
                "The Mac password did not unlock the login Keychain."
            ) from exc
        _setup_password = bytearray(entered)
    finally:
        entered[:] = b"\x00" * len(entered)


def run_sudo(command: list[str]) -> None:
    """Run a privileged command after terminal-only authorization."""
    authorize_macos()
    run(["sudo", "-n", *command])


def require_macos() -> None:
    if sys.platform != "darwin" and not os.environ.get("TINYTOUCH_ALLOW_NON_MACOS"):
        raise ToolError("tinyTouch setup is supported only on macOS.")


def _keychain():
    try:
        import tinytouch_keychain  # type: ignore
    except ImportError as exc:
        raise ToolError("The macOS Keychain helper is not available.") from exc
    return tinytouch_keychain


def keychain_get(service: str, account: str) -> str | None:
    return _keychain().get_password(service, account)


def keychain_set(service: str, account: str, value: str) -> None:
    keychain = _keychain()
    try:
        keychain.set_password(service, account, value)
    except keychain.KeychainError as exc:
        raise ToolError(
            "Could not save the HID credential. Unlock the login Keychain or "
            "run 'tinytouch repair', then retry."
        ) from exc


def keychain_delete(service: str, account: str) -> None:
    _keychain().delete_password(service, account)


def keychain_exists(service: str, account: str) -> bool:
    return keychain_get(service, account) is not None


def notify(title: str, message: str) -> None:
    """Show a native macOS notification; failures do not hide the text prompt."""
    if sys.platform != "darwin":
        return
    script = f"display notification {json.dumps(message)} with title {json.dumps(title)}"
    subprocess.run(
        ["osascript", "-e", script], check=False,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError as exc:
        raise ToolError("This action requires input from an interactive terminal.") from exc


def choose_mode(value: str | None) -> str:
    if value in {"hid", "piv"}:
        return value
    say("Device mode:")
    for _mode, description in MODE_OPTIONS:
        say(f"  {description}")
    answer = ask("Mode [h/p]: ").lower()
    if answer in {"h", "hid"}:
        return "hid"
    if answer in {"p", "piv"}:
        return "piv"
    raise ToolError("Select HID or PIV.")


def detect_ports() -> list[str]:
    return sorted(str(path) for path in Path("/dev").glob("cu.*") if "usb" in path.name.lower() or "TT-" in path.name)


def choose_port(explicit: str | None) -> str:
    if explicit:
        return explicit
    ports = detect_ports()
    if len(ports) == 1:
        return ports[0]
    if not ports:
        raise ToolError("No tinyTouch USB serial device is connected. Connect tinyTouch with a USB cable that supports data. Run 'tinytouch ports' to list devices. Use --port PATH to select a device.")
    say("More than one USB serial device is connected:")
    for index, port in enumerate(ports, 1):
        say(f"  {index}. {port}")
    answer = ask("Select a device number: ")
    try:
        index = int(answer)
        if not 1 <= index <= len(ports):
            raise ValueError()
        return ports[index - 1]
    except (ValueError, IndexError) as exc:
        raise ToolError("Select one of the connected USB serial devices.") from exc


def current_port(port: str) -> str:
    """Use the current macOS CDC node after the device re-enumerates."""
    return port if Path(port).exists() else choose_port(None)


def wait_for_reconnect(port: str, timeout: float = 120.0) -> str:
    """Wait for a physical disconnect followed by a USB serial reconnect."""
    deadline = time.monotonic() + timeout
    disconnected = False
    while time.monotonic() < deadline:
        ports = detect_ports()
        if not disconnected:
            if port not in ports:
                disconnected = True
                say("The device disconnected. Waiting for it to reconnect.")
        elif port in ports:
            return port
        elif len(ports) == 1:
            return ports[0]
        time.sleep(0.1)
    raise ToolError("Timed out waiting for tinyTouch to disconnect and reconnect.")


def is_terminal(command: str, line: str) -> bool:
    words = command.split()
    verb = words[0]
    if line == "ERR COMMAND" or line == "ERR LINE" or line.startswith("ERR LOCKED"):
        return True
    if verb == "PING":
        return line.startswith("PONG") or line.startswith("ERR PING")
    fields = line.split()
    if len(fields) < 2 or fields[0] not in {"OK", "ERR"}:
        return False
    if fields[1] == verb:
        return True
    # Protocol 6 groups live writes under SET, HOST, FINGER, RESET, OTA, and LED.
    grouped = {"SET", "HOST", "FINGER", "RESET", "OTA", "LED"}
    return len(words) > 1 and verb in grouped and fields[1] == words[1]


def human_error(line: str, *, touch_prompted: bool = False) -> str:
    """Turn compact device failures into the next useful user action."""
    if line in {"ERR AUTH", "ERR AUTH no_match", "ERR AUTH sensor=offline"}:
        if line.endswith("sensor=offline"):
            return "Fingerprint sensor unavailable. Please reconnect tinyTouch."
        if line.endswith("no_match"):
            return (
                "No enrolled fingerprint matched. Lift your finger and try an enrolled finger. "
                f"If none work, use Recovery firmware at {FACTORY_FLASH_URL}?firmware=recovery. "
                "Recovery erases fingerprints, device keys, registered computers, and settings."
            )
        if touch_prompted:
            return "Fingerprint authentication timed out. Please try again."
        return "Fingerprint authentication could not start. Please try again."
    if line == "ERR FINGER update_cli":
        return "Update the tinyTouch CLI. Enrollment now uses complete fingerprint blocks instead of individual templates."
    if line == "ERR FINGER inventory_unavailable":
        return "Could not check the occupied fingerprint blocks. The existing enrollment was not changed."
    if line == "ERR SET LED reconnect_required":
        return "LED preference saved. Unplug tinyTouch and reconnect it to finish applying the lighting setting."
    if line.startswith("ERR LOCKED"):
        return "Fingerprint authentication expired. Please try again."
    if line.startswith("ERR SET"):
        return "Could not apply this setting. Please try again."
    if line == "ERR COMMAND":
        return "The firmware does not support this command. Run 'tinytouch update'. Then reconnect the device."
    if line.startswith("ERR LED"):
        return "Could not apply sensor lighting. Please reconnect tinyTouch."
    if line.startswith("ERR "):
        return "tinyTouch rejected the request: " + line[4:]
    return line


def unload_helper() -> bool:
    if _helper_suppressed:
        return False
    should_restart = LAUNCH_AGENT.exists()
    if not helper_loaded():
        return should_restart
    stop_helper()
    HELPER_SUSPEND.unlink(missing_ok=True)
    HELPER_SUSPEND_ACK.unlink(missing_ok=True)
    return should_restart


def helper_loaded() -> bool:
    try:
        return subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/com.tinytouch.helper"],
            check=False, timeout=5,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        ).returncode == 0
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ToolError(
            "Could not check the HID background service. Please try again."
        ) from exc


def load_helper() -> None:
    if _helper_suppressed:
        return
    if helper_loaded():
        return
    try:
        run(
            ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(LAUNCH_AGENT)],
            timeout=5,
        )
    except (ToolError, OSError, subprocess.TimeoutExpired) as exc:
        # Another CLI can finish the same bootstrap between print and bootstrap.
        if helper_loaded():
            return
        raise ToolError(
            "Could not start the HID background service. Run 'tinytouch repair'."
        ) from exc
    if not helper_loaded():
        raise ToolError(
            "The HID background service did not load. Run 'tinytouch repair'."
        )


def stop_helper() -> None:
    """Verify that launchd released the service before changing its files."""
    service = f"gui/{os.getuid()}/com.tinytouch.helper"
    try:
        subprocess.run(
            ["launchctl", "bootout", service],
            check=False,
            timeout=5,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ToolError(
            "Could not stop the background service. Please try again."
        ) from exc
    if helper_loaded():
        raise ToolError("Could not stop the background service. Please try again.")


def remove_helper() -> None:
    """Stop the helper before deleting its LaunchAgent and foreground state."""
    global _helper_suppressed
    stop_helper()
    _helper_suppressed = True
    try:
        LAUNCH_AGENT.unlink(missing_ok=True)
        HELPER_SUSPEND.unlink(missing_ok=True)
        HELPER_SUSPEND_ACK.unlink(missing_ok=True)
    except OSError as exc:
        raise ToolError(
            "Could not remove the background service. Please try again."
        ) from exc


def command_uninstall(args: argparse.Namespace) -> None:
    """Remove the background service without changing device or credential data."""
    require_macos()
    remove_helper()
    say("Background service uninstalled.")


def ensure_helper_environment() -> Path:
    if FROZEN:
        return Path(sys.executable).resolve()
    python = VENV / "bin" / "python"
    if not python.exists():
        run([sys.executable, "-m", "venv", str(VENV)])
    probe = subprocess.run([str(python), "-c", "import serial"], check=False)
    if probe.returncode:
        run([str(python), "-m", "pip", "install", "-q", "-r", str(REQUIREMENTS)])
    return python


def install_helper(*, check_saved: bool = False) -> None:
    global _helper_suppressed
    python = ensure_helper_environment()
    arguments = (
        [str(python), str(HELPER)]
        if not FROZEN
        else [str(python), "_helper"]
    )
    # Keychain access depends on the executable's identity. Check the exact
    # replacement process before stopping a helper that can still read secrets.
    credential_check = [*arguments, "--check-credentials"]
    if check_saved:
        credential_check.append("--include-saved")
    try:
        candidate = subprocess.run(
            credential_check,
            check=False,
            timeout=15,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ToolError(
            "Could not check the replacement HID helper. The existing service is unchanged."
        ) from exc
    if candidate.returncode == 1:
        raise HelperCredentialAccessError(
            "The replacement HID helper cannot read the saved Keychain credentials "
            "in the background. Run 'tinytouch repair' to authorize the current CLI "
            "and reinstall its helper. The existing service is unchanged."
        )
    if candidate.returncode != 0:
        raise ToolError(
            "The replacement HID helper failed its credential check. "
            "The existing service is unchanged. Please try again."
        )
    SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    LAUNCH_AGENT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "Label": "com.tinytouch.helper",
        "ProgramArguments": arguments,
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Interactive",
        "ThrottleInterval": 1,
        "StandardOutPath": str(LOG_DIR / "helper.log"),
        "StandardErrorPath": str(LOG_DIR / "helper.err"),
        "EnvironmentVariables": {"TINYTOUCH_SERVICE_SCHEMA": "3"},
    }
    # Use the foreground lock so a replacement cannot interrupt another CLI's
    # USB session or remove its lease while changing the LaunchAgent.
    with ForegroundLease(HELPER_SUSPEND, HELPER_SUSPEND_ACK) as lease:
        try:
            lease.acquire(wait_for_ack=False)
        except (LeaseBusyError, OSError) as exc:
            raise ToolError(f"Could not replace the HID background service: {exc}") from exc
        previous = LAUNCH_AGENT.read_bytes() if LAUNCH_AGENT.exists() else None
        was_loaded = helper_loaded()
        was_suppressed = _helper_suppressed
        _helper_suppressed = False
        try:
            unload_helper()
            atomic_write_bytes(
                LAUNCH_AGENT, plistlib.dumps(payload, sort_keys=False), mode=0o644
            )
            load_helper()
            if not helper_loaded():
                raise ToolError("The HID helper did not load.")
        except BaseException as exc:
            try:
                unload_helper()
                if previous is None:
                    LAUNCH_AGENT.unlink(missing_ok=True)
                else:
                    atomic_write_bytes(LAUNCH_AGENT, previous, mode=0o644)
                    if was_loaded:
                        load_helper()
                        if not helper_loaded():
                            raise ToolError("The previous HID helper did not reload.")
            except Exception as rollback_error:
                raise ToolError(
                    "The replacement HID helper failed, and the previous service could not be restored."
                ) from rollback_error
            finally:
                _helper_suppressed = was_suppressed
            if not isinstance(exc, Exception):
                raise
            raise ToolError(
                "The replacement HID helper failed. The previous service was restored."
            ) from exc


def command_repair(args: argparse.Namespace) -> None:
    """Repair credential access and reinstall the helper from this CLI."""
    require_macos()
    if not FROZEN:
        raise ToolError("Run repair from the certificate-signed standalone CLI.")
    if args.port:
        device_ids = {device_account(choose_port(args.port))}
    else:
        from tinytouch_helper import known_device_ids

        device_ids = known_device_ids()
    keychain = _keychain()
    from tinytouch_helper import password_accounts

    accounts = []
    for account in sorted(device_ids):
        pairing = [(PAIRING_SERVICE, account), (PASSWORD_SERVICE, account)]
        if not all(keychain.has_password(service, name) for service, name in pairing):
            continue
        accounts.extend(pairing)
        accounts.extend(
            (PASSWORD_SERVICE, name)
            for name in password_accounts(account)
            if keychain.has_password(PASSWORD_SERVICE, name)
        )
    if not accounts:
        raise ToolError(
            "This Mac has no complete HID pairing. Run 'tinytouch setup --mode hid'."
        )
    say(
        "Checking saved HID credentials. Approve macOS Keychain authorization if prompted."
    )
    owner_password = None

    def password_provider():
        nonlocal owner_password
        if owner_password is None:
            owner_password = keychain.prompt_keychain_password()
        return bytearray(owner_password)

    try:
        for service, name in accounts:
            if not keychain.can_read_password(service, name):
                keychain.authorize_executable(
                    service, name, sys.executable, password_provider=password_provider
                )
                if not keychain.can_read_password(service, name):
                    raise ToolError(
                        "The current CLI still cannot read the credential. The helper was not replaced."
                    )
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        raise ToolError(f"Keychain repair did not finish for {name}: {exc}") from exc
    finally:
        if owner_password is not None:
            owner_password[:] = b"\x00" * len(owner_password)
    install_helper()
    say(
        "Current HID helper reinstalled. Saved passwords and pairing keys are unchanged."
    )


def command_upgrade_helper(args: argparse.Namespace) -> None:
    """Repair access before replacing an existing service during an upgrade."""
    if not LAUNCH_AGENT.exists():
        return
    say("Updating the HID background service...")
    try:
        install_helper(check_saved=True)
    except HelperCredentialAccessError:
        say("The new CLI needs Keychain authorization. Starting repair...")
        # Repair saved devices too, including upgrades while USB is disconnected.
        command_repair(argparse.Namespace(port=None))


def exchange_serial(
    device,
    command: str,
    *,
    timeout: float,
    touch_prompt: str | None = None,
    lift_prompt: str | None = "Lift your finger away from the sensor.",
    touch_again_prompt: str | None = None,
    wait_message: str | None = None,
    event_handler=None,
) -> list[str]:
    lines: list[str] = []
    touch_prompted = False
    device.write((command + "\n").encode("ascii"))
    device.flush()
    deadline = time.monotonic() + timeout
    frames = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
    animated_wait = bool(wait_message and sys.stdout.isatty())
    if wait_message and not animated_wait:
        say(wait_message)
    frame = 0
    try:
        while time.monotonic() < deadline:
            if animated_wait:
                print(
                    f"\r{frames[frame % len(frames)]} {wait_message}",
                    end="",
                    flush=True,
                )
                frame += 1
            raw = device.readline()
            if not raw:
                continue
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            lines.append(line)
            verbose("device: " + line)
            if line in {"EVENT TOUCH", "EVENT TOUCH_AGAIN"}:
                chime("Tink")
            elif line == "EVENT LIFT":
                chime("Pop")
            if line.startswith("EVENT ") and event_handler is not None:
                event_handler(line)
                if line == "EVENT TOUCH":
                    touch_prompted = True
            elif line == "EVENT TOUCH" and not touch_prompted:
                say(touch_prompt or "Touch the fingerprint sensor.")
                touch_prompted = True
            elif line == "EVENT LIFT" and lift_prompt:
                say(lift_prompt)
            elif line == "EVENT TOUCH_AGAIN":
                if touch_prompt:
                    say("")
                say(touch_again_prompt or "Touch the sensor again with the same finger.")
            if is_terminal(command, line):
                break
    finally:
        if animated_wait:
            print("\r\033[2K", end="", flush=True)
    if not lines or not is_terminal(command, lines[-1]):
        raise SerialTimeout(f"tinyTouch did not acknowledge {command.split()[0]}.")
    if lines[-1].startswith("ERR "):
        raise ToolError(human_error(lines[-1], touch_prompted=touch_prompted))
    return lines


def active_session_port(port: str) -> str:
    """Require nested commands to use the device owned by the outer session."""
    active_port = _active_serial.port
    if port != active_port and current_port(port) != active_port:
        raise ToolError("A foreground session is already using another device.")
    return active_port


def helper_supports_foreground_lease() -> bool:
    """Check whether the installed helper can drain USB workers for a lease."""
    try:
        payload = plistlib.loads(LAUNCH_AGENT.read_bytes())
    except (OSError, ValueError, plistlib.InvalidFileException):
        return False
    if not isinstance(payload, dict):
        return False
    environment = payload.get("EnvironmentVariables", {})
    return isinstance(environment, dict) and environment.get(
        "TINYTOUCH_SERVICE_SCHEMA"
    ) == "3"


@contextmanager
def foreground_helper():
    """Pause a current helper without removing its launchd registration."""
    use_lease = not _helper_suppressed and helper_supports_foreground_lease()
    if use_lease:
        load_helper()
    was_loaded = False
    # The lock also serializes commands when no helper has been installed.
    with ForegroundLease(HELPER_SUSPEND, HELPER_SUSPEND_ACK) as lease:
        try:
            lease.acquire(wait_for_ack=use_lease)
        except (LeaseBusyError, LeaseProtocolError) as exc:
            raise ToolError(str(exc)) from exc
        except OSError as exc:
            raise ToolError("Could not reserve the tinyTouch USB connection.") from exc
        try:
            if not use_lease:
                was_loaded = unload_helper()
            yield
        finally:
            if was_loaded:
                load_helper()


@contextmanager
def foreground_session(port: str):
    """Open one verified CDC session for a foreground device operation."""
    global _active_serial
    if _active_serial is not None:
        yield active_session_port(port)
        return
    try:
        import serial  # type: ignore
    except ImportError as exc:
        raise ToolError("The pyserial package is required. Run setup again.") from exc
    device = None
    with foreground_helper():
        try:
            deadline = time.monotonic() + 6.0
            last_error: Exception | None = None
            while time.monotonic() < deadline:
                try:
                    port = current_port(port)
                    device = serial.Serial(port, 115200, timeout=0.25, write_timeout=2)
                    time.sleep(0.2)
                    device.reset_input_buffer()
                    exchange_serial(device, "PING", timeout=3)
                    break
                except Exception as exc:
                    last_error = exc
                    if device is not None:
                        device.close()
                        device = None
                    time.sleep(0.25)
            if device is None:
                if "Device not configured" in str(last_error):
                    raise ToolError(
                        "tinyTouch is reconnecting. Please try again in a moment."
                    ) from last_error
                raise ToolError(
                    f"Could not communicate with tinyTouch on {port}. Error: {last_error}"
                ) from last_error
            _active_serial = device
            yield port
        finally:
            _active_serial = None
            if device is not None:
                device.close()


def serial_command(
    port: str, command: str, *, timeout: float = 20.0,
    touch_prompt: str | None = None,
    lift_prompt: str | None = "Lift your finger away from the sensor.",
    touch_again_prompt: str | None = None,
    wait_message: str | None = None,
    event_handler=None,
) -> list[str]:
    if _active_serial is not None:
        active_session_port(port)
        try:
            return exchange_serial(
                _active_serial, command, timeout=timeout, touch_prompt=touch_prompt,
                lift_prompt=lift_prompt, touch_again_prompt=touch_again_prompt,
                wait_message=wait_message, event_handler=event_handler,
            )
        except Exception as exc:
            if "Device not configured" in str(exc):
                raise ToolError(
                    "The tinyTouch USB serial port reconnected during this command. No incomplete configuration was saved. Wait two seconds and run the command again."
                ) from exc
            raise
    try:
        with foreground_session(port):
            return exchange_serial(
                _active_serial, command, timeout=timeout, touch_prompt=touch_prompt,
                lift_prompt=lift_prompt, touch_again_prompt=touch_again_prompt,
                wait_message=wait_message, event_handler=event_handler,
            )
    except Exception as exc:
        if "Device not configured" in str(exc):
            raise ToolError(
                "The tinyTouch USB serial port became unavailable when this command started. Please try again in a moment."
            ) from exc
        raise


def fields_from(lines: list[str], prefix: str) -> dict[str, str]:
    line = next((item for item in reversed(lines) if item.startswith(prefix)), "")
    if not line:
        raise ToolError(f"tinyTouch returned no {prefix.split()[1].lower()} status.")
    return dict(re.findall(r"([A-Za-z_]+)=([^ ]+)", line))


def status(port: str) -> dict[str, str]:
    deadline = time.monotonic() + 6.0
    while True:
        try:
            return fields_from(serial_command(port, "STATUS", timeout=4), "OK STATUS")
        except ToolError as exc:
            if "USB serial port became unavailable" not in str(exc) or time.monotonic() >= deadline:
                raise
            time.sleep(0.25)


def protocol6(device: dict[str, str]) -> None:
    if not device.get("firmware"):
        raise ToolError(f'The device did not report a firmware version. Use the flasher at {FACTORY_FLASH_URL}.')
    try:
        protocol = int(device.get("protocol", "0"))
    except ValueError as exc:
        raise ToolError("The device reported an invalid protocol version.") from exc
    if protocol != CURRENT_PROTOCOL:
        raise ToolError("This CLI requires protocol 6. Use the ROM flasher to update the device.")


def sensor_ready(device: dict[str, str]) -> None:
    if device.get("sensor") not in {"ready", "ok"}:
        raise ToolError("Fingerprint sensor unavailable. Please reconnect tinyTouch.")


def unlock(
    port: str,
    *,
    explain_pin: bool = False,
    reason: str = "unlock configuration",
) -> None:
    verbose(f"Authorizing: {reason}.")
    deadline = time.monotonic() + 6.0
    while True:
        try:
            serial_command(
                port,
                "AUTH",
                timeout=15,
                touch_prompt="Touch the device with a registered finger to unlock configuration.",
            )
            if explain_pin:
                explain_piv_pin()
            return
        except ToolError as exc:
            if (
                "The USB serial port became unavailable when this command started."
                not in str(exc)
            ):
                raise
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.25)


def fresh_status(port: str, expected: dict[str, str]) -> dict[str, str]:
    # macOS can briefly release the CDC device while it probes the smart-card
    # interface after PIV is enabled. The firmware keeps running and retains
    # the mode change, so wait for the same USB device instead of reporting a
    # completed write as a failed setup.
    deadline = time.monotonic() + 6.0
    last_error: ToolError | None = None
    while True:
        try:
            current = status(port)
            break
        except ToolError as exc:
            last_error = exc
            if time.monotonic() >= deadline:
                raise last_error
            time.sleep(0.25)
    for key, value in expected.items():
        if current.get(key) != value:
            raise ToolError(f'Verification failed. {key} is {current.get(key)!r}. Expected {value!r}.')
    return current


def device_account(port: str) -> str:
    override = os.environ.get("TINYTOUCH_DEVICE_ACCOUNT")
    if override:
        return override
    try:
        from tinytouch_ports import comports
    except ImportError as exc:
        raise ToolError(
            "The pyserial package is required to identify this tinyTouch device."
        ) from exc
    # Reconnecting can change the port opened by the foreground session.
    active_port = getattr(_active_serial, "port", None)
    if isinstance(active_port, str):
        port = active_port
    deadline = time.monotonic() + 6.0
    while True:
        try:
            candidates = comports()
        except OSError:
            candidates = []
        for candidate in candidates:
            if candidate.device != port:
                continue
            serial_number = re.sub(
                r"[^A-Za-z0-9_.-]", "", candidate.serial_number or ""
            ).upper()
            if serial_number:
                return serial_number
        # The CDC port can appear before its USB serial metadata is available.
        if time.monotonic() >= deadline:
            raise ToolError("tinyTouch did not report a stable USB serial identity.")
        time.sleep(0.1)


def host_id(key: bytes) -> str:
    return hashlib.sha256(key).hexdigest()[:16]


def hid_pairing_key(value: str | None) -> bytes:
    """Validate a saved pairing key before using its host identifier."""
    if value is None:
        raise HidSetupIncompleteError("This Mac has no saved HID pairing key. Run 'tinytouch setup --mode hid'.")
    try:
        key = bytes.fromhex(value.strip())
    except ValueError as exc:
        raise HidSetupIncompleteError("The saved HID pairing key is invalid. Run 'tinytouch setup --mode hid'.") from exc
    if len(key) != 32:
        raise HidSetupIncompleteError("The saved HID pairing key is invalid. Run 'tinytouch setup --mode hid'.")
    return key


def verify_hid_host(port: str, device: dict[str, str], account: str | None = None) -> None:
    """Check that this Mac can use one of the device's registered hosts."""
    account = account or device_account(port)
    key = hid_pairing_key(keychain_get(PAIRING_SERVICE, account))
    password = keychain_get(PASSWORD_SERVICE, account)
    if not password or len(password.encode("utf-8")) > 160:
        raise HidSetupIncompleteError("This Mac has no usable HID password. Run 'tinytouch setup --mode hid'.")
    registered, _capacity = host_list(port)
    try:
        count = int(device["hosts"])
    except (KeyError, ValueError) as exc:
        raise ToolError("HID setup is incomplete. The device did not report a valid computer count.") from exc
    if count < 1 or count != len(registered) or host_id(key) not in registered:
        raise HidSetupIncompleteError("HID setup is incomplete. This Mac is not registered. Run 'tinytouch setup --mode hid'.")


def prompt_hid_password() -> str:
    """Confirm a password without including it in command arguments."""
    say("Nothing appears as you type. Enter the password twice to catch typing errors.")
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            first = getpass.getpass("Password: ")
            second = getpass.getpass("Password again: ")
        except (getpass.GetPassWarning, EOFError) as exc:
            raise ToolError("Run this command in an interactive terminal.") from exc
    if not first or first != second:
        raise ToolError("Enter matching passwords. Neither password can be empty.")
    if len(first.encode()) > 160:
        raise ToolError("Use a password of 160 UTF-8 bytes or fewer.")
    return first


def password_for(account: str) -> str:
    global _setup_password
    if _setup_password is not None:
        try:
            value = _setup_password.decode("utf-8")
            if len(_setup_password) > 160:
                raise ToolError("Use a password of 160 UTF-8 bytes or fewer.")
            keychain_set(PASSWORD_SERVICE, account, value)
            return value
        finally:
            _setup_password[:] = b"\x00" * len(_setup_password)
            _setup_password = None
    first = prompt_hid_password()
    keychain_set(PASSWORD_SERVICE, account, first)
    return first


@contextmanager
def hid_settings_change():
    """Restart the existing helper so it discards cached host credentials."""
    was_loaded = unload_helper()
    completed = False
    try:
        yield
        completed = True
    finally:
        if was_loaded:
            try:
                load_helper()
            except (ToolError, OSError, subprocess.SubprocessError) as exc:
                outcome = "was saved" if completed else "did not finish"
                raise ToolError(
                    f"The HID change {outcome}, but the background service "
                    "could not restart. Run 'tinytouch repair'."
                ) from exc


def command_password(args: argparse.Namespace) -> None:
    """Change a host password without repeating enrollment or HID pairing."""
    require_macos()
    from tinytouch_helper import (
        current_keyboard_output_map, finger_password_account, load_settings,
        translate_password,
    )

    account = device_account(choose_port(args.port))
    keychain = _keychain()
    try:
        configured = all(keychain.has_password(service, account)
                         for service in (PAIRING_SERVICE, PASSWORD_SERVICE))
    except keychain.KeychainError as exc:
        raise ToolError("Unlock the login Keychain, then retry.") from exc
    if not configured:
        raise ToolError("Run 'tinytouch setup --mode hid' before changing a password.")
    value = prompt_hid_password()
    try:
        mapping = (current_keyboard_output_map()
                   if load_settings(account)["keyboard_layout"] == "auto" else None)
        translate_password(value.encode("utf-8"), mapping)
    except (UnicodeError, ValueError, RuntimeError, OSError) as exc:
        raise ToolError(
            "This password cannot be typed with the selected keyboard layout. "
            "Select a compatible macOS layout and use at most 160 typed keys."
        ) from exc
    target = finger_password_account(account, args.finger) if args.finger else account
    with hid_settings_change():
        keychain_set(PASSWORD_SERVICE, target, value)
    say(f"HID password saved for finger {args.finger}." if args.finger
        else "Default HID password saved.")


def command_keyboard_layout(args: argparse.Namespace) -> None:
    """Read or change the host layout used for HID password translation."""
    require_macos()
    from tinytouch_helper import load_settings, settings_path

    account = device_account(choose_port(args.port))
    if args.layout is None:
        say(f'HID keyboard layout: {load_settings(account)["keyboard_layout"]}.')
        return
    path = settings_path(account)
    try:
        try:
            settings = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, UnicodeError, json.JSONDecodeError):
            settings = {}
        if not isinstance(settings, dict):
            settings = {}
        settings["keyboard_layout"] = args.layout
        with hid_settings_change():
            atomic_write_json(path, settings)
    except OSError as exc:
        raise ToolError("Could not save the HID keyboard layout. Check file permissions and retry.") from exc
    say(f"HID keyboard layout saved: {args.layout}.")


def configure_hid(port: str, device: dict[str, str]) -> None:
    global _setup_password
    account = device_account(port)
    previous: dict[str, str | None] = {}
    written: list[str] = []
    added: str | None = None
    try:
        prepare_hid_password()
        previous = {
            service: keychain_get(service, account)
            for service in (PAIRING_SERVICE, PASSWORD_SERVICE)
        }
        saved_key = previous[PAIRING_SERVICE]
        try:
            key = hid_pairing_key(saved_key)
            valid_saved_key = True
        except HidSetupIncompleteError:
            key = secrets.token_bytes(32)
            valid_saved_key = False
        identifier = host_id(key)
        registered, capacity = host_list(port)
        if identifier not in registered and len(registered) >= capacity:
            raise ToolError("This device has no available HID computer slot. Remove a registered computer before adding another.")
        if not valid_saved_key:
            written.append(PAIRING_SERVICE)
            keychain_set(PAIRING_SERVICE, account, key.hex())
        saved_password = previous[PASSWORD_SERVICE]
        if not saved_password or len(saved_password.encode("utf-8")) > 160:
            written.append(PASSWORD_SERVICE)
            password_for(account)
        if identifier not in registered:
            # A timeout can occur after the device saves the host.
            added = identifier
            serial_command(port, f"HOST ADD {identifier} {key.hex()}", timeout=4)
        verify_hid_host(port, status(port), account)
    except (Exception, KeyboardInterrupt) as exc:
        cleanup_failed = False
        if added is not None:
            try:
                if added in host_list(port)[0]:
                    serial_command(port, f"HOST REMOVE {added}", timeout=4)
                    if added in host_list(port)[0]:
                        raise ToolError("The new HID computer is still registered.")
            except Exception:
                cleanup_failed = True
        for service in reversed(written):
            try:
                value = previous[service]
                if value is None:
                    keychain_delete(service, account)
                else:
                    keychain_set(service, account, value)
            except Exception:
                cleanup_failed = True
        if cleanup_failed:
            say("Some HID setup changes could not be restored. Reconnect tinyTouch and run 'tinytouch setup --mode hid' again.")
        if isinstance(exc, KeyboardInterrupt):
            raise
        suffix = " Some setup changes could not be restored." if cleanup_failed else ""
        raise ToolError(
            f"HID setup did not finish. {exc}{suffix} Run 'tinytouch setup --mode hid' again."
        ) from exc
    finally:
        if _setup_password is not None:
            _setup_password[:] = b"\x00" * len(_setup_password)
            _setup_password = None


def command_hid_smoke(_: argparse.Namespace) -> None:
    """Run the HID setup path against a temporary protocol-6 device."""
    require_macos()
    device_id = "TT-SMOKE-" + secrets.token_hex(6).upper()
    master, slave = pty.openpty()
    port = os.ttyname(slave)
    tty.setraw(slave)
    state = {"mode": "piv", "hosts": set()}
    stopped = threading.Event()

    def reply(line: str) -> str:
        if line == "STATUS":
            return (
                "OK STATUS firmware=0.8.4 protocol=6 mode=" + state["mode"]
                + f" sensor=ready fingerprints=4 piv=none hosts={len(state['hosts'])}"
            )
        if line == "AUTH":
            return "EVENT TOUCH\nOK AUTH"
        if line == "HOST LIST":
            identifiers = ",".join(sorted(state["hosts"])) or "none"
            return f"OK HOST LIST capacity=8 ids={identifiers}"
        if line.startswith("HOST ADD "):
            state["hosts"].add(line.split()[2].lower())
            return "OK ADD"
        if line == "SET MODE HID":
            state["mode"] = "hid"
            return "OK MODE"
        return "ERR COMMAND"

    def serve() -> None:
        buffered = b""
        while not stopped.is_set():
            try:
                readable, _, _ = select.select([master], [], [], 0.1)
                if not readable:
                    continue
                chunk = os.read(master, 256)
            except OSError:
                return
            if not chunk:
                continue
            buffered += chunk
            while b"\n" in buffered:
                raw, buffered = buffered.split(b"\n", 1)
                response = reply(raw.decode("ascii", "replace").strip())
                os.write(master, (response + "\n").encode("ascii"))

    worker = threading.Thread(target=serve, daemon=True)
    previous_account = os.environ.get("TINYTOUCH_DEVICE_ACCOUNT")
    try:
        remove_helper()
        os.environ["TINYTOUCH_DEVICE_ACCOUNT"] = device_id
        worker.start()
        command_setup(argparse.Namespace(mode="hid", port=port, skip_enroll=True, no_pair=False))
        run([sys.executable, "_helper", "--self-test", "--device-id", device_id])
        say(f"HID bridge password: {keychain_get(PASSWORD_SERVICE, device_id)}")
        say("HID setup and the helper communication test passed.")
    finally:
        stopped.set()
        worker.join(timeout=1)
        os.close(slave)
        os.close(master)
        if previous_account is None:
            os.environ.pop("TINYTOUCH_DEVICE_ACCOUNT", None)
        else:
            os.environ["TINYTOUCH_DEVICE_ACCOUNT"] = previous_account
        keychain_delete(PAIRING_SERVICE, device_id)
        keychain_delete(PASSWORD_SERVICE, device_id)
        remove_helper()


def host_list(port: str) -> tuple[set[str], int]:
    lines = serial_command(port, "HOST LIST", timeout=4)
    line = next((item for item in reversed(lines) if item.startswith("OK HOST LIST ")), "")
    data = dict(re.findall(r"([A-Za-z_]+)=([^ ]*)", line))
    try:
        capacity = int(data["capacity"])
        values = [] if data["ids"] in {"", "none"} else data["ids"].lower().split(",")
        ids = set(values)
        if (
            not 1 <= capacity <= 8
            or len(values) != len(ids)
            or len(ids) > capacity
            or any(re.fullmatch(r"[0-9a-f]{16}", value) is None for value in ids)
        ):
            raise ValueError()
    except (KeyError, ValueError) as exc:
        raise ToolError("The device returned an invalid HID computer inventory.") from exc
    return ids, capacity


def finger_slots(finger: int) -> tuple[int, ...]:
    """Map a fingerprint block to sensor slots, including slot zero."""
    return tuple(slot % 40 for slot in range((finger - 1) * 4 + 1, finger * 4 + 1))


def finger_inventory(
    port: str, device: dict[str, str], *, required_finger: int | None = None,
) -> tuple[dict[int, int], int]:
    if device.get("finger_groups") != "1":
        raise ToolError(
            "This firmware requires an update for fingerprint block enrollment. Run 'tinytouch update'. Then unplug and reconnect tinyTouch."
        )
    data = fields_from(serial_command(port, "FINGER LIST", timeout=6), "OK FINGER LIST")
    try:
        entries = [] if data["groups"] == "none" else [
            tuple(map(int, entry.split(":"))) for entry in data["groups"].split(",")
        ]
        groups = dict(entries)
        available = int(data["available"])
        capacity = int(data["capacity"])
        pending = int(data["pending"])
        if not 1 <= capacity <= 40 or len(groups) != len(entries) or any(
            not 1 <= number <= 10 or not 0 <= views <= 4 for number, views in groups.items()
        ):
            raise ValueError()
        for number, views in groups.items():
            if views > sum(slot < capacity for slot in finger_slots(number)):
                raise ValueError()
            if views == 0 and number != pending:
                raise ValueError()
        expected_available = sum(
            finger not in groups and max(finger_slots(finger)) < capacity
            for finger in range(1, 11)
        )
        if available != expected_available:
            raise ValueError()
        if pending:
            if pending not in groups:
                raise ValueError()
            groups[pending] = -1
    except (KeyError, ValueError) as exc:
        raise ToolError("The device returned an invalid fingerprint inventory.") from exc
    if required_finger is not None and max(finger_slots(required_finger)) >= capacity:
        raise ToolError(
            f"This sensor cannot store all four views for finger {required_finger}. "
            "Run 'tinytouch fingers' and choose a fingerprint block within its capacity."
        )
    return groups, available


def enroll_finger(port: str, device: dict[str, str], finger: int, replace: bool = False) -> None:
    groups, available = finger_inventory(port, device, required_finger=finger)
    occupied = finger in groups
    if occupied and not replace:
        say(f'Finger {finger} already has fingerprint enrollment data.')
        say("Replacement removes all existing views for this finger.")
        if ask(f"Replace finger {finger}? [y/N] ").lower() not in {"y", "yes"}:
            raise ToolError("Fingerprint enrollment was not changed.")
        replace = True
    if not occupied and available == 0:
        raise ToolError("No empty fingerprint blocks remain. Delete a finger before enrolling another.")
    visual = sys.stdout.isatty()
    if visual and sys.stdin.isatty():
        introduce_enrollment()
    view_index = 0

    def event_handler(event: str) -> None:
        nonlocal view_index
        if event.startswith("EVENT VIEW "):
            try:
                view_index = int(event.split()[-1]) - 1
                if not 0 <= view_index < len(ENROLLMENT_VIEWS):
                    raise ValueError()
            except ValueError as exc:
                raise ToolError("The device returned an invalid enrollment step.") from exc
            say(f'Finger {finger}: View {view_index + 1} of 4.')
        elif visual:
            show_enrollment_event(view_index, event)
        elif event == "EVENT TOUCH":
            say(f'Touch the sensor with the {ENROLLMENT_VIEWS[view_index][1]} of the same finger.')
        elif event == "EVENT TOUCH_AGAIN":
            say(f'Touch the sensor again with the {ENROLLMENT_VIEWS[view_index][1]} of the same finger.')
        elif event == "EVENT LIFT":
            say("Lift your finger away from the sensor.")

    unlock(port, reason=f"begin enrolling finger {finger}")
    command = f"FINGER ENROLL_GROUP {finger}" + (" REPLACE" if replace else "")
    try:
        serial_command(port, command, timeout=300, event_handler=event_handler)
    except (ToolError, KeyboardInterrupt):
        say(f'Enrollment of finger {finger} did not finish. The other fingerprint blocks were not changed.')
        if replace:
            say(f'The previous views for finger {finger} may have been removed. Enroll this finger again.')
        raise
    current = status(port)
    groups, _available = finger_inventory(port, current)
    if groups.get(finger) != 4:
        raise ToolError("Verification failed. The device did not report all four fingerprint views.")
    chime("Glass")
    say(f'Finger {finger} is enrolled with all four views.')


def enroll(port: str, skip: bool) -> None:
    if skip:
        return
    current = status(port)
    count = int(current.get("fingerprints", "-1"))
    if count < 0:
        raise ToolError(
            "The fingerprint sensor is unavailable. The existing enrollment was not changed."
        )
    if count:
        return
    enroll_finger(port, current, 1)


def command_setup(args: argparse.Namespace) -> None:
    require_macos()
    mode = choose_mode(args.mode)
    port = choose_port(args.port)
    # HID reconfiguration pauses the helper through its foreground lease.
    # Keep that service available if device validation or password entry fails.
    if mode == "piv":
        remove_helper()
    piv_rescan_needed = False
    created_piv_identities = None
    previous_piv_identities: set[str] = set()
    expected_account: str | None = None
    reconnected = False
    hid_configured = False
    while True:
        mode_changed = False
        with foreground_session(port) as connected_port:
            if isinstance(connected_port, str):
                port = connected_port
            if expected_account is not None and device_account(port) != expected_account:
                raise ToolError("A different device reconnected. Connect the original tinyTouch and run setup again.")
            device = fresh_status(port, {"mode": mode}) if reconnected else status(port)
            protocol6(device)
            sensor_ready(device)
            if (
                mode == "piv"
                and device.get("mode") == "piv"
                and device.get("piv") == "ready"
                and int(device.get("fingerprints", "0")) > 0
                and paired_piv_identities()
            ):
                say("PIV setup is already complete on this Mac.")
                explain_piv_pin()
                return
            if mode == "hid" and not hid_configured:
                expected_account = device_account(port)
                unlock(port, reason="configure HID mode")
                configure_hid(port, device)
                hid_configured = True
            if device.get("mode") != mode:
                expected_account = device_account(port)
                unlock(
                    port,
                    reason=f"switch to {mode.upper()} mode",
                )
                serial_command(port, f"SET MODE {mode.upper()}", timeout=4)
                mode_changed = True
            elif mode == "piv" and device.get("piv") != "ready":
                say("")
                say("Setting up PIV certificates. This can take up to 30 seconds.")
                paired, available = piv_identities()
                previous_piv_identities = set(paired + available)
                unlock(port, reason="create your PIV identity")
                serial_command(
                    port, "PIV CREATE", timeout=45,
                    wait_message="Creating PIV identities. This can take up to 30 seconds. Keep your finger off the sensor.",
                )
                piv_rescan_needed = True
            if not mode_changed and not piv_rescan_needed:
                enroll(port, args.skip_enroll)
                device = status(port)
                protocol6(device)
                sensor_ready(device)
        if not mode_changed:
            break
        notify(
            "tinyTouch mode changed",
            "Reconnect tinyTouch to apply the new device mode.",
        )
        say(f"Unplug and reconnect tinyTouch to use {mode.upper()} mode.")
        try:
            port = wait_for_reconnect(port)
        except ToolError as exc:
            raise ToolError(
                f"{mode.upper()} mode was selected, but setup did not finish. {exc} "
                f"Unplug and reconnect tinyTouch, then run 'tinytouch setup --mode {mode}' again."
            ) from exc
        reconnected = True
    if piv_rescan_needed:
        say("")
        created_piv_identities = wait_for_piv_identities(
            timeout=30.0,
            excluding=previous_piv_identities,
            message=(
                "Waiting for macOS to detect the PIV identity. Keep your finger off the sensor."
            ),
        )
        device = fresh_status(port, {"piv": "ready"})
        enroll(port, args.skip_enroll)
        device = status(port)
        protocol6(device)
        sensor_ready(device)
    if mode == "piv" and device.get("piv") != "ready":
        raise ToolError("PIV setup is incomplete. The identity is not ready.")
    if mode == "hid":
        verify_hid_host(port, device)
        install_helper()
        if not helper_loaded():
            raise ToolError("HID setup is incomplete. The helper is not loaded.")
    if mode == "piv" and not args.no_pair:
        command_pair(
            args,
            identities=created_piv_identities,
            separate_identity_list=created_piv_identities is not None,
        )
    say("")
    say(f"Ready ({mode.upper()}).")


def command_mode(args: argparse.Namespace) -> None:
    port = choose_port(args.port)
    with foreground_session(port) as connected_port:
        if isinstance(connected_port, str):
            port = connected_port
        device = status(port)
        protocol6(device)
        account = device_account(port)
        if args.mode == "hid":
            try:
                verify_hid_host(port, device, account)
            except HidSetupIncompleteError as exc:
                say(f"HID password typing needs setup on this Mac. {exc}")
        unlock(port, reason=f"switch to {args.mode.upper()} mode")
        serial_command(port, f"SET MODE {args.mode.upper()}", timeout=4)
        if args.mode == "piv":
            remove_helper()
    notify("tinyTouch mode changed", "Reconnect tinyTouch to apply the new device mode.")
    say(f"{args.mode.upper()} mode was selected.")
    say("")
    say("Unplug and reconnect tinyTouch to apply the new device mode.")
    say("Waiting for the device to disconnect from USB.")
    try:
        reconnected_port = wait_for_reconnect(port)
    except ToolError as exc:
        recovery = "tinytouch setup --mode hid" if args.mode == "hid" else "tinytouch mode piv"
        raise ToolError(
            f"{args.mode.upper()} mode was selected, but reconnect did not finish. {exc} "
            f"Unplug and reconnect tinyTouch, then run '{recovery}' again."
        ) from exc
    with foreground_session(reconnected_port) as connected_port:
        if isinstance(connected_port, str):
            reconnected_port = connected_port
        if device_account(reconnected_port) != account:
            raise ToolError("A different device reconnected. Connect the original tinyTouch and run the mode command again.")
        device = fresh_status(reconnected_port, {"mode": args.mode.lower()})
        if args.mode == "hid":
            verify_hid_host(reconnected_port, device, account)
    if args.mode == "hid":
        install_helper()
        if not helper_loaded():
            raise ToolError("HID mode was selected, but the helper is not loaded. Run 'tinytouch setup --mode hid'.")
    say(f"{args.mode.upper()} mode is active.")


def command_led(args: argparse.Namespace) -> None:
    state = args.state
    if state in {None, "show"}:
        show_settings(args, led_only=True)
        return
    if state == "color":
        if args.role is None or args.color is None:
            raise ToolError("Select a role and color. Example: tinytouch led color idle purple. Valid roles: idle, success, failure, end. Run 'tinytouch led --help' to list colors.")
        name = {"idle": "led_idle_color", "success": "led_success_color", "failure": "led_failure_color", "end": "led_idle_end_color"}[args.role]
        command_config(argparse.Namespace(name=name, value=args.color, port=args.port, json=False))
        return
    if state == "effect":
        if args.effect_name is None:
            raise ToolError("Select an effect: steady, breathe, flash, fade-in, or fade-out. Example: tinytouch led effect breathe.")
        command_config(argparse.Namespace(name="led_idle_effect", value=args.effect_name, port=args.port, json=False))
        return
    if state == "preset":
        if args.preset_name is None:
            raise ToolError("Select a preset: default, ocean, neon, or sunset. Example: tinytouch led preset ocean.")
        idle, end, success, failure, effect = LED_PRESETS[args.preset_name]
        values = dict(zip(("led_idle_color", "led_idle_end_color", "led_success_color", "led_failure_color", "led_idle_effect"), (idle, end, success, failure, effect)))
        values["led_idle_cycles"] = "0"
        apply_settings(args.port, values)
        say(f"Applied the '{args.preset_name}' LED preset. The lighting mode did not change.")
        return
    if state == "preview":
        if args.preview_color is None:
            raise ToolError("Select a preview color. Example: tinytouch led preview purple --effect breathe.")
        color = setting_value("led_idle_color", args.preview_color)
        effect = setting_value("led_idle_effect", args.effect)
        port = choose_port(args.port)
        with foreground_session(port):
            device = status(port)
            protocol6(device)
            require_setting_support(device, "led_idle_color")
            unlock(port, reason="preview sensor lighting")
            serial_command(port, f"LED PREVIEW {color} {effect} {args.duration_ms}", timeout=10)
        say("The LED preview is complete. The saved settings were not changed.")
        return
    port = choose_port(args.port)
    with foreground_session(port):
        device = status(port)
        protocol6(device)
        if "led" not in device:
            raise ToolError(
                "This firmware does not support LED control. Run 'tinytouch update'. Then unplug and reconnect tinyTouch before trying again."
            )
        if args.state == "off" and "led_control" not in device:
            raise ToolError(
                "This firmware cannot disable the sensor's automatic authentication flashes. "
                "Update the firmware before setting the LED fully off, then unplug and reconnect "
                "tinyTouch when prompted."
            )
        if args.state == "only-auth" and device.get("led_only_auth") != "1":
            raise ToolError(
                "This firmware does not support authentication-only lighting. Run 'tinytouch update'. Then unplug and reconnect tinyTouch before trying again."
            )
        unlock(port, reason=f'set the sensor lighting to {args.state}')
        value = {"off": 0, "on": 1, "only-auth": 2}[args.state]
        serial_command(port, f"SET LED {value}", timeout=5)
        updated = fresh_status(port, {"led": args.state})
        if updated.get("led_control") == "reconnect":
            say(f"Sensor LED preference saved as {args.state}.")
            say("Unplug tinyTouch and reconnect it to finish disabling the sensor's automatic lighting.")
            return
        if updated.get("led_control") not in (None, "manual") or updated.get("led_sync") == "pending":
            raise ToolError("LED preference saved, but the sensor has not applied it. Please reconnect tinyTouch.")
        say(f'Sensor lighting mode: {args.state}. This setting is saved on tinyTouch.')


def command_piv_touch(args: argparse.Namespace) -> None:
    port = choose_port(args.port)
    with foreground_session(port):
        device = status(port)
        protocol6(device)
        if "piv_touch" not in device:
            raise ToolError(
                "This firmware does not support touch-activated PIV. Run 'tinytouch update', "
                "then unplug and reconnect tinyTouch before trying again."
            )
        unlock(port, reason=f"turn touch-activated PIV {args.state}")
        serial_command(port, f"SET PIV_TOUCH {int(args.state == 'on')}", timeout=5)
        fresh_status(port, {"piv_touch": args.state})
    say(f"Touch-activated PIV is saved as {args.state}.")
    say("Unplug and reconnect tinyTouch to apply this setting.")


def command_config(args: argparse.Namespace) -> None:
    if getattr(args, "json", False) and args.value is not None:
        raise ToolError("Use --json when reading settings or running 'config list'. Omit --json when changing a setting.")
    if args.name == "list":
        if args.value is not None:
            raise ToolError("'config list' does not accept a value. Run 'tinytouch config NAME VALUE' to change a setting.")
        if getattr(args, "json", False):
            say(json.dumps({name: {"default": spec.default, "values": list(spec.choices) if spec.choices else {"minimum": spec.minimum, "maximum": spec.maximum}, "description": spec.description, "requires_new_firmware": bool(spec.capability)} for name, spec in SETTINGS.items()}, indent=2))
            return
        for name, spec in SETTINGS.items():
            say(f"{name}: {spec.allowed()} (default: {spec.default})")
            say(f"  {spec.description}")
        return
    if args.name in {None, "show"}:
        if args.value is not None:
            raise ToolError("'config show' does not accept a value. Run 'tinytouch config NAME VALUE' to change a setting.")
        show_settings(args)
        return
    name = setting_name(args.name)
    if args.value is None:
        port = choose_port(args.port)
        with foreground_session(port):
            device = status(port)
            protocol6(device)
        if name not in device:
            raise ToolError(f"This firmware does not report {name}. Run 'tinytouch update'. Then reconnect the device.")
        value = SETTINGS[name].decode(device[name])
        say(json.dumps({name: value}) if getattr(args, "json", False) else f"{name}={value}")
        return
    value = setting_value(name, args.value)
    if name == "mode":
        command_mode(argparse.Namespace(mode=SETTINGS[name].decode(value), port=args.port))
    elif name == "led":
        command_led(argparse.Namespace(state=SETTINGS[name].decode(value), port=args.port))
    else:
        apply_settings(args.port, {name: args.value})
        say(f"Updated {name} to {SETTINGS[name].decode(value)}.")


def require_setting_support(device: dict[str, str], name: str) -> None:
    if name == "piv_delay_ms" and "piv_delay_ms" not in device:
        raise ToolError("This firmware does not support configurable PIV delay. Update its firmware first.")
    capability = SETTINGS[name].capability
    if capability and device.get(capability) != "1":
        raise ToolError(f"This firmware does not support {name}. Run 'tinytouch update' to install firmware 0.1.34-dev.1 or later. Then unplug and reconnect tinyTouch.")


def apply_settings(explicit_port: str | None, values: dict[str, str]) -> None:
    normalized = {name: setting_value(name, value) for name, value in values.items()}
    port = choose_port(explicit_port)
    with foreground_session(port):
        device = status(port)
        protocol6(device)
        for name in values:
            require_setting_support(device, name)
        unlock(port, reason="change device settings")
        applied = []
        try:
            for name, value in normalized.items():
                serial_command(port, f"SET {SETTINGS[name].wire} {value}", timeout=5)
                applied.append(name)
            if device.get("config_values") == "1" or "piv_delay_ms" in normalized:
                fresh_status(port, normalized)
        except ToolError:
            if applied:
                say("Settings acknowledged before the error: " + ", ".join(applied) + ".")
            raise


def show_settings(args: argparse.Namespace, *, led_only: bool = False) -> None:
    port = choose_port(args.port)
    with foreground_session(port):
        device = status(port)
        protocol6(device)
    specs = {name: spec for name, spec in SETTINGS.items() if not led_only or name.startswith("led")}
    values = {name: spec.decode(device[name]) for name, spec in specs.items() if name in device}
    if getattr(args, "json", False):
        say(json.dumps(values, indent=2, sort_keys=True))
        return
    show_fields(
        [(spec.label, values.get(name, "Unavailable")) for name, spec in specs.items()]
    )


def command_enroll(args: argparse.Namespace) -> None:
    port = choose_port(args.port)
    with foreground_session(port):
        device = status(port)
        protocol6(device)
        sensor_ready(device)
        enroll_finger(port, device, args.finger, args.replace)


def finger_description(views: int | None) -> str:
    """Use the same inventory labels in commands and interactive selection."""
    if views is None:
        return "Empty."
    if views == -1:
        return "Cleanup pending. Reconnect the device."
    if views == 4:
        return "Enrolled: 4 fingerprint views."
    return f"Partially enrolled: {views} of 4 fingerprint views."


def command_fingers(args: argparse.Namespace) -> None:
    port = choose_port(args.port)
    with foreground_session(port):
        device = status(port)
        protocol6(device)
        groups, available = finger_inventory(port, device)
        for finger, views in sorted(groups.items()):
            say(f"Finger {finger}: {finger_description(views)}")
        if not groups:
            say("No fingerprints enrolled.")
        say(f"Space for {available} additional fingers.")


def command_delete(args: argparse.Namespace) -> None:
    port = choose_port(args.port)
    with foreground_session(port):
        device = status(port)
        protocol6(device)
        finger_inventory(port, device)
        unlock(port, reason=f"delete finger {args.finger}")
        serial_command(port, f"FINGER DELETE_GROUP {args.finger}", timeout=25)
        groups, _available = finger_inventory(port, status(port))
        if args.finger in groups:
            raise ToolError("Verification failed after deleting the fingerprint block.")
        say(f"Finger {args.finger} was deleted.")


def command_computers(args: argparse.Namespace) -> None:
    port = choose_port(args.port)
    device = status(port)
    protocol6(device)
    registered, capacity = host_list(port)
    if args.action == "list":
        say(f"HID computers ({len(registered)} of {capacity}):")
        for identifier in sorted(registered):
            say(f"  {identifier}")
        return
    if args.remove:
        identifier = args.remove.lower()
        if identifier not in registered:
            raise ToolError(f'HID computer {args.remove} is not registered on this device.')
        unlock(port, reason="remove this computer")
        serial_command(port, f"HOST REMOVE {identifier}", timeout=5)
        if identifier in host_list(port)[0]:
            raise ToolError("Verification failed. The HID computer is still registered.")
        account = device_account(port)
        local = keychain_get(PAIRING_SERVICE, account)
        if local:
            try:
                if host_id(bytes.fromhex(local)) == identifier:
                    keychain_delete(PAIRING_SERVICE, account)
            except ValueError:
                pass
        say(f"HID computer {identifier} was removed.")


def command_factory_reset(args: argparse.Namespace) -> None:
    port = choose_port(args.port)
    with foreground_session(port):
        device = status(port)
        protocol6(device)
        if ask("Factory reset clears fingerprints, keys, registered computers, and device settings. Continue? [y/N] ").lower() not in {"y", "yes"}:
            raise ToolError("Factory reset cancelled.")
        # Capture Mac cleanup data while the device identity still exists.
        account = device_account(port)
        paired_identities = paired_piv_identities()
        if paired_identities:
            authorize_macos()
        # The administrator prompt can outlast device authorization.
        unlock(port, reason="confirm the factory reset")
        serial_command(port, "RESET FACTORY", timeout=15)
        cleared = status(port)
        for key, expected in (("fingerprints", "0"), ("hosts", "0"), ("piv", "unconfigured")):
            if key == "piv" and key not in cleared:
                continue
            if cleared.get(key) != expected:
                raise ToolError(f"Factory reset verification failed. {key}={cleared.get(key)!r}.")
        # Keep the saved service and Mac pairings if approval or reset fails.
        remove_helper()
    if paired_identities:
        for identity in paired_identities:
            run(
                [
                    "sudo", "-n", "sc_auth", "unpair", "-u",
                    getpass.getuser(), "-h", identity,
                ]
            )
    keychain_delete(PAIRING_SERVICE, account)
    keychain_delete(PASSWORD_SERVICE, account)
    say("Factory reset complete.")


def response_next(lines: list[str], verb: str) -> int:
    line = next((item for item in reversed(lines) if item.startswith(f"OK {verb}")), "")
    match = re.search(r"(?:^| )next=(\d+)(?: |$)", line)
    if not match:
        raise ToolError(f'The device returned an invalid response to {verb}.')
    return int(match.group(1))


def stage_ota(port: str, image: bytes, digest: str) -> None:
    with foreground_session(port) as session_port:
        # Clear an incomplete upload from an interrupted client. Committed
        # firmware remains in its separate OTA slot.
        try:
            serial_command(session_port, "OTA ABORT", timeout=2)
        except ToolError:
            # Firmware before 0.1.1 does not support an unscoped abort.
            pass
        serial_command(
            session_port,
            "AUTH",
            timeout=15,
            touch_prompt="Touch the device with a registered finger to approve the firmware update.",
        )
        token = secrets.token_hex(16)
        device = _active_serial
        previous_write_timeout = device.write_timeout
        device.write_timeout = 5
        try:
            lines = serial_exchange(device, f"OTA BEGIN {token} {len(image)} {digest}")
            offset = response_next(lines, "OTA")
            say("Uploading firmware: 0%")
            next_progress = 10
            starts = list(range(offset, len(image), OTA_CHUNK_SIZE))
            for index in range(0, len(starts), OTA_WRITE_WINDOW):
                commands = []
                for start in starts[index:index + OTA_WRITE_WINDOW]:
                    payload = base64.b64encode(
                        image[start:start + OTA_CHUNK_SIZE]
                    ).decode()
                    command = f"OTA WRITE {token} {start} {payload}"
                    device.write((command + "\n").encode("ascii"))
                    commands.append(command)
                device.flush()
                for command in commands:
                    lines = serial_response(device, command)
                    offset = response_next(lines, "OTA")
                progress = min(100, offset * 100 // len(image))
                if progress >= next_progress:
                    say(f"Uploading firmware: {progress}%")
                    next_progress = progress + 10
            say("Verifying firmware...")
            commit = serial_exchange(device, f"OTA COMMIT {token}", timeout=10)
        except BaseException:
            try:
                serial_exchange(device, f"OTA ABORT {token}", timeout=2)
            except Exception:
                pass
            raise
        finally:
            device.write_timeout = previous_write_timeout
    line = next((item for item in commit if item.startswith("OK OTA STAGED")), "")
    if "power_cycle=required" not in line:
        raise ToolError("The firmware did not confirm that the OTA slot was staged safely.")


def serial_exchange(device, command: str, *, timeout: float = 8.0) -> list[str]:
    device.write((command + "\n").encode("ascii"))
    device.flush()
    return serial_response(device, command, timeout=timeout)


def serial_response(device, command: str, *, timeout: float = 8.0) -> list[str]:
    """Read one terminal response for a command already sent to the device."""
    lines: list[str] = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        raw = device.readline()
        if raw:
            line = raw.decode("utf-8", "replace").strip()
            if line:
                lines.append(line)
                if is_terminal(command, line):
                    break
    if not lines or not is_terminal(command, lines[-1]):
        raise SerialTimeout(f"tinyTouch did not acknowledge {command.split()[0]}.")
    if lines[-1].startswith("ERR "):
        raise ToolError(human_error(lines[-1]))
    return lines


def download(url: str) -> bytes:
    try:
        request = urllib.request.Request(
            url,
            headers={
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "User-Agent": f"tinytouch/{CLI_VERSION}",
            },
        )
        with urllib.request.urlopen(request, timeout=20, context=TLS) as response:
            return response.read()
    except OSError as exc:
        raise ToolError("Could not download the selected tinyTouch release.") from exc


def release_root(version: str) -> str:
    """Return an immutable asset root for a stable or explicitly selected dev version."""
    if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-dev\.[0-9]+)?", version) is None:
        raise ToolError("The release manifest contains an invalid release version.")
    return f"{RELEASE_DOWNLOAD_URL}/v{version}"


def update_release(version: str | None = None) -> tuple[str, dict]:
    """Resolve latest once, then verify and use its immutable release assets."""
    if version is None:
        nonce = time.time_ns()
        latest = json.loads(
            download(f"{LATEST_RELEASE_URL}/release-manifest.json?nocache={nonce}").decode()
        )
        version = latest.get("version") if isinstance(latest, dict) else None
        if not isinstance(version, str):
            raise ToolError("The release manifest contains no valid version.")
        if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version) is None:
            raise ToolError("The latest release manifest does not identify a stable version.")

    root = release_root(version)
    manifest = json.loads(download(f"{root}/release-manifest.json").decode())
    if not isinstance(manifest, dict) or manifest.get("version") != version:
        raise ToolError("The release files do not match the selected version.")
    return root, manifest


def package_test() -> None:
    """Check bundled imports and report the executable architecture."""
    import certifi  # noqa: F401
    import serial  # type: ignore  # noqa: F401
    from tinytouch_ports import comports
    comports()
    say(f"package ok ({platform.machine()})")


def network_test() -> None:
    """Check the latest stable release and one downloadable OTA asset."""
    try:
        root, manifest = update_release()
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ToolError("The release manifest contains invalid JSON data.") from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("version"), str):
        raise ToolError("The release manifest is incomplete.")
    ota = manifest.get("ota")
    if not isinstance(ota, dict):
        raise ToolError("The release manifest contains no OTA firmware file.")
    filename = ota.get("file")
    size = ota.get("size")
    digest = str(ota.get("sha256", ""))
    if (
        not isinstance(filename, str)
        or not filename
        or urlparse(filename).scheme
        or urlparse(filename).netloc
        or Path(filename).is_absolute()
        or ".." in Path(filename).parts
        or not isinstance(size, int)
        or isinstance(size, bool)
        or size <= 0
        or not re.fullmatch(r"[0-9a-f]{64}", digest)
    ):
        raise ToolError("The release manifest contains an invalid OTA firmware file.")
    image = download(f"{root}/{filename}")
    if len(image) != size or hashlib.sha256(image).hexdigest() != digest:
        raise ToolError("The OTA firmware file does not match the release manifest.")
    say("network ok")


def command_update(args: argparse.Namespace) -> None:
    root, manifest = update_release(getattr(args, "release_version", None))
    release_version = manifest["version"]
    if not getattr(args, "firmware_only", False) and release_version != CLI_VERSION:
        say(f"Updating tinyTouch CLI to {release_version}...")
        installer = download(f"{root}/install.sh")
        environment = os.environ.copy()
        environment["TINYTOUCH_RELEASE_ROOT"] = root
        result = subprocess.run(["/bin/sh"], input=installer, check=False, env=environment)
        if result.returncode != 0:
            raise ToolError("The CLI update failed. The firmware was not changed.")
        executable = shutil.which("tinytouch")
        if executable is None:
            raise ToolError("The new CLI is installed, but it is not on this terminal's PATH.")
        installed = subprocess.run(
            [executable, "--version"], capture_output=True, check=False, text=True
        )
        if (
            installed.returncode != 0
            or installed.stdout.strip() != f"tinyTouch CLI {release_version}"
        ):
            raise ToolError("The installed CLI version does not match the selected release.")
        command = [
            executable,
            "update",
            "--firmware-only",
            "--release-version",
            release_version,
        ]
        if args.port:
            command.extend(["--port", args.port])
        try:
            os.execv(executable, command)
        except OSError as exc:
            raise ToolError(
                "The CLI was installed, but the update could not restart. "
                "Run 'tinytouch update' again. The firmware was not changed."
            ) from exc

    command_upgrade_helper(args)

    port = choose_port(args.port)
    device = status(port)
    protocol6(device)
    metadata = manifest.get("ota") if isinstance(manifest, dict) else None
    if not isinstance(metadata, dict) or not metadata.get("file") or not metadata.get("sha256"):
        raise ToolError("The release manifest contains no verified firmware file.")
    image = download(f"{root}/{metadata['file']}")
    digest = hashlib.sha256(image).hexdigest()
    if digest != metadata["sha256"]:
        raise ToolError(
            "The downloaded firmware checksum does not match the release manifest."
        )
    stage_ota(port, image, digest)
    message = "Update ready. Unplug and reconnect tinyTouch to finish."
    notify("tinyTouch update ready", message)
    say(message)


def command_rom(args: argparse.Namespace) -> None:
    if args.port:
        say(f"Selected device: {args.port}")
    notify("tinyTouch ROM mode", "Unplug tinyTouch. Reconnect it in download mode. Then flash the device.")
    say("ROM flashing requires a physical reconnect. Unplug tinyTouch. Reconnect it in download mode. Then flash the device.")
    say("After flashing, unplug tinyTouch and reconnect it normally.")


def command_status(args: argparse.Namespace) -> None:
    port = choose_port(args.port)
    if not getattr(args, "summary", False):
        data = status(port)
        if getattr(args, "details", False):
            labels = {
                "fingerprints": "Saved scans",
                "finger_groups": "Grouped enrollment",
                "hosts": "Registered computers",
                "config_values": "Setting readback",
                "custom_config": "Custom settings",
                "led_control": "Lighting control",
                "led_sync": "Lighting sync",
                "led_only_auth": "Auth lighting support",
                "ota": "Firmware update",
                "piv": "Smart card",
                "piv_touch": "Touch-activated PIV",
                "piv_touch_active": "PIV touch active",
                "piv_visible": "Smart card visible",
            }
            show_fields(
                [
                    (
                        (
                            SETTINGS[key].label
                            if key in SETTINGS
                            else labels.get(key, key.replace("_", " ").capitalize())
                        ),
                        SETTINGS[key].decode(value) if key in SETTINGS else value,
                    )
                    for key, value in sorted(data.items())
                ]
            )
        else:
            say(json.dumps(data, indent=2, sort_keys=True))
        return
    with foreground_session(port):
        data = status(port)
        fingerprints = "Unavailable"
        if data.get("finger_groups") == "1":
            try:
                groups, _available = finger_inventory(port, data)
                enrolled = sum(views == 4 for views in groups.values())
                incomplete = len(groups) - enrolled
                fingerprints = f"{enrolled} enrolled"
                if incomplete:
                    fingerprints += f" ({incomplete} incomplete)"
            except ToolError:
                pass
        elif data.get("fingerprints", "-1").isdigit():
            fingerprints = f"{data['fingerprints']} saved scans"
    lighting = {"on": "On", "off": "Off", "only-auth": "Authentication only"}
    sensor = "Ready" if data.get("sensor") in {"ready", "ok"} else "Unavailable"
    rows = [
        ("Mode", data.get("mode", "Unknown").upper()),
        ("Fingerprints", fingerprints),
        ("Firmware", data.get("firmware", "Unknown")),
        ("Lighting", lighting.get(data.get("led"), "Unknown")),
        ("Sensor", sensor),
    ]
    if data.get("mode") == "piv":
        rows.append(
            ("Smart card", "Ready" if data.get("piv") == "ready" else "Not configured")
        )
    show_fields(rows)


def command_logs(args: argparse.Namespace) -> None:
    port = choose_port(args.port)
    for line in serial_command(port, "LOGS", timeout=4):
        if line.startswith("LOG "):
            say(line)


def command_test(args: argparse.Namespace) -> None:
    port = choose_port(args.port)
    status(port)
    serial_command(port, "PING", timeout=4)
    say("tinyTouch answered PING.")


def command_keys(args: argparse.Namespace) -> None:
    port = choose_port(args.port)
    device = status(port)
    protocol6(device)
    unlock(port, reason="create the PIV identity")
    serial_command(port, "PIV CREATE", timeout=15)
    if status(port).get("piv") != "ready":
        raise ToolError("Verification failed. The PIV identity is not ready.")
    say("The PIV identity is ready.")


def piv_identities() -> tuple[list[str], list[str]]:
    """Return the paired and unpaired smart-card identity hashes."""
    result = subprocess.run(
        ["sc_auth", "identities"], check=False, text=True, capture_output=True
    )
    paired_text, separator, unpaired_text = result.stdout.partition("Unpaired identities:")
    paired = re.findall(r"(?m)^([0-9A-Fa-f]{40})\b", paired_text)
    unpaired = re.findall(r"(?m)^([0-9A-Fa-f]{40})\b", unpaired_text if separator else "")
    return paired, unpaired


def paired_piv_identities() -> list[str]:
    return piv_identities()[0]


def user_piv_identities() -> list[str]:
    """Return smart-card identities registered to the current Mac user."""
    result = subprocess.run(
        ["sc_auth", "list", "-u", getpass.getuser()],
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode:
        raise ToolError("Could not read PIV pairings.")
    return [
        identity.upper()
        for identity in re.findall(
            r"(?m)^Hash:[ \t]+([0-9A-Fa-f]{40})\b", result.stdout
        )
    ]


def explain_piv_pin() -> None:
    say("If macOS prompts for the smart card PIN, enter 111111.")


def wait_for_piv_identities(
    timeout: float = 20.0,
    excluding: set[str] | None = None,
    message: str = (
        "Configuring PIV. This can take up to 20 seconds. Keep your finger off the sensor."
    ),
) -> tuple[list[str], list[str]]:
    """Wait for macOS smart-card discovery with a compact terminal spinner."""
    frames = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
    animated = sys.stdout.isatty()
    if not animated:
        say(message)
    deadline = time.monotonic() + timeout
    frame = 0
    try:
        while time.monotonic() < deadline:
            if animated:
                print(f"\r{frames[frame % len(frames)]} {message}", end="", flush=True)
                frame += 1
            paired, available = piv_identities()
            if excluding is not None:
                paired = [identity for identity in paired if identity not in excluding]
                available = [identity for identity in available if identity not in excluding]
            if paired or available:
                return paired, available
            time.sleep(0.25)
    finally:
        if animated:
            print("\r\033[2K", end="", flush=True)
    return [], []


def command_pair(
    args: argparse.Namespace,
    *,
    identities: tuple[list[str], list[str]] | None = None,
    separate_identity_list: bool = False,
) -> None:
    require_macos()
    port = choose_port(args.port)
    prepare_piv_discovery(port)
    paired, available = identities or wait_for_piv_identities()
    if paired:
        say("PIV is already paired with this Mac.")
        explain_piv_pin()
        return
    if not available:
        raise ToolError("macOS has not detected a PIV identity. Select PIV mode and try again.")
    if len(available) > 1:
        if separate_identity_list:
            say("")
        say("Available PIV identities:")
        for index, identity in enumerate(available, 1):
            say(f"  {index}. {identity}")
        say("Select 1 if you are not sure which identity to use.")
        choice = ask("Identity [1]: ") or "1"
        try:
            identity = available[int(choice) - 1]
        except (ValueError, IndexError) as exc:
            raise ToolError("Invalid identity selection.") from exc
    else:
        identity = available[0]
    # Obtain sudo first, then grant fresh device presence. Enrollment and a
    # terminal password prompt can outlive firmware's short PIV authorization.
    authorize_macos()
    # Selecting an identity or entering the macOS password can outlast the
    # discovery window. Reopen first, then grant presence after the USB reset.
    reopened = prepare_piv_discovery(port, refresh=True)
    if reopened:
        _, available_now = wait_for_piv_identities()
        if identity not in available_now and identity not in paired_piv_identities():
            raise ToolError("The selected PIV identity is no longer available. Try pairing again.")
    say("")
    if reopened is not False:
        unlock(
            port,
            explain_pin=True,
            reason="pair PIV with this Mac",
        )
    else:
        # Refresh authorized a card that was already visible, without a reset.
        explain_piv_pin()
    keychain_warning = False
    try:
        result = run(
            ["sudo", "-n", "sc_auth", "pair", "-u", getpass.getuser(), "-h", identity],
            text=True,
            capture_output=True,
        )
        pair_output = "\n".join(
            output
            for output in (result.stdout, result.stderr)
            if isinstance(output, str)
        )
        keychain_warning = (
            "password will be required after next SmartCard login" in pair_output
        )
    except ToolError as exc:
        # sc_auth can persist the user pairing before a later card proof fails.
        # Treat that partial command result as success only when macOS confirms
        # that it paired the identity selected above.
        paired_after, _ = piv_identities()
        if identity not in paired_after:
            if "CryptoTokenKit error -8" in str(exc):
                raise ToolError(
                    "macOS rejected this PIV identity. Run 'tinytouch update'. Then run setup again."
                ) from exc
            raise
    if keychain_warning:
        # macOS can complete login pairing without automatic Keychain unlock.
        # Confirm the selected user's pairing before preserving that result.
        if identity.upper() not in user_piv_identities():
            raise ToolError("macOS could not confirm PIV pairing. Please try again.")
    say("PIV pairing with this Mac is complete.")
    if keychain_warning:
        say(
            terminal_style(
                "Keychain needs your Mac password after the next PIV login.", "33"
            )
        )


def select_option(
    title: str,
    options: list[tuple[str, str]],
    *,
    back: str = "Back",
) -> str | None:
    """Select an option by number or name; return None to leave this menu."""
    if supports_arrows():
        try:
            return select_menu(title, options, back=back, width=panel_width(), style=terminal_style)
        except EOFError as exc:
            raise ToolError("This action requires input from an interactive terminal.") from exc
    while True:
        say("")
        if title:
            say(terminal_style(title, "1;36"))
        for index, (_key, label) in enumerate(options, 1):
            prefix = f"  {index}. "
            say(
                textwrap.fill(
                    label.removesuffix("."),
                    width=panel_width(),
                    initial_indent=prefix,
                    subsequent_indent=" " * len(prefix),
                )
            )
        say("")
        say(f"  0. {back}")
        answer = ask("Select: ").lower()
        if answer in {"0", "b", "back", "q", "quit", "exit"}:
            return None
        for index, (key, label) in enumerate(options, 1):
            name = label.split(" — ", 1)[0].removesuffix(".").lower()
            if answer in {str(index), key.lower(), name}:
                return key
        say("Invalid selection. Select one of the listed options.")


def interactive_finger(args: argparse.Namespace, action: str) -> list[str] | None:
    """Read inventory before selecting a complete finger block."""
    port = choose_port(args.port)
    with foreground_session(port):
        device = status(port)
        protocol6(device)
        groups, available = finger_inventory(port, device)
    say(f"Space for {available} additional fingers.")
    if action == "delete":
        candidates = sorted(groups)
        if not candidates:
            say("No fingerprints enrolled.")
            return None
    else:
        # Supported blocks are contiguous. Limit empty choices to the capacity
        # reported by the firmware rather than assuming a 40-template sensor.
        empty = [finger for finger in range(1, 11) if finger not in groups][:available]
        candidates = sorted([*groups, *empty])
        if not candidates:
            raise ToolError("No fingerprint blocks are available on this sensor.")
    options = []
    for finger in candidates:
        views = groups.get(finger)
        description = finger_description(views).removesuffix(".")
        options.append((f"finger-{finger}", f"Finger {finger} ({description})"))
    selected = select_option(
        "Enroll or replace a fingerprint" if action == "enroll" else "Delete a fingerprint",
        options,
    )
    if selected is None:
        return None
    finger = int(selected.split("-")[1])
    if action == "delete":
        if ask(f"Delete all views for finger {finger}? [y/N] ").lower() not in {"y", "yes"}:
            say("Deletion cancelled.")
            return None
    return [action, str(finger), "--port", port]


def interactive_computer(args: argparse.Namespace) -> list[str] | None:
    port = choose_port(args.port)
    with foreground_session(port):
        protocol6(status(port))
        registered, _capacity = host_list(port)
    if not registered:
        say("No HID computers are registered.")
        return None
    identifier = select_option("Remove a registered computer", [(host, host) for host in sorted(registered)])
    if identifier is None:
        return None
    if ask(f"Remove HID computer {identifier}? [y/N] ").lower() not in {"y", "yes"}:
        say("Removal cancelled.")
        return None
    return ["computers", "remove", identifier, "--port", port]


def prompt_setting(name: str) -> list[str] | None:
    spec = SETTINGS[name]
    say(spec.description)
    say(f"Default: {spec.default}. Values: {spec.allowed()}.")
    if spec.choices:
        selected = select_option(spec.label, [(key, key) for key in spec.choices])
        return ["config", name, selected] if selected is not None else None
    while True:
        value = ask(f"{spec.label} (Enter to cancel): ")
        if not value or value.lower() in {"b", "back", "q", "quit", "exit"}:
            return None
        try:
            return ["config", name, setting_value(name, value)]
        except ToolError as exc:
            say(str(exc))


def interactive_config() -> list[str] | None:
    settings = {name: spec for name, spec in SETTINGS.items() if name not in {"mode", "led"}}
    name = select_option("Configurable settings", [(name, spec.label) for name, spec in settings.items()])
    if name is None:
        return None
    return prompt_setting(name)


def interactive_command(args: argparse.Namespace, command: list[str]) -> bool:
    """Resolve interactive parameters, then use the regular CLI parser and handler."""
    global _sudo_session_ready, _setup_password
    command = list(command)
    if command[0] in {"setup", "mode"} and len(command) == 1:
        mode = select_option("Mode", list(MODE_OPTIONS))
        if mode is None:
            return False
        command.extend(["--mode", mode] if command[0] == "setup" else [mode])
    elif command == ["led"]:
        state = select_option(
            "Sensor lighting",
            [
                ("on", "On — idle lighting and feedback"),
                ("off", "Off — no lighting"),
                ("only-auth", "Authentication only — match feedback"),
            ],
        )
        if state is None:
            return False
        command.append(state)
    elif command == ["piv-touch"]:
        state = select_option(
            "Touch-activated PIV", [("on", "Enable"), ("off", "Disable")]
        )
        if state is None:
            return False
        command.append(state)
    elif command == ["led", "preset"]:
        preset = select_option("LED preset", [(key, key.capitalize()) for key in LED_PRESETS])
        if preset is None:
            return False
        command.append(preset)
    elif command == ["led", "preview"]:
        color = select_option("Preview color", [(key, key.capitalize()) for key in LED_COLORS])
        if color is None:
            return False
        effect = select_option("Preview effect", [(key, key.capitalize()) for key in LED_EFFECTS])
        if effect is None:
            return False
        command.extend([color, "--effect", effect])
    elif command[0] in {"enroll", "delete"}:
        selected = interactive_finger(args, command[0])
        if selected is None:
            return False
        command = selected
    elif command == ["computers", "remove"]:
        selected = interactive_computer(args)
        if selected is None:
            return False
        command = selected
    elif command[0] == "config":
        if len(command) > 1 and command[1] in {"show", "list"}:
            selected = command
        else:
            selected = prompt_setting(command[1]) if len(command) > 1 else interactive_config()
        if selected is None:
            return False
        command = selected
    global_options = ["--verbose"] if args.verbose else []
    if args.port:
        global_options.extend(["--port", args.port])
    selected_args = parser().parse_args([*global_options, *command])
    say("")
    title = COMMAND_TITLES.get(selected_args.command, selected_args.command.replace("-", " ").capitalize())
    if selected_args.command == "status" and selected_args.details:
        title = "Full status"
    show_section(title)
    # Each menu action gets the same credential lifetime as a standalone command.
    _sudo_session_ready = False
    try:
        selected_args.func(selected_args)
    finally:
        if _setup_password is not None:
            _setup_password[:] = b"\x00" * len(_setup_password)
            _setup_password = None

    return True

INTERACTIVE_MENUS = {
    "home": (
        "",
        (
            ("setup", "Setup", ["setup"]),
            ("enroll", "Enroll", ["enroll"]),
            ("update", "Update", ["update"]),
            ("status", "Status", ["status", "--summary"]),
            ("advanced", "Advanced", "advanced"),
        ),
    ),
    "fingers": (
        "Fingerprints",
        (
            ("list", "List fingerprints", ["fingers"]),
            ("enroll", "Enroll or replace", ["enroll"]),
            ("delete", "Delete", ["delete"]),
        ),
    ),
    "settings": (
        "Settings",
        (
            ("show", "Current settings", ["config", "show"]),
            ("mode", "Mode", ["mode"]),
            ("piv-touch", "Touch-activated PIV", ["piv-touch"]),
            ("led", "Lighting", "lighting"),
            ("config", "Edit a setting", ["config"]),
            ("list", "All settings", ["config", "list"]),
        ),
    ),
    "lighting": (
        "Lighting",
        (
            ("show", "Current lighting", ["led", "show"]),
            ("mode", "Mode", ["led"]),
            ("colors", "Colors", "colors"),
            ("effect", "Idle effect", ["config", "led_idle_effect"]),
            ("cycles", "Animation repeats", ["config", "led_idle_cycles"]),
            ("feedback", "Feedback duration", ["config", "led_feedback_ms"]),
            ("preset", "Preset", ["led", "preset"]),
            ("preview", "Preview", ["led", "preview"]),
        ),
    ),
    "colors": (
        "LED colors",
        (
            ("idle", "Idle", ["config", "led_idle_color"]),
            ("success", "Success", ["config", "led_success_color"]),
            ("failure", "Failure", ["config", "led_failure_color"]),
            ("end", "Breathing end", ["config", "led_idle_end_color"]),
        ),
    ),
    "computers": (
        "Computers",
        (
            ("list", "List computers", ["computers", "list"]),
            ("add", "Add this Mac", ["setup", "--mode", "hid"]),
            ("remove", "Remove a computer", ["computers", "remove"]),
        ),
    ),
    "advanced": (
        "Advanced",
        (
            ("settings", "Settings", "settings"),
            ("fingers", "Fingerprints", "fingers"),
            ("computers", "Computers", "computers"),
            ("test", "Test connection", ["test"]),
            ("piv", "PIV", "piv"),
            ("diagnostics", "Diagnostics", "diagnostics"),
            ("status", "Full status", ["status", "--details"]),
            ("uninstall", "Uninstall service", ["uninstall"]),
        ),
    ),
    "piv": (
        "PIV",
        (
            ("keys", "Create identity", ["keys"]),
            ("pair", "Pair with this Mac", ["pair"]),
        ),
    ),
    "diagnostics": (
        "Diagnostics",
        (
            ("ports", "USB ports", ["ports"]),
            ("logs", "Logs", ["logs"]),
            ("repair", "Repair Keychain", ["repair"]),
            ("rom", "ROM mode", ["rom"]),
            ("enroll-demo", "Enrollment demo", ["enroll-demo"]),
            ("factory-reset", "Factory reset", ["factory-reset"]),
        ),
    ),
}


def interactive_menu(args: argparse.Namespace, name: str = "home") -> bool:
    """Return True after an action, or False when the user goes back."""
    title, entries = INTERACTIVE_MENUS[name]
    while True:
        selected = select_option(
            title,
            [(key, label) for key, label, _target in entries],
            back="Exit" if name == "home" else "Back",
        )
        if selected is None:
            return False
        target = next(target for key, _label, target in entries if key == selected)
        if isinstance(target, str):
            if interactive_menu(args, target):
                return True
        elif interactive_command(args, target):
            return True


def command_menu(args: argparse.Namespace) -> None:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        parser().print_help()
        return
    if args.port:
        say(f"Selected device: {args.port}")
    try:
        interactive_menu(args)
    except ToolError as exc:
        if not isinstance(exc.__cause__, EOFError):
            raise


class FriendlyArgumentParser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._positionals.title = "Arguments"
        self._optionals.title = "Options"
        for action in self._actions:
            if isinstance(action, argparse._HelpAction):
                action.help = "Show the help text and exit the CLI."

    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        suggestion = ""
        choice = re.search(r"invalid choice: '([^']+)' \(choose from (.+)\)", message)
        if choice:
            candidates = re.findall(r"'([^']+)'", choice[2])
            matches = difflib.get_close_matches(choice[1], candidates, n=1)
            if matches:
                suggestion = f"Did you mean '{matches[0]}'?\n"
        self.exit(2, f"Error: {message}\n{suggestion}Run '{self.prog} --help' for syntax and command examples.\n")


def bounded_integer(minimum: int, maximum: int):
    def parse(value: str) -> int:
        try:
            number = int(value)
            if minimum <= number <= maximum:
                return number
        except ValueError:
            pass
        raise argparse.ArgumentTypeError(f"Use an integer from {minimum} to {maximum}. Received '{value}'.")
    return parse


def command_ports(args: argparse.Namespace) -> None:
    ports = detect_ports()
    if args.json:
        say(json.dumps(ports))
    elif ports:
        for port in ports:
            say(port)
        say("Run 'tinytouch --port PATH' to select a device.")
    else:
        say("No USB serial devices were found. Connect tinyTouch with a USB cable that supports data and try again.")


def command_help(args: argparse.Namespace) -> None:
    if args.topic:
        parser().parse_args([args.topic, "--help"])
    else:
        parser().print_help()


def prepare_piv_discovery(port: str, *, refresh: bool = False) -> bool | None:
    """Expose for pairing; return whether USB resets, or None for legacy mode."""
    with foreground_session(port):
        device = status(port)
        if device.get("piv_touch_active") != "on":
            return None
        if device.get("mode") != "piv" or device.get("piv") != "ready":
            raise ToolError("Select PIV mode and create a PIV identity before pairing.")
        visible = device.get("piv_visible") == "yes"
        if visible and not refresh:
            return False
        unlock(port, reason="make the PIV identity available for pairing")
        response = serial_command(port, "PIV OPEN", timeout=5)
    # Visibility may have expired while AUTH waited for the user's finger.
    # Use the device's decision at OPEN, not the earlier STATUS snapshot.
    if not any("reconnect=required" in line for line in response):
        return False
    # The device deliberately drains its reply before disconnecting CDC.
    time.sleep(1)
    fresh_status(port, {"piv_visible": "yes"})
    return True


def parser() -> argparse.ArgumentParser:
    parser = FriendlyArgumentParser(
        prog="tinytouch",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Configure tinyTouch. Run 'tinytouch' without a command to open the interactive menu.",
        epilog=("Examples:\n  tinytouch                         Open the interactive menu\n  tinytouch config                  Show saved settings\n  tinytouch config list             List values and limits\n  tinytouch led preset ocean        Apply an LED theme\n  tinytouch led color idle purple   Set the idle ring color\n  tinytouch enroll 2                 Enroll one complete finger\n  tinytouch --verbose test           Diagnose a connection\n\nRun 'tinytouch COMMAND --help' for syntax and command examples.\nAfter enrollment, changes require fingerprint approval. The new lighting controls require firmware 0.1.34-dev.1 or later."),
    )
    parser.add_argument("--verbose", action="store_true", help="Show command and device diagnostics.")
    parser.add_argument("--port", help="Use this USB serial device for commands and the interactive menu.")
    parser.add_argument("--version", action="version", version=f"tinyTouch CLI {CLI_VERSION}",
                        help="Show the CLI version and exit the CLI.")
    parser.set_defaults(func=command_menu)
    sub = parser.add_subparsers(dest="command", title="Commands", metavar="COMMAND")
    menu = sub.add_parser("menu", help="Open the interactive menu.")
    menu.add_argument("--port", default=argparse.SUPPRESS, help="Use this USB serial path instead of the global --port value.")
    menu.set_defaults(func=command_menu)
    setup = sub.add_parser("setup", help="Set up fingerprints and HID or PIV authentication on this Mac.")
    setup.add_argument("--mode", choices=("hid", "piv"), help="Select HID password entry or PIV smart card authentication. If omitted, the command prompts for a mode.")
    setup.add_argument("--port", default=argparse.SUPPRESS, help="Use this USB serial path instead of the global --port value.")
    setup.add_argument("--skip-enroll", action="store_true", help="Skip initial enrollment and keep the existing fingerprints.")
    setup.add_argument("--no-pair", action="store_true", help="Skip the macOS PIV pairing step during setup.")
    setup.set_defaults(func=command_setup)
    uninstall = sub.add_parser("uninstall", help="Remove the background service.")
    uninstall.set_defaults(func=command_uninstall)
    repair = sub.add_parser(
        "repair", help="repair Keychain access and reinstall the current HID helper"
    )
    repair.add_argument("--port")
    repair.set_defaults(func=command_repair)
    password = sub.add_parser("password", help="Change the password typed on this Mac.")
    password.add_argument("--finger", type=int, choices=range(1, 11),
                          help="Set one password for all four views of this finger.")
    password.add_argument("--port", default=argparse.SUPPRESS,
                          help="Use this USB serial path instead of the global --port value.")
    password.set_defaults(func=command_password)
    layout = sub.add_parser("keyboard-layout", help="Read or change HID keyboard translation.")
    layout.add_argument("layout", nargs="?", choices=("auto", "us"),
                        help="Use the active macOS layout with auto, or US key positions with us.")
    layout.add_argument("--port", default=argparse.SUPPRESS,
                        help="Use this USB serial path instead of the global --port value.")
    layout.set_defaults(func=command_keyboard_layout)
    upgrade_helper = sub.add_parser("_upgrade-helper", help=argparse.SUPPRESS)
    upgrade_helper.add_argument("--port")
    upgrade_helper.set_defaults(func=command_upgrade_helper)
    mode = sub.add_parser("mode", help="Switch the device between HID and PIV mode.")
    mode.add_argument("mode", choices=("hid", "piv"))
    mode.add_argument("--port", default=argparse.SUPPRESS, help="Use this USB serial path instead of the global --port value.")
    mode.set_defaults(func=command_mode)
    led = sub.add_parser("led", help="Configure sensor ring colors, effects, presets, and previews.",
                         formatter_class=argparse.RawDescriptionHelpFormatter,
                         description="View or change the sensor ring settings. The on, off, and only-auth commands remain available.",
                         epilog="Colors: " + ", ".join(LED_COLORS) + "\nEffects: " + ", ".join(LED_EFFECTS) +
                         "\nExamples:\n  tinytouch led on\n  tinytouch led color idle purple\n  tinytouch led effect breathe\n  tinytouch led preset ocean\n  tinytouch led preview cyan --effect flash --duration-ms 2000\n\nPresets keep the current lighting mode. Preview restores the saved lighting settings without saving changes.\nRun 'tinytouch config led_idle_cycles 0' for continuous animation. Colors use fixed RGB combinations. This command format does not support hex colors or brightness control.")
    led.add_argument("--port", default=argparse.SUPPRESS, help="Use this USB serial path instead of the global --port value.")
    led.set_defaults(func=command_led)
    piv_touch = sub.add_parser("piv-touch", help="allow password entry in PIV mode by hiding the card until touch")
    piv_touch.add_argument("state", choices=("on", "off"))
    piv_touch.add_argument("--port")
    piv_touch.set_defaults(func=command_piv_touch)
    led_actions = led.add_subparsers(dest="state", metavar="ACTION")
    for action, description in (("on", "Enable all sensor lighting."), ("off", "Disable all sensor lighting."),
                                ("only-auth", "Show only success and failure feedback on the sensor ring."),
                                ("show", "Show current lighting settings."),
                                ("color", "Set a sensor ring color by role."), ("effect", "Set the idle sensor ring animation."),
                                ("preset", "Apply an LED color preset."), ("preview", "Temporarily display a sensor ring color and effect.")):
        entry = led_actions.add_parser(action, help=description, description=description)
        entry.add_argument("--port", default=argparse.SUPPRESS, help="Use this USB serial path instead of the global --port value.")
        if action == "color":
            entry.add_argument("role", choices=("idle", "success", "failure", "end"))
            entry.add_argument("color", choices=tuple(LED_COLORS), help="Select a fixed RGB color.")
        elif action == "effect":
            entry.add_argument("effect_name", choices=tuple(LED_EFFECTS))
        elif action == "preset":
            entry.add_argument("preset_name", choices=tuple(LED_PRESETS))
        elif action == "preview":
            entry.add_argument("preview_color", choices=tuple(LED_COLORS))
            entry.add_argument("--effect", choices=tuple(LED_EFFECTS), default="steady")
            entry.add_argument("--duration-ms", type=bounded_integer(100, 5000), default=1500, help="Set the preview duration to 100–5000 ms. The default is 1500 ms.")
    config_help = "Settings (name: values; default):\n" + "\n".join(
        f"  {name}: {spec.allowed()}; {spec.default}\n    {spec.description}" for name, spec in SETTINGS.items())
    config = sub.add_parser("config", aliases=["settings"], help="Show, list, read, or change the device settings.",
                            formatter_class=argparse.RawDescriptionHelpFormatter,
                            description="With no arguments, show the current settings. Use 'list' to list settings without connecting a device. Use NAME to read a setting. Use NAME VALUE to change it.",
                            epilog=config_help + "\n\nExamples:\n  tinytouch config --json\n  tinytouch config led_idle_color purple\n  tinytouch config submit_enter off\n  tinytouch config piv_auto_type off\n  tinytouch config typing_delay_ms 7")
    config.add_argument("name", nargs="?", help="Select a setting name, 'show', or 'list'.")
    config.add_argument("value", nargs="?", help="Set a new value. Omit the value to read the current setting.")
    config.add_argument("--json", action="store_true", help="Show current values as JSON.")
    config.add_argument("--port", default=argparse.SUPPRESS, help="Use this USB serial path instead of the global --port value.")
    config.set_defaults(func=command_config)
    enroll_cmd = sub.add_parser("enroll", help="Enroll all four views for a finger numbered 1–10.")
    enroll_cmd.add_argument("finger", type=int, choices=range(1, 11), help="Select a fingerprint block. Each block contains four views of one finger.")
    enroll_cmd.add_argument("--replace", action="store_true", help="Replace this fingerprint block without a confirmation prompt.")
    enroll_cmd.add_argument("--port", default=argparse.SUPPRESS, help="Use this USB serial path instead of the global --port value.")
    enroll_cmd.set_defaults(func=command_enroll)
    delete = sub.add_parser("delete", help="Delete all views for a finger numbered 1–10.")
    delete.add_argument("finger", type=int, choices=range(1, 11), help="Delete all fingerprint templates in the selected block.")
    delete.add_argument("--port", default=argparse.SUPPRESS, help="Use this USB serial path instead of the global --port value.")
    delete.set_defaults(func=command_delete)
    fingers = sub.add_parser("fingers", help="List occupied fingerprint blocks and available enrollment capacity.")
    fingers.add_argument("--port", default=argparse.SUPPRESS, help="Use this USB serial path instead of the global --port value.")
    fingers.set_defaults(func=command_fingers)
    computers = sub.add_parser("computers", help="List or remove the registered HID computers.")
    computers.add_argument("action", choices=("list", "remove"), nargs="?", default="list")
    computers.add_argument("host_id", nargs="?")
    computers.add_argument("--remove", dest="remove_flag")
    computers.add_argument("--port", default=argparse.SUPPRESS, help="Use this USB serial path instead of the global --port value.")
    def normalize_computers(args: argparse.Namespace) -> None:
        args.remove = args.remove_flag or args.host_id
        if args.action == "list" and args.remove:
            raise ToolError("Use 'tinytouch computers remove HOST_ID'.")
        if args.action == "remove" and not args.remove:
            raise ToolError("Specify a registered computer with 'tinytouch computers remove HOST_ID'.")
        command_computers(args)
    computers.set_defaults(func=normalize_computers)
    reset = sub.add_parser("factory-reset", help="Clear fingerprints, keys, registered computers, and device settings.")
    reset.add_argument("--port", default=argparse.SUPPRESS, help="Use this USB serial path instead of the global --port value.")
    reset.set_defaults(func=command_factory_reset)
    update = sub.add_parser("update", help="Update the CLI, HID helper, and device firmware.")
    update.add_argument("--port", default=argparse.SUPPRESS, help="Use this USB serial path instead of the global --port value.")
    update.add_argument("--firmware-only", action="store_true", help=argparse.SUPPRESS)
    update.add_argument("--release-version", help=argparse.SUPPRESS)
    update.set_defaults(func=command_update)
    rom = sub.add_parser(
        "rom",
        aliases=["bootloader"],
        help="Show the physical ROM bootloader instructions.",
    )
    rom.add_argument(
        "--port",
        default=argparse.SUPPRESS,
        help="Use this USB serial path instead of the global --port value.",
    )
    rom.set_defaults(func=command_rom)
    status_cmd = sub.add_parser("status", help="Show device status as JSON.")
    status_cmd.add_argument(
        "--port",
        default=argparse.SUPPRESS,
        help="Use this USB serial path instead of the global --port value.",
    )
    status_view = status_cmd.add_mutually_exclusive_group()
    status_view.add_argument(
        "--summary", action="store_true", help="Show a short, readable summary."
    )
    status_view.add_argument(
        "--details",
        action="store_true",
        help="Show all status fields with readable labels.",
    )
    status_cmd.set_defaults(func=command_status)
    logs = sub.add_parser(
        "logs", help="Show the device event log without starting fingerprint capture."
    )
    logs.add_argument(
        "--port",
        default=argparse.SUPPRESS,
        help="Use this USB serial path instead of the global --port value.",
    )
    logs.set_defaults(func=command_logs)
    test = sub.add_parser("test", help="Check the USB serial connection.")
    test.add_argument(
        "--port",
        default=argparse.SUPPRESS,
        help="Use this USB serial path instead of the global --port value.",
    )
    test.set_defaults(func=command_test)
    keys = sub.add_parser("keys", help="Create a PIV identity on the device.")
    keys.add_argument("--port", default=argparse.SUPPRESS, help="Use this USB serial path instead of the global --port value.")
    keys.set_defaults(func=command_keys)
    pair = sub.add_parser("pair", help="Pair the PIV identity with the current macOS user.")
    pair.add_argument("--port", default=argparse.SUPPRESS, help="Use this USB serial path instead of the global --port value.")
    pair.set_defaults(func=command_pair)
    hid_smoke = sub.add_parser("hid-smoke", help="Test the HID helper without connecting a physical device.")
    hid_smoke.set_defaults(func=command_hid_smoke)
    enroll_demo = sub.add_parser("enroll-demo", help="Preview fingerprint enrollment without connecting a device.")
    enroll_demo.set_defaults(func=command_enroll_demo)
    ports = sub.add_parser("ports", help="List USB serial ports without opening a device connection.")
    ports.add_argument("--json", action="store_true", help="Show USB serial paths as JSON.")
    ports.set_defaults(func=command_ports)
    help_cmd = sub.add_parser("help", help="Show general or command help.")
    help_cmd.add_argument("topic", nargs="?", help="Select a command to explain.")
    help_cmd.set_defaults(func=command_help)
    examples = {
        "menu": ("Choose an action with the arrow keys and Enter. The CLI exits after the action finishes.", "tinytouch\ntinytouch menu --port /dev/cu.usbmodem101"),
        "setup": ("Configure this Mac. Enroll a fingerprint if the sensor is empty. Keep any existing fingerprint enrollment.", "tinytouch setup\ntinytouch setup --mode hid\ntinytouch setup --mode piv --no-pair"),
        "mode": ("Change the device mode after fingerprint approval. Reconnect when prompted. The command checks the active mode.", "tinytouch mode hid\ntinytouch mode piv"),
        "enroll": ("Enroll the left, right, top, and center views of the same finger. Confirm replacement before using an occupied fingerprint block.", "tinytouch enroll 2\ntinytouch enroll 2 --replace"),
        "delete": ("Delete all views in the selected fingerprint block after fingerprint approval. Keep the other fingerprint blocks.", "tinytouch delete 2"),
        "fingers": ("Read occupied fingerprint blocks, partial enrollment, pending cleanup, and available capacity. Keep the existing enrollment.", "tinytouch fingers"),
        "computers": ("List or remove registered HID computers. Use HID setup to add this Mac. Removing the last computer selects PIV mode.", "tinytouch computers\ntinytouch computers remove HOST_ID\ntinytouch setup --mode hid"),
        "factory-reset": ("Clear fingerprints, PIV identities, registered computers, device settings, and local pairing. Confirm the reset and approve it with an enrolled fingerprint.", "tinytouch factory-reset"),
        "update": ("Update the CLI, HID helper, and firmware from one verified release. Reconnect after the firmware update is staged.", "tinytouch update"),
        "uninstall": ("Stop and remove the background service. Saved credentials and the CLI stay installed.", "tinytouch uninstall"),
        "rom": ("Show the physical ROM bootloader instructions. This command does not flash the device.", "tinytouch rom"),
        "status": ("Show full device status as JSON, or use --summary for a short overview.", "tinytouch status --summary\ntinytouch status"),
        "test": ("Check USB serial communication and device status. Use --verbose to show protocol diagnostics.", "tinytouch test\ntinytouch --verbose test\ntinytouch ports"),
        "logs": ("Read recent touch and HID events without starting fingerprint capture.", "tinytouch logs"),
        "keys": ("Create a PIV identity after fingerprint approval. Use 'setup' for the full macOS configuration process.", "tinytouch keys"),
        "pair": ("Pair the PIV identity with the current macOS user. Authorize macOS as an administrator and approve pairing with an enrolled fingerprint.", "tinytouch pair"),
        "enroll-demo": ("Preview enrollment without connecting a device. Press Tab to simulate a tap. Press q to exit.", "tinytouch enroll-demo"),
        "ports": ("List available USB serial paths without opening a device connection. Use --port PATH to select a device for a command.", "tinytouch ports\ntinytouch ports --json\ntinytouch --port /dev/cu.usbmodem101 status"),
    }
    for name, (description, commands) in examples.items():
        entry = sub.choices[name]
        entry.description = description
        entry.epilog = "Examples:\n" + "\n".join(f"  {line}" for line in commands.splitlines())
        entry.formatter_class = argparse.RawDescriptionHelpFormatter
    return parser


def main() -> int:
    global VERBOSE
    if len(sys.argv) > 1 and sys.argv[1] == "_package_test":
        package_test()
        return 0
    if len(sys.argv) > 1 and sys.argv[1] == "_network_test":
        try:
            network_test()
        except ToolError as exc:
            say(f"network error: {exc}")
            return 1
        return 0
    if len(sys.argv) > 1 and sys.argv[1] == "_helper":
        from tinytouch_helper import main as helper_main  # type: ignore
        sys.argv = [str(HELPER), *sys.argv[2:]]
        helper_main()
        return 0
    args = parser().parse_args()
    VERBOSE = args.verbose
    if args.command != "help" and (args.command or (sys.stdin.isatty() and sys.stdout.isatty())):
        show_startup_mark(args.command or "menu")
    try:
        args.func(args)
    except KeyboardInterrupt:
        say("Cancelled.")
        return 130
    except ToolError as exc:
        print(terminal_style(f"Error: {exc}", "31"), file=sys.stderr, flush=True)
        return 1
    return 0


def chime(name: str) -> None:
    """Play optional sensor feedback without blocking the serial exchange."""
    global _sound_process
    if sys.platform != "darwin" or os.environ.get("TINYTOUCH_NO_SOUND"):
        return
    sound = Path("/System/Library/Sounds") / f"{name}.aiff"
    if not sound.is_file():
        return
    if _sound_process is not None and _sound_process.poll() is None:
        return
    try:
        _sound_process = subprocess.Popen(
            ["/usr/bin/afplay", str(sound)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass


if __name__ == "__main__":
    raise SystemExit(main())

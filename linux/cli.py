#!/usr/bin/env python3
"""KDE/Wayland HID CLI, sharing device transport with the macOS CLI."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "macos"))
sys.path.insert(0, str(ROOT / "linux"))
spec = importlib.util.spec_from_file_location("tinytouch_device_cli", ROOT / "macos/cli.py")
device = importlib.util.module_from_spec(spec)
spec.loader.exec_module(device)
import tinytouch_helper as helper
from tinytouch_linux import desktop, service, secrets
from tinytouch_runtime import atomic_write_json


def require_session():
    if (os.environ.get("XDG_SESSION_TYPE") != "wayland"
            or "KDE" not in os.environ.get("XDG_CURRENT_DESKTOP", "").upper().split(":")):
        raise device.ToolError("Run this port inside your KDE Plasma Wayland session.")


def save_settings(account, update):
    path = helper.settings_path(account)
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, UnicodeError, json.JSONDecodeError):
        settings = {}
    if not isinstance(settings, dict):
        settings = {}
    settings.update(update)
    atomic_write_json(path, settings)


def setup(args):
    require_session()
    if not args.us_keyboard:
        raise device.ToolError("Select a US keyboard layout in KDE, then use setup --us-keyboard.")
    port = device.choose_port(args.port)
    # Capture/validate the password before any device or credential changes.
    device.prepare_hid_password()
    try:
        with device.foreground_session(port):
            info = device.status(port)
            device.protocol6(info)
            device.sensor_ready(info)
            account = device.device_account(port)
            device.enroll(port, False)
            device.unlock(port, reason="configure HID mode")
            device.configure_hid(port, info)
            save_settings(account, {"keyboard_layout": "us"})
            if info.get("mode") != "hid":
                device.unlock(port, reason="switch to HID mode")
                device.serial_command(port, "SET MODE HID", timeout=4)
                print("HID mode selected. Unplug/reconnect tinyTouch, then run setup --us-keyboard again.")
                return
        service.install()
        print("Ready. The helper starts with your KDE session. Keep a US keyboard layout selected.")
    finally:
        if device._setup_password is not None:
            device._setup_password[:] = bytes(len(device._setup_password))
            device._setup_password = None


def resolve_app(value):
    app = desktop.normalize_app_id(value)
    if app is None:
        raise device.ToolError("Use the app ID printed by 'tinytouch app', without a file path.")
    # Validate with the same conservative format that shared settings accepts.
    return app


def password(args):
    if args.remove and not args.app:
        raise device.ToolError("Use --remove with --app.")
    if args.list and (args.app or args.finger or args.remove):
        raise device.ToolError("Use --list by itself.")
    account = device.device_account(device.choose_port(args.port))
    if args.list:
        device.list_passwords(account)
        return
    app = resolve_app(args.app) if args.app else None
    target = (helper.app_password_account(account, app, args.finger or 0) if app else
              helper.finger_password_account(account, args.finger) if args.finger else account)
    value = None if args.remove else device.prompt_hid_password()
    if value is not None:
        try:
            helper.translate_password(value.encode("utf-8"), None)
        except ValueError:
            raise device.ToolError("Use printable US ASCII, at most 160 typed keys.") from None
    with device.hid_settings_change():
        if helper.load_settings(account)["keyboard_layout"] != "us":
            raise device.ToolError("Run setup --us-keyboard before changing passwords.")
        if not helper.credentials_exist(account):
            raise device.ToolError("Run setup --us-keyboard before changing passwords.")
        if args.remove:
            secrets.delete_password(helper.SERVICE, target)
        else:
            secrets.set_password(helper.SERVICE, target, value)
        if app:
            device.update_app_password_settings(account, app, args.finger or 0, not args.remove)
    print("Password removed." if args.remove else "Password saved.")


def show_app(args):
    require_session()
    if not 0 <= args.delay <= 60:
        raise device.ToolError("Use a delay between 0 and 60 seconds.")
    if args.delay:
        print(f"Focus the target app within {args.delay:g} seconds.", flush=True)
        time.sleep(args.delay)
    app = desktop.frontmost_app()
    if app is None:
        raise device.ToolError("No app ID available. Unlock KDE and check that KWin scripting is enabled.")
    print(app)


def parser():
    result = argparse.ArgumentParser(description="tinyTouch HID for KDE Plasma / Wayland")
    result.add_argument("--port")
    sub = result.add_subparsers(dest="command", required=True)
    setup_cmd = sub.add_parser("setup", help="Pair HID and install the user helper")
    setup_cmd.add_argument("--us-keyboard", action="store_true")
    setup_cmd.set_defaults(func=setup)
    pw = sub.add_parser("password", help="Save default, finger or app passwords")
    pw.add_argument("--app")
    pw.add_argument("--finger", type=int, choices=range(1, 11))
    pw.add_argument("--remove", action="store_true")
    pw.add_argument("--list", action="store_true")
    pw.set_defaults(func=password)
    app = sub.add_parser("app", help="Print the focused app's ID")
    app.add_argument("--delay", type=float, default=0, choices=None)
    app.set_defaults(func=show_app)
    status = sub.add_parser("status")
    status.set_defaults(func=lambda args: print(json.dumps(device.status(device.choose_port(args.port)), indent=2)))
    ports = sub.add_parser("ports")
    ports.set_defaults(func=lambda args: print("\n".join(device.detect_ports()) or "No tinyTouch connected."))
    fingers = sub.add_parser("fingers")
    fingers.set_defaults(func=device.command_fingers)
    enroll = sub.add_parser("enroll")
    enroll.add_argument("finger", type=int, choices=range(1, 11))
    enroll.add_argument("--replace", action="store_true")
    enroll.set_defaults(func=device.command_enroll)
    delete = sub.add_parser("delete")
    delete.add_argument("finger", type=int, choices=range(1, 11))
    delete.set_defaults(func=device.command_delete)
    for cmd in (setup_cmd, pw, status, ports, fingers, enroll, delete):
        cmd.add_argument("--port", default=argparse.SUPPRESS)
    return result


def main():
    args = parser().parse_args()
    try:
        args.func(args)
    except (device.ToolError, secrets.KeychainError, OSError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Cancelled.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

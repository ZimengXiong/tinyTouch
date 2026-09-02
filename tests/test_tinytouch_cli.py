"""Focused protocol-6 tests for the host state machine."""

import base64
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("tinytouch_cli", str(ROOT / "tinytouch"))
spec = importlib.util.spec_from_loader(loader.name, loader)
cli = importlib.util.module_from_spec(spec)
loader.exec_module(cli)


class ProtocolSixTests(unittest.TestCase):
    def test_protocol_six_is_required(self):
        cli.protocol6({"firmware": "unified", "protocol": "6"})
        with self.assertRaisesRegex(cli.ToolError, "protocol 6"):
            cli.protocol6({"firmware": "unified", "protocol": "5"})

    def test_protocol_six_requires_a_firmware_version(self):
        with self.assertRaisesRegex(cli.ToolError, "did not report a firmware version"):
            cli.protocol6({"protocol": "6"})

    def test_protocol_six_terminal_responses_are_grouped_by_command(self):
        self.assertTrue(cli.is_terminal("SET MODE HID", "OK SET MODE"))
        self.assertTrue(cli.is_terminal("HOST ADD AABB 00", "OK HOST ADD"))
        self.assertTrue(cli.is_terminal("FINGER DELETE 1", "OK FINGER"))
        self.assertFalse(cli.is_terminal("SET MODE HID", "OK STATUS mode=hid"))

    def test_auth_failure_explains_whether_touch_started(self):
        self.assertIn("expired", cli.human_error("ERR AUTH", touch_prompted=True))
        self.assertIn("did not start", cli.human_error("ERR AUTH"))

    def test_status_requires_a_terminal_status_line(self):
        with mock.patch.object(cli, "serial_command", return_value=["OK STATUS protocol=6 mode=hid sensor=ready hosts=1"]):
            result = cli.status("/dev/cu.TT-1234")
        self.assertEqual(result["protocol"], "6")
        self.assertEqual(result["hosts"], "1")

    def test_hid_add_is_live_and_does_not_provision_piv(self):
        computer = "test-mac"
        key = hashlib.sha256(
            f"tinyTouch HID pairing|TT-1234|{computer}".encode("utf-8")
        ).digest()
        commands = []
        identifier = cli.host_id(key)
        registered = set()

        def exchange(_port, command, **_kwargs):
            commands.append(command)
            if command == "HOST LIST":
                ids = ",".join(sorted(registered)) or "none"
                return [f"OK HOST LIST ids={ids} capacity=8"]
            if command.startswith("HOST ADD "):
                registered.add(identifier)
                return ["OK HOST ADD"]
            if command == "STATUS":
                return ["OK STATUS protocol=6 firmware=unified mode=piv sensor=ready hosts=1"]
            return ["OK AUTH"]

        with (
            mock.patch.object(cli, "prepare_hid_password"),
            mock.patch.object(cli, "keychain_get", return_value=None),
            mock.patch.object(cli, "device_account", return_value="TT-1234"),
            mock.patch.object(cli, "keychain_exists", return_value=True),
            mock.patch.object(cli, "keychain_set"),
            mock.patch.object(cli, "password_for", return_value="test-password"),
            mock.patch.object(cli, "serial_command", side_effect=exchange),
            mock.patch.object(cli, "install_helper"),
            mock.patch.object(cli, "helper_loaded", return_value=True),
            mock.patch.object(cli.platform, "node", return_value=computer),
        ):
            cli.configure_hid("/dev/cu.TT-1234", {"mode": "piv", "hosts": "0"})

        self.assertIn(f"HOST ADD {identifier} {key.hex()}", commands)
        self.assertNotIn("PROVISION_BEGIN", " ".join(commands))
        self.assertNotIn("HOST ADD", " ".join(command for command in commands if command == "HOST LIST"))

    def test_piv_setup_stops_the_hid_helper_before_authorization(self):
        args = SimpleNamespace(port="/dev/cu.TT-1234", mode="piv", skip_enroll=True, no_pair=True)
        calls = []
        device = {
            "firmware": "unified", "protocol": "6", "mode": "hid", "sensor": "ready",
            "piv": "ready", "fingerprints": "0",
        }
        with (
            mock.patch.object(cli, "require_macos"),
            mock.patch.object(cli, "choose_mode", return_value="piv"),
            mock.patch.object(cli, "choose_port", return_value=args.port),
            mock.patch.object(cli, "status", return_value=device),
            mock.patch.object(cli, "protocol6"),
            mock.patch.object(cli, "sensor_ready"),
            mock.patch.object(cli, "foreground_session", return_value=mock.MagicMock(
                __enter__=mock.Mock(return_value=None),
                __exit__=mock.Mock(return_value=False),
            )),
            mock.patch.object(cli, "remove_helper", side_effect=lambda: calls.append("remove_helper")),
            mock.patch.object(cli, "unlock", side_effect=lambda _port: calls.append("unlock")),
            mock.patch.object(cli, "serial_command", return_value=["OK SET MODE"]),
            mock.patch.object(cli, "fresh_status", return_value={"mode": "piv"}),
            mock.patch.object(cli, "enroll"),
            mock.patch.object(cli, "notify"),
            mock.patch.object(cli, "wait_for_reconnect", side_effect=RuntimeError("stop after mode change")),
        ):
            with self.assertRaisesRegex(RuntimeError, "stop after mode change"):
                cli.command_setup(args)
        self.assertEqual(calls, ["remove_helper", "unlock"])

    def test_hid_host_list_preserves_eight_host_capacity(self):
        with mock.patch.object(
            cli, "serial_command",
            return_value=["OK HOST LIST ids=0011223344556677,8899AABBCCDDEEFF capacity=8"],
        ):
            identifiers, capacity = cli.host_list("/dev/cu.TT-1234")
        self.assertEqual(capacity, 8)
        self.assertEqual(len(identifiers), 2)

    def test_factory_reset_requires_live_clear_before_local_cleanup(self):
        args = SimpleNamespace(port="/dev/cu.TT-1234")
        statuses = iter([
            {"firmware": "unified", "protocol": "6", "mode": "hid", "sensor": "ready", "hosts": "1", "fingerprints": "1"},
            {"firmware": "unified", "protocol": "6", "mode": "piv", "sensor": "ready", "hosts": "0", "fingerprints": "0"},
        ])
        calls = []
        with (
            mock.patch.object(cli, "choose_port", return_value=args.port),
            mock.patch.object(cli, "status", side_effect=lambda _port: next(statuses)),
            mock.patch.object(cli, "protocol6"),
            mock.patch.object(cli, "ask", return_value="y"),
            mock.patch.object(cli, "unlock"),
            mock.patch.object(cli, "serial_command", side_effect=lambda _p, command, **_k: calls.append(command) or ["OK RESET FACTORY"]),
            mock.patch.object(cli, "remove_helper"),
            mock.patch.object(cli, "device_account", return_value="TT-1234"),
            mock.patch.object(cli, "keychain_delete"),
        ):
            cli.command_factory_reset(args)
        self.assertEqual(calls, ["RESET FACTORY"])

    def test_mode_verifies_the_live_mode_without_reconnect_command(self):
        args = SimpleNamespace(port="/dev/cu.TT-1234", mode="hid")
        calls = []
        with (
            mock.patch.object(cli, "choose_port", return_value=args.port),
            mock.patch.object(cli, "status", side_effect=[
                {"firmware": "unified", "protocol": "6", "mode": "piv", "sensor": "ready", "hosts": "1"},
                {"firmware": "unified", "protocol": "6", "mode": "hid", "sensor": "ready", "hosts": "1"},
            ]),
            mock.patch.object(cli, "protocol6"),
            mock.patch.object(cli, "unlock"),
            mock.patch.object(cli, "serial_command", side_effect=lambda _p, command, **_k: calls.append(command) or ["OK SET MODE"]),
            mock.patch.object(cli, "wait_for_reconnect", return_value=args.port),
            mock.patch.object(cli, "fresh_status", return_value={"mode": "hid"}),
            mock.patch.object(cli, "notify"),
        ):
            cli.command_mode(args)
        self.assertEqual(calls, ["SET MODE HID"])
        self.assertFalse(any("RESET" in command or "RECONNECT" in command for command in calls))

    def test_ota_staging_uses_inactive_slot_and_requires_power_cycle(self):
        try:
            import serial  # type: ignore
        except ImportError:
            self.skipTest("pyserial is not installed")

        writes = []

        class FakeSerial:
            def __init__(self, *_args, **_kwargs):
                self.responses = []

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def write(self, payload):
                command = payload.decode("ascii").strip()
                writes.append(command)
                words = command.split()
                if words[:2] == ["OTA", "BEGIN"]:
                    self.responses.append(b"OK OTA BEGIN next=0\n")
                elif words[:2] == ["OTA", "WRITE"]:
                    offset = int(words[3])
                    size = len(base64.b64decode(words[4]))
                    self.responses.append(f"OK OTA WRITE next={offset + size}\n".encode())
                elif words[:2] == ["OTA", "COMMIT"]:
                    self.responses.append(b"OK OTA STAGED power_cycle=required\n")

            def flush(self):
                pass

            def readline(self):
                return self.responses.pop(0) if self.responses else b""

        image = bytes(range(256)) * 2
        digest = hashlib.sha256(image).hexdigest()
        with (
            mock.patch.object(serial, "Serial", FakeSerial),
            mock.patch.object(cli, "serial_command", return_value=["OK AUTH"]),
            mock.patch.object(cli, "unload_helper", return_value=False),
        ):
            cli.stage_ota("/dev/cu.TT-1234", image, digest)
        self.assertTrue(writes[0].startswith("OTA BEGIN "))
        self.assertTrue(writes[-1].startswith("OTA COMMIT "))
        self.assertNotIn("RESET", " ".join(writes))

    def test_rom_flow_only_prompts_for_a_physical_reconnect(self):
        args = SimpleNamespace(port=None)
        with mock.patch.object(cli, "notify") as notify, mock.patch.object(cli, "say") as say:
            cli.command_rom(args)
        notify.assert_called_once()
        self.assertIn("physical reconnect", " ".join(call.args[0] for call in say.call_args_list))

    def test_helper_has_no_legacy_default_device_identity(self):
        source = (ROOT / "software" / "macos-helper" / "tinytouch_helper.py").read_text()
        self.assertNotIn("PREFERRED_SERIAL", source)
        self.assertNotIn("protocol-v5-compatible", source)


if __name__ == "__main__":
    unittest.main()

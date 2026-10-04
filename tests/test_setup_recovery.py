"""Exercise complete HID setup with simulated USB and credential storage."""

import contextlib
import importlib.util
import io
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("setup_recovery_cli", ROOT / "macos/cli.py")
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)

OLD_PORT = "/dev/cu.TT-OLD"
NEW_PORT = "/dev/cu.TT-NEW"
ACCOUNT = "TT-TEST-DEVICE"


class SetupRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.mode = "piv"
        self.hosts = set()
        self.credentials = {}
        self.commands = []
        self.sessions = []
        self.output = io.StringIO()
        self.enterContext(contextlib.redirect_stdout(self.output))
        self.enterContext(mock.patch.object(cli, "require_macos"))
        self.enterContext(mock.patch.object(cli, "choose_port", side_effect=lambda explicit: explicit or OLD_PORT))
        self.enterContext(mock.patch.object(cli, "device_account", return_value=ACCOUNT))
        self.enterContext(mock.patch.object(cli, "foreground_session", side_effect=self.session))
        self.enterContext(mock.patch.object(cli, "serial_command", side_effect=self.exchange))
        self.enterContext(mock.patch.object(cli, "keychain_get", side_effect=lambda service, _account: self.credentials.get(service)))
        self.enterContext(mock.patch.object(cli, "keychain_set", side_effect=self.save))
        self.enterContext(mock.patch.object(cli, "keychain_delete", side_effect=lambda service, _account: self.credentials.pop(service, None)))
        self.enterContext(mock.patch.object(cli, "_setup_password", None))
        self.prepare = self.enterContext(mock.patch.object(cli, "prepare_hid_password", side_effect=self.password))
        self.enterContext(mock.patch.object(cli, "notify"))
        self.remove = self.enterContext(mock.patch.object(cli, "remove_helper"))
        self.reconnect = self.enterContext(mock.patch.object(cli, "wait_for_reconnect", return_value=NEW_PORT))
        self.install = self.enterContext(mock.patch.object(cli, "install_helper"))
        self.enterContext(mock.patch.object(cli, "helper_loaded", return_value=True))
        self.args = cli.parser().parse_args(["setup", "--mode", "hid", "--skip-enroll"])

    @contextlib.contextmanager
    def session(self, port):
        self.sessions.append(port)
        yield port

    def password(self):
        cli._setup_password = bytearray(b"test-login-password")

    def save(self, service, account, value):
        self.assertEqual(account, ACCOUNT)
        self.credentials[service] = value

    def exchange(self, port, command, **_kwargs):
        self.commands.append((port, command))
        if command == "STATUS":
            return [
                f"OK STATUS firmware=0.1.34 protocol=6 mode={self.mode} "
                f"sensor=ready fingerprints=4 hosts={len(self.hosts)} piv=ready",
            ]
        if command == "AUTH":
            return ["OK AUTH"]
        if command == "HOST LIST":
            return [f"OK HOST LIST ids={','.join(sorted(self.hosts))} capacity=8"]
        if command.startswith("HOST ADD "):
            self.hosts.add(command.split()[2])
            return ["OK HOST ADD"]
        if command.startswith("HOST REMOVE "):
            self.hosts.remove(command.split()[2])
            return ["OK HOST REMOVE"]
        if command == "SET MODE HID":
            self.mode = "hid"
            return ["OK SET MODE"]
        raise AssertionError(f"Unexpected simulated command: {command}")

    def assert_usable_host(self):
        key = cli.hid_pairing_key(self.credentials[cli.PAIRING_SERVICE])
        self.assertEqual(self.hosts, {cli.host_id(key)})
        self.assertEqual(self.credentials[cli.PASSWORD_SERVICE], "test-login-password")

    def test_first_setup_creates_usable_host_before_reconnect_timeout(self):
        self.reconnect.side_effect = cli.ToolError("Timed out waiting for USB")
        with self.assertRaisesRegex(cli.ToolError, "tinytouch setup --mode hid"):
            cli.command_setup(self.args)
        self.assert_usable_host()
        self.assertEqual(self.mode, "hid")
        self.install.assert_not_called()
        self.assertNotIn("Ready (HID)", self.output.getvalue())

    def test_setup_resumes_after_the_user_reconnects_following_timeout(self):
        self.reconnect.side_effect = cli.ToolError("Timed out waiting for USB")
        with self.assertRaises(cli.ToolError):
            cli.command_setup(self.args)
        saved = dict(self.credentials)
        self.reconnect.side_effect = None
        self.args.port = NEW_PORT
        cli.command_setup(self.args)
        self.assert_usable_host()
        self.assertEqual(self.credentials, saved)
        add_commands = [command for _port, command in self.commands if command.startswith("HOST ADD ")]
        self.assertEqual(len(add_commands), 1)
        self.install.assert_called_once()
        self.assertIn("Ready (HID)", self.output.getvalue())

    def test_first_setup_survives_a_changed_usb_port_without_repeating_pairing(self):
        cli.command_setup(self.args)
        self.assert_usable_host()
        self.assertEqual(self.sessions, [OLD_PORT, NEW_PORT])
        commands = [command for _port, command in self.commands]
        add = next(index for index, command in enumerate(commands) if command.startswith("HOST ADD "))
        self.assertLess(add, commands.index("SET MODE HID"))
        self.prepare.assert_called_once()
        self.remove.assert_not_called()
        self.install.assert_called_once()

    def test_missing_host_after_reconnect_cannot_start_helper(self):
        def reconnect(_port):
            self.hosts.clear()
            return NEW_PORT

        self.reconnect.side_effect = reconnect
        with self.assertRaisesRegex(cli.ToolError, "This Mac is not registered"):
            cli.command_setup(self.args)
        self.install.assert_not_called()
        self.assertNotIn("Ready (HID)", self.output.getvalue())

    def test_invalid_inventory_stops_without_changing_mode_or_credentials(self):
        def invalid_inventory(port, command, **kwargs):
            if command == "HOST LIST":
                return ["OK HOST LIST capacity=8"]
            return self.exchange(port, command, **kwargs)

        with mock.patch.object(cli, "serial_command", side_effect=invalid_inventory):
            with self.assertRaisesRegex(cli.ToolError, "invalid HID computer inventory"):
                cli.command_setup(self.args)
        self.assertEqual(self.credentials, {})
        self.assertEqual(self.mode, "piv")
        self.assertEqual(self.hosts, set())
        self.install.assert_not_called()


if __name__ == "__main__":
    unittest.main()

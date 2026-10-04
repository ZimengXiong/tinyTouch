"""Device identity checks during USB reconnects and HID setup."""

import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "setup_identity_cli", ROOT / "macos" / "cli.py"
)
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)

OLD_PORT = "/dev/cu.usbmodem1001"
NEW_PORT = "/dev/cu.usbmodem1003"
IDENTITY = "TT-B8F862FB478C"


def usb_port(device=NEW_PORT, serial_number=IDENTITY):
    return SimpleNamespace(device=device, serial_number=serial_number)


class SetupIdentityTests(unittest.TestCase):
    def setUp(self):
        service_root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for name, value in (
            ("LAUNCH_AGENT", service_root / "agent.plist"),
            ("HELPER_SUSPEND", service_root / "suspend"),
            ("HELPER_SUSPEND_ACK", service_root / "ack"),
            ("_helper_suppressed", False),
        ):
            self.enterContext(mock.patch.object(cli, name, value))
        self.now = 0.0
        self.ports = mock.Mock()
        self.enterContext(
            mock.patch.dict(cli.sys.modules, {"tinytouch_ports": self.ports})
        )
        self.enterContext(mock.patch.dict(cli.os.environ, {}, clear=True))
        self.enterContext(mock.patch.object(cli, "_active_serial", None))
        self.enterContext(
            mock.patch.object(cli.time, "monotonic", side_effect=lambda: self.now)
        )
        self.enterContext(mock.patch.object(cli.time, "sleep", side_effect=self.sleep))

    def sleep(self, seconds):
        self.now += seconds

    def test_lookup_uses_the_open_connection_after_port_changes(self):
        cli._active_serial = SimpleNamespace(port=NEW_PORT)
        self.ports.comports.return_value = [usb_port()]
        self.assertEqual(cli.device_account(OLD_PORT), IDENTITY)

    def test_lookup_does_not_use_another_connected_devices_identity(self):
        cli._active_serial = SimpleNamespace(port=NEW_PORT)
        self.ports.comports.return_value = [
            usb_port(OLD_PORT, "TT-OTHER"),
            usb_port(),
        ]
        self.assertEqual(cli.device_account(OLD_PORT), IDENTITY)

    def test_lookup_waits_for_the_target_port_to_appear(self):
        self.ports.comports.side_effect = [[], [usb_port()]]
        self.assertEqual(cli.device_account(NEW_PORT), IDENTITY)
        self.assertGreater(self.now, 0)
        self.assertEqual(self.ports.comports.call_count, 2)

    def test_lookup_waits_for_serial_metadata_to_appear(self):
        self.ports.comports.side_effect = [
            [usb_port(serial_number=None)],
            [usb_port(serial_number="")],
            [usb_port()],
        ]
        self.assertEqual(cli.device_account(NEW_PORT), IDENTITY)
        self.assertEqual(self.ports.comports.call_count, 3)

    def test_lookup_retries_a_transient_metadata_error(self):
        self.ports.comports.side_effect = [OSError("USB reconnecting"), [usb_port()]]
        self.assertEqual(cli.device_account(NEW_PORT), IDENTITY)
        self.assertEqual(self.ports.comports.call_count, 2)

    def test_lookup_times_out_without_using_a_different_device(self):
        self.ports.comports.return_value = [usb_port(OLD_PORT, "TT-OTHER")]
        with self.assertRaisesRegex(cli.ToolError, "stable USB serial identity"):
            cli.device_account(NEW_PORT)
        self.assertGreaterEqual(self.now, 6)
        self.assertLess(self.now, 6.3)

    def test_lookup_times_out_if_the_serial_number_remains_empty(self):
        self.ports.comports.return_value = [usb_port(serial_number=None)]
        with self.assertRaisesRegex(cli.ToolError, "stable USB serial identity"):
            cli.device_account(NEW_PORT)
        self.assertGreaterEqual(self.now, 6)
        self.assertLess(self.now, 6.3)

    def test_lookup_outside_a_session_keeps_the_requested_port(self):
        self.ports.comports.return_value = [usb_port()]
        self.assertEqual(cli.device_account(NEW_PORT), IDENTITY)

    def test_explicit_smoke_test_identity_does_not_scan_usb(self):
        with mock.patch.dict(cli.os.environ, {"TINYTOUCH_DEVICE_ACCOUNT": "TT-SMOKE"}):
            self.assertEqual(cli.device_account(OLD_PORT), "TT-SMOKE")
        self.ports.comports.assert_not_called()

    def test_missing_identity_stops_before_password_or_pairing_changes(self):
        self.ports.comports.return_value = []
        with (
            mock.patch.object(cli, "prepare_hid_password") as password,
            mock.patch.object(cli, "serial_command") as command,
            mock.patch.object(cli, "keychain_set") as save,
        ):
            with self.assertRaisesRegex(cli.ToolError, "stable USB serial identity"):
                cli.configure_hid(NEW_PORT, {"mode": "hid"})
        password.assert_not_called()
        command.assert_not_called()
        save.assert_not_called()

    def test_first_setup_succeeds_after_port_and_metadata_change(self):
        connection = mock.Mock(port=NEW_PORT)
        serial = SimpleNamespace(Serial=mock.Mock(return_value=connection))
        self.ports.comports.side_effect = [
            [usb_port(serial_number=None)],
            [usb_port()],
        ]
        events = []
        credentials = {}

        def save(service, account, value):
            self.assertEqual(account, IDENTITY)
            events.append(service)
            credentials[service] = value

        def password(account):
            self.assertEqual(account, IDENTITY)
            events.append("saved password")
            credentials[cli.PASSWORD_SERVICE] = "test-password"

        with (
            mock.patch.dict(cli.sys.modules, {"serial": serial}),
            mock.patch.object(cli, "foreground_helper"),
            mock.patch.object(cli, "unload_helper", return_value=False),
            mock.patch.object(cli, "current_port", return_value=NEW_PORT),
            mock.patch.object(cli, "exchange_serial", return_value=["OK PING"]),
            mock.patch.object(
                cli,
                "prepare_hid_password",
                side_effect=lambda: events.append("password prompt"),
            ),
            mock.patch.object(cli, "host_id", return_value="registered-host"),
            mock.patch.object(cli, "host_list", return_value=({"registered-host"}, 8)),
            mock.patch.object(cli, "keychain_get", side_effect=lambda service, _account: credentials.get(service)),
            mock.patch.object(cli, "keychain_set", side_effect=save),
            mock.patch.object(cli, "password_for", side_effect=password),
            mock.patch.object(cli, "status", return_value={"hosts": "1"}),
        ):
            with cli.foreground_session(OLD_PORT) as opened_port:
                self.assertEqual(opened_port, NEW_PORT)
                cli.configure_hid(OLD_PORT, {"mode": "hid"})

        self.assertEqual(
            events, ["password prompt", cli.PAIRING_SERVICE, "saved password"]
        )
        serial.Serial.assert_called_once_with(
            NEW_PORT, 115200, timeout=0.25, write_timeout=2
        )
        connection.close.assert_called_once()
        self.assertIsNone(cli._active_serial)


if __name__ == "__main__":
    unittest.main()

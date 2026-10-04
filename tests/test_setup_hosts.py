"""HID setup validates the local host before reporting success."""

import contextlib
import importlib.util
import io
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("setup_hosts_cli", ROOT / "macos/cli.py")
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)

PORT = "/dev/cu.TT-TEST"
ACCOUNT = "TT-TEST"
KEY = bytes(range(32))
IDENTIFIER = cli.host_id(KEY)
DEVICE = {
    "firmware": "0.1.34", "protocol": "6", "sensor": "ready",
    "mode": "hid", "hosts": "1", "fingerprints": "4",
}


class HostValidationTests(unittest.TestCase):
    def setUp(self):
        self.credentials = {
            cli.PAIRING_SERVICE: KEY.hex(), cli.PASSWORD_SERVICE: "saved-password",
        }
        self.enterContext(mock.patch.object(cli, "device_account", return_value=ACCOUNT))
        self.enterContext(mock.patch.object(
            cli, "keychain_get", side_effect=lambda service, _account: self.credentials.get(service),
        ))
        self.inventory = self.enterContext(mock.patch.object(
            cli, "host_list", return_value=({IDENTIFIER}, 8),
        ))

    def test_saved_key_matches_a_registered_host(self):
        cli.verify_hid_host(PORT, DEVICE)

    def test_another_computers_host_does_not_make_this_mac_ready(self):
        self.inventory.return_value = ({"a" * 16}, 8)
        with self.assertRaisesRegex(cli.ToolError, "This Mac is not registered"):
            cli.verify_hid_host(PORT, DEVICE)

    def test_missing_or_invalid_count_cannot_report_success(self):
        for count in (None, "0", "-1", "garbage", "2"):
            with self.subTest(count=count):
                device = dict(DEVICE)
                if count is None:
                    device.pop("hosts")
                else:
                    device["hosts"] = count
                with self.assertRaises(cli.ToolError):
                    cli.verify_hid_host(PORT, device)

    def test_missing_or_invalid_local_credentials_cannot_report_success(self):
        for service, value in (
            (cli.PAIRING_SERVICE, None), (cli.PAIRING_SERVICE, "not-hex"),
            (cli.PAIRING_SERVICE, "ab"), (cli.PASSWORD_SERVICE, None),
            (cli.PASSWORD_SERVICE, ""), (cli.PASSWORD_SERVICE, "é" * 81),
        ):
            with self.subTest(service=service, value=value):
                old = self.credentials[service]
                self.credentials[service] = value
                with self.assertRaises(cli.ToolError):
                    cli.verify_hid_host(PORT, DEVICE)
                self.credentials[service] = old

    def test_setup_rechecks_the_host_after_configuration(self):
        output = io.StringIO()
        with (
            contextlib.redirect_stdout(output),
            mock.patch.object(cli, "require_macos"),
            mock.patch.object(cli, "choose_port", return_value=PORT),
            mock.patch.object(cli, "remove_helper"),
            mock.patch.object(cli, "foreground_session"),
            mock.patch.object(cli, "status", return_value={**DEVICE, "hosts": "0"}),
            mock.patch.object(cli, "unlock"),
            mock.patch.object(cli, "configure_hid"),
            mock.patch.object(cli, "enroll"),
            mock.patch.object(cli, "install_helper") as install,
        ):
            args = cli.parser().parse_args(["setup", "--mode", "hid", "--skip-enroll"])
            with self.assertRaises(cli.ToolError):
                cli.command_setup(args)
        install.assert_not_called()
        self.assertNotIn("Ready (HID)", output.getvalue())


class HostInventoryTests(unittest.TestCase):
    def test_valid_inventory_normalizes_identifiers(self):
        with mock.patch.object(cli, "serial_command", return_value=[
            f"OK HOST LIST ids={IDENTIFIER.upper()} capacity=8",
        ]):
            self.assertEqual(cli.host_list(PORT), ({IDENTIFIER}, 8))

    def test_empty_inventory_is_explicit(self):
        with mock.patch.object(cli, "serial_command", return_value=[
            "OK HOST LIST ids=none capacity=8",
        ]):
            self.assertEqual(cli.host_list(PORT), (set(), 8))

    def test_malformed_inventory_never_becomes_an_empty_host_list(self):
        for response in (
            "OK HOST", "OK HOST LISTING ids=none capacity=8",
            "OK HOST LIST capacity=8", "OK HOST LIST ids=none",
            "OK HOST LIST ids=none capacity=0", "OK HOST LIST ids=none capacity=-1",
            "OK HOST LIST ids=none capacity=9", "OK HOST LIST ids=none capacity=bad",
            "OK HOST LIST ids=bad capacity=8",
            f"OK HOST LIST ids={IDENTIFIER},{IDENTIFIER} capacity=8",
            f"OK HOST LIST ids={IDENTIFIER},{'a' * 16} capacity=1",
        ):
            with self.subTest(response=response), mock.patch.object(
                cli, "serial_command", return_value=[response],
            ):
                with self.assertRaises(cli.ToolError):
                    cli.host_list(PORT)


if __name__ == "__main__":
    unittest.main()

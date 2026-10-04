"""Keep the simulated HID diagnostic separate from saved Mac setup."""

import contextlib
import io
import unittest
from unittest import mock

from test_tinytouch_cli import cli
import tinytouch_helper as helper


class HidSmokeTests(unittest.TestCase):
    def setUp(self):
        self.output = io.StringIO()
        self.enterContext(contextlib.redirect_stdout(self.output))
        self.enterContext(mock.patch.object(cli, "require_macos"))
        for module, names in (
            (cli, ("remove_helper", "install_helper", "keychain_get",
                   "keychain_set", "keychain_delete", "command_setup",
                   "foreground_session", "run")),
            (helper, ("keychain_get", "pairing_keychain_get", "save_state",
                      "diagnostic")),
        ):
            for name in names:
                self.enterContext(mock.patch.object(
                    module, name,
                    side_effect=AssertionError(f"Diagnostic touched saved setup: {name}"),
                ))

    def test_protocol_round_trip_uses_only_a_simulated_serial_device(self):
        with mock.patch.object(helper, "handle_event", wraps=helper.handle_event) as event:
            cli.command_hid_smoke(cli.argparse.Namespace())
        self.assertEqual(event.call_count, 2)
        self.assertTrue(event.call_args_list[0].args[0].startswith("EV "))
        self.assertTrue(event.call_args_list[1].args[0].startswith("EV2 "))
        self.assertIn("HID helper protocol test passed", self.output.getvalue())
        self.assertNotIn("smoke test password", self.output.getvalue())

    def test_invalid_encrypted_response_cannot_report_success(self):
        with mock.patch.object(helper, "handle_event", return_value="PW invalid\n"):
            with self.assertRaises(cli.ToolError):
                cli.command_hid_smoke(cli.argparse.Namespace())
        self.assertNotIn("test passed", self.output.getvalue())

    def test_transport_failure_cannot_report_success(self):
        with mock.patch.object(cli, "exchange_serial", side_effect=cli.ToolError("timeout")):
            with self.assertRaisesRegex(cli.ToolError, "timeout"):
                cli.command_hid_smoke(cli.argparse.Namespace())
        self.assertNotIn("test passed", self.output.getvalue())


if __name__ == "__main__":
    unittest.main()

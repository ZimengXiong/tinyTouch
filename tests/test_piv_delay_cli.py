"""Verify authorization, validation, and readback of PIV timing changes."""
import unittest
from unittest import mock

from test_tinytouch_cli import cli


class PivDelayCliTests(unittest.TestCase):
    def device(self):
        return {"firmware": "0.1.31", "protocol": "6", "piv_delay_ms": "50"}

    def test_saved_values_are_authorized_and_verified(self):
        for value, expected in (("0", "0"), ("50", "50"), ("0100", "100"), ("5000", "5000")):
            with (
                self.subTest(value=value),
                mock.patch.object(cli, "choose_port", return_value="TT"),
                mock.patch.object(cli, "status", return_value=self.device()),
                mock.patch.object(cli, "unlock") as unlock,
                mock.patch.object(cli, "serial_command") as command,
                mock.patch.object(cli, "fresh_status") as verify,
                mock.patch.object(cli, "say"),
            ):
                args = cli.parser().parse_args(["config", "piv_delay_ms", value])
                args.func(args)
                unlock.assert_called_once_with("TT", reason="change this setting")
                command.assert_called_once_with("TT", f"SET PIV_DELAY {expected}", timeout=4)
                verify.assert_called_once_with("TT", {"piv_delay_ms": expected})

    def test_invalid_values_never_request_a_fingerprint_or_write(self):
        for value in ("-1", "5001", "65536", "50.5", "abc", "５０", ""):
            with (
                self.subTest(value=value),
                mock.patch.object(cli, "choose_port", return_value="TT"),
                mock.patch.object(cli, "status", return_value=self.device()),
                mock.patch.object(cli, "unlock") as unlock,
                mock.patch.object(cli, "serial_command") as command,
            ):
                args = cli.parser().parse_args(["config", "piv_delay_ms", value])
                with self.assertRaisesRegex(cli.ToolError, "0 to 5000"):
                    args.func(args)
                unlock.assert_not_called()
                command.assert_not_called()

    def test_old_firmware_is_rejected_before_authorization(self):
        with (
            mock.patch.object(cli, "choose_port", return_value="TT"),
            mock.patch.object(cli, "status", return_value={"firmware": "0.1.30", "protocol": "6"}),
            mock.patch.object(cli, "unlock") as unlock,
            mock.patch.object(cli, "serial_command") as command,
        ):
            args = cli.parser().parse_args(["config", "piv_delay_ms", "50"])
            with self.assertRaisesRegex(cli.ToolError, "does not support"):
                args.func(args)
            unlock.assert_not_called()
            command.assert_not_called()

    def test_failed_save_and_mismatched_readback_never_claim_success(self):
        for failed_write in (True, False):
            with (
                self.subTest(failed_write=failed_write),
                mock.patch.object(cli, "choose_port", return_value="TT"),
                mock.patch.object(cli, "status", side_effect=[self.device(), {"piv_delay_ms": "50"}]),
                mock.patch.object(cli, "unlock"),
                mock.patch.object(cli, "serial_command", side_effect=cli.ToolError("ERR SET") if failed_write else None),
                mock.patch.object(cli, "say") as output,
            ):
                args = cli.parser().parse_args(["config", "piv_delay_ms", "100"])
                with self.assertRaises(cli.ToolError):
                    args.func(args)
                output.assert_not_called()

    def test_read_only_status_needs_no_authorization(self):
        with (
            mock.patch.object(cli, "choose_port", return_value="TT"),
            mock.patch.object(cli, "status", return_value=self.device()),
            mock.patch.object(cli, "unlock") as unlock,
            mock.patch.object(cli, "serial_command") as command,
            mock.patch.object(cli, "say") as output,
        ):
            args = cli.parser().parse_args(["config", "piv_delay_ms"])
            args.func(args)
            unlock.assert_not_called()
            command.assert_not_called()
            self.assertIn('"piv_delay_ms": "50"', output.call_args.args[0])

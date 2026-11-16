"""Optional PIV PIN: prompts stay off the command line and failures never claim success."""
import unittest
from unittest import mock

from test_tinytouch_cli import cli

READY = {"firmware": "0.1.30", "protocol": "6", "mode": "piv", "piv": "ready"}


def run(action, device, pins, serial=None, fresh=None):
    with (
        mock.patch.object(cli, "choose_port", return_value="TT"),
        mock.patch.object(cli, "foreground_session"),
        mock.patch.object(cli, "status", return_value=device),
        mock.patch.object(cli.getpass, "getpass", side_effect=pins),
        mock.patch.object(cli, "unlock") as unlock,
        mock.patch.object(cli, "serial_command", side_effect=serial) as command,
        mock.patch.object(cli, "fresh_status", side_effect=fresh) as verify,
        mock.patch.object(cli, "say") as output,
    ):
        args = cli.parser().parse_args(["piv-pin", action])
        error = None
        try:
            args.func(args)
        except cli.ToolError as exc:
            error = exc
        return error, unlock, command, verify, output


class PivPinCliTests(unittest.TestCase):
    def test_set_change_and_remove_send_the_entered_pins_and_verify_state(self):
        for action, state, pins, sent, expected in (
            ("set", "off", ["24681012", "24681012"], "PIV PIN SET 24681012", "on"),
            ("change", "on", ["246810", "135790", "135790"], "PIV PIN CHANGE 246810 135790", "on"),
            ("change", "required", ["246810", "135790", "135790"], "PIV PIN CHANGE 246810 135790", "on"),
            ("off", "on", ["246810"], "PIV PIN CLEAR 246810", "off"),
            ("unlock", "required", ["246810"], "PIV PIN UNLOCK 246810", "on"),
        ):
            with self.subTest(action=action, state=state):
                error, unlock, command, verify, _ = run(action, {**READY, "piv_pin": state}, pins)
                self.assertIsNone(error)
                unlock.assert_called_once()
                command.assert_called_once_with("TT", sent, timeout=10)
                verify.assert_called_once_with("TT", {"piv_pin": expected})

    def test_bad_input_is_rejected_before_fingerprint_approval(self):
        for action, state, pins, message in (
            ("set", "off", ["246810", "246811"], "did not match"),
            ("set", "off", ["12345", "12345"], "6 to 8 digits"),
            ("set", "off", ["24681a", "24681a"], "6 to 8 digits"),
            ("set", "off", ["111111", "111111"], "other than 111111"),
            ("set", "on", [], "already set"),
            ("change", "off", [], "No PIV PIN"),
            ("unlock", "on", [], "not required"),
            ("off", "blocked", [], "factory-reset"),
            ("change", "locked", [], "Try again in 30 seconds"),
        ):
            with self.subTest(pins=pins, state=state):
                device = {**READY, "piv_pin": state, "piv_pin_wait_s": "30"}
                error, unlock, command, _, output = run(action, device, pins)
                self.assertRegex(str(error), message)
                unlock.assert_not_called()
                command.assert_not_called()
                self.assertNotIn("saved", " ".join(c.args[0] for c in output.call_args_list))

    def test_unsupported_firmware_or_missing_identity_stops_early(self):
        for device, message in (
            (READY, "does not support"),
            ({**READY, "piv": "missing", "piv_pin": "off"}, "Create the PIV identity"),
        ):
            with self.subTest(device=device):
                error, unlock, *_ = run("set", device, [])
                self.assertRegex(str(error), message)
                unlock.assert_not_called()

    def test_device_rejection_reports_remaining_attempts_without_success(self):
        failure = cli.ToolError(cli.human_error("ERR PIV PIN wrong retries=3"))
        error, _, _, verify, output = run(
            "off", {**READY, "piv_pin": "on"}, ["000000"], serial=failure)
        self.assertIn("3 attempts remain", str(error))
        verify.assert_not_called()
        output.assert_not_called()

    def test_device_errors_map_to_next_actions(self):
        self.assertIn("factory-reset", cli.human_error("ERR PIV PIN blocked"))
        self.assertIn("Try again in 600 seconds", cli.human_error("ERR PIV PIN locked wait=600"))
        self.assertIn("setup --mode piv", cli.human_error("ERR PIV PIN not_ready"))
        self.assertIn("try again", cli.human_error("ERR PIV PIN storage"))


if __name__ == "__main__":
    unittest.main()

"""User setting and temporary discovery during PIV pairing."""
import unittest
from unittest import mock

from test_tinytouch_cli import cli


class PivTouchCliTests(unittest.TestCase):
    def test_setting_saves_verifies_and_requests_reconnect(self):
        for state, value in (("on", 1), ("off", 0)):
            with (
                self.subTest(state=state),
                mock.patch.object(cli, "choose_port", return_value="TT"),
                mock.patch.object(cli, "foreground_session"),
                mock.patch.object(cli, "status", side_effect=[
                    {"firmware": "0.1.30", "protocol": "6", "piv_touch": "off"}, {"piv_touch": state},
                ]),
                mock.patch.object(cli, "unlock") as unlock,
                mock.patch.object(cli, "serial_command") as command,
                mock.patch.object(cli, "say") as output,
            ):
                args = cli.parser().parse_args(["piv-touch", state])
                args.func(args)
                unlock.assert_called_once()
                command.assert_called_once_with("TT", f"SET PIV_TOUCH {value}", timeout=5)
                self.assertIn("reconnect", " ".join(c.args[0] for c in output.call_args_list))

    def test_old_firmware_and_failed_save_never_claim_success(self):
        for device, error in (({"firmware": "0.1.30", "protocol": "6"}, "does not support"),
                              ({"firmware": "0.1.30", "protocol": "6", "piv_touch": "off"}, "ERR SET")):
            with (
                self.subTest(device=device),
                mock.patch.object(cli, "choose_port", return_value="TT"),
                mock.patch.object(cli, "foreground_session"),
                mock.patch.object(cli, "status", return_value=device),
                mock.patch.object(cli, "unlock") as unlock,
                mock.patch.object(cli, "serial_command", side_effect=cli.ToolError("ERR SET")),
                mock.patch.object(cli, "say") as output,
            ):
                args = cli.parser().parse_args(["piv-touch", "on"])
                with self.assertRaisesRegex(cli.ToolError, error):
                    args.func(args)
                output.assert_not_called()
                if "piv_touch" not in device:
                    unlock.assert_not_called()

    def test_hidden_pairing_identity_opens_after_authorization_and_closes_serial(self):
        events = []
        session = mock.MagicMock()
        session.__exit__.side_effect = lambda *a: events.append("close")
        with (
            mock.patch.object(cli, "foreground_session", return_value=session),
            mock.patch.object(cli, "status", return_value={
                "piv_touch_active": "on", "piv_visible": "no", "mode": "piv", "piv": "ready",
            }),
            mock.patch.object(cli, "unlock", side_effect=lambda *a, **k: events.append("auth")),
            mock.patch.object(cli, "serial_command", side_effect=lambda *a, **k:
                              events.append(a[1]) or ["OK PIV OPEN reconnect=required"]),
            mock.patch.object(cli.time, "sleep", side_effect=lambda *a: events.append("wait")),
            mock.patch.object(cli, "fresh_status", side_effect=lambda *a: events.append("status")),
        ):
            self.assertTrue(cli.prepare_piv_discovery("TT"))
        self.assertEqual(events, ["auth", "PIV OPEN", "close", "wait", "status"])

    def test_existing_firmware_or_visible_card_needs_no_open_command(self):
        for device in ({}, {"piv_touch_active": "off"}, {
            "piv_touch_active": "on", "piv_visible": "yes", "mode": "piv", "piv": "ready",
        }):
            with (
                self.subTest(device=device),
                mock.patch.object(cli, "foreground_session"),
                mock.patch.object(cli, "status", return_value=device),
                mock.patch.object(cli, "unlock") as unlock,
                mock.patch.object(cli, "serial_command") as command,
            ):
                result = cli.prepare_piv_discovery("TT")
                self.assertIs(result, False if device.get("piv_visible") == "yes" else None)
                unlock.assert_not_called()
                command.assert_not_called()

    def test_pairing_refresh_renews_visible_card_without_serial_reconnect(self):
        with (
            mock.patch.object(cli, "foreground_session"),
            mock.patch.object(cli, "status", return_value={
                "piv_touch_active": "on", "piv_visible": "yes", "mode": "piv", "piv": "ready",
            }),
            mock.patch.object(cli, "unlock") as unlock,
            mock.patch.object(cli, "serial_command", return_value=["OK PIV OPEN reconnect=none"]) as command,
            mock.patch.object(cli.time, "sleep") as sleep,
        ):
            self.assertFalse(cli.prepare_piv_discovery("TT", refresh=True))
            unlock.assert_called_once()
            command.assert_called_once_with("TT", "PIV OPEN", timeout=5)
            sleep.assert_not_called()

    def test_pairing_waits_if_visibility_expired_during_authorization(self):
        with (
            mock.patch.object(cli, "foreground_session"),
            mock.patch.object(cli, "status", return_value={
                "piv_touch_active": "on", "piv_visible": "yes", "mode": "piv", "piv": "ready",
            }),
            mock.patch.object(cli, "unlock"),
            mock.patch.object(cli, "serial_command", return_value=["OK PIV OPEN reconnect=required"]),
            mock.patch.object(cli.time, "sleep") as sleep,
            mock.patch.object(cli, "fresh_status") as status,
        ):
            self.assertTrue(cli.prepare_piv_discovery("TT", refresh=True))
            sleep.assert_called_once()
            status.assert_called_once_with("TT", {"piv_visible": "yes"})


if __name__ == "__main__":
    unittest.main()

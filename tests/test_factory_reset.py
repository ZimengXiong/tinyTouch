"""Verify device reset before removing the Mac's saved setup."""

import unittest
from types import SimpleNamespace
from unittest import mock

from test_tinytouch_cli import cli


class FactoryResetTests(unittest.TestCase):
    def setUp(self):
        self.args = SimpleNamespace(port="TT")
        self.enterContext(mock.patch.object(cli, "choose_port", return_value="TT"))
        self.session = self.enterContext(mock.patch.object(cli, "foreground_session"))
        self.status = self.enterContext(mock.patch.object(cli, "status", side_effect=[
            {"firmware": "0.1.34", "protocol": "6", "fingerprints": "4"},
            {"fingerprints": "0", "hosts": "0", "piv": "unconfigured"},
        ]))
        self.confirm = self.enterContext(mock.patch.object(cli, "ask", return_value="y"))
        self.account = self.enterContext(
            mock.patch.object(cli, "device_account", return_value="TT-1234")
        )
        self.pairings = self.enterContext(
            mock.patch.object(cli, "paired_piv_identities", return_value=["A" * 40])
        )
        self.admin = self.enterContext(mock.patch.object(cli, "authorize_macos"))
        self.unlock = self.enterContext(mock.patch.object(cli, "unlock"))
        self.reset = self.enterContext(mock.patch.object(cli, "serial_command"))
        self.unpair = self.enterContext(mock.patch.object(cli, "run"))
        self.remove = self.enterContext(mock.patch.object(cli, "remove_helper"))
        self.credentials = self.enterContext(mock.patch.object(cli, "keychain_delete"))
        self.output = self.enterContext(mock.patch.object(cli, "say"))

    def assert_mac_setup_preserved(self):
        self.remove.assert_not_called()
        self.unpair.assert_not_called()
        self.credentials.assert_not_called()
        self.output.assert_not_called()

    def test_cancel_preserves_mac_setup(self):
        self.confirm.return_value = "no"
        with self.assertRaisesRegex(cli.ToolError, "cancelled"):
            cli.command_factory_reset(self.args)
        self.unlock.assert_not_called()
        self.reset.assert_not_called()
        self.assert_mac_setup_preserved()

    def test_failed_fingerprint_approval_preserves_mac_setup(self):
        self.unlock.side_effect = cli.ToolError("Fingerprint not recognized.")
        with self.assertRaisesRegex(cli.ToolError, "not recognized"):
            cli.command_factory_reset(self.args)
        self.reset.assert_not_called()
        self.assert_mac_setup_preserved()
        self.session.return_value.__exit__.assert_called_once()

    def test_failed_administrator_approval_preserves_mac_setup(self):
        self.admin.side_effect = cli.ToolError("Administrator approval failed.")
        with self.assertRaisesRegex(cli.ToolError, "approval failed"):
            cli.command_factory_reset(self.args)
        self.unlock.assert_not_called()
        self.reset.assert_not_called()
        self.assert_mac_setup_preserved()

    def test_rejected_reset_preserves_mac_setup(self):
        self.reset.side_effect = cli.ToolError("ERR RESET FACTORY")
        with self.assertRaisesRegex(cli.ToolError, "RESET FACTORY"):
            cli.command_factory_reset(self.args)
        self.assert_mac_setup_preserved()

    def test_failed_verification_preserves_mac_setup(self):
        for field, value in (("fingerprints", "1"), ("hosts", "1"), ("piv", "ready")):
            with self.subTest(field=field):
                self.status.side_effect = [
                    {"firmware": "0.1.34", "protocol": "6"},
                    {"fingerprints": "0", "hosts": "0", "piv": "unconfigured", field: value},
                ]
                with self.assertRaisesRegex(cli.ToolError, "verification failed"):
                    cli.command_factory_reset(self.args)
                self.assert_mac_setup_preserved()

    def test_disconnected_readback_preserves_mac_setup(self):
        self.status.side_effect = [
            {"firmware": "0.1.34", "protocol": "6"},
            cli.ToolError("Serial connection lost."),
        ]
        with self.assertRaisesRegex(cli.ToolError, "connection lost"):
            cli.command_factory_reset(self.args)
        self.assert_mac_setup_preserved()

    def test_success_authorizes_reset_before_mac_cleanup(self):
        events = []
        self.account.side_effect = lambda _p: events.append("account") or "TT-1234"
        self.pairings.side_effect = lambda: events.append("pairings") or ["A" * 40]
        self.admin.side_effect = lambda: events.append("admin")
        self.unlock.side_effect = lambda *_a, **_k: events.append("unlock")
        self.reset.side_effect = lambda *_a, **_k: events.append("reset")
        statuses = iter([
            {"firmware": "0.1.34", "protocol": "6"},
            {"fingerprints": "0", "hosts": "0", "piv": "unconfigured"},
        ])
        self.status.side_effect = lambda _p: events.append("status") or next(statuses)
        self.remove.side_effect = lambda: events.append("remove")
        self.unpair.side_effect = lambda *_a, **_k: events.append("unpair")
        self.credentials.side_effect = lambda *_a: events.append("credentials")
        cli.command_factory_reset(self.args)
        self.assertEqual(events, [
            "status", "account", "pairings", "admin", "unlock", "reset", "status",
            "remove", "unpair", "credentials", "credentials",
        ])
        self.session.assert_called_once_with("TT")
        self.reset.assert_called_once_with("TT", "RESET FACTORY", timeout=15)
        self.credentials.assert_has_calls([
            mock.call(cli.PAIRING_SERVICE, "TT-1234"),
            mock.call(cli.PASSWORD_SERVICE, "TT-1234"),
        ])
        self.output.assert_called_once_with("Factory reset complete.")

    def test_unpaired_device_needs_no_administrator_cleanup(self):
        self.pairings.return_value = []
        cli.command_factory_reset(self.args)
        self.admin.assert_not_called()
        self.unpair.assert_not_called()
        self.remove.assert_called_once()


if __name__ == "__main__":
    unittest.main()

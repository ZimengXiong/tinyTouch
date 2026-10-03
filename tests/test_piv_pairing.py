"""Verify PIV pairing separately from automatic login Keychain unlock."""

import unittest
from types import SimpleNamespace
from unittest import mock

from test_tinytouch_cli import cli

IDENTITY = "A" * 40
OTHER_IDENTITY = "B" * 40
KEYCHAIN_WARNING = (
    "User was successfully paired but user password will be required "
    "after next SmartCard login to unlock Login keychain."
)


class PivPairingTests(unittest.TestCase):
    def setUp(self):
        self.args = SimpleNamespace(port="TT")
        self.enterContext(mock.patch.object(cli, "require_macos"))
        self.enterContext(mock.patch.object(cli, "choose_port", return_value="TT"))
        self.enterContext(
            mock.patch.object(cli, "prepare_piv_discovery", return_value=None)
        )
        self.enterContext(
            mock.patch.object(
                cli, "wait_for_piv_identities", return_value=([], [IDENTITY])
            )
        )
        self.enterContext(mock.patch.object(cli, "authorize_macos"))
        self.enterContext(mock.patch.object(cli, "unlock"))
        self.output = self.enterContext(mock.patch.object(cli, "say"))
        self.paired = self.enterContext(
            mock.patch.object(cli, "user_piv_identities", return_value=[IDENTITY])
        )
        self.run = self.enterContext(
            mock.patch.object(
                cli,
                "run",
                return_value=SimpleNamespace(stdout=KEYCHAIN_WARNING, stderr=""),
            )
        )

    def test_verified_pairing_survives_a_keychain_warning_on_either_stream(self):
        for stdout, stderr in ((KEYCHAIN_WARNING, ""), ("", KEYCHAIN_WARNING)):
            with self.subTest(stdout=bool(stdout)):
                self.run.reset_mock()
                self.output.reset_mock()
                self.run.return_value = SimpleNamespace(stdout=stdout, stderr=stderr)
                cli.command_pair(self.args)
                self.run.assert_called_once()
                self.assertNotIn("unpair", self.run.call_args.args[0])
                messages = [call.args[0] for call in self.output.call_args_list]
                self.assertIn("PIV pairing with this Mac is complete.", messages)
                self.assertIn(
                    "Keychain needs your Mac password after the next PIV login.",
                    messages,
                )

    def test_warning_without_selected_user_pairing_is_still_an_error(self):
        self.paired.return_value = [OTHER_IDENTITY]
        with self.assertRaisesRegex(cli.ToolError, "could not confirm PIV pairing"):
            cli.command_pair(self.args)
        messages = [call.args[0] for call in self.output.call_args_list]
        self.assertNotIn("PIV pairing with this Mac is complete.", messages)
        self.run.assert_called_once()

    def test_failed_pairing_verification_does_not_report_success(self):
        self.paired.side_effect = cli.ToolError("Could not read PIV pairings.")
        with self.assertRaisesRegex(cli.ToolError, "Could not read PIV pairings"):
            cli.command_pair(self.args)
        messages = [call.args[0] for call in self.output.call_args_list]
        self.assertNotIn("PIV pairing with this Mac is complete.", messages)

    def test_success_without_a_keychain_warning_does_not_add_warning_text(self):
        self.run.return_value = SimpleNamespace(
            stdout="User was successfully paired.", stderr=""
        )
        cli.command_pair(self.args)
        messages = [call.args[0] for call in self.output.call_args_list]
        self.assertIn("PIV pairing with this Mac is complete.", messages)
        self.assertFalse(any("Keychain needs" in message for message in messages))

    def test_authentication_failure_is_not_treated_as_a_keychain_warning(self):
        self.run.side_effect = cli.ToolError("Authentication failed.")
        with mock.patch.object(cli, "piv_identities", return_value=([], [IDENTITY])):
            with self.assertRaisesRegex(cli.ToolError, "Authentication failed"):
                cli.command_pair(self.args)
        messages = [call.args[0] for call in self.output.call_args_list]
        self.assertNotIn("PIV pairing with this Mac is complete.", messages)


class UserPivIdentityTests(unittest.TestCase):
    def test_verification_reads_only_the_current_users_registered_hashes(self):
        result = SimpleNamespace(
            returncode=0,
            stdout=f"Hash: {IDENTITY.lower()}\nHash: {OTHER_IDENTITY}\n",
        )
        with (
            mock.patch.object(cli.getpass, "getuser", return_value="test-user"),
            mock.patch.object(cli.subprocess, "run", return_value=result) as command,
        ):
            self.assertEqual(cli.user_piv_identities(), [IDENTITY, OTHER_IDENTITY])
        command.assert_called_once_with(
            ["sc_auth", "list", "-u", "test-user"],
            check=False,
            text=True,
            capture_output=True,
        )

    def test_native_list_failure_cannot_confirm_pairing(self):
        result = SimpleNamespace(returncode=1, stdout=f"Hash: {IDENTITY}\n")
        with mock.patch.object(cli.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(cli.ToolError, "Could not read PIV pairings"):
                cli.user_piv_identities()


if __name__ == "__main__":
    unittest.main()

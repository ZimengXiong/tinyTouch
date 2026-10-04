"""Check host credential commands with mocked Keychain and service calls."""

import importlib.util
from pathlib import Path
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("hid_credentials_cli", ROOT / "macos/cli.py")
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)
import tinytouch_helper as helper


class HidPasswordCommandTests(unittest.TestCase):
    def setUp(self):
        self.account = "TT-123456ABCDEF"
        self.keychain = mock.Mock(KeychainError=helper.KeychainError)
        self.keychain.has_password.return_value = True
        self.activity = []
        patches = [
            mock.patch.object(cli, "require_macos"),
            mock.patch.object(cli, "choose_port", return_value="/dev/test"),
            mock.patch.object(cli, "device_account", return_value=self.account),
            mock.patch.object(cli, "_keychain", return_value=self.keychain),
            mock.patch.object(cli.getpass, "getpass", side_effect=["password", "password"]),
            mock.patch.object(helper, "load_settings", return_value={"keyboard_layout": "us"}),
            mock.patch.object(cli, "unload_helper",
                              side_effect=lambda: self.activity.append("stop") or True),
            mock.patch.object(cli, "load_helper", side_effect=lambda: self.activity.append("reload")),
            mock.patch.object(cli, "say"),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.keychain.set_password.side_effect = lambda *args: self.activity.append("save")

    def run_command(self, *arguments):
        args = cli.parser().parse_args(["password", *arguments])
        args.func(args)

    def test_default_update_reloads_cached_password_after_saving(self):
        self.run_command()
        self.keychain.set_password.assert_called_once_with(
            cli.PASSWORD_SERVICE, self.account, "password"
        )
        self.assertEqual(self.activity, ["stop", "save", "reload"])

    def test_finger_update_writes_one_group_item(self):
        self.run_command("--finger", "10")
        self.keychain.set_password.assert_called_once_with(
            cli.PASSWORD_SERVICE, helper.finger_password_account(self.account, 10), "password"
        )

    def test_failed_update_restores_existing_helper(self):
        self.keychain.set_password.side_effect = helper.KeychainError("update", -25293)
        with self.assertRaisesRegex(cli.ToolError, "tinytouch repair"):
            self.run_command()
        self.assertEqual(self.activity, ["stop", "reload"])

    def test_unconfigured_device_does_not_save_or_restart(self):
        self.keychain.has_password.return_value = False
        with self.assertRaisesRegex(cli.ToolError, "setup --mode hid"):
            self.run_command()
        self.keychain.set_password.assert_not_called()
        self.assertEqual(self.activity, [])

    def test_typo_does_not_save_or_restart(self):
        with mock.patch.object(cli.getpass, "getpass", side_effect=["password", "typo"]):
            with self.assertRaisesRegex(cli.ToolError, "matching passwords"):
                self.run_command()
        self.keychain.set_password.assert_not_called()
        self.assertEqual(self.activity, [])

    def test_unrepresentable_password_does_not_save_or_restart(self):
        with (
            mock.patch.object(cli.getpass, "getpass", side_effect=["é", "é"]),
            mock.patch.object(helper, "load_settings", return_value={"keyboard_layout": "auto"}),
            mock.patch.object(helper, "current_keyboard_output_map", return_value={"e": "e"}),
        ):
            with self.assertRaisesRegex(cli.ToolError, "cannot be typed"):
                self.run_command()
        self.keychain.set_password.assert_not_called()
        self.assertEqual(self.activity, [])

    def test_group_credential_is_included_in_access_repair(self):
        group = helper.finger_password_account(self.account, 10)
        self.keychain.has_password.side_effect = lambda service, name: name in {self.account, group}
        self.keychain.can_read_password.return_value = True
        with mock.patch.object(cli, "FROZEN", True), mock.patch.object(cli, "install_helper"):
            args = cli.parser().parse_args(["repair", "--port", "/dev/test"])
            args.func(args)
        self.keychain.can_read_password.assert_any_call(cli.PASSWORD_SERVICE, group)


class FingerPasswordLoadingTests(unittest.TestCase):
    def test_all_four_views_use_group_password_over_legacy_slot_values(self):
        account = "TT-123456ABCDEF"
        default, group = bytearray(b"default"), bytearray(b"group password")
        with (
            mock.patch.object(helper, "keychain_get",
                              side_effect=lambda name: default if name == account else group),
            mock.patch.object(helper, "has_password",
                              side_effect=lambda service, name: name.endswith(":group:2")) as exists,
        ):
            passwords = helper.load_passwords(account)
        self.assertEqual(set(passwords), {0, 5, 6, 7, 8})
        for slot in (5, 6, 7, 8):
            self.assertIs(passwords[slot], group)
            self.assertNotIn(mock.call(helper.SERVICE, helper.fingerprint_account(account, slot)),
                             exists.call_args_list)
        key = bytes(range(32))
        for slot in (5, 6, 7, 8):
            nonce = f"{slot:02x}" * 16
            signature = helper.mac_hex(key, f"EV|{nonce}|1|{slot}|42")
            with mock.patch.object(helper, "encrypt_password", return_value=("00" * 16, "aa")) as encrypt:
                helper.handle_event(f"EV {nonce} 1 {slot} 42 {signature}", passwords, key)
            self.assertEqual(encrypt.call_args.args[2], group)


if __name__ == "__main__":
    unittest.main()

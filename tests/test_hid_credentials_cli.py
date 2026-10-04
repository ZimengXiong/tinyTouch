"""Check host credential commands with mocked Keychain and service calls."""

import importlib.util
import json
from pathlib import Path
import tempfile
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

    def test_password_update_does_not_install_an_absent_helper(self):
        with mock.patch.object(cli, "unload_helper", return_value=False):
            self.run_command()
        self.assertEqual(self.activity, ["save"])

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

    def test_restart_failure_reports_that_the_credential_was_saved(self):
        with mock.patch.object(cli, "load_helper", side_effect=OSError("fixture failure")):
            with self.assertRaisesRegex(cli.ToolError, "was saved.*could not restart"):
                self.run_command()
        self.keychain.set_password.assert_called_once()
        self.assertEqual(self.activity, ["stop", "save"])

    def test_password_prompt_refuses_echo_fallback_and_closed_input(self):
        for error in (cli.getpass.GetPassWarning("echo unavailable"), EOFError()):
            with self.subTest(error=type(error).__name__):
                with mock.patch.object(cli.getpass, "getpass", side_effect=error):
                    with self.assertRaisesRegex(cli.ToolError, "interactive terminal"):
                        self.run_command()
        self.keychain.set_password.assert_not_called()
        self.assertEqual(self.activity, [])

    def test_native_layout_loading_failure_does_not_save_or_restart(self):
        with (
            mock.patch.object(helper, "load_settings", return_value={"keyboard_layout": "auto"}),
            mock.patch.object(helper, "current_keyboard_output_map", side_effect=OSError("fixture")),
        ):
            with self.assertRaisesRegex(cli.ToolError, "cannot be typed"):
                self.run_command()
        self.keychain.set_password.assert_not_called()
        self.assertEqual(self.activity, [])

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
    def test_later_group_denial_wipes_all_previously_loaded_passwords(self):
        default, group = bytearray(b"default"), bytearray(b"first group")
        with (
            mock.patch.object(helper, "keychain_get", side_effect=[
                default, group, helper.KeychainError("read", -25308),
            ]),
            mock.patch.object(helper, "has_password", return_value=True),
        ):
            with self.assertRaises(helper.KeychainError):
                helper.load_passwords("TT-123456ABCDEF")
        self.assertEqual(default, bytearray(len(default)))
        self.assertEqual(group, bytearray(len(group)))

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


class KeyboardLayoutCommandTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.account = "TT-123456ABCDEF"
        self.activity = []
        patches = [
            mock.patch.object(helper, "STATE_DIR", self.root),
            mock.patch.object(cli, "require_macos"),
            mock.patch.object(cli, "choose_port", return_value="/dev/test"),
            mock.patch.object(cli, "device_account", return_value=self.account),
            mock.patch.object(cli, "unload_helper",
                              side_effect=lambda: self.activity.append("stop") or True),
            mock.patch.object(cli, "load_helper", side_effect=lambda: self.activity.append("reload")),
            mock.patch.object(cli, "say"),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def run_command(self, *arguments):
        args = cli.parser().parse_args(["keyboard-layout", *arguments])
        args.func(args)

    def test_query_does_not_write_or_restart(self):
        self.run_command()
        self.assertEqual(self.activity, [])
        self.assertEqual(list(self.root.iterdir()), [])
        cli.say.assert_called_once_with("HID keyboard layout: auto.")

    def test_change_preserves_other_settings_and_scopes_the_device(self):
        path = helper.settings_path(self.account)
        path.write_text(json.dumps({"keyboard_layout": "us", "future_setting": 7}))
        other = helper.settings_path("TT-000000000000")
        other.write_text('{"keyboard_layout":"us"}')
        self.run_command("auto")
        self.assertEqual(json.loads(path.read_text()),
                         {"keyboard_layout": "auto", "future_setting": 7})
        self.assertEqual(other.read_text(), '{"keyboard_layout":"us"}')
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.activity, ["stop", "reload"])

    def test_failed_write_keeps_previous_settings_and_reloads_helper(self):
        path = helper.settings_path(self.account)
        path.write_text('{"keyboard_layout":"auto"}')
        with mock.patch.object(cli, "atomic_write_json", side_effect=OSError("fixture failure")):
            with self.assertRaisesRegex(cli.ToolError, "Could not save"):
                self.run_command("us")
        self.assertEqual(helper.load_settings(self.account), {"keyboard_layout": "auto"})
        self.assertEqual(self.activity, ["stop", "reload"])

    def test_explicit_layout_recovers_invalid_utf8_settings(self):
        helper.settings_path(self.account).write_bytes(b"\xff")
        self.assertEqual(helper.load_settings(self.account), {"keyboard_layout": "auto"})
        self.run_command("us")
        self.assertEqual(helper.load_settings(self.account), {"keyboard_layout": "us"})


if __name__ == "__main__":
    unittest.main()

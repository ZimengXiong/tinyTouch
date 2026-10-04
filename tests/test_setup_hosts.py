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
        for ids in ("", "none"):
            with self.subTest(ids=ids), mock.patch.object(cli, "serial_command", return_value=[
                f"OK HOST LIST ids={ids} capacity=8",
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


class SetupFlowTests(unittest.TestCase):
    def setUp(self):
        self.output = io.StringIO()
        self.enterContext(contextlib.redirect_stdout(self.output))
        self.enterContext(mock.patch.object(cli, "require_macos"))
        self.enterContext(mock.patch.object(cli, "choose_port", return_value=PORT))
        self.remove = self.enterContext(mock.patch.object(cli, "remove_helper"))
        self.session = self.enterContext(mock.patch.object(cli, "foreground_session"))
        self.account = self.enterContext(mock.patch.object(cli, "device_account", return_value=ACCOUNT))
        self.status = self.enterContext(mock.patch.object(cli, "status", return_value=DEVICE))
        self.fresh = self.enterContext(mock.patch.object(cli, "fresh_status", return_value=DEVICE))
        self.enterContext(mock.patch.object(cli, "unlock"))
        self.events = []
        self.configure = self.enterContext(mock.patch.object(cli, "configure_hid", side_effect=lambda *_args: self.events.append("configure")))
        self.command = self.enterContext(mock.patch.object(cli, "serial_command", side_effect=lambda _port, command, **_kwargs: self.events.append(command)))
        self.reconnect = self.enterContext(mock.patch.object(cli, "wait_for_reconnect", return_value="/dev/cu.TT-NEW"))
        self.enroll = self.enterContext(mock.patch.object(cli, "enroll"))
        self.enterContext(mock.patch.object(cli, "notify"))
        self.verify = self.enterContext(mock.patch.object(cli, "verify_hid_host"))
        self.install = self.enterContext(mock.patch.object(cli, "install_helper"))
        self.loaded = self.enterContext(mock.patch.object(cli, "helper_loaded", return_value=True))
        self.args = cli.parser().parse_args(["setup", "--mode", "hid", "--skip-enroll"])

    def test_setup_registers_before_mode_switch_and_does_not_recurse(self):
        self.status.side_effect = [{**DEVICE, "mode": "piv"}, DEVICE]
        cli.command_setup(self.args)
        self.assertEqual(self.events, ["configure", "SET MODE HID"])
        self.remove.assert_called_once()
        self.configure.assert_called_once()
        self.assertEqual(self.session.call_count, 2)
        self.reconnect.assert_called_once_with(PORT)
        self.fresh.assert_called_once_with("/dev/cu.TT-NEW", {"mode": "hid"})
        self.enroll.assert_called_once_with("/dev/cu.TT-NEW", True)
        self.verify.assert_called_once_with("/dev/cu.TT-NEW", DEVICE)
        self.assertIn("Ready (HID)", self.output.getvalue())

    def test_reconnect_timeout_explains_how_to_finish_setup(self):
        self.status.return_value = {**DEVICE, "mode": "piv"}
        self.reconnect.side_effect = cli.ToolError("Timed out waiting for USB")
        with self.assertRaisesRegex(cli.ToolError, "tinytouch setup --mode hid"):
            cli.command_setup(self.args)
        self.assertEqual(self.events, ["configure", "SET MODE HID"])
        self.install.assert_not_called()
        self.assertNotIn("Ready (HID)", self.output.getvalue())

    def test_failed_pairing_stops_before_selecting_hid(self):
        self.status.return_value = {**DEVICE, "mode": "piv"}
        self.configure.side_effect = cli.ToolError("No host saved")
        with self.assertRaisesRegex(cli.ToolError, "No host saved"):
            cli.command_setup(self.args)
        self.command.assert_not_called()
        self.reconnect.assert_not_called()

    def test_wrong_mode_after_reconnect_stops_without_another_write(self):
        self.status.return_value = {**DEVICE, "mode": "piv"}
        self.fresh.side_effect = cli.ToolError("Verification failed. mode is piv")
        with self.assertRaisesRegex(cli.ToolError, "Verification failed"):
            cli.command_setup(self.args)
        self.command.assert_called_once()
        self.reconnect.assert_called_once()
        self.install.assert_not_called()

    def test_a_different_reconnected_device_is_never_configured(self):
        self.status.return_value = {**DEVICE, "mode": "piv"}
        self.account.side_effect = [ACCOUNT, ACCOUNT, "TT-OTHER"]
        with self.assertRaisesRegex(cli.ToolError, "different device reconnected"):
            cli.command_setup(self.args)
        self.configure.assert_called_once()
        self.fresh.assert_not_called()
        self.install.assert_not_called()

    def test_existing_hid_mode_does_not_require_a_reconnect(self):
        cli.command_setup(self.args)
        self.command.assert_not_called()
        self.reconnect.assert_not_called()
        self.install.assert_called_once()


class ModeFlowTests(unittest.TestCase):
    def setUp(self):
        self.output = io.StringIO()
        self.enterContext(contextlib.redirect_stdout(self.output))
        self.enterContext(mock.patch.object(cli, "choose_port", return_value=PORT))
        self.session = self.enterContext(mock.patch.object(cli, "foreground_session"))
        self.account = self.enterContext(mock.patch.object(cli, "device_account", return_value=ACCOUNT))
        self.enterContext(mock.patch.object(cli, "status", return_value={**DEVICE, "mode": "piv"}))
        self.fresh = self.enterContext(mock.patch.object(cli, "fresh_status", return_value=DEVICE))
        self.enterContext(mock.patch.object(cli, "unlock"))
        self.command = self.enterContext(mock.patch.object(cli, "serial_command"))
        self.enterContext(mock.patch.object(cli, "notify"))
        self.reconnect = self.enterContext(mock.patch.object(cli, "wait_for_reconnect", return_value="/dev/cu.TT-NEW"))
        self.verify = self.enterContext(mock.patch.object(cli, "verify_hid_host"))
        self.install = self.enterContext(mock.patch.object(cli, "install_helper"))
        self.loaded = self.enterContext(mock.patch.object(cli, "helper_loaded", return_value=True))
        self.remove = self.enterContext(mock.patch.object(cli, "remove_helper"))
        self.args = cli.parser().parse_args(["mode", "hid"])

    def test_hid_mode_checks_local_host_before_and_after_reconnect(self):
        cli.command_mode(self.args)
        self.assertEqual(self.verify.call_args_list, [
            mock.call(PORT, {**DEVICE, "mode": "piv"}, ACCOUNT),
            mock.call("/dev/cu.TT-NEW", DEVICE, ACCOUNT),
        ])
        self.assertEqual(self.session.call_count, 2)
        self.command.assert_called_once_with(PORT, "SET MODE HID", timeout=4)
        self.install.assert_called_once()
        self.assertIn("HID mode is active.", self.output.getvalue())

    def test_no_local_pairing_warns_and_cannot_claim_hid_success(self):
        self.verify.side_effect = cli.HidSetupIncompleteError("Run 'tinytouch setup --mode hid'.")
        with self.assertRaisesRegex(cli.ToolError, "tinytouch setup --mode hid"):
            cli.command_mode(self.args)
        self.assertIn("HID password typing needs setup", self.output.getvalue())
        self.install.assert_not_called()
        self.assertNotIn("HID mode is active", self.output.getvalue())

    def test_malformed_inventory_blocks_mode_write(self):
        self.verify.side_effect = cli.ToolError("invalid HID computer inventory")
        with self.assertRaisesRegex(cli.ToolError, "invalid HID computer inventory"):
            cli.command_mode(self.args)
        self.command.assert_not_called()
        self.reconnect.assert_not_called()

    def test_reconnect_timeout_provides_setup_recovery(self):
        self.reconnect.side_effect = cli.ToolError("Timed out waiting for USB")
        with self.assertRaisesRegex(cli.ToolError, "tinytouch setup --mode hid"):
            cli.command_mode(self.args)
        self.install.assert_not_called()

    def test_a_different_device_cannot_start_the_helper(self):
        self.account.side_effect = [ACCOUNT, "TT-OTHER"]
        with self.assertRaisesRegex(cli.ToolError, "different device reconnected"):
            cli.command_mode(self.args)
        self.fresh.assert_not_called()
        self.install.assert_not_called()

    def test_missing_helper_cannot_claim_hid_success(self):
        self.loaded.return_value = False
        with self.assertRaisesRegex(cli.ToolError, "helper is not loaded"):
            cli.command_mode(self.args)
        self.assertNotIn("HID mode is active", self.output.getvalue())

    def test_piv_mode_removes_helper_without_hid_pairing_changes(self):
        self.args.mode = "piv"
        self.fresh.return_value = {**DEVICE, "mode": "piv"}
        cli.command_mode(self.args)
        self.remove.assert_called_once()
        self.install.assert_not_called()
        self.verify.assert_not_called()
        self.command.assert_called_once_with(PORT, "SET MODE PIV", timeout=4)


class HostRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.credentials = {cli.PAIRING_SERVICE: KEY.hex(), cli.PASSWORD_SERVICE: "custom-password"}
        self.registered = {IDENTIFIER}
        self.commands = []
        self.enterContext(mock.patch.object(cli, "device_account", return_value=ACCOUNT))
        self.enterContext(mock.patch.object(cli, "_setup_password", None))
        self.enterContext(mock.patch.object(cli, "prepare_hid_password", side_effect=self.prepare))
        self.enterContext(mock.patch.object(cli, "keychain_get", side_effect=lambda service, _account: self.credentials.get(service)))
        self.save = self.enterContext(mock.patch.object(cli, "keychain_set", side_effect=self.write))
        self.delete = self.enterContext(mock.patch.object(cli, "keychain_delete", side_effect=lambda service, _account: self.credentials.pop(service, None)))
        self.enterContext(mock.patch.object(cli, "host_list", side_effect=lambda _port: (set(self.registered), 8)))
        self.status = self.enterContext(mock.patch.object(cli, "status", side_effect=lambda _port: {**DEVICE, "hosts": str(len(self.registered))}))
        self.command = self.enterContext(mock.patch.object(cli, "serial_command", side_effect=self.exchange))
        self.enterContext(mock.patch.object(cli, "say"))

    def prepare(self):
        self.captured = bytearray(b"mac-login-password")
        cli._setup_password = self.captured

    def write(self, service, _account, value):
        self.credentials[service] = value

    def exchange(self, _port, command, **_kwargs):
        self.commands.append(command)
        if command.startswith("HOST ADD "):
            self.registered.add(command.split()[2])
        elif command.startswith("HOST REMOVE "):
            self.registered.remove(command.split()[2])
        return ["OK HOST"]

    def test_repeated_setup_preserves_custom_password_and_existing_pairing(self):
        with mock.patch.object(cli.platform, "node", return_value="renamed-mac"):
            cli.configure_hid(PORT, DEVICE)
        self.assertEqual(self.credentials[cli.PASSWORD_SERVICE], "custom-password")
        self.assertEqual(self.credentials[cli.PAIRING_SERVICE], KEY.hex())
        self.save.assert_not_called()
        self.command.assert_not_called()
        self.assertEqual(self.captured, bytearray(len(self.captured)))
        self.assertIsNone(cli._setup_password)

    def test_saved_pairing_is_registered_again_after_factory_reset(self):
        self.registered.clear()
        cli.configure_hid(PORT, DEVICE)
        self.assertEqual(self.commands, [f"HOST ADD {IDENTIFIER} {KEY.hex()}"])
        self.save.assert_not_called()

    def test_setup_repairs_an_invalid_saved_key(self):
        self.credentials[cli.PAIRING_SERVICE] = "invalid"
        self.registered.clear()
        cli.configure_hid(PORT, DEVICE)
        repaired_key = cli.hid_pairing_key(self.credentials[cli.PAIRING_SERVICE])
        self.assertIn(cli.host_id(repaired_key), self.registered)
        self.assertEqual(self.credentials[cli.PASSWORD_SERVICE], "custom-password")

    def test_verification_failure_removes_only_the_new_host(self):
        self.registered = {"a" * 16}
        self.status.side_effect = cli.ToolError("status unavailable")
        with self.assertRaisesRegex(cli.ToolError, "status unavailable"):
            cli.configure_hid(PORT, DEVICE)
        self.assertEqual(self.registered, {"a" * 16})
        self.assertEqual(self.commands[-1], f"HOST REMOVE {IDENTIFIER}")
        self.assertEqual(self.credentials[cli.PASSWORD_SERVICE], "custom-password")

    def test_new_credentials_are_removed_after_failed_registration(self):
        self.credentials.clear()
        self.registered.clear()
        self.command.side_effect = cli.ToolError("registration failed")
        with self.assertRaisesRegex(cli.ToolError, "registration failed"):
            cli.configure_hid(PORT, DEVICE)
        self.assertEqual(self.credentials, {})
        self.assertEqual(self.delete.call_count, 2)
        self.assertIsNone(cli._setup_password)

    def test_lost_add_acknowledgment_is_rolled_back(self):
        self.registered.clear()

        def timeout_after_add(port, command, **kwargs):
            result = self.exchange(port, command, **kwargs)
            if command.startswith("HOST ADD "):
                raise cli.SerialTimeout("lost acknowledgment")
            return result

        self.command.side_effect = timeout_after_add
        with self.assertRaisesRegex(cli.ToolError, "lost acknowledgment"):
            cli.configure_hid(PORT, DEVICE)
        self.assertEqual(self.registered, set())
        self.assertEqual(self.commands[-1], f"HOST REMOVE {IDENTIFIER}")

    def test_failed_password_write_restores_previous_value(self):
        self.credentials[cli.PASSWORD_SERVICE] = ""

        def fail_after_write(service, account, value):
            self.write(service, account, value)
            if value == "mac-login-password":
                raise cli.ToolError("password save failed")

        self.save.side_effect = fail_after_write
        with self.assertRaisesRegex(cli.ToolError, "password save failed"):
            cli.configure_hid(PORT, DEVICE)
        self.assertEqual(self.credentials[cli.PASSWORD_SERVICE], "")
        self.command.assert_not_called()

    def test_interrupt_clears_password_and_rolls_back_new_credentials(self):
        self.credentials.clear()
        self.registered.clear()
        self.command.side_effect = KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            cli.configure_hid(PORT, DEVICE)
        self.assertEqual(self.credentials, {})
        self.assertEqual(self.captured, bytearray(len(self.captured)))

    def test_full_inventory_stops_before_writing_credentials(self):
        self.registered = {f"{number:016x}" for number in range(8)}
        with self.assertRaisesRegex(cli.ToolError, "no available HID computer slot"):
            cli.configure_hid(PORT, DEVICE)
        self.save.assert_not_called()
        self.command.assert_not_called()


if __name__ == "__main__":
    unittest.main()

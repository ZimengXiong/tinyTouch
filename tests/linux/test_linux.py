"""Linux integration contracts. Run with unittest discover -s tests/linux."""
import asyncio
from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "linux"), str(ROOT / "macos")]
from dbus_next import Message, MessageType
from tinytouch_linux import desktop, secrets, service
import tinytouch_helper as helper
spec = importlib.util.spec_from_file_location("linux_cli", ROOT / "linux/cli.py")
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


class FakeBus:
    unique_name = ":1.100"
    def __init__(self, locked=False, app="com.example.App", spoof=False, hang=False, lock_after=False):
        self.locked, self.app, self.spoof, self.hang = locked, app, spoof, hang
        self.lock_after = lock_after
        self.handler = None
        self.calls = []
        self.disconnected = False
        self.lock_checks = 0

    async def connect(self):
        return self

    def add_message_handler(self, handler):
        self.handler = handler

    async def call(self, message):
        self.calls.append(message.member)
        body = []
        if message.member == "GetActive":
            self.lock_checks += 1
            body = [self.locked or (self.lock_after and self.lock_checks > 1)]
        elif message.member == "GetNameOwner":
            body = [":1.50"]
        elif message.member == "loadScript":
            script = Path(message.body[0]).read_text()
            # The rendered template contains JSON literals for the destination and token.
            import re
            self.token = re.search(r'"Report", ("[a-f0-9]+")', script).group(1)
            self.token = json.loads(self.token)
            body = [42]
        elif message.member == "run" and not self.hang:
            incoming = Message(path="/org/tinytouch/Query", interface="org.tinytouch.Query",
                               member="Report", signature="ss", body=[self.token, self.app])
            incoming.sender = ":1.666" if self.spoof else ":1.50"
            incoming.serial = 9
            self.callback_reply = self.handler(incoming)
        return Message(message_type=MessageType.METHOD_RETURN, reply_serial=1, body=body,
                       signature="b" if message.member == "GetActive" else
                       "s" if message.member == "GetNameOwner" else
                       "i" if message.member == "loadScript" else "")

    def disconnect(self):
        self.disconnected = True


class DesktopTests(unittest.TestCase):
    def query(self, **kwargs):
        bus = FakeBus(**kwargs)
        with mock.patch.object(desktop, "MessageBus", return_value=bus), \
             mock.patch.object(desktop, "LOOKUP_TIMEOUT", 0.01):
            result = desktop.frontmost_app()
        self.assertTrue(bus.disconnected)
        return result, bus

    def test_fresh_app_and_cleanup(self):
        result, bus = self.query(app="com.example.App.desktop")
        self.assertEqual(result, "com.example.App")
        self.assertEqual(bus.calls.count("GetActive"), 2)
        self.assertEqual(bus.calls[-1], "unloadScript")

    def test_locked_skips_kwin(self):
        result, bus = self.query(locked=True)
        self.assertIsNone(result)
        self.assertNotIn("loadScript", bus.calls)

    def test_lock_during_query_disables_app(self):
        self.assertIsNone(self.query(lock_after=True)[0])

    def test_timeout_unloads_script(self):
        result, bus = self.query(hang=True)
        self.assertIsNone(result)
        self.assertEqual(bus.calls[-1], "unloadScript")

    def test_other_bus_sender_cannot_spoof_kwin(self):
        result, bus = self.query(spoof=True)
        self.assertIsNone(result)
        self.assertEqual(bus.callback_reply.message_type, MessageType.ERROR)

    def test_invalid_id_disables_app(self):
        for app in ("", "a/b", "../vault", "app\n", "a" * 256):
            with self.subTest(app=app):
                self.assertIsNone(self.query(app=app)[0])

    def test_underscore_native_and_xwayland_ids(self):
        self.assertEqual(self.query(app="my_app")[0], "my_app")

    def test_dbus_failure_disables_app(self):
        with mock.patch.object(desktop, "MessageBus", side_effect=RuntimeError("secret")):
            self.assertIsNone(desktop.frontmost_app())

    def test_unknown_lock_reply_is_not_unlocked(self):
        with mock.patch.object(desktop, "call", new=mock.AsyncMock(return_value=[])):
            self.assertFalse(asyncio.run(desktop.unlocked(None)))


class SecretsTests(unittest.TestCase):
    def setUp(self):
        self.connection = mock.Mock()
        self.store = mock.Mock()
        self.store.is_locked.return_value = False
        self.enterContext(mock.patch.object(secrets.secretstorage, "dbus_init", return_value=self.connection))
        self.enterContext(mock.patch.object(secrets.secretstorage, "get_default_collection", return_value=self.store))

    def test_locked_wallet_is_not_prompted_or_read(self):
        self.store.is_locked.return_value = True
        with self.assertRaises(secrets.KeychainError):
            secrets.get_password("tinyTouch", "device")
        self.store.unlock.assert_not_called()
        self.store.search_items.assert_not_called()
        self.connection.close.assert_called_once()

    def test_no_match_returns_none(self):
        self.store.search_items.return_value = []
        self.assertIsNone(secrets.get_password_bytes("tinyTouch", "device"))

    def test_read_returns_mutable_bytes_and_closes_connection(self):
        item = mock.Mock()
        item.get_secret.return_value = b"test-password"
        self.store.search_items.return_value = [item]
        self.assertEqual(secrets.get_password_bytes("tinyTouch", "device"), bytearray(b"test-password"))
        self.connection.close.assert_called_once()

    def test_write_uses_scoped_attributes_and_replace(self):
        secrets.set_password("tinyTouch", "device:app:app.id", "test-password")
        self.store.create_item.assert_called_once_with(
            "tinyTouch credential", {"application": "tinyTouch", "service": "tinyTouch", "account": "device:app:app.id"},
            b"test-password", replace=True, content_type="text/plain")

    def test_error_does_not_leak_method_arguments(self):
        self.store.create_item.side_effect = RuntimeError("test-password")
        with self.assertRaises(secrets.KeychainError) as error:
            secrets.set_password("tinyTouch", "device", "test-password")
        self.assertNotIn("test-password", str(error.exception))
        self.assertTrue(error.exception.__suppress_context__)

    def test_delete_only_matching_items(self):
        item = mock.Mock()
        self.store.search_items.return_value = [item]
        secrets.delete_password("tinyTouch", "device")
        item.delete.assert_called_once()


class CliTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.enterContext(mock.patch.object(helper, "STATE_DIR", Path(self.directory.name)))
        self.enterContext(mock.patch.object(cli.device, "choose_port", return_value="/dev/test"))
        self.enterContext(mock.patch.object(cli.device, "device_account", return_value="TT-123456ABCDEF"))
        self.enterContext(mock.patch.object(cli.device, "prompt_hid_password", return_value="test-password"))
        self.enterContext(mock.patch.object(cli.device, "hid_settings_change", side_effect=self.noop))
        self.enterContext(mock.patch.object(helper, "credentials_exist", return_value=True))
        cli.save_settings("TT-123456ABCDEF", {"keyboard_layout": "us"})

    @staticmethod
    @contextmanager
    def noop():
        yield

    def args(self, *values):
        return cli.parser().parse_args(values)

    def test_app_finger_save_and_index(self):
        with mock.patch.object(secrets, "set_password") as save:
            cli.password(self.args("password", "--app", "com.example.App.desktop", "--finger", "2"))
        save.assert_called_once_with("tinyTouch", "TT-123456ABCDEF:app:com.example.App:finger:2", "test-password")
        self.assertEqual(helper.load_app_password_settings("TT-123456ABCDEF"), {"com.example.App": [2]})

    def test_remove_preserves_other_fingers(self):
        cli.save_settings("TT-123456ABCDEF", {"app_passwords": {"app.id": [0, 2]}})
        with mock.patch.object(secrets, "delete_password") as delete:
            cli.password(self.args("password", "--app", "app.id", "--remove"))
        delete.assert_called_once_with("tinyTouch", "TT-123456ABCDEF:app:app.id")
        self.assertEqual(helper.load_app_password_settings("TT-123456ABCDEF"), {"app.id": [2]})

    def test_failed_secret_write_does_not_register_app(self):
        with mock.patch.object(secrets, "set_password", side_effect=secrets.KeychainError("write")):
            with self.assertRaises(secrets.KeychainError):
                cli.password(self.args("password", "--app", "app.id"))
        self.assertEqual(helper.load_app_password_settings("TT-123456ABCDEF"), {})

    def test_non_ascii_is_rejected_before_saving(self):
        with mock.patch.object(cli.device, "prompt_hid_password", return_value="pássword"), \
             mock.patch.object(secrets, "set_password") as save:
            with self.assertRaises(cli.device.ToolError):
                cli.password(self.args("password"))
            save.assert_not_called()

    def test_default_and_finger_accounts(self):
        for options, account in (((), "TT-123456ABCDEF"), (("--finger", "2"), "TT-123456ABCDEF:fingerprint:group:2")):
            with mock.patch.object(secrets, "set_password") as save:
                cli.password(self.args("password", *options))
                self.assertEqual(save.call_args.args[1], account)

    def test_password_requires_setup(self):
        with mock.patch.object(helper, "credentials_exist", return_value=False), \
             mock.patch.object(secrets, "set_password") as save:
            with self.assertRaises(cli.device.ToolError):
                cli.password(self.args("password"))
            save.assert_not_called()

    def test_rejects_ambiguous_or_invalid_options(self):
        for options in (("--remove",), ("--list", "--finger", "2"), ("--app", "../app")):
            with self.subTest(options=options), self.assertRaises(cli.device.ToolError):
                cli.password(self.args("password", *options))

    def test_layout_acknowledgment_required_before_setup_changes(self):
        with mock.patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "KDE", "XDG_SESSION_TYPE": "wayland"}), \
             mock.patch.object(cli.device, "configure_hid") as configure:
            with self.assertRaises(cli.device.ToolError):
                cli.setup(self.args("setup"))
            configure.assert_not_called()

    def test_host_lookup_uses_linux_backend(self):
        with mock.patch.object(desktop, "frontmost_app", return_value="app.id"):
            self.assertEqual(helper.host_frontmost_app(), "app.id")


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(mock.patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "KDE", "XDG_SESSION_TYPE": "wayland"}))
        self.enterContext(mock.patch.object(cli.device, "choose_port", return_value="/dev/test"))
        self.enterContext(mock.patch.object(cli.device, "device_account", return_value="TT-123456ABCDEF"))
        self.enterContext(mock.patch.object(cli.device, "foreground_session"))
        self.enterContext(mock.patch.object(cli.device, "prepare_hid_password"))
        self.enterContext(mock.patch.object(cli.device, "_setup_password", bytearray(b"throwaway")))
        self.info = self.enterContext(mock.patch.object(cli.device, "status", return_value={"mode": "hid"}))
        for name in ("protocol6", "sensor_ready", "enroll", "unlock"):
            self.enterContext(mock.patch.object(cli.device, name))
        self.configure = self.enterContext(mock.patch.object(cli.device, "configure_hid"))
        self.settings = self.enterContext(mock.patch.object(cli, "save_settings"))
        self.install = self.enterContext(mock.patch.object(service, "install"))

    def test_success_saves_explicit_layout_and_installs_service(self):
        cli.setup(cli.parser().parse_args(["setup", "--us-keyboard"]))
        self.configure.assert_called_once()
        self.settings.assert_called_once_with("TT-123456ABCDEF", {"keyboard_layout": "us"})
        self.install.assert_called_once()
        self.assertIsNone(cli.device._setup_password)

    def test_failed_pairing_does_not_install_or_change_layout(self):
        secret = cli.device._setup_password
        self.configure.side_effect = cli.device.ToolError("pairing failed")
        with self.assertRaises(cli.device.ToolError):
            cli.setup(cli.parser().parse_args(["setup", "--us-keyboard"]))
        self.install.assert_not_called()
        self.settings.assert_not_called()
        self.assertEqual(secret, bytearray(len(secret)))

    def test_mode_switch_requires_reconnect_before_service_install(self):
        self.info.return_value = {"mode": "piv"}
        with mock.patch.object(cli.device, "serial_command") as command:
            cli.setup(cli.parser().parse_args(["setup", "--us-keyboard"]))
        command.assert_called_once_with("/dev/test", "SET MODE HID", timeout=4)
        self.install.assert_not_called()


class HandoffTests(unittest.TestCase):
    def test_running_helper_requires_acknowledgement(self):
        with mock.patch.object(service, "helper_active", return_value=True), \
             mock.patch.object(cli.device, "ForegroundLease") as lease:
            with cli.device.foreground_helper():
                lease.return_value.__enter__.return_value.acquire.assert_called_once_with(wait_for_ack=True)

    def test_missing_ack_is_an_actionable_error(self):
        with mock.patch.object(service, "helper_active", return_value=True), \
             mock.patch.object(cli.device, "ForegroundLease") as lease:
            lease.return_value.__enter__.return_value.acquire.side_effect = cli.device.LeaseProtocolError("no acknowledgement")
            with self.assertRaises(cli.device.ToolError):
                with cli.device.foreground_helper():
                    self.fail("USB must remain unopened")

    def test_password_prompt_is_not_repeated_during_pairing(self):
        with mock.patch.object(cli.device, "_setup_password", bytearray(b"throwaway")), \
             mock.patch.object(cli.device, "prompt_hid_password") as prompt:
            cli.device.prepare_hid_password()
            prompt.assert_not_called()


class ServiceTests(unittest.TestCase):
    def test_systemd_exec_escaping(self):
        self.assertEqual(service.quote_exec('/home/a b/"$%'), '"/home/a b/\\"$$%%"')

    def test_unknown_service_is_inactive(self):
        with mock.patch.object(service, "systemctl", return_value=mock.Mock(returncode=4)):
            self.assertFalse(service.helper_active())

    def test_no_bus_is_not_silently_treated_as_inactive(self):
        with mock.patch.object(service, "systemctl", return_value=mock.Mock(returncode=1)):
            with self.assertRaises(RuntimeError):
                service.helper_active()


if __name__ == "__main__":
    unittest.main()

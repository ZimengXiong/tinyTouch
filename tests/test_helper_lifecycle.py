"""Test foreground ownership without changing launchd or opening USB devices."""

import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "lifecycle_tinytouch_cli", ROOT / "macos" / "cli.py"
)
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


class ForegroundSessionTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.port = "/dev/cu.usbmodem101"
        self.device = mock.Mock(port=self.port)
        self.open = mock.Mock(return_value=self.device)
        self.enterContext(mock.patch.dict(sys.modules, {
            "serial": SimpleNamespace(Serial=self.open),
        }))
        self.enterContext(mock.patch.object(cli, "_active_serial", None))
        self.enterContext(mock.patch.object(cli.time, "monotonic", lambda: self.now))
        self.enterContext(mock.patch.object(cli.time, "sleep", self.advance))
        self.current_port = self.enterContext(
            mock.patch.object(cli, "current_port", return_value=self.port)
        )
        self.unload = self.enterContext(
            mock.patch.object(cli, "unload_helper", return_value=True)
        )
        self.load = self.enterContext(mock.patch.object(cli, "load_helper"))
        self.exchange = self.enterContext(
            mock.patch.object(cli, "exchange_serial", return_value=["PONG 6"])
        )

    def advance(self, seconds):
        self.now += seconds

    def test_startup_failures_always_restore_the_helper(self):
        for stage in (self.current_port, self.open, self.device.reset_input_buffer,
                      self.exchange):
            with self.subTest(stage=stage):
                self.now = 0.0
                self.load.reset_mock()
                stage.side_effect = OSError("Device not configured")
                try:
                    with self.assertRaisesRegex(cli.ToolError, "reconnecting"):
                        with cli.foreground_session(self.port):
                            self.fail("An unverified session must not be yielded")
                    self.load.assert_called_once()
                    self.assertIsNone(cli._active_serial)
                finally:
                    stage.side_effect = None

    def test_interrupt_and_exit_during_ping_close_usb_and_restore_helper(self):
        for error in (KeyboardInterrupt(), SystemExit(2)):
            with self.subTest(error=type(error).__name__):
                self.load.reset_mock()
                self.device.close.reset_mock()
                self.exchange.side_effect = error
                with self.assertRaises(type(error)):
                    with cli.foreground_session(self.port):
                        self.fail("PING was interrupted")
                self.device.close.assert_called_once()
                self.load.assert_called_once()
                self.assertIsNone(cli._active_serial)

    def test_close_failure_does_not_skip_helper_restore(self):
        self.device.close.side_effect = OSError("close failed")
        with self.assertRaisesRegex(OSError, "close failed"):
            with cli.foreground_session(self.port):
                pass
        self.assertIsNone(cli._active_serial)
        self.load.assert_called_once()

    def test_command_failure_closes_usb_before_restoring_helper(self):
        events = []
        self.device.close.side_effect = lambda: events.append("close")
        self.load.side_effect = lambda: events.append("load")
        with self.assertRaisesRegex(cli.ToolError, "command failed"):
            with cli.foreground_session(self.port):
                raise cli.ToolError("command failed")
        self.assertEqual(events, ["close", "load"])
        self.assertIsNone(cli._active_serial)

    def test_nested_session_reuses_connection_until_outer_session_exits(self):
        with cli.foreground_session(self.port):
            with cli.foreground_session(self.port) as port:
                self.assertEqual(port, self.port)
                cli.serial_command(port, "STATUS")
            self.assertIs(cli._active_serial, self.device)
            self.device.close.assert_not_called()
            self.load.assert_not_called()
        self.open.assert_called_once()
        self.unload.assert_called_once()
        self.device.close.assert_called_once()
        self.load.assert_called_once()

    def test_nested_session_cannot_redirect_a_command_to_another_device(self):
        with cli.foreground_session(self.port):
            self.current_port.return_value = "/dev/cu.usbmodem102"
            with self.assertRaisesRegex(cli.ToolError, "another device"):
                with cli.foreground_session("/dev/cu.usbmodem102"):
                    self.fail("The outer device still owns the session")
            with self.assertRaisesRegex(cli.ToolError, "another device"):
                cli.serial_command("/dev/cu.usbmodem102", "RESET FACTORY")
            self.assertIs(cli._active_serial, self.device)
        self.exchange.assert_called_once()
        self.open.assert_called_once()

    def test_nested_session_accepts_the_original_path_after_port_renumbering(self):
        self.device.port = "/dev/cu.usbmodem1101"
        self.current_port.return_value = self.device.port
        with cli.foreground_session(self.port):
            with cli.foreground_session(self.port) as port:
                self.assertEqual(port, self.device.port)
            self.assertIs(cli._active_serial, self.device)
        self.open.assert_called_once_with(
            self.device.port, 115200, timeout=0.25, write_timeout=2
        )

    def test_uninstalled_service_is_not_started(self):
        self.unload.return_value = False
        with cli.foreground_session(self.port):
            pass
        self.load.assert_not_called()


if __name__ == "__main__":
    unittest.main()

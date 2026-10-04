"""Test foreground ownership without changing launchd or opening USB devices."""

import importlib.util
import select
import subprocess
import sys
import tempfile
import time
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
from tinytouch_runtime import LeaseObserver


class ForegroundFixture:
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.agent = self.root / "agent.plist"
        self.suspend = self.root / "suspend"
        self.ack = self.root / "ack"
        for name, value in (("LAUNCH_AGENT", self.agent),
                            ("HELPER_SUSPEND", self.suspend),
                            ("HELPER_SUSPEND_ACK", self.ack)):
            self.enterContext(mock.patch.object(cli, name, value))
        self.enterContext(mock.patch.object(cli, "_helper_suppressed", False))
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


class ForegroundSessionTests(ForegroundFixture, unittest.TestCase):
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


class LeaseHandoffTests(ForegroundFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.agent.write_bytes(cli.plistlib.dumps({
            "EnvironmentVariables": {"TINYTOUCH_SERVICE_SCHEMA": "3"},
        }))

    def acknowledge(self, lease, timeout):
        observer = LeaseObserver(self.suspend, self.ack)
        record = observer.active()
        self.assertEqual(record.pid, cli.os.getpid())
        observer.acknowledge(record)

    def test_helper_acknowledges_before_usb_opens_and_stays_registered(self):
        events = []
        self.load.side_effect = lambda: events.append("load")
        self.open.side_effect = lambda *args, **kwargs: (
            events.append("open") or self.device
        )
        def acknowledge(lease, timeout):
            events.append("ack")
            self.acknowledge(lease, timeout)
        with mock.patch.object(cli.ForegroundLease, "_wait_for_ack", acknowledge):
            with cli.foreground_session(self.port):
                self.assertTrue(self.suspend.exists())
                with cli.foreground_session(self.port):
                    cli.serial_command(self.port, "STATUS")
        self.assertEqual(events, ["load", "ack", "open"])
        self.unload.assert_not_called()
        self.assertFalse(self.suspend.exists())
        self.assertFalse(self.ack.exists())
        self.device.close.assert_called_once()

    def test_missing_acknowledgement_never_opens_usb_or_unloads_service(self):
        with mock.patch.object(
            cli.ForegroundLease, "_wait_for_ack",
            side_effect=cli.LeaseProtocolError("Service did not release USB"),
        ):
            with self.assertRaisesRegex(cli.ToolError, "did not release USB"):
                with cli.foreground_session(self.port):
                    self.fail("The helper still owns USB")
        self.open.assert_not_called()
        self.unload.assert_not_called()
        self.assertFalse(self.suspend.exists())

    def test_concurrent_command_does_not_remove_the_first_lease(self):
        first = cli.ForegroundLease(self.suspend, self.ack).acquire(wait_for_ack=False)
        try:
            with self.assertRaisesRegex(cli.ToolError, "Another tinyTouch command"):
                with cli.foreground_session(self.port):
                    self.fail("Another command still owns USB")
            self.assertEqual(LeaseObserver(self.suspend, self.ack).active(), first.record)
            self.open.assert_not_called()
            self.unload.assert_not_called()
        finally:
            first.release()


class CrashRecoveryTests(unittest.TestCase):
    def test_killed_foreground_process_leaves_helper_registered_and_lease_recoverable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.joinpath("agent.plist").write_bytes(cli.plistlib.dumps({
                "EnvironmentVariables": {"TINYTOUCH_SERVICE_SCHEMA": "3"},
            }))
            script = """
import importlib.util, pathlib, signal, sys
from types import SimpleNamespace
from unittest import mock
spec = importlib.util.spec_from_file_location("crash_cli", sys.argv[1])
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)
root = pathlib.Path(sys.argv[2])
cli.LAUNCH_AGENT = root / "agent.plist"
cli.HELPER_SUSPEND = root / "suspend"
cli.HELPER_SUSPEND_ACK = root / "ack"
cli.load_helper = lambda: None
cli.unload_helper = lambda: root.joinpath("bootout").write_text("stopped")
cli.current_port = lambda port: port
cli.exchange_serial = lambda *args, **kwargs: ["PONG 6"]
sys.modules["serial"] = SimpleNamespace(Serial=lambda *args, **kwargs: mock.Mock())
with cli.foreground_session("fake-usb"):
    print("ready", flush=True)
    signal.pause()
"""
            process = subprocess.Popen(
                [sys.executable, "-c", script, str(ROOT / "macos/cli.py"), str(root)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            try:
                observer = LeaseObserver(root / "suspend", root / "ack")
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    record = observer.active()
                    if record is not None:
                        observer.acknowledge(record)
                        break
                    self.assertIsNone(process.poll(), "The child exited before acquiring USB")
                    time.sleep(0.01)
                self.assertIsNotNone(record, "The child did not acquire a lease")
                ready, _, _ = select.select([process.stdout], [], [], 5)
                self.assertTrue(ready, "The child did not finish the USB handoff")
                self.assertEqual(process.stdout.readline().strip(), "ready")
                process.kill()
                process.wait(timeout=5)
                self.assertFalse(root.joinpath("bootout").exists())
                self.assertIsNone(observer.active())
                self.assertFalse(root.joinpath("suspend").exists())
                self.assertFalse(root.joinpath("ack").exists())
            finally:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()

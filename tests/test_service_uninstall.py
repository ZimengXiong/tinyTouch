"""Service removal, failure handling, and saved-data preservation tests."""

import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "uninstall_tinytouch_cli", ROOT / "macos" / "cli.py"
)
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


class ServiceUninstallTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.agent = self.root / "helper.plist"
        self.suspend = self.root / "helper-suspend"
        self.ack = self.root / "helper-suspend-ack"
        for path in (self.agent, self.suspend, self.ack):
            path.write_text("existing service")
        self.enterContext(mock.patch.object(cli, "LAUNCH_AGENT", self.agent))
        self.enterContext(mock.patch.object(cli, "HELPER_SUSPEND", self.suspend))
        self.enterContext(mock.patch.object(cli, "HELPER_SUSPEND_ACK", self.ack))
        self.enterContext(mock.patch.object(cli, "_helper_suppressed", False))
        self.enterContext(mock.patch.object(cli, "require_macos"))
        self.enterContext(mock.patch.object(cli.os, "getuid", return_value=501))
        self.run = self.enterContext(
            mock.patch.object(
                cli.subprocess,
                "run",
                return_value=SimpleNamespace(returncode=0),
            )
        )
        self.loaded = self.enterContext(
            mock.patch.object(cli, "helper_loaded", return_value=False)
        )
        self.output = self.enterContext(mock.patch.object(cli, "say"))
        self.device = self.enterContext(mock.patch.object(cli, "choose_port"))
        self.credentials = self.enterContext(mock.patch.object(cli, "keychain_delete"))
        self.args = cli.parser().parse_args(["uninstall"])

    def test_removal_stops_service_and_preserves_saved_data(self):
        saved = self.root / "saved-devices.json"
        saved.write_text("saved pairing metadata")
        self.args.func(self.args)
        self.run.assert_called_once_with(
            ["launchctl", "bootout", "gui/501/com.tinytouch.helper"],
            check=False,
            timeout=5,
            stdout=cli.subprocess.DEVNULL,
            stderr=cli.subprocess.DEVNULL,
        )
        self.assertFalse(self.agent.exists())
        self.assertFalse(self.suspend.exists())
        self.assertFalse(self.ack.exists())
        self.assertEqual(saved.read_text(), "saved pairing metadata")
        self.assertTrue(cli._helper_suppressed)
        self.device.assert_not_called()
        self.credentials.assert_not_called()
        self.output.assert_called_once_with("Background service uninstalled.")
        cli.load_helper()
        self.assertEqual(self.run.call_count, 1)

    def test_already_removed_service_succeeds(self):
        for path in (self.agent, self.suspend, self.ack):
            path.unlink()
        self.run.return_value.returncode = 113
        self.args.func(self.args)
        self.output.assert_called_once_with("Background service uninstalled.")

    def test_loaded_service_is_removed_even_without_plist(self):
        self.agent.unlink()
        self.args.func(self.args)
        self.run.assert_called_once()
        self.loaded.assert_called_once()
        self.assertFalse(self.agent.exists())

    def test_failed_stop_preserves_launch_agent_and_reports_failure(self):
        self.loaded.return_value = True
        self.run.return_value.returncode = 1
        with self.assertRaisesRegex(cli.ToolError, "Could not stop"):
            self.args.func(self.args)
        self.assertTrue(self.agent.exists())
        self.assertTrue(self.suspend.exists())
        self.assertFalse(cli._helper_suppressed)
        self.output.assert_not_called()

    def test_timeout_preserves_launch_agent_and_reports_failure(self):
        self.run.side_effect = cli.subprocess.TimeoutExpired("launchctl", 5)
        with self.assertRaisesRegex(cli.ToolError, "Could not stop"):
            self.args.func(self.args)
        self.assertTrue(self.agent.exists())
        self.output.assert_not_called()

    def test_failed_file_removal_does_not_report_success(self):
        with mock.patch.object(cli.Path, "unlink", side_effect=PermissionError):
            with self.assertRaisesRegex(cli.ToolError, "Could not remove"):
                self.args.func(self.args)
        self.assertTrue(self.agent.exists())
        self.output.assert_not_called()


if __name__ == "__main__":
    unittest.main()

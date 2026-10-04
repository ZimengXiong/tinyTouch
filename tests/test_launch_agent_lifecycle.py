"""Verify launchd state transitions with temporary files and mocked commands."""

import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "launch_agent_tinytouch_cli", ROOT / "macos" / "cli.py"
)
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


class LaunchAgentLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.agent = self.root / "helper.plist"
        self.suspend = self.root / "suspend"
        self.ack = self.root / "ack"
        self.previous = cli.plistlib.dumps({"ProgramArguments": ["/old/cli", "_helper"]})
        self.agent.write_bytes(self.previous)
        for path in (self.suspend, self.ack):
            path.write_text("foreground state")
        for name, value in (("LAUNCH_AGENT", self.agent),
                            ("HELPER_SUSPEND", self.suspend),
                            ("HELPER_SUSPEND_ACK", self.ack),
                            ("SUPPORT_DIR", self.root / "support"),
                            ("LOG_DIR", self.root / "logs")):
            self.enterContext(mock.patch.object(cli, name, value))
        self.enterContext(mock.patch.object(cli, "_helper_suppressed", False))
        self.enterContext(mock.patch.object(cli.os, "getuid", return_value=501))
        self.process = self.enterContext(mock.patch.object(
            cli.subprocess, "run", return_value=SimpleNamespace(returncode=0),
        ))
        self.run = self.enterContext(mock.patch.object(cli, "run"))
        self.loaded = self.enterContext(mock.patch.object(cli, "helper_loaded"))

    def test_successful_bootout_must_also_release_the_service(self):
        self.loaded.return_value = True
        with self.assertRaisesRegex(cli.ToolError, "Could not stop"):
            cli.unload_helper()
        self.assertTrue(self.suspend.exists())
        self.assertEqual(self.agent.read_bytes(), self.previous)
        self.assertEqual(self.process.call_args.args[0], [
            "launchctl", "bootout", "gui/501/com.tinytouch.helper",
        ])
        self.assertEqual(self.process.call_args.kwargs["timeout"], 5)

    def test_failed_bootout_is_safe_when_another_process_already_stopped_service(self):
        self.loaded.side_effect = [True, False]
        self.process.return_value.returncode = 113
        self.assertTrue(cli.unload_helper())
        self.assertFalse(self.suspend.exists())
        self.assertFalse(self.ack.exists())

    def test_piv_removal_preserves_files_and_restart_state_when_stop_fails(self):
        self.loaded.return_value = True
        with self.assertRaisesRegex(cli.ToolError, "Could not stop"):
            cli.remove_helper()
        self.assertEqual(self.agent.read_bytes(), self.previous)
        self.assertTrue(self.suspend.exists())
        self.assertFalse(cli._helper_suppressed)

    def test_piv_removal_stops_an_orphan_service_without_a_plist(self):
        self.agent.unlink()
        self.loaded.return_value = False
        cli.remove_helper()
        self.process.assert_called_once()
        self.assertTrue(cli._helper_suppressed)
        self.assertFalse(self.suspend.exists())

    def test_bootstrap_race_succeeds_only_if_service_is_registered(self):
        self.loaded.side_effect = [False, True]
        self.run.side_effect = cli.ToolError("already loaded")
        cli.load_helper()
        self.run.assert_called_once_with([
            "launchctl", "bootstrap", "gui/501", str(self.agent),
        ], timeout=5)

    def test_bootstrap_success_without_a_registered_service_is_an_error(self):
        self.loaded.return_value = False
        with self.assertRaisesRegex(cli.ToolError, "did not load"):
            cli.load_helper()

    def test_bootstrap_timeout_reports_a_repair_action(self):
        self.loaded.return_value = False
        self.run.side_effect = cli.subprocess.TimeoutExpired("launchctl", 5)
        with self.assertRaisesRegex(cli.ToolError, "tinytouch repair"):
            cli.load_helper()

    def test_registered_service_is_not_bootstrapped_again(self):
        self.loaded.return_value = True
        cli.load_helper()
        self.run.assert_not_called()

    def test_frozen_helper_checks_and_registers_the_resolved_bundle_executable(self):
        executable = self.root / "cli-current" / "tinytouch"
        executable.parent.mkdir()
        executable.touch()
        command = self.root / "tinytouch"
        command.symlink_to(executable)
        self.loaded.return_value = True
        with (
            mock.patch.object(cli, "FROZEN", True),
            mock.patch.object(cli.sys, "executable", str(command)),
            mock.patch.object(cli, "unload_helper"),
            mock.patch.object(cli, "load_helper"),
        ):
            cli.install_helper()
        payload = cli.plistlib.loads(self.agent.read_bytes())
        self.assertEqual(payload["ProgramArguments"], [str(executable), "_helper"])
        self.assertEqual(self.process.call_args.args[0], [
            str(executable), "_helper", "--check-credentials",
        ])
        replacement = self.root / "replacement"
        replacement.touch()
        command.unlink()
        command.symlink_to(replacement)
        self.assertEqual(payload["ProgramArguments"][0], str(executable))


if __name__ == "__main__":
    unittest.main()

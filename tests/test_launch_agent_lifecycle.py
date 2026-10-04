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
probe_helper_loaded = cli.helper_loaded
import tinytouch_runtime as runtime


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

    def test_unreadable_launchd_state_is_not_treated_as_an_absent_service(self):
        for error in (FileNotFoundError("launchctl"),
                      cli.subprocess.TimeoutExpired("launchctl", 5)):
            with self.subTest(error=type(error).__name__):
                self.process.side_effect = error
                with self.assertRaisesRegex(cli.ToolError, "Could not check"):
                    probe_helper_loaded()
        self.assertEqual(self.process.call_args.kwargs["timeout"], 5)

    def test_removal_preserves_files_if_the_stop_cannot_be_verified(self):
        self.loaded.side_effect = cli.ToolError("Could not check the HID background service")
        with self.assertRaisesRegex(cli.ToolError, "Could not check"):
            cli.remove_helper()
        self.assertEqual(self.agent.read_bytes(), self.previous)
        self.assertTrue(self.suspend.exists())
        self.assertFalse(cli._helper_suppressed)

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


    def test_system_exit_during_replacement_restores_previous_helper_and_files(self):
        self.loaded.return_value = True
        with (
            mock.patch.object(cli, "ensure_helper_environment", return_value=Path("/new/cli")),
            mock.patch.object(cli, "unload_helper"),
            mock.patch.object(cli, "load_helper", side_effect=[SystemExit(2), None]) as load,
        ):
            with self.assertRaises(SystemExit) as raised:
                cli.install_helper()
        self.assertEqual(raised.exception.code, 2)
        self.assertEqual(self.agent.read_bytes(), self.previous)
        self.assertEqual(load.call_count, 2)
        self.assertFalse(cli._helper_suppressed)

    def test_failed_fresh_install_removes_its_agent_and_restores_suppression(self):
        self.agent.unlink()
        self.loaded.return_value = False
        cli._helper_suppressed = True
        with (
            mock.patch.object(cli, "ensure_helper_environment", return_value=Path("/new/cli")),
            mock.patch.object(cli, "unload_helper"),
            mock.patch.object(cli, "load_helper", side_effect=cli.ToolError("bootstrap failed")),
        ):
            with self.assertRaisesRegex(cli.ToolError, "previous service was restored"):
                cli.install_helper()
        self.assertFalse(self.agent.exists())
        self.assertTrue(cli._helper_suppressed)


    def test_replacement_cannot_stop_a_helper_owned_by_a_foreground_command(self):
        lease = cli.ForegroundLease(self.suspend, self.ack).acquire(wait_for_ack=False)
        try:
            with (
                mock.patch.object(cli, "ensure_helper_environment", return_value=Path("/new/cli")),
                mock.patch.object(cli, "unload_helper") as unload,
            ):
                with self.assertRaisesRegex(cli.ToolError, "Another tinyTouch command"):
                    cli.install_helper()
            unload.assert_not_called()
            self.loaded.assert_not_called()
            self.assertEqual(self.agent.read_bytes(), self.previous)
            self.assertEqual(runtime.LeaseObserver(self.suspend, self.ack).active(), lease.record)
        finally:
            lease.release()

    def test_failed_lease_write_releases_lock_without_stopping_the_service(self):
        with (
            mock.patch.object(cli, "ensure_helper_environment", return_value=Path("/new/cli")),
            mock.patch.object(cli, "unload_helper") as unload,
            mock.patch.object(runtime, "atomic_write_json", side_effect=PermissionError("read-only")),
        ):
            with self.assertRaisesRegex(cli.ToolError, "Could not replace"):
                cli.install_helper()
        unload.assert_not_called()
        self.assertEqual(self.agent.read_bytes(), self.previous)
        with cli.ForegroundLease(self.suspend, self.ack) as lease:
            lease.acquire(wait_for_ack=False)


class UpdateRestartTests(unittest.TestCase):
    def setUp(self):
        self.args = SimpleNamespace(port="/dev/cu.selected", firmware_only=False)
        self.version = "9.9.9"
        self.enterContext(mock.patch.object(cli, "update_release", return_value=(
            "https://release", {"version": self.version},
        )))
        self.enterContext(mock.patch.object(cli, "download", return_value=b"installer"))
        self.enterContext(mock.patch.object(cli, "say"))
        self.enterContext(mock.patch.object(cli.shutil, "which", return_value="/bin/tinytouch"))
        self.process = self.enterContext(mock.patch.object(cli.subprocess, "run"))
        self.exec = self.enterContext(mock.patch.object(cli.os, "execv"))
        self.ota = self.enterContext(mock.patch.object(cli, "stage_ota"))
        self.upgrade = self.enterContext(mock.patch.object(cli, "command_upgrade_helper"))

    def results(self, stdout):
        self.process.side_effect = [
            SimpleNamespace(returncode=0),
            SimpleNamespace(returncode=0, stdout=stdout),
        ]

    def test_version_suffix_match_cannot_restart_the_wrong_release(self):
        self.results("tinyTouch CLI 19.9.9\n")
        with self.assertRaisesRegex(cli.ToolError, "does not match"):
            cli.command_update(self.args)
        self.exec.assert_not_called()
        self.ota.assert_not_called()

    def test_failed_exec_reports_retry_and_does_not_start_firmware_upload(self):
        self.results(f"tinyTouch CLI {self.version}\n")
        self.exec.side_effect = PermissionError("not executable")
        with self.assertRaisesRegex(cli.ToolError, "Run 'tinytouch update' again"):
            cli.command_update(self.args)
        self.assertEqual(self.exec.call_args.args[1][-2:], ["--port", self.args.port])
        self.ota.assert_not_called()
        self.upgrade.assert_not_called()

    def test_failed_installer_does_not_restart_or_touch_firmware(self):
        self.process.return_value = SimpleNamespace(returncode=1)
        with self.assertRaisesRegex(cli.ToolError, "firmware was not changed"):
            cli.command_update(self.args)
        self.exec.assert_not_called()
        self.ota.assert_not_called()
        self.upgrade.assert_not_called()


if __name__ == "__main__":
    unittest.main()

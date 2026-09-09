"""Check command dispatch without changing helpers, credentials, or devices."""

import importlib.machinery
import importlib.util
from contextlib import nullcontext
from pathlib import Path
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "software/macos-helper"))
from tinytouch_channel import Channel
import tinytouch_helper

loader = importlib.machinery.SourceFileLoader("beta_lifecycle_cli", str(ROOT / "tinytouch"))
spec = importlib.util.spec_from_loader(loader.name, loader)
cli = importlib.util.module_from_spec(spec)
loader.exec_module(cli)


class BetaCommandLifecycleTests(unittest.TestCase):
    def patched(self, target, name, *args, **kwargs):
        patcher = mock.patch.object(target, name, *args, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def setUp(self):
        self.beta = Channel("0.1.26-beta.1")
        self.patched(cli, "CHANNEL", self.beta)
        self.patched(cli.sys, "platform", "darwin")
        self.patched(cli.os, "geteuid", return_value=501)
        self.patched(cli.os, "getuid", return_value=501)
        self.patched(cli, "say")
        self.patched(cli, "show_startup_mark")
        self.patched(cli, "command_lock", side_effect=nullcontext)
        self.patched(cli, "_beta_command_ready", False)
        self.activate = self.patched(cli, "activate_beta")
        self.exit_beta = self.patched(cli, "exit_beta")
        self.guard = self.patched(self.beta, "require_device_access")

    def invoke(self, *arguments):
        with mock.patch.object(cli.sys, "argv", ["tinytouch-beta", *arguments]):
            return cli.main()

    def test_status_activates_beta_before_guard_and_dispatch(self):
        calls = mock.Mock()
        calls.attach_mock(self.activate, "activate")
        calls.attach_mock(self.guard, "guard")
        status = self.patched(cli, "command_status")
        calls.attach_mock(status, "status")
        self.assertEqual(self.invoke("status"), 0)
        self.assertEqual([call[0] for call in calls.mock_calls], ["activate", "guard", "status"])
        self.exit_beta.assert_not_called()

    def test_exit_restores_session_without_reactivating_beta(self):
        self.assertEqual(self.invoke("exit"), 0)
        self.exit_beta.assert_called_once_with()
        self.activate.assert_not_called()
        self.guard.assert_not_called()

    def test_nested_device_checks_do_not_restart_the_paused_helper(self):
        def status(args):
            self.assertTrue(cli._beta_command_ready)
            cli.require_device_access()
            cli.require_device_access()

        with mock.patch.object(cli, "command_status", side_effect=status):
            self.assertEqual(self.invoke("status"), 0)
        self.activate.assert_called_once_with()
        self.assertEqual(self.guard.call_count, 3)
        self.assertFalse(cli._beta_command_ready)

    def test_failed_command_clears_activation_state_before_the_next_command(self):
        def failing_status(args):
            self.assertTrue(cli._beta_command_ready)
            cli.require_device_access()
            cli.require_device_access()
            raise RuntimeError("Status interrupted")

        with mock.patch.object(cli, "command_status", side_effect=failing_status):
            self.assertEqual(self.invoke("status"), 1)
        self.activate.assert_called_once_with()
        self.assertEqual(self.guard.call_count, 3)
        self.assertFalse(cli._beta_command_ready)
        with mock.patch.object(cli, "command_status"):
            self.assertEqual(self.invoke("status"), 0)
        self.assertEqual(self.activate.call_count, 2)
        self.assertFalse(cli._beta_command_ready)

    def test_oserror_is_reported_and_clears_activation_state(self):
        with mock.patch.object(cli, "command_status", side_effect=OSError("Serial port unavailable")):
            self.assertEqual(self.invoke("status"), 1)
        cli.say.assert_called_with("Error: Serial port unavailable")
        self.assertFalse(cli._beta_command_ready)

    def test_production_status_does_not_activate_beta(self):
        production = Channel("0.1.24-prod")
        with mock.patch.object(cli, "CHANNEL", production), mock.patch.object(
            production, "require_device_access"
        ) as guard, mock.patch.object(cli, "command_status") as status:
            self.assertEqual(self.invoke("status"), 0)
        status.assert_called_once()
        guard.assert_called_once()
        self.activate.assert_not_called()
        self.exit_beta.assert_not_called()

    def test_exit_is_not_a_production_command(self):
        with mock.patch.object(cli, "CHANNEL", Channel("0.1.24-prod")), mock.patch(
            "sys.stderr"
        ), self.assertRaises(SystemExit) as error:
            self.invoke("exit")
        self.assertEqual(error.exception.code, 2)
        self.activate.assert_not_called()
        self.exit_beta.assert_not_called()

    def test_root_commands_stop_before_activation_or_dispatch(self):
        with mock.patch.object(cli.os, "geteuid", return_value=0), mock.patch.object(
            cli.os, "getuid", return_value=0
        ), mock.patch.object(cli, "command_status") as status:
            for argument in ("status", "exit"):
                with self.subTest(argument=argument):
                    self.assertNotEqual(self.invoke(argument), 0)
        status.assert_not_called()
        self.activate.assert_not_called()
        self.exit_beta.assert_not_called()
        self.guard.assert_not_called()

    def test_root_helper_stops_before_helper_dispatch(self):
        with mock.patch.object(cli.os, "geteuid", return_value=0), mock.patch.object(
            cli.os, "getuid", return_value=0
        ), mock.patch.object(tinytouch_helper, "main") as helper:
            self.assertNotEqual(self.invoke("_helper"), 0)
        helper.assert_not_called()
        self.activate.assert_not_called()
        self.guard.assert_not_called()

    def test_root_direct_helper_operations_stop_before_side_effects(self):
        with mock.patch.object(cli.os, "geteuid", return_value=0), mock.patch.object(
            cli, "run"
        ) as run, mock.patch.object(cli, "ensure_helper_environment") as environment:
            for operation in (cli.load_helper, cli.install_helper):
                with self.subTest(operation=operation.__name__), self.assertRaises(cli.ToolError):
                    operation()
        run.assert_not_called()
        environment.assert_not_called()

    def test_activation_failure_prevents_status_dispatch(self):
        self.activate.side_effect = RuntimeError("Could not switch helpers")
        with mock.patch.object(cli, "command_status") as status:
            self.assertEqual(self.invoke("status"), 1)
        status.assert_not_called()
        self.guard.assert_not_called()

    def test_exit_failure_is_reported_without_activation(self):
        self.exit_beta.side_effect = RuntimeError("Could not restore production")
        self.assertEqual(self.invoke("exit"), 1)
        self.activate.assert_not_called()
        self.guard.assert_not_called()

    def test_root_build_smoke_tests_remain_available(self):
        with mock.patch.object(cli.os, "geteuid", return_value=0), mock.patch.object(
            cli.os, "getuid", return_value=0
        ):
            for argument, function in (("_package_test", "package_test"),
                                       ("_network_test", "network_test")):
                with self.subTest(argument=argument), mock.patch.object(cli, function) as smoke:
                    self.assertEqual(self.invoke(argument), 0)
                    smoke.assert_called_once_with()
        self.activate.assert_not_called()
        self.guard.assert_not_called()

    def test_root_help_and_version_do_not_activate_beta(self):
        with mock.patch.object(cli.os, "geteuid", return_value=0), mock.patch.object(
            cli.os, "getuid", return_value=0
        ), mock.patch("sys.stdout"):
            for argument in ("--help", "--version"):
                with self.subTest(argument=argument), self.assertRaises(SystemExit) as error:
                    self.invoke(argument)
                self.assertEqual(error.exception.code, 0)
        self.activate.assert_not_called()
        self.guard.assert_not_called()

    def test_load_helper_enables_its_own_label_before_bootstrap(self):
        for identity in (self.beta, Channel("0.1.24-prod")):
            plist = Path("/isolated/LaunchAgents") / f"{identity.label}.plist"
            with self.subTest(label=identity.label), mock.patch.object(
                cli, "CHANNEL", identity
            ), mock.patch.object(cli, "LAUNCH_AGENT", plist), mock.patch.object(
                cli, "_helper_suppressed", False
            ), mock.patch.object(cli, "helper_loaded", return_value=False), mock.patch.object(
                cli, "run"
            ) as run:
                cli.load_helper()
                self.assertEqual(run.call_args_list, [
                    mock.call(["launchctl", "enable", f"gui/501/{identity.label}"]),
                    mock.call(["launchctl", "bootstrap", "gui/501", str(plist)]),
                ])

    def test_suppressed_helper_is_not_enabled_or_bootstrapped(self):
        with mock.patch.object(cli, "_helper_suppressed", True), mock.patch.object(cli, "run") as run:
            cli.load_helper()
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()

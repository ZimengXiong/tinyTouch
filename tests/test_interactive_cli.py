"""Interactive entry-point, command compatibility, and cancellation tests."""

import contextlib
import importlib.util
import io
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("interactive_tinytouch_cli", ROOT / "macos" / "cli.py")
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


class InteractiveCliTests(unittest.TestCase):
    def setUp(self):
        self.output = io.StringIO()
        self.enterContext(contextlib.redirect_stdout(self.output))
        self.args = cli.parser().parse_args([])

    def terminal(self):
        self.enterContext(mock.patch.object(cli.sys.stdin, "isatty", return_value=True))
        self.enterContext(mock.patch.object(cli.sys.stdout, "isatty", return_value=True))

    def test_no_command_opens_menu_and_exits_without_device_access(self):
        self.terminal()
        with (
            mock.patch.object(cli.sys, "argv", ["tinytouch"]),
            mock.patch("builtins.input", return_value="0"),
            mock.patch.object(cli, "choose_port") as device,
        ):
            self.assertEqual(cli.main(), 0)
        device.assert_not_called()
        text = self.output.getvalue()
        for label in ("1. Setup", "2. Enroll", "3. Update", "4. Status", "5. Advanced"):
            self.assertIn(label, text)

    def test_noninteractive_no_command_prints_help_without_prompting(self):
        for stdin_tty, stdout_tty in ((False, True), (True, False), (False, False)):
            with (
                self.subTest(stdin=stdin_tty, stdout=stdout_tty),
                mock.patch.object(cli.sys.stdin, "isatty", return_value=stdin_tty),
                mock.patch.object(cli.sys.stdout, "isatty", return_value=stdout_tty),
                mock.patch.object(cli.sys, "argv", ["tinytouch"]),
                mock.patch("builtins.input") as prompt,
                mock.patch.object(cli, "choose_port") as device,
            ):
                self.assertEqual(cli.main(), 0)
                prompt.assert_not_called()
                device.assert_not_called()
        self.assertIn("usage: tinytouch", self.output.getvalue())

    def test_regular_commands_keep_their_parameters(self):
        examples = [
            (["setup", "--mode", "hid", "--skip-enroll", "--no-pair"], "command_setup"),
            (["mode", "piv"], "command_mode"),
            (["led", "only-auth"], "command_led"),
            (["config", "submit_enter", "0"], "command_config"),
            (["enroll", "10", "--replace"], "command_enroll"),
            (["delete", "2"], "command_delete"),
            (["fingers"], "command_fingers"),
            (["computers", "remove", "aabb"], "command_computers"),
            (["factory-reset"], "command_factory_reset"),
            (["update", "--firmware-only", "--release-version", "0.1.31"], "command_update"),
            (["bootloader"], "command_rom"),
            (["status"], "command_status"),
            (["logs"], "command_logs"),
            (["test"], "command_test"),
            (["keys"], "command_keys"),
            (["pair"], "command_pair"),
            (["hid-smoke"], "command_hid_smoke"),
            (["enroll-demo"], "command_enroll_demo"),
        ]
        for argv, handler_name in examples:
            with self.subTest(argv=argv), mock.patch.object(cli, handler_name) as handler:
                args = cli.parser().parse_args(argv)
                args.func(args)
                handler.assert_called_once_with(args)
                self.assertIsNone(args.port)

    def test_global_port_survives_subcommand_defaults_and_can_be_overridden(self):
        for argv, expected in (
            (["--port", "global", "status"], "global"),
            (["--port", "global", "status", "--port", "local"], "local"),
            (["status", "--port", "local"], "local"),
            (["--port", "global"], "global"),
            (["menu", "--port", "local"], "local"),
        ):
            with self.subTest(argv=argv):
                self.assertEqual(cli.parser().parse_args(argv).port, expected)

    def test_numeric_and_named_navigation_and_back(self):
        with (
            mock.patch(
                "builtins.input",
                side_effect=["5", "fingers", "back", "0", "4"],
            ),
            mock.patch.object(cli, "command_fingers") as fingers,
            mock.patch.object(cli, "command_status") as status,
        ):
            cli.interactive_menu(self.args)
        fingers.assert_not_called()
        status.assert_called_once()

    def test_advanced_actions_exit_from_nested_menus(self):
        for path, handler_name in (
            (["advanced", "piv", "pair"], "command_pair"),
            (["advanced", "diagnostics", "repair"], "command_repair"),
        ):
            with (
                self.subTest(path=path),
                mock.patch("builtins.input", side_effect=path) as prompt,
                mock.patch.object(cli, handler_name) as handler,
            ):
                self.assertTrue(cli.interactive_menu(self.args))
                handler.assert_called_once()
                self.assertEqual(prompt.call_count, len(path))

    def test_home_status_uses_summary_and_advanced_status_keeps_details(self):
        with (
            mock.patch("builtins.input", side_effect=["4", "5", "full status"]),
            mock.patch.object(cli, "command_status") as status,
        ):
            cli.interactive_menu(self.args)
            cli.interactive_menu(self.args)
        self.assertEqual(
            [call.args[0].summary for call in status.call_args_list], [True, False]
        )
        self.assertTrue(status.call_args_list[1].args[0].details)

    def test_advanced_uninstall_uses_regular_handler(self):
        with (
            mock.patch(
                "builtins.input", side_effect=["5", "uninstall service", "0", "0"]
            ),
            mock.patch.object(cli, "command_uninstall") as uninstall,
        ):
            cli.interactive_menu(self.args)
        uninstall.assert_called_once()

    def test_invalid_menu_input_reprompts(self):
        with mock.patch("builtins.input", side_effect=["", "-1", "99", "status"]):
            self.assertEqual(
                cli.select_option("Action", [("status", "Status")]), "status"
            )
        self.assertEqual(self.output.getvalue().count("Invalid selection."), 3)

    def test_selection_accepts_visible_label_and_existing_command_name(self):
        options = [
            ("led", "Lighting"),
            ("only-auth", "Authentication only — match feedback"),
        ]
        for answer, expected in (
            ("lighting", "led"),
            ("led", "led"),
            ("authentication only", "only-auth"),
        ):
            with (
                self.subTest(answer=answer),
                mock.patch("builtins.input", return_value=answer),
            ):
                self.assertEqual(cli.select_option("Lighting", options), expected)

    def test_menu_passes_led_selection_and_global_options_to_regular_handler(self):
        args = cli.parser().parse_args(["--verbose", "--port", "selected"])
        with (
            mock.patch(
                "builtins.input",
                side_effect=[
                    "advanced",
                    "settings",
                    "led",
                    "mode",
                    "3",
                    "0",
                    "0",
                    "0",
                    "0",
                ],
            ),
            mock.patch.object(cli, "command_led") as handler,
        ):
            cli.interactive_menu(args)
        received = handler.call_args.args[0]
        self.assertEqual(received.state, "only-auth")
        self.assertEqual(received.port, "selected")
        self.assertTrue(received.verbose)

    def test_setup_and_mode_use_regular_handlers(self):
        for command, selection, expected, handler_name in (
            ("setup", "hid", "hid", "command_setup"),
            ("mode", "piv", "piv", "command_mode"),
        ):
            with (
                self.subTest(command=command),
                mock.patch("builtins.input", return_value=selection),
                mock.patch.object(cli, handler_name) as handler,
            ):
                cli.interactive_command(self.args, [command])
            self.assertEqual(handler.call_args.args[0].mode, expected)

    def test_cancel_parameter_selection_does_not_execute_command(self):
        with (
            mock.patch("builtins.input", return_value="0"),
            mock.patch.object(cli, "command_mode") as handler,
        ):
            cli.interactive_command(self.args, ["mode"])
        handler.assert_not_called()

    def test_failed_action_exits_without_redrawing_menu(self):
        with (
            mock.patch("builtins.input", return_value="status") as prompt,
            mock.patch.object(
                cli, "command_status", side_effect=cli.ToolError("Device disconnected")
            ),
            self.assertRaisesRegex(cli.ToolError, "Device disconnected"),
        ):
            cli.interactive_menu(self.args)
        prompt.assert_called_once()

    def test_action_interrupt_exits_without_redrawing_menu(self):
        with (
            mock.patch("builtins.input", return_value="status") as prompt,
            mock.patch.object(cli, "command_status", side_effect=KeyboardInterrupt),
            self.assertRaises(KeyboardInterrupt),
        ):
            cli.interactive_menu(self.args)
        prompt.assert_called_once()

    def test_eof_in_submenu_closes_session(self):
        self.terminal()
        with mock.patch(
            "builtins.input", side_effect=["advanced", "fingers", EOFError]
        ):
            cli.command_menu(self.args)

    def test_prompt_interrupt_exits_nested_menus(self):
        with (
            mock.patch(
                "builtins.input", side_effect=["advanced", "fingers", KeyboardInterrupt]
            ),
            self.assertRaises(KeyboardInterrupt),
        ):
            cli.interactive_menu(self.args)

    def test_cancelled_parameter_returns_to_menu_without_running_action(self):
        with (
            mock.patch(
                "builtins.input", side_effect=["setup", "0", "status"]
            ) as prompt,
            mock.patch.object(cli, "command_setup") as setup,
            mock.patch.object(cli, "command_status") as status,
        ):
            self.assertTrue(cli.interactive_menu(self.args))
        setup.assert_not_called()
        status.assert_called_once()
        self.assertEqual(prompt.call_count, 3)

    def inventory(self, groups, available):
        self.enterContext(mock.patch.object(cli, "choose_port", return_value="selected"))
        self.enterContext(mock.patch.object(cli, "foreground_session"))
        self.enterContext(mock.patch.object(cli, "status", return_value={"protocol": "6", "firmware": "0.1.30"}))
        self.enterContext(mock.patch.object(cli, "finger_inventory", return_value=(groups, available)))

    def test_enrollment_uses_live_inventory_and_retains_replacement_confirmation(self):
        self.inventory({1: 4, 2: 1, 3: -1}, 7)
        with (
            mock.patch("builtins.input", return_value="2"),
            mock.patch.object(cli, "command_enroll") as handler,
        ):
            cli.interactive_command(self.args, ["enroll"])
        received = handler.call_args.args[0]
        self.assertEqual(received.finger, 2)
        self.assertEqual(received.port, "selected")
        self.assertFalse(received.replace)
        text = self.output.getvalue()
        self.assertIn("Partially enrolled: 1 of 4 fingerprint views", text)
        self.assertIn("Cleanup pending", text)

    def test_enrollment_limits_empty_choices_to_device_capacity(self):
        self.inventory({1: 4}, 1)
        with mock.patch("builtins.input", side_effect=["3", "2"]):
            self.assertEqual(cli.interactive_finger(self.args, "enroll"), ["enroll", "2", "--port", "selected"])
        self.assertNotIn("Finger 3", self.output.getvalue())
        self.assertIn("Invalid selection", self.output.getvalue())

    def test_deletion_selects_an_occupied_block_and_requires_confirmation(self):
        self.inventory({2: 1, 7: 4}, 8)
        with mock.patch("builtins.input", side_effect=["2", "yes"]):
            self.assertEqual(cli.interactive_finger(self.args, "delete"), ["delete", "7", "--port", "selected"])
        self.assertNotIn("Finger 1", self.output.getvalue())

    def test_deletion_defaults_to_cancel(self):
        self.inventory({2: 4}, 9)
        with (
            mock.patch("builtins.input", side_effect=["1", ""]),
            mock.patch.object(cli, "command_delete") as handler,
        ):
            cli.interactive_command(self.args, ["delete"])
        handler.assert_not_called()

    def test_empty_deletion_does_not_prompt(self):
        self.inventory({}, 10)
        with mock.patch("builtins.input") as prompt:
            self.assertIsNone(cli.interactive_finger(self.args, "delete"))
        prompt.assert_not_called()

    def test_computer_removal_uses_registered_host_and_requires_confirmation(self):
        with (
            mock.patch.object(cli, "choose_port", return_value="selected"),
            mock.patch.object(cli, "foreground_session"),
            mock.patch.object(cli, "status", return_value={"protocol": "6", "firmware": "0.1.30"}),
            mock.patch.object(cli, "host_list", return_value=({"aabb", "ccdd"}, 8)),
            mock.patch("builtins.input", side_effect=["2", "yes"]),
            mock.patch.object(cli, "command_computers") as handler,
        ):
            cli.interactive_command(self.args, ["computers", "remove"])
        received = handler.call_args.args[0]
        self.assertEqual(received.remove, "ccdd")
        self.assertEqual(received.port, "selected")

    def test_setting_value_validation_and_cancel(self):
        with mock.patch("builtins.input", side_effect=["typing_delay_ms", "invalid", "0", "101", "7"]):
            self.assertEqual(cli.interactive_config(), ["config", "typing_delay_ms", "7"])
        with mock.patch("builtins.input", side_effect=["submit_enter", "off"]):
            self.assertEqual(cli.interactive_config(), ["config", "submit_enter", "off"])
        with mock.patch("builtins.input", side_effect=["touch_cooldown_ms", ""]):
            self.assertIsNone(cli.interactive_config())

    def test_failed_setup_clears_password_and_next_action_rechecks_sudo(self):
        password = bytearray(b"test-password")

        def failed_setup(_args):
            cli._setup_password = password
            cli._sudo_session_ready = True
            raise cli.ToolError("Setup failed")

        with (
            mock.patch.object(cli, "_setup_password", None),
            mock.patch.object(cli, "_sudo_session_ready", False),
            mock.patch.object(cli, "command_setup", side_effect=failed_setup),
            mock.patch.object(cli, "command_status") as status,
        ):
            with self.assertRaisesRegex(cli.ToolError, "Setup failed"):
                cli.interactive_command(self.args, ["setup", "--mode", "hid"])
            self.assertIsNone(cli._setup_password)
            self.assertEqual(password, bytearray(len(password)))
            cli.interactive_command(self.args, ["status"])
            status.assert_called_once()
            self.assertFalse(cli._sudo_session_ready)

    def test_device_number_must_be_in_list(self):
        for number in ("0", "-1", "3", "text"):
            with (
                self.subTest(number=number),
                mock.patch.object(cli, "detect_ports", return_value=["first", "last"]),
                mock.patch("builtins.input", return_value=number),
                self.assertRaisesRegex(cli.ToolError, "connected USB serial devices"),
            ):
                cli.choose_port(None)

    def test_config_acknowledgement_succeeds_without_unsupported_readback(self):
        with (
            mock.patch.object(cli, "choose_port", return_value="selected"),
            mock.patch.object(cli, "foreground_session"),
            mock.patch.object(cli, "status", return_value={"protocol": "6", "firmware": "0.1.30"}) as status,
            mock.patch.object(cli, "unlock"),
            mock.patch.object(cli, "serial_command", return_value=["OK SET TYPE_DELAY"]) as command,
        ):
            args = cli.parser().parse_args(["config", "typing_delay_ms", "7"])
            args.func(args)
        command.assert_called_once_with("selected", "SET TYPE_DELAY 7", timeout=5)
        status.assert_called_once()
        self.assertIn("Updated typing_delay_ms to 7", self.output.getvalue())

    def test_failed_config_write_does_not_report_success(self):
        with (
            mock.patch.object(cli, "choose_port", return_value="selected"),
            mock.patch.object(cli, "foreground_session"),
            mock.patch.object(cli, "status", return_value={"protocol": "6", "firmware": "0.1.30"}),
            mock.patch.object(cli, "unlock"),
            mock.patch.object(cli, "serial_command", side_effect=cli.ToolError("Write failed")),
        ):
            with self.assertRaisesRegex(cli.ToolError, "Write failed"):
                args = cli.parser().parse_args(["config", "submit_enter", "0"])
                args.func(args)
        self.assertNotIn("Updated", self.output.getvalue())

    def test_missing_computer_id_is_an_actionable_error(self):
        with mock.patch.object(cli, "command_computers") as command:
            with self.assertRaisesRegex(cli.ToolError, "Specify a registered computer"):
                args = cli.parser().parse_args(["computers", "remove"])
                args.func(args)
        command.assert_not_called()

    def test_direct_command_keeps_error_and_interrupt_exit_codes(self):
        for error, expected in ((cli.ToolError("Device disconnected"), 1), (KeyboardInterrupt(), 130)):
            with (
                self.subTest(error=error),
                mock.patch.object(cli.sys, "argv", ["tinytouch", "status"]),
                mock.patch.object(cli, "command_status", side_effect=error),
            ):
                self.assertEqual(cli.main(), expected)


if __name__ == "__main__":
    unittest.main()

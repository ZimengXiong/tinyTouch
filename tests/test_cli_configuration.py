"""Settings catalog, capability negotiation, LED commands, and user diagnostics."""
import contextlib
import importlib.util
import io
import json
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("customization_cli", ROOT / "macos/cli.py")
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.output = io.StringIO()
        self.enterContext(contextlib.redirect_stdout(self.output))
        self.device = {"protocol": "6", "firmware": "0.1.31", "custom_config": "1", "config_values": "1", "mode": "piv", "led": "on", "led_only_auth": "1"}
        for name, setting in cli.SETTINGS.items():
            if name not in self.device:
                self.device[name] = cli.setting_value(name, setting.default)

    def mock_device(self, device=None):
        self.enterContext(mock.patch.object(cli, "choose_port", return_value="port"))
        self.session = self.enterContext(mock.patch.object(cli, "foreground_session"))
        self.status = self.enterContext(mock.patch.object(cli, "status", return_value=device or self.device))
        self.unlock = self.enterContext(mock.patch.object(cli, "unlock"))
        self.command = self.enterContext(mock.patch.object(cli, "serial_command"))
        self.verify = self.enterContext(mock.patch.object(cli, "fresh_status"))

    def invoke(self, argv):
        args = cli.parser().parse_args(argv)
        args.func(args)
        return args

    def test_list_and_help_require_no_device(self):
        with mock.patch.object(cli, "choose_port") as port:
            self.invoke(["config", "list"])
            for name in cli.SETTINGS:
                self.assertIn(name, self.output.getvalue())
            self.output.seek(0)
            self.output.truncate()
            self.invoke(["config", "list", "--json"])
            metadata = json.loads(self.output.getvalue())
            self.assertEqual(metadata["led_idle_color"]["default"], "blue")
            self.assertEqual(metadata["typing_delay_ms"]["values"], {"minimum": 1, "maximum": 100})
            port.assert_not_called()

    def test_json_reads_report_actual_values_and_named_colors(self):
        self.mock_device()
        self.device["led_idle_color"] = "5"
        self.device["typing_delay_ms"] = "23"
        self.invoke(["config", "--json"])
        values = json.loads(self.output.getvalue())
        self.assertEqual(values["led_idle_color"], "purple")
        self.assertEqual(values["typing_delay_ms"], "23")
        self.assertEqual(values["submit_enter"], "on")
        self.unlock.assert_not_called()
        self.command.assert_not_called()

    def test_single_setting_read_returns_its_saved_value(self):
        self.mock_device()
        self.device["touch_cooldown_ms"] = "900"
        self.invoke(["config", "touch_cooldown_ms"])
        self.assertEqual(self.output.getvalue().strip(), "touch_cooldown_ms=900")
        self.unlock.assert_not_called()

    def test_all_writable_settings_map_to_wire_values_and_verify(self):
        for name, setting in cli.SETTINGS.items():
            if name in {"mode", "led"}:
                continue
            with self.subTest(name=name):
                self.mock_device()
                self.invoke(["config", name, setting.default])
                value = cli.setting_value(name, setting.default)
                self.command.assert_called_once_with("port", f"SET {setting.wire} {value}", timeout=5)
                self.verify.assert_called_once_with("port", {name: value})
                self.session.assert_called_once_with("port")

    def test_invalid_names_and_values_fail_before_device_access(self):
        for name, value in (("led_idle_colour", "blue"), ("typing_delay_ms", "0"),
                            ("touch_cooldown_ms", "5001"), ("submit_enter", "2"),
                            ("led_idle_color", "#ff00ff"), ("led_idle_effect", "rainbow"),
                            ("led_idle_cycles", "256"), ("led_feedback_ms", "49"),
                            ("piv_auto_type", "maybe")):
            with self.subTest(name=name, value=value), mock.patch.object(cli, "choose_port") as port:
                with self.assertRaises(cli.ToolError):
                    self.invoke(["config", name, value])
                port.assert_not_called()
        with self.assertRaisesRegex(cli.ToolError, "Did you mean 'led_idle_color'"):
            cli.setting_name("led_idle_colour")

    def test_bool_numeric_and_legacy_aliases(self):
        self.assertEqual(cli.setting_name("TYPE_DELAY"), "typing_delay_ms")
        self.assertEqual(cli.setting_name("touch-cooldown-ms"), "touch_cooldown_ms")
        for value in ("off", "0", "false", "no"):
            self.assertEqual(cli.setting_value("submit_enter", value), "0")
        self.assertEqual(cli.setting_value("led_idle_color", "magenta"), "5")
        self.assertEqual(cli.setting_value("led_idle_effect", "breathing"), "1")

    def test_new_controls_on_old_firmware_require_update_before_authorization(self):
        self.mock_device({"protocol": "6", "firmware": "0.1.30"})
        with self.assertRaisesRegex(cli.ToolError, "firmware 0.1.34"):
            self.invoke(["config", "led_idle_color", "purple"])
        self.unlock.assert_not_called()
        self.command.assert_not_called()

    def test_old_firmware_can_still_set_existing_timing_preferences(self):
        self.mock_device({"protocol": "6", "firmware": "0.1.30"})
        self.invoke(["config", "typing_delay_ms", "7"])
        self.command.assert_called_once_with("port", "SET TYPE_DELAY 7", timeout=5)
        self.verify.assert_not_called()

    def test_missing_readback_is_not_replaced_by_a_default(self):
        self.mock_device({"protocol": "6", "firmware": "0.1.30"})
        with self.assertRaisesRegex(cli.ToolError, "does not report typing_delay_ms"):
            self.invoke(["config", "typing_delay_ms"])
        self.unlock.assert_not_called()

    def test_led_color_and_effect_use_canonical_configuration(self):
        self.mock_device()
        self.invoke(["led", "color", "success", "blue"])
        self.command.assert_called_once_with("port", "SET LED_SUCCESS_COLOR 1", timeout=5)
        self.command.reset_mock()
        self.verify.reset_mock()
        self.invoke(["led", "effect", "flash"])
        self.command.assert_called_once_with("port", "SET LED_IDLE_EFFECT 2", timeout=5)

    def test_preset_preserves_lighting_mode_and_uses_one_authorization(self):
        self.mock_device()
        self.device["led"] = "off"
        self.invoke(["led", "preset", "ocean"])
        commands = [call.args[1] for call in self.command.call_args_list]
        self.assertIn("SET LED_IDLE_COLOR 1", commands)
        self.assertIn("SET LED_IDLE_END_COLOR 3", commands)
        self.assertIn("SET LED_IDLE_EFFECT 1", commands)
        self.assertNotIn("SET LED 1", commands)
        self.unlock.assert_called_once()
        self.session.assert_called_once()

    def test_partial_preset_failure_reports_acknowledged_fields(self):
        self.mock_device()
        self.command.side_effect = [[], cli.ToolError("Write failed")]
        with self.assertRaisesRegex(cli.ToolError, "Write failed"):
            self.invoke(["led", "preset", "ocean"])
        self.assertIn("Settings acknowledged before the error: led_idle_color", self.output.getvalue())
        self.assertNotIn("Applied 'ocean'", self.output.getvalue())

    def test_preview_sends_only_temporary_command(self):
        self.mock_device()
        self.invoke(["led", "preview", "purple", "--effect", "breathe", "--duration-ms", "2000"])
        self.command.assert_called_once_with("port", "LED PREVIEW 5 1 2000", timeout=10)
        self.verify.assert_not_called()
        self.assertTrue(cli.is_terminal("LED PREVIEW 5 1 2000", "OK LED PREVIEW"))

    def test_bad_preview_duration_is_a_parser_error_with_help(self):
        error = io.StringIO()
        with contextlib.redirect_stderr(error), self.assertRaises(SystemExit) as result:
            cli.parser().parse_args(["led", "preview", "blue", "--duration-ms", "5001"])
        self.assertEqual(result.exception.code, 2)
        self.assertIn("100 to 5000", error.getvalue())
        self.assertIn("tinytouch led preview --help", error.getvalue())

    def test_unknown_command_suggests_spelling_and_command_help(self):
        error = io.StringIO()
        with contextlib.redirect_stderr(error), self.assertRaises(SystemExit):
            cli.parser().parse_args(["staus"])
        self.assertIn("Did you mean 'status'?", error.getvalue())
        self.assertIn("tinytouch --help", error.getvalue())

    def test_help_shortcut_and_settings_alias(self):
        with self.assertRaises(SystemExit) as result:
            self.invoke(["help", "config"])
        self.assertEqual(result.exception.code, 0)
        self.assertIn("led_idle_color", self.output.getvalue())
        self.assertEqual(cli.parser().parse_args(["settings"]).func, cli.command_config)

    def test_error_output_does_not_pollute_stdout(self):
        error = io.StringIO()
        with (
            contextlib.redirect_stderr(error),
            mock.patch.object(cli.sys, "argv", ["tinytouch", "config", "unknown", "1"]),
        ):
            self.assertEqual(cli.main(), 1)
        self.assertEqual(self.output.getvalue(), "")
        self.assertIn("config list", error.getvalue())

    def test_ports_json_does_not_open_a_device(self):
        with mock.patch.object(cli, "detect_ports", return_value=["/dev/cu.TT"]), mock.patch.object(cli, "foreground_session") as session:
            self.invoke(["ports", "--json"])
        self.assertEqual(json.loads(self.output.getvalue()), ["/dev/cu.TT"])
        session.assert_not_called()

    def test_interactive_color_preset_and_preview_reach_regular_handlers(self):
        for command, answers, expected in (
            (["config", "led_idle_color"], ["purple"], "command_config"),
            (["led", "preset"], ["ocean"], "command_led"),
            (["led", "preview"], ["cyan", "flash"], "command_led"),
        ):
            with self.subTest(command=command), mock.patch("builtins.input", side_effect=answers), mock.patch.object(cli, expected) as handler:
                cli.interactive_command(cli.parser().parse_args([]), command)
                handler.assert_called_once()

    def test_auth_and_storage_failures_have_next_actions(self):
        self.assertIn(
            "timed out", cli.human_error("ERR AUTH no_match", touch_prompted=True)
        )
        self.assertIn("reconnect", cli.human_error("ERR AUTH sensor=offline"))
        self.assertIn("try again", cli.human_error("ERR LOCKED run=AUTH"))
        self.assertIn("try again", cli.human_error("ERR SET"))
        self.assertIn("update", cli.human_error("ERR COMMAND"))

    def test_general_device_errors_terminate_the_exchange(self):
        for error in ("ERR LOCKED run=AUTH", "ERR COMMAND", "ERR LINE"):
            with self.subTest(error=error):
                self.assertTrue(cli.is_terminal("SET LED_IDLE_COLOR 5", error))
        self.assertFalse(cli.is_terminal("SET LED_IDLE_COLOR 5", "OK STATUS led=on"))

    def test_json_write_is_rejected_before_device_access(self):
        with mock.patch.object(cli, "choose_port") as port:
            with self.assertRaisesRegex(cli.ToolError, "Omit --json when changing a setting"):
                self.invoke(["config", "led_idle_color", "purple", "--json"])
        port.assert_not_called()


if __name__ == "__main__":
    unittest.main()

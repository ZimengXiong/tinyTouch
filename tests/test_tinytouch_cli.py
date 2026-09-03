"""Focused protocol-6 tests for the host state machine."""

import base64
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("tinytouch_cli", str(ROOT / "tinytouch"))
spec = importlib.util.spec_from_loader(loader.name, loader)
cli = importlib.util.module_from_spec(spec)
loader.exec_module(cli)


class ProtocolSixTests(unittest.TestCase):
    def test_startup_mark_shows_version_and_command_section(self):
        with (
            mock.patch.object(cli.sys.stdout, "isatty", return_value=True),
            mock.patch.dict(cli.os.environ, {}, clear=True),
            mock.patch.object(cli, "say") as output,
        ):
            cli.show_startup_mark("factory-reset")
        text = "\n".join(call.args[0] for call in output.call_args_list)
        self.assertIn("⣰⣷⣼⣇", text)
        self.assertIn(cli.CLI_VERSION, text)
        self.assertIn("Factory Reset", text)

    def test_terminal_style_respects_no_color(self):
        with (
            mock.patch.object(cli.sys.stdout, "isatty", return_value=True),
            mock.patch.dict(cli.os.environ, {"NO_COLOR": "1"}, clear=True),
        ):
            self.assertEqual(cli.terminal_style("Setup", "36"), "Setup")

    def test_mode_prompt_explains_hid_and_piv(self):
        with (
            mock.patch.object(cli, "say") as output,
            mock.patch.object(cli, "ask", return_value="h"),
        ):
            self.assertEqual(cli.choose_mode(None), "hid")
        text = "\n".join(call.args[0] for call in output.call_args_list)
        self.assertIn("HID — Types your password", text)
        self.assertIn("PIV — Acts as a smart card", text)

    def test_enrollment_uses_directional_prompts(self):
        statuses = iter([{"fingerprints": "0"}, {"fingerprints": "4"}])
        with (
            mock.patch.object(cli, "status", side_effect=lambda _port: next(statuses)),
            mock.patch.object(cli, "unlock"),
            mock.patch.object(cli, "serial_command") as command,
            mock.patch.object(cli, "say"),
        ):
            cli.enroll("/dev/cu.TT-1234", False)
        prompts = [call.kwargs["touch_prompt"] for call in command.call_args_list]
        self.assertEqual(
            prompts,
            [
                "Tap the fingerprint sensor with the left edge of your finger, then lift your finger from the sensor.",
                "Tap the fingerprint sensor with the right edge of your finger, then lift your finger from the sensor.",
                "Tap the fingerprint sensor with the top of your finger, then lift your finger from the sensor.",
                "Tap the fingerprint sensor with the center of your finger, then lift your finger from the sensor.",
            ],
        )
        repeat_prompts = [
            call.kwargs["touch_again_prompt"] for call in command.call_args_list
        ]
        self.assertEqual(
            repeat_prompts,
            [
                "Tap the fingerprint sensor with the left edge of your finger again.",
                "Tap the fingerprint sensor with the right edge of your finger again.",
                "Tap the fingerprint sensor with the top of your finger again.",
                "Tap the fingerprint sensor with the center of your finger again.",
            ],
        )
        self.assertTrue(
            all(call.kwargs["lift_prompt"] is None for call in command.call_args_list)
        )

    def test_enrollment_events_follow_both_sensor_taps(self):
        with mock.patch.object(cli, "show_enrollment_view") as view:
            cli.show_enrollment_event(2, "EVENT TOUCH")
            cli.show_enrollment_event(2, "EVENT LIFT")
            cli.show_enrollment_event(2, "EVENT TOUCH_AGAIN")
        self.assertEqual(
            view.call_args_list,
            [
                mock.call(2, 0, True),
                mock.call(2, 0, False),
                mock.call(2, 1, True),
            ],
        )

    def test_serial_events_reach_enrollment_ui_in_protocol_order(self):
        class Device:
            responses = iter(
                (
                    b"EVENT TOUCH\n",
                    b"EVENT LIFT\n",
                    b"EVENT TOUCH_AGAIN\n",
                    b"OK FINGER\n",
                )
            )

            def write(self, _payload):
                pass

            def flush(self):
                pass

            def readline(self):
                return next(self.responses, b"")

        events = []
        cli.exchange_serial(
            Device(), "FINGER ENROLL 1", timeout=1, event_handler=events.append,
        )
        self.assertEqual(
            events,
            ["EVENT TOUCH", "EVENT LIFT", "EVENT TOUCH_AGAIN"],
        )

    def test_enrollment_intro_precedes_the_full_screen_view(self):
        with (
            mock.patch.object(cli, "say") as output,
            mock.patch.object(cli, "ask", return_value="") as ask,
        ):
            cli.introduce_enrollment()
        text = "\n".join(call.args[0] for call in output.call_args_list)
        self.assertIn("enroll different views of your fingerprint", text)
        self.assertIn("instructions on the next screen", text)
        ask.assert_called_once_with("Press Enter to continue.")

    def test_enrollment_oval_has_no_repeat_badge(self):
        self.assertNotIn("×2", cli.fingerprint_oval("left"))

    def test_update_release_refetches_latest_from_immutable_version(self):
        latest = json.dumps({"version": "0.1.10-prod"}).encode()
        exact = json.dumps({"version": "0.1.10-prod", "ota": {}}).encode()
        with mock.patch.object(cli, "download", side_effect=[latest, exact]) as download:
            root, manifest = cli.update_release()
        self.assertEqual(
            root,
            "https://github.com/ZimengXiong/tinyTouch/releases/download/v0.1.10-prod",
        )
        self.assertEqual(manifest["version"], "0.1.10-prod")
        self.assertIn("?nocache=", download.call_args_list[0].args[0])
        self.assertEqual(
            download.call_args_list[1].args[0], f"{root}/release-manifest.json"
        )

    def test_cli_update_pins_installer_and_firmware_to_one_release(self):
        target_version = "9.9.9-prod"
        root = f"https://github.com/ZimengXiong/tinyTouch/releases/download/v{target_version}"
        manifest = {"version": target_version, "ota": {}}
        args = SimpleNamespace(port=None, firmware_only=False, release_version=None)
        installer_result = SimpleNamespace(returncode=0)
        version_result = SimpleNamespace(
            returncode=0, stdout=f"tinyTouch CLI {target_version}\n"
        )
        with (
            mock.patch.object(cli, "update_release", return_value=(root, manifest)),
            mock.patch.object(cli, "download", return_value=b"installer") as download,
            mock.patch.object(
                cli.subprocess, "run", side_effect=[installer_result, version_result]
            ) as run,
            mock.patch.object(cli.shutil, "which", return_value="/usr/local/bin/tinytouch"),
            mock.patch.object(cli.os, "execv", side_effect=RuntimeError("exec")) as execv,
            self.assertRaisesRegex(RuntimeError, "exec"),
        ):
            cli.command_update(args)
        download.assert_called_once_with(f"{root}/install.sh")
        self.assertEqual(run.call_args_list[0].kwargs["env"]["TINYTOUCH_RELEASE_ROOT"], root)
        execv.assert_called_once_with(
            "/usr/local/bin/tinytouch",
            [
                "/usr/local/bin/tinytouch",
                "update",
                "--firmware-only",
                "--release-version",
                target_version,
            ],
        )

    def test_firmware_update_refreshes_an_existing_hid_helper(self):
        image = b"firmware"
        digest = hashlib.sha256(image).hexdigest()
        manifest = {
            "version": cli.CLI_VERSION,
            "ota": {"file": "tiny_touch_unified.bin", "sha256": digest},
        }
        args = SimpleNamespace(
            port=None, firmware_only=True, release_version=cli.CLI_VERSION
        )
        launch_agent = mock.MagicMock()
        launch_agent.exists.return_value = True
        with (
            mock.patch.object(cli, "update_release", return_value=("https://release", manifest)),
            mock.patch.object(cli, "LAUNCH_AGENT", launch_agent),
            mock.patch.object(cli, "install_helper") as install_helper,
            mock.patch.object(cli, "choose_port", return_value="/dev/cu.TT-1234"),
            mock.patch.object(cli, "status", return_value={"protocol": "6", "firmware": "x"}),
            mock.patch.object(cli, "protocol6"),
            mock.patch.object(cli, "download", return_value=image),
            mock.patch.object(cli, "stage_ota"),
            mock.patch.object(cli, "notify"),
        ):
            cli.command_update(args)
        install_helper.assert_called_once_with()

    def test_protocol_six_is_required(self):
        cli.protocol6({"firmware": "unified", "protocol": "6"})
        with self.assertRaisesRegex(cli.ToolError, "protocol 6"):
            cli.protocol6({"firmware": "unified", "protocol": "5"})

    def test_protocol_six_requires_a_firmware_version(self):
        with self.assertRaisesRegex(cli.ToolError, "did not report a firmware version"):
            cli.protocol6({"protocol": "6"})

    def test_protocol_six_terminal_responses_are_grouped_by_command(self):
        self.assertTrue(cli.is_terminal("SET MODE HID", "OK SET MODE"))
        self.assertTrue(cli.is_terminal("HOST ADD AABB 00", "OK HOST ADD"))
        self.assertTrue(cli.is_terminal("FINGER DELETE 1", "OK FINGER"))
        self.assertFalse(cli.is_terminal("SET MODE HID", "OK STATUS mode=hid"))

    def test_auth_failure_explains_whether_touch_started(self):
        self.assertIn("expired", cli.human_error("ERR AUTH", touch_prompted=True))
        self.assertIn("did not start", cli.human_error("ERR AUTH"))

    def test_status_requires_a_terminal_status_line(self):
        with mock.patch.object(cli, "serial_command", return_value=["OK STATUS protocol=6 mode=hid sensor=ready hosts=1"]):
            result = cli.status("/dev/cu.TT-1234")
        self.assertEqual(result["protocol"], "6")
        self.assertEqual(result["hosts"], "1")

    def test_hid_add_is_live_and_does_not_provision_piv(self):
        computer = "test-mac"
        key = hashlib.sha256(
            f"tinyTouch HID pairing|TT-1234|{computer}".encode("utf-8")
        ).digest()
        commands = []
        identifier = cli.host_id(key)
        registered = set()

        def exchange(_port, command, **_kwargs):
            commands.append(command)
            if command == "HOST LIST":
                ids = ",".join(sorted(registered)) or "none"
                return [f"OK HOST LIST ids={ids} capacity=8"]
            if command.startswith("HOST ADD "):
                registered.add(identifier)
                return ["OK HOST ADD"]
            if command == "STATUS":
                return ["OK STATUS protocol=6 firmware=unified mode=piv sensor=ready hosts=1"]
            return ["OK AUTH"]

        with (
            mock.patch.object(cli, "prepare_hid_password"),
            mock.patch.object(cli, "keychain_get", return_value=None),
            mock.patch.object(cli, "device_account", return_value="TT-1234"),
            mock.patch.object(cli, "keychain_exists", return_value=True),
            mock.patch.object(cli, "keychain_set"),
            mock.patch.object(cli, "password_for", return_value="test-password"),
            mock.patch.object(cli, "serial_command", side_effect=exchange),
            mock.patch.object(cli, "install_helper"),
            mock.patch.object(cli, "helper_loaded", return_value=True),
            mock.patch.object(cli.platform, "node", return_value=computer),
        ):
            cli.configure_hid("/dev/cu.TT-1234", {"mode": "piv", "hosts": "0"})

        self.assertIn(f"HOST ADD {identifier} {key.hex()}", commands)
        self.assertNotIn("PROVISION_BEGIN", " ".join(commands))
        self.assertNotIn("HOST ADD", " ".join(command for command in commands if command == "HOST LIST"))

    def test_piv_setup_stops_the_hid_helper_before_authorization(self):
        args = SimpleNamespace(port="/dev/cu.TT-1234", mode="piv", skip_enroll=True, no_pair=True)
        calls = []
        device = {
            "firmware": "unified", "protocol": "6", "mode": "hid", "sensor": "ready",
            "piv": "ready", "fingerprints": "0",
        }
        with (
            mock.patch.object(cli, "require_macos"),
            mock.patch.object(cli, "choose_mode", return_value="piv"),
            mock.patch.object(cli, "choose_port", return_value=args.port),
            mock.patch.object(cli, "status", return_value=device),
            mock.patch.object(cli, "protocol6"),
            mock.patch.object(cli, "sensor_ready"),
            mock.patch.object(cli, "foreground_session", return_value=mock.MagicMock(
                __enter__=mock.Mock(return_value=None),
                __exit__=mock.Mock(return_value=False),
            )),
            mock.patch.object(cli, "remove_helper", side_effect=lambda: calls.append("remove_helper")),
            mock.patch.object(
                cli, "unlock", side_effect=lambda _port, **_kwargs: calls.append("unlock")
            ),
            mock.patch.object(cli, "serial_command", return_value=["OK SET MODE"]),
            mock.patch.object(cli, "fresh_status", return_value={"mode": "piv"}),
            mock.patch.object(cli, "enroll"),
            mock.patch.object(cli, "notify"),
            mock.patch.object(cli, "wait_for_reconnect", side_effect=RuntimeError("stop after mode change")),
        ):
            with self.assertRaisesRegex(RuntimeError, "stop after mode change"):
                cli.command_setup(args)
        self.assertEqual(calls, ["remove_helper", "unlock"])

    def test_piv_pair_refreshes_presence_after_sudo_and_accepts_persisted_pairing(self):
        identity = "A" * 40
        args = SimpleNamespace(port="/dev/cu.TT-1234")
        calls = []
        with (
            mock.patch.object(cli, "require_macos"),
            mock.patch.object(cli, "piv_identities", side_effect=[([], [identity]), ([identity], [])]),
            mock.patch.object(cli, "authorize_macos", side_effect=lambda: calls.append("sudo")),
            mock.patch.object(cli, "choose_port", return_value=args.port),
            mock.patch.object(
                cli, "unlock", side_effect=lambda _port, **_kwargs: calls.append("touch")
            ) as unlock,
            mock.patch.object(cli, "run", side_effect=cli.ToolError("sc_auth failed")),
            mock.patch.object(cli, "say") as output,
        ):
            cli.command_pair(args)
        self.assertEqual(calls, ["sudo", "touch"])
        self.assertTrue(unlock.call_args.kwargs["explain_pin"])

    def test_piv_pair_explains_rejected_legacy_identity(self):
        identity = "A" * 40
        args = SimpleNamespace(port="/dev/cu.TT-1234")
        with (
            mock.patch.object(cli, "require_macos"),
            mock.patch.object(
                cli, "wait_for_piv_identities", return_value=([], [identity])
            ),
            mock.patch.object(cli, "authorize_macos"),
            mock.patch.object(cli, "choose_port", return_value=args.port),
            mock.patch.object(cli, "unlock"),
            mock.patch.object(
                cli, "run", side_effect=cli.ToolError("CryptoTokenKit error -8")
            ),
            mock.patch.object(cli, "piv_identities", return_value=([], [identity])),
        ):
            with self.assertRaisesRegex(
                cli.ToolError, "macOS rejected this PIV identity"
            ):
                cli.command_pair(args)

    def test_piv_pair_reports_missing_keychain_wrapping(self):
        identity = "A" * 40
        args = SimpleNamespace(port="/dev/cu.TT-1234")
        result = SimpleNamespace(
            stdout=(
                "User was successfully paired but user password will be required "
                "after next SmartCard login to unlock Login keychain."
            ),
            stderr="",
        )
        with (
            mock.patch.object(cli, "require_macos"),
            mock.patch.object(
                cli, "wait_for_piv_identities", return_value=([], [identity])
            ),
            mock.patch.object(cli, "authorize_macos"),
            mock.patch.object(cli, "choose_port", return_value=args.port),
            mock.patch.object(cli, "unlock"),
            mock.patch.object(cli, "run", return_value=result) as run,
        ):
            with self.assertRaisesRegex(cli.ToolError, "incomplete pairing was removed"):
                cli.command_pair(args)
        self.assertEqual(run.call_count, 2)
        self.assertIn("unpair", run.call_args.args[0])

    def test_piv_identity_selection_recommends_the_default(self):
        identities = ["A" * 40, "B" * 40]
        args = SimpleNamespace(port="/dev/cu.TT-1234")
        with (
            mock.patch.object(cli, "require_macos"),
            mock.patch.object(
                cli, "wait_for_piv_identities", return_value=([], identities)
            ),
            mock.patch.object(cli, "authorize_macos"),
            mock.patch.object(cli, "choose_port", return_value=args.port),
            mock.patch.object(cli, "unlock"),
            mock.patch.object(cli, "run"),
            mock.patch.object(cli, "ask", return_value="1"),
            mock.patch.object(cli, "say") as output,
        ):
            cli.command_pair(args)
        text = "\n".join(call.args[0] for call in output.call_args_list)
        self.assertIn("If you are not sure, select 1.", text)

    def test_new_piv_identity_wait_has_creation_guidance(self):
        args = SimpleNamespace(
            mode="piv", port="/dev/cu.TT-1234", skip_enroll=False, no_pair=False
        )
        device = {
                "firmware": "0.1.15-prod",
                "protocol": "6",
                "mode": "piv",
                "piv": "unconfigured",
                "fingerprints": "4",
                "sensor": "ready",
        }
        statuses = iter([device, {**device, "piv": "ready"}])
        identities = ([], ["A" * 40, "B" * 40])
        with (
            mock.patch.object(cli, "require_macos"),
            mock.patch.object(cli, "choose_port", return_value=args.port),
            mock.patch.object(cli, "remove_helper"),
            mock.patch.object(cli, "foreground_session"),
            mock.patch.object(cli, "status", side_effect=lambda _port: next(statuses)),
            mock.patch.object(cli, "protocol6"),
            mock.patch.object(cli, "sensor_ready"),
            mock.patch.object(cli, "unlock") as unlock,
            mock.patch.object(cli, "serial_command") as command,
            mock.patch.object(
                cli, "piv_identities", return_value=([], ["OLD" * 10 + "0" * 10])
            ),
            mock.patch.object(
                cli, "wait_for_piv_identities", return_value=identities
            ) as wait,
            mock.patch.object(cli, "fresh_status", return_value={"piv": "ready"}),
            mock.patch.object(cli, "enroll"),
            mock.patch.object(cli, "command_pair") as pair,
            mock.patch.object(cli, "say") as output,
        ):
            cli.command_setup(args)
        self.assertNotIn("explain_pin", unlock.call_args.kwargs)
        self.assertEqual(wait.call_args.kwargs["timeout"], 30.0)
        self.assertEqual(
            wait.call_args.kwargs["excluding"], {"OLD" * 10 + "0" * 10}
        )
        create_call = next(
            call for call in command.call_args_list if call.args[1] == "PIV CREATE"
        )
        self.assertIn("Creating PIV identities", create_call.kwargs["wait_message"])
        self.assertIn("Waiting for macOS", wait.call_args.kwargs["message"])
        self.assertTrue(pair.call_args.kwargs["separate_identity_list"])
        self.assertIn(
            "tinyTouch is ready in PIV mode.",
            [call.args[0] for call in output.call_args_list],
        )
        self.assertIn(
            "Setting up PIV certificates. This may take up to 30 seconds.",
            [call.args[0] for call in output.call_args_list],
        )

    def test_piv_identity_wait_ignores_the_identity_replaced_by_create(self):
        old_identity = "A" * 40
        new_identity = "B" * 40
        with (
            mock.patch.object(
                cli,
                "piv_identities",
                side_effect=[([], [old_identity]), ([], [new_identity])],
            ),
            mock.patch.object(cli.time, "sleep"),
        ):
            paired, available = cli.wait_for_piv_identities(
                timeout=1.0,
                excluding={old_identity},
            )
        self.assertEqual(paired, [])
        self.assertEqual(available, [new_identity])

    def test_macos_authorization_explains_hidden_password_input(self):
        results = [SimpleNamespace(returncode=1), SimpleNamespace(returncode=0)]
        with (
            mock.patch.object(cli, "_sudo_session_ready", False),
            mock.patch.object(cli.subprocess, "run", side_effect=results),
            mock.patch.object(cli, "say") as output,
        ):
            cli.authorize_macos()
        text = "\n".join(call.args[0] for call in output.call_args_list)
        self.assertIn("Authorize macOS in this terminal.", text)
        self.assertIn("Your typing is hidden", text)

    def test_piv_unlock_prints_pin_before_macos_can_prompt(self):
        with (
            mock.patch.object(
                cli, "serial_command", return_value=["OK AUTH"]
            ) as command,
            mock.patch.object(cli, "explain_piv_pin") as explain,
        ):
            cli.unlock(
                "/dev/cu.TT-1234",
                explain_pin=True,
                reason="pair PIV with this Mac",
            )
        explain.assert_called_once_with()
        self.assertEqual(
            command.call_args.kwargs["touch_prompt"],
            "Touch the fingerprint sensor now to pair PIV with this Mac.",
        )

    def test_hid_host_list_preserves_eight_host_capacity(self):
        with mock.patch.object(
            cli, "serial_command",
            return_value=["OK HOST LIST ids=0011223344556677,8899AABBCCDDEEFF capacity=8"],
        ):
            identifiers, capacity = cli.host_list("/dev/cu.TT-1234")
        self.assertEqual(capacity, 8)
        self.assertEqual(len(identifiers), 2)

    def test_factory_reset_requires_live_clear_before_local_cleanup(self):
        args = SimpleNamespace(port="/dev/cu.TT-1234")
        statuses = iter([
            {"firmware": "unified", "protocol": "6", "mode": "hid", "sensor": "ready", "hosts": "1", "fingerprints": "1"},
            {"firmware": "unified", "protocol": "6", "mode": "piv", "sensor": "ready", "hosts": "0", "fingerprints": "0"},
        ])
        calls = []
        with (
            mock.patch.object(cli, "choose_port", return_value=args.port),
            mock.patch.object(cli, "status", side_effect=lambda _port: next(statuses)),
            mock.patch.object(cli, "protocol6"),
            mock.patch.object(cli, "ask", return_value="y"),
            mock.patch.object(cli, "unlock"),
            mock.patch.object(cli, "serial_command", side_effect=lambda _p, command, **_k: calls.append(command) or ["OK RESET FACTORY"]),
            mock.patch.object(cli, "remove_helper"),
            mock.patch.object(cli, "device_account", return_value="TT-1234"),
            mock.patch.object(cli, "keychain_delete"),
            mock.patch.object(cli, "say") as output,
        ):
            cli.command_factory_reset(args)
        self.assertEqual(calls, ["RESET FACTORY"])
        output.assert_called_once_with("Factory reset completed.")

    def test_mode_verifies_the_live_mode_without_reconnect_command(self):
        args = SimpleNamespace(port="/dev/cu.TT-1234", mode="hid")
        calls = []
        with (
            mock.patch.object(cli, "choose_port", return_value=args.port),
            mock.patch.object(cli, "status", side_effect=[
                {"firmware": "unified", "protocol": "6", "mode": "piv", "sensor": "ready", "hosts": "1"},
                {"firmware": "unified", "protocol": "6", "mode": "hid", "sensor": "ready", "hosts": "1"},
            ]),
            mock.patch.object(cli, "protocol6"),
            mock.patch.object(cli, "unlock"),
            mock.patch.object(cli, "serial_command", side_effect=lambda _p, command, **_k: calls.append(command) or ["OK SET MODE"]),
            mock.patch.object(cli, "wait_for_reconnect", return_value=args.port),
            mock.patch.object(cli, "fresh_status", return_value={"mode": "hid"}),
            mock.patch.object(cli, "install_helper"),
            mock.patch.object(cli, "notify"),
        ):
            cli.command_mode(args)
        self.assertEqual(calls, ["SET MODE HID"])
        self.assertFalse(any("RESET" in command or "RECONNECT" in command for command in calls))

    def test_ota_staging_uses_inactive_slot_and_requires_power_cycle(self):
        try:
            import serial  # type: ignore
        except ImportError:
            self.skipTest("pyserial is not installed")

        writes = []

        class FakeSerial:
            def __init__(self, *_args, **_kwargs):
                self.responses = []

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def write(self, payload):
                command = payload.decode("ascii").strip()
                writes.append(command)
                words = command.split()
                if words[:2] == ["OTA", "BEGIN"]:
                    self.responses.append(b"OK OTA BEGIN next=0\n")
                elif words[:2] == ["OTA", "WRITE"]:
                    offset = int(words[3])
                    size = len(base64.b64decode(words[4]))
                    self.responses.append(f"OK OTA WRITE next={offset + size}\n".encode())
                elif words[:2] == ["OTA", "COMMIT"]:
                    self.responses.append(b"OK OTA STAGED power_cycle=required\n")

            def flush(self):
                pass

            def readline(self):
                return self.responses.pop(0) if self.responses else b""

        image = bytes(range(256)) * 2
        digest = hashlib.sha256(image).hexdigest()
        with (
            mock.patch.object(serial, "Serial", FakeSerial),
            mock.patch.object(cli, "serial_command", return_value=["OK"]) as command,
            mock.patch.object(cli, "unload_helper", return_value=False),
            mock.patch.object(cli, "say") as say,
        ):
            cli.stage_ota("/dev/cu.TT-1234", image, digest)
        self.assertEqual(command.call_args_list[0].args, ("/dev/cu.TT-1234", "OTA ABORT"))
        self.assertEqual(command.call_args_list[1].args, ("/dev/cu.TT-1234", "AUTH"))
        self.assertTrue(writes[0].startswith("OTA BEGIN "))
        self.assertTrue(writes[-1].startswith("OTA COMMIT "))
        self.assertNotIn("RESET", " ".join(writes))
        write_commands = [command for command in writes if command.startswith("OTA WRITE ")]
        self.assertEqual(len(write_commands), 1)
        self.assertEqual(len(base64.b64decode(write_commands[0].split()[4])), len(image))
        output = [call.args[0] for call in say.call_args_list]
        self.assertIn("Uploading firmware: 0%", output)
        self.assertIn("Uploading firmware: 100%", output)
        self.assertIn("Verifying firmware...", output)

    def test_interrupted_ota_aborts_its_session(self):
        try:
            import serial  # type: ignore
        except ImportError:
            self.skipTest("pyserial is not installed")

        writes = []

        class InterruptedSerial:
            def __init__(self, *_args, **_kwargs):
                self.responses = []

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def write(self, payload):
                command = payload.decode("ascii").strip()
                writes.append(command)
                if command.startswith("OTA BEGIN "):
                    self.responses.append(b"OK OTA BEGIN next=0\n")
                elif command.startswith("OTA WRITE "):
                    raise KeyboardInterrupt
                elif command.startswith("OTA ABORT "):
                    self.responses.append(b"OK OTA ABORT\n")

            def flush(self):
                pass

            def readline(self):
                return self.responses.pop(0) if self.responses else b""

        image = bytes(range(64))
        digest = hashlib.sha256(image).hexdigest()
        with (
            mock.patch.object(serial, "Serial", InterruptedSerial),
            mock.patch.object(cli, "serial_command", return_value=["OK"]),
            mock.patch.object(cli, "unload_helper", return_value=False),
            self.assertRaises(KeyboardInterrupt),
        ):
            cli.stage_ota("/dev/cu.TT-1234", image, digest)
        self.assertTrue(writes[-1].startswith("OTA ABORT "))

    def test_ota_writes_are_windowed(self):
        try:
            import serial  # type: ignore
        except ImportError:
            self.skipTest("pyserial is not installed")

        activity = []

        class WindowedSerial:
            def __init__(self, *_args, **_kwargs):
                self.responses = []

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def write(self, payload):
                command = payload.decode("ascii").strip()
                words = command.split()
                activity.append(("write", command))
                if words[:2] == ["OTA", "BEGIN"]:
                    self.responses.append(b"OK OTA BEGIN next=0\n")
                elif words[:2] == ["OTA", "WRITE"]:
                    offset = int(words[3])
                    size = len(base64.b64decode(words[4]))
                    self.responses.append(f"OK OTA WRITE next={offset + size}\n".encode())
                elif words[:2] == ["OTA", "COMMIT"]:
                    self.responses.append(b"OK OTA STAGED power_cycle=required\n")

            def flush(self):
                pass

            def readline(self):
                activity.append(("read", ""))
                return self.responses.pop(0) if self.responses else b""

        image = bytes(range(256)) * 40
        digest = hashlib.sha256(image).hexdigest()
        with (
            mock.patch.object(serial, "Serial", WindowedSerial),
            mock.patch.object(cli, "serial_command", return_value=["OK"]),
            mock.patch.object(cli, "unload_helper", return_value=False),
            mock.patch.object(cli, "say"),
        ):
            cli.stage_ota("/dev/cu.TT-1234", image, digest)

        begin = next(index for index, item in enumerate(activity) if "OTA BEGIN" in item[1])
        first_read = next(
            index for index, item in enumerate(activity[begin + 2:], begin + 2)
            if item[0] == "read"
        )
        writes_before_read = [
            item for item in activity[begin + 2:first_read] if item[0] == "write"
        ]
        self.assertGreater(len(writes_before_read), 1)

    def test_rom_flow_only_prompts_for_a_physical_reconnect(self):
        args = SimpleNamespace(port=None)
        with mock.patch.object(cli, "notify") as notify, mock.patch.object(cli, "say") as say:
            cli.command_rom(args)
        notify.assert_called_once()
        self.assertIn("physical reconnect", " ".join(call.args[0] for call in say.call_args_list))

    def test_helper_has_no_legacy_default_device_identity(self):
        source = (ROOT / "software" / "macos-helper" / "tinytouch_helper.py").read_text()
        self.assertNotIn("PREFERRED_SERIAL", source)
        self.assertNotIn("protocol-v5-compatible", source)

    def test_helper_retries_login_keychain_without_a_long_delay(self):
        source = (ROOT / "software" / "macos-helper" / "tinytouch_helper.py").read_text()
        self.assertIn("KEYCHAIN_RETRY_SECONDS = 0.5", source)
        self.assertIn("maximum=MAX_WORKER_RETRY_SECONDS", source)

    def test_launch_agent_is_latency_sensitive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                mock.patch.object(cli, "SUPPORT_DIR", root / "support"),
                mock.patch.object(cli, "LOG_DIR", root / "logs"),
                mock.patch.object(cli, "LAUNCH_AGENT", root / "agent.plist"),
                mock.patch.object(cli, "ensure_helper_environment", return_value=Path("/cli")),
                mock.patch.object(cli, "unload_helper"),
                mock.patch.object(cli, "load_helper"),
                mock.patch.object(cli, "helper_loaded", return_value=True),
                mock.patch.object(cli, "atomic_write_bytes") as write,
                mock.patch.object(cli, "FROZEN", True),
                mock.patch.object(cli.sys, "executable", "/cli"),
            ):
                cli.install_helper()
        payload = write.call_args.args[1]
        launch_agent = cli.plistlib.loads(payload)
        self.assertEqual(launch_agent["ProcessType"], "Interactive")
        self.assertEqual(launch_agent["ThrottleInterval"], 1)


if __name__ == "__main__":
    unittest.main()

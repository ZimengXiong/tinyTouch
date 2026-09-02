import importlib.machinery
import importlib.util
import plistlib
import tempfile
import unittest
from unittest import mock
from pathlib import Path
from types import SimpleNamespace
import base64
import hashlib
import os
import subprocess
import sys
import serial


ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("tinytouch_cli", str(ROOT / "tinytouch"))
spec = importlib.util.spec_from_loader(loader.name, loader)
cli = importlib.util.module_from_spec(spec)
loader.exec_module(cli)


class PackagingTests(unittest.TestCase):
    def test_browser_firmware_is_release_generated(self):
        import json

        public = ROOT / "docs" / "public"
        release = json.loads((public / "release.json").read_text())
        for kind in ("factory", "recovery"):
            manifest_path = public / "flash" / kind / "manifest.json"
            firmware = manifest_path.parent / "firmware"
            manifest = json.loads(manifest_path.read_text())
            self.assertEqual(manifest["version"], release["version"])
            for metadata in [*manifest["images"], manifest["fullImage"]]:
                image = firmware / metadata["file"]
                self.assertEqual(image.stat().st_size, metadata["size"])
                self.assertEqual(hashlib.sha256(image.read_bytes()).hexdigest(), metadata["sha256"])

    def test_web_flasher_progress_tracks_manifest_length(self):
        source = (ROOT / "docs" / ".vitepress" / "theme" / "FlashTool.vue").read_text()
        self.assertIn("fileArray.map(() => 0)", source)
        self.assertNotIn("[0, 0, 0]", source)

    def test_idf_version_guard_accepts_only_5_3(self):
        checker = ROOT / "firmware" / "check-idf-version"
        with tempfile.TemporaryDirectory() as directory:
            fake = Path(directory) / "idf.py"
            fake.write_text("#!/bin/sh\nprintf '%s\\n' \"$FAKE_IDF_VERSION\"\n")
            fake.chmod(0o755)
            environment = dict(os.environ, PATH=f"{directory}:{os.environ['PATH']}")
            for version in ("ESP-IDF v5.3", "ESP-IDF v5.3.2"):
                result = subprocess.run(
                    [str(checker)], env=dict(environment, FAKE_IDF_VERSION=version),
                    text=True, capture_output=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
            for version in ("ESP-IDF v5.2.4", "ESP-IDF v5.4.0", "ESP-IDF v6.0", "unknown"):
                result = subprocess.run(
                    [str(checker)], env=dict(environment, FAKE_IDF_VERSION=version),
                    text=True, capture_output=True,
                )
                self.assertNotEqual(result.returncode, 0, version)

    def test_factory_migration_stops_when_fingerprint_authorization_fails(self):
        authorization_error = cli.ToolError("Fingerprint not recognized.")
        with (
            mock.patch.object(cli, "run_idf") as run_idf,
            mock.patch.object(cli, "port_usb_location", return_value="1-2"),
            mock.patch.object(cli, "port_is_download_mode", return_value=False),
            mock.patch.object(cli, "status_fields", return_value={"firmware": "unified"}),
            mock.patch.object(
                cli, "unlock_configuration", side_effect=authorization_error
            ),
            mock.patch.object(cli, "serial_command") as serial_command,
            mock.patch.object(cli, "wait_for_download_port") as wait_for_download_port,
        ):
            with self.assertRaisesRegex(cli.ToolError, "Fingerprint not recognized"):
                cli.flash_piv("/dev/cu.example")
        serial_command.assert_not_called()
        wait_for_download_port.assert_not_called()
        run_idf.assert_not_called()

    def test_version_comes_from_shared_version_file(self):
        self.assertEqual(cli.CLI_VERSION, (ROOT / "VERSION").read_text().strip())

    def test_batch_channel_points_to_a_github_release_manifest(self):
        import json

        channel = json.loads((ROOT / "channels" / "batch-0.json").read_text())
        self.assertRegex(
            channel["manifest"],
            r"^https://github\.com/ZimengXiong/TinyTouch/releases/download/.+/release-manifest\.json$",
        )

    def test_local_release_directory_supplies_manifest_and_asset(self):
        import json
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            payload = b"local firmware"
            (path / "tiny_touch_unified.bin").write_bytes(payload)
            (path / "release-manifest.json").write_text(json.dumps({
                "version": "test", "build": "abc", "firmware": {},
            }))
            with mock.patch.dict(os.environ, {"TINYTOUCH_LOCAL_RELEASE_DIR": directory}):
                manifest = cli.release_manifest()
                result = cli.verified_release_asset({
                    "file": "tiny_touch_unified.bin",
                    "size": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }, manifest)
        self.assertEqual(result, payload)

    def test_only_unified_firmware_source_is_present(self):
        firmware = ROOT / "firmware"
        self.assertTrue((firmware / "tiny_touch_unified" / "CMakeLists.txt").is_file())
        self.assertFalse((firmware / "tiny_touch_keyboard").exists())
        self.assertFalse((firmware / "tiny_touch_smartcard").exists())

    def test_launch_agent_uses_current_repository(self):
        python = Path("/tmp/example-python")
        payload = plistlib.loads(cli.launch_agent_contents(python))
        self.assertEqual(payload["ProgramArguments"], [str(python), str(cli.HELPER)])
        self.assertTrue(payload["KeepAlive"])
        self.assertEqual(payload["ProcessType"], "Background")
        self.assertEqual(payload["ThrottleInterval"], 10)
        self.assertEqual(payload["EnvironmentVariables"]["TINYTOUCH_SERVICE_SCHEMA"], "2")
        self.assertEqual(payload["StandardOutPath"], str(cli.HELPER_LOG_PATH))
        self.assertNotIn("/tmp/", payload["StandardOutPath"])

    def test_helper_suspension_keeps_launchd_loaded_and_recovers_after_cli_exit(self):
        source = (ROOT / "tinytouch").read_text()
        unload = source.split("def unload_helper", 1)[1].split(
            "def remove_helper", 1
        )[0]
        load = source.split("def load_helper", 1)[1].split(
            "def install_helper", 1
        )[0]
        helper = (ROOT / "software" / "macos-helper" / "tinytouch_helper.py").read_text()
        self.assertNotIn('"bootout"', unload)
        self.assertIn("HELPER_SUSPEND_PATH", unload)
        self.assertIn("ForegroundLease", unload)
        self.assertNotIn('"pkill"', unload)
        self.assertIn("HELPER_LEASE.release", load)
        self.assertIn("LeaseObserver", helper)
        self.assertIn('diagnostic("manager.resumed")', helper)

    def test_legacy_helper_migration_is_restartable(self):
        with tempfile.TemporaryDirectory() as directory:
            launch_agent = Path(directory) / "com.tinytouch.helper.plist"
            migration = Path(directory) / "migration.json"
            launch_agent.write_bytes(plistlib.dumps({
                "Label": "com.tinytouch.helper",
                "ProgramArguments": ["/old/tinytouch", "_helper"],
                "KeepAlive": True,
            }))
            with (
                mock.patch.object(cli, "LAUNCH_AGENT", launch_agent),
                mock.patch.object(cli, "HELPER_MIGRATION_PATH", migration),
                mock.patch.object(cli, "HELPER_LOG_PATH", Path(directory) / "helper.log"),
                mock.patch.object(cli, "HELPER_ERROR_LOG_PATH", Path(directory) / "helper.err"),
                mock.patch.object(cli.subprocess, "run") as subprocess_run,
                mock.patch.object(cli, "run") as run,
            ):
                self.assertTrue(cli.migrate_helper_service(Path("/new/python")))
                self.assertFalse(migration.exists())
                payload = plistlib.loads(launch_agent.read_bytes())
                self.assertEqual(
                    payload["EnvironmentVariables"]["TINYTOUCH_SERVICE_SCHEMA"], "2"
                )
                subprocess_run.assert_called_once()
                run.assert_called_once_with([
                    "launchctl", "bootstrap", f"gui/{cli.os.getuid()}", str(launch_agent)
                ])

                migration.write_text("unfinished")
                self.assertTrue(cli.migrate_helper_service(Path("/new/python")))
                self.assertFalse(migration.exists())

    def test_factory_reset_removes_all_local_device_credentials(self):
        args = SimpleNamespace(port=None, yes=True)
        deleted = []
        with (
            mock.patch.object(cli, "require_macos"),
            mock.patch.object(cli, "choose_port", return_value="/dev/cu.example"),
            mock.patch.object(
                cli,
                "status_fields",
                return_value={"firmware": "unified", "mode": "hid", "sensor": "ok"},
            ),
            mock.patch.object(cli, "serial_command"),
            mock.patch.object(cli, "pairing_account_for_port", return_value="DEVICE"),
            mock.patch.object(
                cli, "keychain_delete", side_effect=lambda service, account: deleted.append((service, account))
            ),
            mock.patch.object(cli, "keyboard_settings_path") as settings_path,
            mock.patch.object(cli, "port_usb_location", return_value=None),
            mock.patch.object(cli, "wait_for_runtime_port"),
            mock.patch.object(cli.time, "sleep"),
        ):
            cli.command_factory_reset(args)
        expected_accounts = {"DEVICE"} | {
            f"DEVICE:fingerprint:{slot}" for slot in range(1, 6)
        }
        self.assertEqual(
            {account for service, account in deleted if service == cli.PASSWORD_SERVICE},
            expected_accounts,
        )
        self.assertIn((cli.PAIRING_SERVICE, "DEVICE"), deleted)
        settings_path.return_value.unlink.assert_called_once_with(missing_ok=True)

    def test_device_errors_are_translated(self):
        message = cli.human_device_error("ERR STATUS sensor")
        self.assertIn("fingerprint sensor", message)
        self.assertNotIn("ERR STATUS", message)

    def test_closed_input_has_human_instruction(self):
        with mock.patch("builtins.input", side_effect=EOFError):
            with self.assertRaises(cli.ToolError) as context:
                cli.choose_mode(None)
        self.assertIn("--mode piv", str(context.exception))

    def test_frozen_cli_installs_itself_and_updates_zprofile(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            source = home / "downloaded-tinytouch"
            source.write_bytes(b"signed executable")
            install_dir = home / ".local" / "bin"
            install_path = install_dir / "tinytouch"
            with (
                mock.patch.object(cli, "FROZEN", True),
                mock.patch.object(cli, "CLI_INSTALL_DIR", install_dir),
                mock.patch.object(cli, "CLI_INSTALL_PATH", install_path),
                mock.patch.object(cli.sys, "executable", str(source)),
                mock.patch.object(cli.Path, "home", return_value=home),
                mock.patch.dict(cli.os.environ, {"SHELL": "/bin/zsh"}),
            ):
                cli.install_command_if_needed()
            self.assertEqual(install_path.read_bytes(), source.read_bytes())
            self.assertIn(".local/bin", (home / ".zprofile").read_text())

    def test_frozen_cli_updates_fish_path(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            source = home / "downloaded-tinytouch"
            source.write_bytes(b"signed executable")
            install_dir = home / ".local" / "bin"
            install_path = install_dir / "tinytouch"
            with (
                mock.patch.object(cli, "FROZEN", True),
                mock.patch.object(cli, "CLI_INSTALL_DIR", install_dir),
                mock.patch.object(cli, "CLI_INSTALL_PATH", install_path),
                mock.patch.object(cli.sys, "executable", str(source)),
                mock.patch.object(cli.Path, "home", return_value=home),
                mock.patch.dict(cli.os.environ, {"SHELL": "/opt/homebrew/bin/fish"}),
            ):
                cli.install_command_if_needed()
            fish_config = home / ".config" / "fish" / "config.fish"
            self.assertIn('fish_add_path "$HOME/.local/bin"', fish_config.read_text())

    def test_unhealthy_sensor_status_still_identifies_unified_firmware(self):
        response = [
            "OK STATUS firmware=unified mode=piv sensor=no_response "
            "fingerprints=unknown keys=nvs hid_key=unconfigured"
        ]
        with mock.patch.object(cli, "serial_command", return_value=response):
            fields = cli.status_fields("/dev/cu.example")
        self.assertEqual(fields["firmware"], "unified")
        self.assertEqual(fields["sensor"], "no_response")

    def test_malformed_status_is_explained(self):
        with mock.patch.object(cli, "serial_command", return_value=["OK STATUS sensor=ok"]):
            with self.assertRaises(cli.ToolError) as context:
                cli.status_fields("/dev/cu.example")
        self.assertIn("without a runtime mode", str(context.exception))

    def test_legacy_firmware_error_has_update_action(self):
        with self.assertRaises(cli.ToolError) as context:
            cli.require_unified_firmware({"mode": "piv", "sensor": "ok"})
        self.assertIn("Older tinyTouch firmware", str(context.exception))
        self.assertIn(cli.FACTORY_FLASH_URL, str(context.exception))

    def test_sensor_error_names_required_uart_wiring(self):
        with self.assertRaises(cli.ToolError) as context:
            cli.require_fingerprint_sensor({"firmware": "unified", "sensor": "no_response"})
        message = str(context.exception)
        self.assertIn("firmware is running", message)
        self.assertIn("GPIO44", message)
        self.assertIn("GPIO43", message)
        self.assertIn("GPIO2", message)

    def test_busy_serial_port_has_specific_recovery(self):
        message = cli.serial_failure_message("/dev/cu.example", OSError(16, "Device busy"))
        self.assertIn("is busy", message)
        self.assertIn("Serial Monitor", message)

    def test_setup_preserves_status_failure_reason(self):
        args = cli.parser().parse_args(["setup", "--mode", "piv"])
        with (
            mock.patch.object(cli, "require_macos"),
            mock.patch.object(cli, "show_startup_mark"),
            mock.patch.object(cli, "choose_port", return_value="/dev/cu.example"),
            mock.patch.object(cli, "port_is_download_mode", return_value=False),
            mock.patch.object(
                cli, "status_fields", side_effect=cli.ToolError("fingerprint sensor detail")
            ),
        ):
            with self.assertRaises(cli.ToolError) as context:
                cli.command_setup(args)
        message = str(context.exception)
        self.assertIn("could not read its status", message)
        self.assertIn("fingerprint sensor detail", message)
        self.assertNotIn("factory firmware was not detected", message.lower())

    def test_protocol_two_adds_this_mac_without_replacing_existing_mac(self):
        key = bytes(range(32))
        commands = []
        with (
            mock.patch.object(cli, "ensure_helper_environment", return_value=Path("/tmp/python")),
            mock.patch.object(cli, "pairing_account_for_port", return_value="DEVICE"),
            mock.patch.object(cli, "keychain_get", return_value=None),
            mock.patch.object(cli, "keychain_exists", return_value=False),
            mock.patch.object(cli, "hid_key_ids", return_value=({"aaaaaaaaaaaaaaaa"}, 8)),
            mock.patch.object(cli.secrets, "token_bytes", return_value=key),
            mock.patch.object(cli, "serial_command", side_effect=lambda _p, command, **_k: commands.append(command) or ["OK"]),
            mock.patch.object(cli, "prompt_password", return_value="password"),
            mock.patch.object(cli, "keychain_set"),
            mock.patch.object(cli, "install_helper"),
        ):
            cli.configure_hid_credentials(
                "/dev/cu.example", {"protocol": "2", "hid_key": "configured"}
            )
        self.assertIn(f"HID_KEY_ADD {cli.hid_key_id(key)} {key.hex()}", commands)
        self.assertFalse(any(command.startswith("HID_KEY ") for command in commands))

    def test_legacy_hid_firmware_never_rekeys_another_mac_implicitly(self):
        with (
            mock.patch.object(cli, "ensure_helper_environment", return_value=Path("/tmp/python")),
            mock.patch.object(cli, "pairing_account_for_port", return_value="DEVICE"),
            mock.patch.object(cli, "keychain_get", return_value=None),
            mock.patch.object(cli, "keychain_exists", return_value=False),
        ):
            with self.assertRaises(cli.ToolError) as context:
                cli.configure_hid_credentials(
                    "/dev/cu.example", {"protocol": "1", "hid_key": "configured"}
                )
        self.assertIn("preserves the existing key", str(context.exception))

    def test_guided_enrollment_records_complete_profile(self):
        commands = []
        with mock.patch.object(
            cli, "serial_command",
            side_effect=lambda _port, command, **_kwargs: commands.append(command) or ["OK"],
        ):
            cli.enroll_finger_profile("/dev/cu.example")
        self.assertEqual(commands, [
            "ENROLL 1", "ENROLL 2", "ENROLL 3", "ENROLL 4", "PROFILE_COMPLETE 4",
        ])

    def test_migration_stages_recoverable_slot_before_activation(self):
        calls = []
        fake_esptool = SimpleNamespace(main=lambda arguments: calls.append(arguments))
        images = [
            {"file": name, "size": 1, "sha256": "0" * 64}
            for name in (
                "bootloader.bin", "partition-table.bin", "tiny_touch_unified.bin",
                "ota_data_initial.bin",
            )
        ]
        manifest = {
            "version": "0.4.3-preprod",
            "firmware": {"factory": {"images": images}},
        }
        with (
            mock.patch.dict(sys.modules, {"esptool": fake_esptool}),
            mock.patch.object(cli, "verified_release_asset", return_value=b"x"),
            mock.patch.object(cli, "port_is_download_mode", return_value=True),
            mock.patch.object(cli, "port_usb_location", return_value="1-2"),
            mock.patch.object(cli, "wait_for_runtime_port", return_value="/dev/cu.runtime"),
            mock.patch.object(cli, "classify_partition_layout", return_value="current-ota"),
            mock.patch.object(cli, "read_rom_mac", return_value="001122334455"),
            mock.patch.object(cli, "read_rom_flash", return_value=b"x"),
        ):
            result = cli.migrate_partition_layout(
                "/dev/cu.download", manifest, allow_uncorrelated_download=True
            )
        self.assertEqual(result, "/dev/cu.runtime")
        self.assertEqual(len(calls), 5)
        self.assertIn("0x110000", calls[0])
        self.assertNotIn("0x8000", calls[0])
        self.assertEqual(calls[0][9], "no_reset")
        self.assertIn("0x8000", calls[1])
        self.assertIn("0x210000", calls[2])
        self.assertIn("0x10000", calls[3])
        self.assertIn("0x0", calls[4])
        self.assertTrue(all(call[9] == "no_reset" for call in calls[:4]))
        self.assertEqual(calls[4][9], "hard_reset")

    def test_ota_transfer_uses_authenticated_ordered_session(self):
        writes = []

        class FakeSerial:
            def __init__(self, *_args, **_kwargs):
                self.responses = []
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def reset_input_buffer(self): pass
            def flush(self): pass
            def write(self, payload):
                command = payload.decode("ascii").strip()
                writes.append(command)
                if command.startswith("UPDATE_BEGIN "):
                    self.responses.append(b"OK UPDATE_BEGIN next=0\n")
                elif command.startswith("UPDATE_CHUNK "):
                    _, _, offset, encoded = command.split()
                    next_offset = int(offset) + len(base64.b64decode(encoded))
                    self.responses.append(f"OK UPDATE_CHUNK next={next_offset}\n".encode())
                elif command.startswith("UPDATE_COMMIT "):
                    self.responses.append(b"OK UPDATE_COMMIT\n")
                else:
                    self.responses.append(b"OK\n")
            def readline(self):
                return self.responses.pop(0) if self.responses else b""

        image = bytes(range(256)) * 3
        with (
            mock.patch.object(serial, "Serial", FakeSerial),
            mock.patch.object(cli, "serial_command", return_value=["OK UPDATE_UNLOCK"]),
            mock.patch.object(cli, "port_usb_location", return_value="1-2"),
            mock.patch.object(cli, "wait_for_port_departure"),
            mock.patch.object(cli, "wait_for_runtime_port", return_value="/dev/cu.runtime"),
            mock.patch.object(cli.secrets, "token_hex", return_value="a" * 32),
            mock.patch.object(cli.time, "sleep"),
        ):
            result = cli.install_ota_firmware("/dev/cu.example", image, "b" * 64)
        self.assertEqual(result, "/dev/cu.runtime")
        self.assertTrue(writes[0].startswith("UPDATE_BEGIN " + "a" * 32))
        self.assertEqual([item.split()[2] for item in writes[1:-1]], ["0", "360", "720"])
        self.assertEqual(writes[-1], "UPDATE_COMMIT " + "a" * 32)

    def test_ota_transfer_supports_configurable_chunk_size(self):
        writes = []

        class FakeSerial:
            def __init__(self, *_args, **_kwargs):
                self.responses = []
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def reset_input_buffer(self): pass
            def flush(self): pass
            def write(self, payload):
                command = payload.decode("ascii").strip()
                writes.append(command)
                if command.startswith("UPDATE_BEGIN "):
                    self.responses.append(b"OK UPDATE_BEGIN next=0\n")
                elif command.startswith("UPDATE_CHUNK "):
                    _, _, offset, encoded = command.split()
                    next_offset = int(offset) + len(base64.b64decode(encoded))
                    self.responses.append(f"OK UPDATE_CHUNK next={next_offset}\n".encode())
                elif command.startswith("UPDATE_COMMIT "):
                    self.responses.append(b"OK UPDATE_COMMIT\n")
                else:
                    self.responses.append(b"OK\n")
            def readline(self):
                return self.responses.pop(0) if self.responses else b""

        image = bytes(range(256)) * 20  # 5120 bytes
        with (
            mock.patch.object(serial, "Serial", FakeSerial),
            mock.patch.object(cli, "serial_command", return_value=["OK UPDATE_UNLOCK"]),
            mock.patch.object(cli, "port_usb_location", return_value="1-2"),
            mock.patch.object(cli, "wait_for_port_departure"),
            mock.patch.object(cli, "wait_for_runtime_port", return_value="/dev/cu.runtime"),
            mock.patch.object(cli.secrets, "token_hex", return_value="a" * 32),
            mock.patch.object(cli.time, "sleep"),
        ):
            result = cli.install_ota_firmware("/dev/cu.example", image, "b" * 64, chunk_size=3072)
        self.assertEqual(result, "/dev/cu.runtime")
        self.assertEqual([item.split()[2] for item in writes[1:-1]], ["0", "3072"])

    def test_update_skips_firmware_that_is_already_current(self):
        args = SimpleNamespace(port="/dev/cu.example", force=False)
        manifest = {"version": "0.4.3-preprod", "build": "abc123def456"}
        status = {
            "firmware": "unified", "sensor": "ok", "protocol": "5", "ota": "ready",
            "firmware_version": manifest["version"], "build": manifest["build"],
            "ota_state": "valid",
        }
        with (
            mock.patch.object(cli, "require_macos"),
            mock.patch.object(cli, "release_manifest", return_value=manifest),
            mock.patch.object(cli, "update_installed_cli", return_value=False),
            mock.patch.object(cli, "choose_port", return_value=args.port),
            mock.patch.object(cli, "port_is_download_mode", return_value=False),
            mock.patch.object(cli, "status_fields", return_value=status),
            mock.patch.object(cli, "serial_command", return_value=["OK CONFIRM_FIRMWARE"]) as serial_command,
            mock.patch.object(cli, "install_ota_firmware") as install_ota,
            mock.patch.object(cli, "migrate_partition_layout") as migrate,
            mock.patch.object(cli, "say") as say,
        ):
            cli.command_update(args)
        install_ota.assert_not_called()
        migrate.assert_not_called()
        serial_command.assert_not_called()
        say.assert_called_once_with("tinyTouch is up to date.")

    def test_config_set_validates_and_sends_authenticated_setting(self):
        args = cli.parser().parse_args(["config", "set", "typing-delay", "20"])
        with (
            mock.patch.object(cli, "choose_port", return_value="/dev/cu.example"),
            mock.patch.object(cli, "status_fields", return_value={"firmware": "unified"}),
            mock.patch.object(cli, "unlock_configuration") as unlock,
            mock.patch.object(cli, "serial_command") as serial_command,
        ):
            cli.command_config(args)
        unlock.assert_called_once_with("/dev/cu.example")
        serial_command.assert_called_once_with(
            "/dev/cu.example", "SETTING typing_delay_ms 20", timeout=3
        )

    def test_password_set_replaces_keychain_secret_without_printing_it(self):
        args = cli.parser().parse_args(["password", "set", "--fingerprint", "5"])
        with (
            mock.patch.object(cli, "require_macos"),
            mock.patch.object(cli, "choose_port", return_value="/dev/cu.example"),
            mock.patch.object(cli, "status_fields", return_value={"firmware": "unified"}),
            mock.patch.object(cli, "pairing_account_for_port", return_value="DEVICE"),
            mock.patch.object(cli, "prompt_password", return_value="top secret"),
            mock.patch.object(cli, "keychain_set") as keychain_set,
            mock.patch.object(cli, "ensure_helper_environment", return_value=Path("/tmp/python")),
            mock.patch.object(cli, "install_helper"),
            mock.patch.object(cli, "say") as say,
        ):
            cli.command_password(args)
        keychain_set.assert_called_once_with(
            cli.PASSWORD_SERVICE, "DEVICE:fingerprint:5", "top secret"
        )
        self.assertNotIn("top secret", " ".join(str(call) for call in say.call_args_list))

    def test_legacy_credentials_migrate_only_after_key_matches_device(self):
        legacy_key = bytes(range(32))
        credentials = {
            (cli.PAIRING_SERVICE, cli.DEFAULT_DEVICE_ACCOUNT): legacy_key.hex(),
            (cli.PASSWORD_SERVICE, cli.DEFAULT_DEVICE_ACCOUNT): "secret",
        }

        def get_secret(service, account):
            return credentials.get((service, account))

        def set_secret(service, account, value):
            credentials[(service, account)] = value

        with (
            mock.patch.object(cli, "keychain_get", side_effect=get_secret),
            mock.patch.object(cli, "keychain_exists", side_effect=lambda s, a: (s, a) in credentials),
            mock.patch.object(cli, "keychain_set", side_effect=set_secret),
            mock.patch.object(Path, "is_file", return_value=False),
        ):
            self.assertFalse(
                cli.migrate_legacy_hid_credentials("TT-NEW", {"other"}, single_key_device=False)
            )
            self.assertTrue(
                cli.migrate_legacy_hid_credentials(
                    "TT-NEW", {cli.hid_key_id(legacy_key)}, single_key_device=False
                )
            )
        self.assertEqual(credentials[(cli.PAIRING_SERVICE, "TT-NEW")], legacy_key.hex())
        self.assertEqual(credentials[(cli.PASSWORD_SERVICE, "TT-NEW")], "secret")

    def test_keychain_secrets_never_use_process_arguments(self):
        cli_source = (ROOT / "tinytouch").read_text()
        helper_source = (ROOT / "software" / "macos-helper" / "tinytouch_helper.py").read_text()
        self.assertNotIn("add-generic-password", cli_source)
        self.assertNotIn("add-generic-password", helper_source)
        self.assertNotIn("--set-password", helper_source)
        self.assertNotIn("--set-pairing-key", helper_source)
        self.assertIn("SecKeychainAddGenericPassword", (
            ROOT / "software" / "macos-helper" / "tinytouch_keychain.py"
        ).read_text())

    def test_uninstall_preserves_support_data_and_keychain(self):
        with tempfile.TemporaryDirectory() as directory:
            support = Path(directory) / "support"
            generation = support / "cli-generation"
            generation.mkdir(parents=True)
            executable = generation / "tinytouch"
            executable.write_text("executable")
            command = Path(directory) / "bin" / "tinytouch"
            command.parent.mkdir()
            command.symlink_to(executable)
            state = support / "state-TT-DEVICE.json"
            state.write_text("{}")
            with (
                mock.patch.object(cli, "SUPPORT_DIR", support),
                mock.patch.object(cli, "CLI_INSTALL_PATH", command),
                mock.patch.object(cli, "remove_helper"),
                mock.patch.object(cli.shutil, "which", return_value=str(command)),
                mock.patch.object(cli, "keychain_delete") as keychain_delete,
                mock.patch.object(cli, "say"),
            ):
                cli.command_uninstall(cli.parser().parse_args(["uninstall"]))
            self.assertFalse(command.exists())
            self.assertTrue(executable.exists())
            self.assertTrue(state.exists())
            keychain_delete.assert_not_called()


class ParserTests(unittest.TestCase):
    def test_setup_mode(self):
        args = cli.parser().parse_args(["setup", "--mode", "piv", "--skip-enroll"])
        self.assertEqual(args.mode, "piv")
        self.assertTrue(args.skip_enroll)

    def test_delete_slot(self):
        args = cli.parser().parse_args(["delete", "--slot", "5"])
        self.assertEqual(args.slot, 5)
        self.assertFalse(args.all)
        self.assertFalse(args.yes)

    def test_mode_alias(self):
        args = cli.parser().parse_args(["mode", "hid", "--skip-enroll"])
        self.assertEqual(args.mode, "hid")
        self.assertTrue(args.skip_enroll)

    def test_add_computer_uses_current_mode(self):
        args = cli.parser().parse_args(["add-computer", "--port", "/dev/cu.example"])
        self.assertEqual(args.port, "/dev/cu.example")

    def test_add_computer_in_hid_mode_only_adds_hid_credentials(self):
        args = cli.parser().parse_args(["add-computer", "--port", "/dev/cu.example"])
        status = {
            "firmware": "unified", "mode": "hid", "sensor": "ok",
            "fingerprints": "1", "keys": "nvs", "protocol": "2",
        }
        with (
            mock.patch.object(cli, "require_macos"),
            mock.patch.object(cli, "choose_port", return_value="/dev/cu.example"),
            mock.patch.object(cli, "status_fields", return_value=status),
            mock.patch.object(cli, "unlock_configuration") as unlock,
            mock.patch.object(cli, "configure_hid_credentials") as configure_hid,
            mock.patch.object(cli, "pair_piv") as pair_piv,
        ):
            cli.command_add_computer(args)
        unlock.assert_called_once_with("/dev/cu.example")
        configure_hid.assert_called_once_with("/dev/cu.example", status)
        pair_piv.assert_not_called()

    def test_add_computer_in_piv_mode_only_pairs_piv(self):
        args = cli.parser().parse_args(["add-computer", "--port", "/dev/cu.example"])
        status = {
            "firmware": "unified", "mode": "piv", "sensor": "ok",
            "fingerprints": "1", "keys": "nvs", "protocol": "2",
        }
        with (
            mock.patch.object(cli, "require_macos"),
            mock.patch.object(cli, "choose_port", return_value="/dev/cu.example"),
            mock.patch.object(cli, "status_fields", return_value=status),
            mock.patch.object(cli, "unlock_configuration") as unlock,
            mock.patch.object(cli, "configure_hid_credentials") as configure_hid,
            mock.patch.object(cli, "pair_piv") as pair_piv,
        ):
            cli.command_add_computer(args)
        unlock.assert_not_called()
        configure_hid.assert_not_called()
        pair_piv.assert_called_once_with(port="/dev/cu.example")

    def test_computers_remove_accepts_host_id(self):
        args = cli.parser().parse_args(["computers", "remove", "0123456789abcdef"])
        self.assertEqual(args.action, "remove")
        self.assertEqual(args.host_id, "0123456789abcdef")

    def test_customer_setup_has_no_firmware_build_options(self):
        args = cli.parser().parse_args(["setup", "--mode", "hid"])
        self.assertEqual(args.mode, "hid")
        self.assertFalse(hasattr(args, "board"))
        self.assertFalse(hasattr(args, "fqbn"))

    def test_verbose_before_command(self):
        args = cli.parser().parse_args(["--verbose", "status"])
        self.assertTrue(args.verbose)

    def test_verbose_after_command(self):
        args = cli.parser().parse_args(["status", "--verbose"])
        self.assertTrue(args.verbose)

    def test_verbose_defaults_off(self):
        args = cli.parser().parse_args(["status"])
        self.assertFalse(args.verbose)

    def test_update_is_a_customer_command(self):
        args = cli.parser().parse_args(["update", "--port", "/dev/cu.example"])
        self.assertEqual(args.port, "/dev/cu.example")

    def test_enroll_defaults_to_guided_profile(self):
        args = cli.parser().parse_args(["enroll"])
        self.assertIsNone(args.slot)

    def test_customization_commands_are_customer_commands(self):
        password = cli.parser().parse_args(["password", "set", "--fingerprint", "5"])
        config = cli.parser().parse_args(["config", "set", "enter", "off"])
        layout = cli.parser().parse_args(["keyboard-layout", "auto"])
        self.assertEqual(password.fingerprint, 5)
        self.assertEqual((config.setting, config.value), ("enter", "off"))
        self.assertEqual(layout.layout, "auto")


if __name__ == "__main__":
    unittest.main()

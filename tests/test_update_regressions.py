import base64
import hashlib
import importlib.machinery
import importlib.util
import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import serial

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("tinytouch_update_regression_cli", str(ROOT / "tinytouch"))
spec = importlib.util.spec_from_loader(loader.name, loader)
cli = importlib.util.module_from_spec(spec)
loader.exec_module(cli)


class UpdateRegressionTests(unittest.TestCase):
    @staticmethod
    def partition_table(entries):
        payload = bytearray(b"\xFF" * 0x1000)
        for index, entry in enumerate(entries):
            label, part_type, subtype, address, size, *optional_flags = entry
            flags = optional_flags[0] if optional_flags else 0
            encoded_label = label.encode("ascii").ljust(16, b"\0")
            payload[index * 32:(index + 1) * 32] = struct.pack(
                "<HBBII16sI", 0x50AA, part_type, subtype, address, size, encoded_label, flags
            )
        trailer_offset = len(entries) * 32
        payload[trailer_offset:trailer_offset + 32] = (
            b"\xEB\xEB" + b"\xFF" * 14 + hashlib.md5(payload[:trailer_offset]).digest()
        )
        return bytes(payload)

    def test_rollback_is_enabled_and_startup_does_not_auto_confirm(self):
        config = (ROOT / "firmware" / "tiny_touch_unified" / "sdkconfig").read_text()
        main = (ROOT / "firmware" / "tiny_touch_unified" / "main" / "main.c").read_text()
        self.assertIn("CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y", config)
        self.assertIn("CONFIG_APP_ROLLBACK_ENABLE=y", config)
        self.assertNotIn("esp_ota_mark_app_valid_cancel_rollback", main)

    def test_commit_never_rewrites_otadata_by_hand(self):
        source = (ROOT / "firmware" / "tiny_touch_unified" / "main" / "firmware_update.c").read_text()
        worker = source.split("static void firmware_update_commit_task", 1)[1].split(
            "bool firmware_update_commit", 1
        )[0]
        self.assertIn("esp_ota_set_boot_partition", worker)
        self.assertNotIn("esp_partition_erase_range", source)
        self.assertNotIn("esp_ota_select_entry_t", source)
        self.assertNotIn("esp_rom_crc32", source)

    def test_commit_validation_runs_on_a_dedicated_stack(self):
        update = (ROOT / "firmware" / "tiny_touch_unified" / "main" / "firmware_update.c").read_text()
        header = (ROOT / "firmware" / "tiny_touch_unified" / "main" / "firmware_update.h").read_text()
        console = (ROOT / "firmware" / "tiny_touch_unified" / "main" / "config_console.c").read_text()
        worker = update.split("static void firmware_update_commit_task", 1)[1].split(
            "bool firmware_update_commit", 1
        )[0]
        commit = update.split("bool firmware_update_commit", 1)[1]
        stack_size = int(
            header.split("#define FIRMWARE_UPDATE_COMMIT_STACK_SIZE", 1)[1].splitlines()[0]
        )
        self.assertGreaterEqual(stack_size, 8192)
        self.assertIn("esp_ota_end", worker)
        self.assertIn("esp_ota_set_boot_partition", worker)
        self.assertIn("uxTaskGetStackHighWaterMark", worker)
        self.assertIn("xTaskCreate", commit)
        self.assertIn("xSemaphoreTake(completed, portMAX_DELAY)", commit)
        self.assertIn("update_commit_stack_free=%u", console)
        self.assertIn('"OK UPDATE_COMMIT stack_free=%u"', console)

    def test_commit_worker_reports_end_and_boot_selection_phases(self):
        source = (ROOT / "firmware" / "tiny_touch_unified" / "main" / "firmware_update.c").read_text()
        worker = source.split("static void firmware_update_commit_task", 1)[1].split(
            "bool firmware_update_commit", 1
        )[0]
        self.assertLess(worker.index('set_error("ota_end_running"'), worker.index("esp_ota_end"))
        self.assertLess(worker.index('set_error("set_boot_running"'), worker.index("esp_ota_set_boot_partition"))
        self.assertIn('set_error("ota_end", end_result)', worker)
        self.assertIn('set_error("set_boot", boot_result)', worker)
        console = (ROOT / "firmware" / "tiny_touch_unified" / "main" / "config_console.c").read_text()
        commit_response = console.split('strncmp(command, "UPDATE_COMMIT ', 1)[1].split(
            'strncmp(command, "CONFIRM_FIRMWARE ', 1
        )[0]
        self.assertIn('"ERR UPDATE_COMMIT active=0 error=%s"', commit_response)

        for response in (
            "ERR UPDATE_COMMIT active=0 error=commit_sync",
            "ERR UPDATE_COMMIT active=0 error=commit_task_create",
        ):
            self.assertIn("current firmware", cli.human_device_error(response))

    def test_confirm_command_marks_pending_image_valid(self):
        update = (ROOT / "firmware" / "tiny_touch_unified" / "main" / "firmware_update.c").read_text()
        console = (ROOT / "firmware" / "tiny_touch_unified" / "main" / "config_console.c").read_text()
        self.assertIn("esp_ota_mark_app_valid_cancel_rollback()", update)
        self.assertIn("state != ESP_OTA_IMG_PENDING_VERIFY", update)
        confirm = console.split('strncmp(command, "CONFIRM_FIRMWARE ', 1)[1].split("FACTORY_RESET", 1)[0]
        self.assertIn("firmware_update_confirm_running()", confirm)
        self.assertIn("fingerprint_count()", confirm)

    def test_update_protocol_exposes_resync_status_and_preserves_unlock_on_failed_begin(self):
        source = (ROOT / "firmware" / "tiny_touch_unified" / "main" / "config_console.c").read_text()
        begin = source.split('strncmp(command, "UPDATE_BEGIN ', 1)[1].split('UPDATE_CHUNK', 1)[0]
        self.assertLess(begin.index("begin_firmware_update"), begin.index("update_authorized_until = 0"))
        self.assertIn('strncmp(command, "UPDATE_STATUS ', source)
        self.assertIn('"OK UPDATE_STATUS next=%u"', source)

    def test_serial_exchange_does_not_accept_async_lines_as_success(self):
        class Device:
            def __init__(self): self.reads = [b"PROMPT touch\n"]
            def write(self, _payload): pass
            def flush(self): pass
            def readline(self): return self.reads.pop(0) if self.reads else b""
        with self.assertRaises(cli.SerialResponseTimeout):
            cli.serial_exchange(Device(), "UPDATE_STATUS " + "a" * 32, timeout=0.001)

    def test_update_exchange_ignores_unrelated_error_response(self):
        class Device:
            def __init__(self): self.reads = [b"ERR MODE mode=piv\n", b"OK UPDATE_STATUS next=0\n"]
            def write(self, _payload): pass
            def flush(self): pass
            def readline(self): return self.reads.pop(0) if self.reads else b""
        lines = cli.serial_exchange(Device(), "UPDATE_STATUS " + "a" * 32, timeout=0.1)
        self.assertEqual(lines[-1], "OK UPDATE_STATUS next=0")

    def test_lost_chunk_ack_is_resolved_by_update_status(self):
        token = "a" * 32
        encoded = base64.b64encode(b"abcd").decode("ascii")
        commands = []
        def exchange(_device, command, **_kwargs):
            commands.append(command)
            if command == f"UPDATE_CHUNK {token} 0 {encoded}":
                raise cli.SerialResponseTimeout("lost ack")
            if command == f"UPDATE_STATUS {token}":
                return ["OK UPDATE_STATUS next=4"]
            self.fail(command)
        with mock.patch.object(cli, "serial_exchange", side_effect=exchange):
            result = cli.write_update_chunk(object(), token, 0, b"abcd", 4)
        self.assertEqual(result, 4)
        self.assertEqual(commands, [f"UPDATE_CHUNK {token} 0 {encoded}", f"UPDATE_STATUS {token}"])

    def test_commit_ack_loss_never_triggers_abort(self):
        writes = []
        class FakeSerial:
            def __init__(self, *_args, **_kwargs): pass
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def reset_input_buffer(self): pass
        def exchange(_device, command, **_kwargs):
            writes.append(command)
            if command.startswith("UPDATE_BEGIN "):
                return ["OK UPDATE_BEGIN next=0"]
            if command.startswith("UPDATE_CHUNK "):
                _, _, offset, encoded = command.split()
                return [f"OK UPDATE_CHUNK next={int(offset) + len(base64.b64decode(encoded))}"]
            if command.startswith("UPDATE_COMMIT "):
                raise cli.SerialResponseTimeout("USB reset before ACK")
            if command.startswith("UPDATE_ABORT "):
                self.fail("commit-time ambiguity must not abort")
            return ["OK"]
        image = bytes(range(32))
        with (
            mock.patch.object(serial, "Serial", FakeSerial),
            mock.patch.object(cli, "serial_command", return_value=["OK UPDATE_UNLOCK"]),
            mock.patch.object(cli, "serial_exchange", side_effect=exchange),
            mock.patch.object(cli, "port_usb_location", return_value="1-2"),
            mock.patch.object(cli, "port_usb_serial", return_value="TT-001122334455"),
            mock.patch.object(cli, "wait_for_port_departure"),
            mock.patch.object(cli, "wait_for_runtime_port", return_value="/dev/cu.runtime"),
            mock.patch.object(cli.secrets, "token_hex", return_value="a" * 32),
            mock.patch.object(cli.time, "sleep"),
        ):
            result = cli.install_ota_firmware("/dev/cu.example", image, "b" * 64)
        self.assertEqual(result, "/dev/cu.runtime")
        self.assertTrue(any(command.startswith("UPDATE_COMMIT ") for command in writes))
        self.assertFalse(any(command.startswith("UPDATE_ABORT ") for command in writes))


    def test_pending_current_image_is_repaired_before_update_short_circuit(self):
        args = SimpleNamespace(port="/dev/cu.example", force=False, local=False)
        manifest = {"version": "0.7.17-preprod", "build": "abc123def456"}
        pending = {
            "firmware": "unified", "sensor": "ok", "protocol": "5", "ota": "ready",
            "firmware_version": manifest["version"], "build": manifest["build"],
            "ota_state": "pending",
        }
        valid = dict(pending, ota_state="valid")
        statuses = [pending, valid]
        with (
            mock.patch.object(cli, "require_macos"),
            mock.patch.object(cli, "release_manifest", return_value=manifest),
            mock.patch.object(cli, "update_installed_cli", return_value=False),
            mock.patch.object(cli, "choose_port", return_value=args.port),
            mock.patch.object(cli, "port_is_download_mode", return_value=False),
            mock.patch.object(cli, "status_fields", side_effect=statuses),
            mock.patch.object(cli, "serial_command", return_value=["OK CONFIRM_FIRMWARE"]) as command,
            mock.patch.object(cli, "install_ota_firmware") as install,
        ):
            cli.command_update(args)
        command.assert_called_once_with(args.port, f"CONFIRM_FIRMWARE {manifest['build']}", timeout=3)
        install.assert_not_called()

    def test_protocol_five_confirmation_requires_explicit_pending_then_valid(self):
        status = {
            "firmware": "unified", "sensor": "ok", "protocol": "5",
            "firmware_version": "test", "build": "abc123def456",
        }
        with self.assertRaisesRegex(cli.SupportError, "omitted its running OTA state"):
            cli._confirm_running_firmware("/dev/cu.example", status)

        pending = dict(status, ota_state="pending")
        with (
            mock.patch.object(cli, "serial_command", return_value=["OK CONFIRM_FIRMWARE"]),
            mock.patch.object(cli, "status_fields", return_value=pending),
        ):
            with self.assertRaisesRegex(cli.SupportError, "without reporting a valid OTA state"):
                cli._confirm_running_firmware("/dev/cu.example", pending)

    def test_enriched_update_errors_are_actionable(self):
        cases = {
            "ERR UPDATE_BEGIN active=0 error=image_size": "does not fit",
            "ERR UPDATE_CHUNK next=360 active=1 error=write_offset": "disagreed",
            "ERR UPDATE_STATUS active=0 error=ota_write:-1": "flash write failed",
            "ERR UPDATE_COMMIT active=0 error=digest_mismatch": "SHA-256",
        }
        for response, expected in cases.items():
            with self.subTest(response=response):
                message = cli.human_device_error(response)
                self.assertIn(expected, message)
                self.assertIn("current firmware", message)

    def test_commit_panic_before_boot_selection_is_identified(self):
        status = {
            "reset_reason": "4",
            "update_last_reason": "none",
            "update_error": "none",
            "ota_running": "ota_0",
            "ota_boot": "ota_0",
            "ota_next": "ota_1",
            "ota_slot1": "ota_1:0.7.18-preprod:missing",
        }
        detail = cli.ota_commit_failure_detail(status, "0.7.18-preprod")
        self.assertIn("panicked", detail)
        self.assertIn("before it selected", detail)

        status["reset_reason"] = "3"
        self.assertEqual(cli.ota_commit_failure_detail(status, "0.7.18-preprod"), "")

        status["reset_reason"] = "4"
        status["update_last_reason"] = "commit_failed"
        status["update_error"] = "ota_end:-1"
        self.assertEqual(cli.ota_commit_failure_detail(status, "0.7.18-preprod"), "")

    def test_local_flash_uses_same_pending_confirmation_contract(self):
        source = (ROOT / "tinytouch").read_text()
        body = source.split("def command_flash_local", 1)[1].split("def command_pair", 1)[0]
        self.assertIn("time.sleep(2)", body)
        self.assertIn("_confirm_running_firmware", body)

    def test_local_flash_refuses_the_broken_protocol_four_committer(self):
        args = SimpleNamespace(binary=__file__, port="/dev/cu.example")
        status = {"firmware": "unified", "sensor": "ok", "protocol": "4", "ota": "ready"}
        with (
            mock.patch.object(cli, "require_macos"),
            mock.patch.object(cli, "choose_port", return_value=args.port),
            mock.patch.object(cli, "status_fields", return_value=status),
            mock.patch.object(cli, "install_ota_firmware") as install,
        ):
            with self.assertRaisesRegex(cli.ToolError, "protocol-5 updater"):
                cli.command_flash_local(args)
        install.assert_not_called()

    def test_ota_end_cleanup_never_aborts_consumed_handle(self):
        source = (ROOT / "firmware" / "tiny_touch_unified" / "main" / "firmware_update.c").read_text()
        body = source.split("bool firmware_update_commit", 1)[1]
        worker = source.split("static void firmware_update_commit_task", 1)[1].split(
            "bool firmware_update_commit", 1
        )[0]
        self.assertNotIn("firmware_update_abort", worker)
        self.assertLess(body.index("update_active = false"), body.index("xTaskCreate"))
        self.assertIn("update_handle = commit_handle", body)
        write = source.split("bool firmware_update_write", 1)[1].split("size_t firmware_update_written", 1)[0]
        self.assertIn("length > expected_size - written_size", write)

    def test_failed_final_confirmation_is_not_swallowed(self):
        args = SimpleNamespace(port="/dev/cu.example", force=False, local=False)
        manifest = {"version": "0.7.17-preprod", "build": "abc123def456"}
        status = {
            "firmware": "unified", "sensor": "ok", "protocol": "5", "ota": "ready",
            "firmware_version": manifest["version"], "build": manifest["build"],
            "ota_state": "pending",
        }
        with (
            mock.patch.object(cli, "require_macos"),
            mock.patch.object(cli, "release_manifest", return_value=manifest),
            mock.patch.object(cli, "update_installed_cli", return_value=False),
            mock.patch.object(cli, "choose_port", return_value=args.port),
            mock.patch.object(cli, "port_is_download_mode", return_value=False),
            mock.patch.object(cli, "status_fields", return_value=status),
            mock.patch.object(cli, "serial_command", side_effect=cli.ToolError("confirmation failed")),
        ):
            with self.assertRaisesRegex(cli.ToolError, "confirmation failed"):
                cli.command_update(args)

    def test_protocol_four_bootstraps_through_rom_instead_of_cdc_ota(self):
        args = SimpleNamespace(port="/dev/cu.example", force=False, local=False)
        manifest = {"version": "0.7.18-preprod", "build": "abc123def456"}
        old = {
            "firmware": "unified", "sensor": "ok", "protocol": "4", "ota": "ready",
            "firmware_version": "0.7.17-preprod", "build": "111111111111",
        }
        candidate = {
            "firmware": "unified", "sensor": "ok", "protocol": "5", "ota": "ready",
            "firmware_version": manifest["version"], "build": manifest["build"],
            "ota_state": "pending",
        }
        with (
            mock.patch.object(cli, "require_macos"),
            mock.patch.object(cli, "release_manifest", return_value=manifest),
            mock.patch.object(cli, "update_installed_cli", return_value=False),
            mock.patch.object(cli, "choose_port", return_value=args.port),
            mock.patch.object(cli, "port_is_download_mode", return_value=False),
            mock.patch.object(cli, "status_fields", side_effect=[old, candidate, candidate]),
            mock.patch.object(cli, "migrate_partition_layout", return_value=args.port) as migrate,
            mock.patch.object(cli, "install_ota_firmware") as cdc_ota,
            mock.patch.object(cli, "_confirm_running_firmware", return_value=candidate),
            mock.patch.object(cli.time, "sleep"),
        ):
            cli.command_update(args)
        migrate.assert_called_once_with(args.port, manifest)
        cdc_ota.assert_not_called()

    def test_migration_uses_idf_generated_otadata_and_bootloader_last(self):
        packaging = (ROOT / "packaging" / "assemble-release.py").read_text()
        self.assertNotIn("write_ota_slot", packaging)
        self.assertIn('output / "factory" / "ota_data_initial.bin"', packaging)
        source = (ROOT / "tinytouch").read_text()
        migration = source.split("def migrate_partition_layout", 1)[1].split("def enroll_finger_profile", 1)[0]
        self.assertIn('"0x110000"', migration)
        self.assertIn('"0x210000"', migration)
        self.assertIn('flash_arguments("hard_reset", "0x0"', migration)
        self.assertLess(migration.index('"0x110000"'), migration.index('"0x8000"'))
        self.assertLess(migration.index('"0x8000"'), migration.index('"0x210000"'))
        self.assertLess(migration.index('"0x210000"'), migration.index('"0x10000"'))

    def test_migration_downloads_before_switching_to_rom(self):
        images = [
            {"file": name, "size": 1, "sha256": "0" * 64}
            for name in ("bootloader.bin", "partition-table.bin", "tiny_touch_unified.bin", "ota_data_initial.bin")
        ]
        manifest = {"version": "test", "firmware": {"factory": {"images": images}}}
        events = []
        fake_esptool = SimpleNamespace(main=lambda arguments: events.append(("flash", arguments)))
        with (
            mock.patch.dict(__import__("sys").modules, {"esptool": fake_esptool}),
            mock.patch.object(cli, "verified_release_asset", side_effect=lambda meta, _manifest: events.append(("download", meta["file"])) or b"x"),
            mock.patch.object(cli, "port_usb_location", return_value="1-2"),
            mock.patch.object(cli, "port_usb_serial", return_value="TT-001122334455"),
            mock.patch.object(cli, "port_is_download_mode", return_value=False),
            mock.patch.object(cli, "unlock_configuration", side_effect=lambda _port: events.append(("unlock", None))),
            mock.patch.object(cli, "serial_command", side_effect=lambda _port, command, **_kwargs: events.append(("serial", command)) or ["OK"]),
            mock.patch.object(cli, "wait_for_download_port", return_value="/dev/cu.rom"),
            mock.patch.object(cli, "wait_for_runtime_port", return_value="/dev/cu.runtime"),
            mock.patch.object(cli, "classify_partition_layout", return_value="current-ota"),
            mock.patch.object(cli, "read_rom_mac", return_value="001122334455"),
            mock.patch.object(cli, "read_rom_flash", return_value=b"x"),
        ):
            cli.migrate_partition_layout("/dev/cu.runtime", manifest)
        boot_index = events.index(("serial", "BOOTLOADER"))
        download_indexes = [i for i, event in enumerate(events) if event[0] == "download"]
        self.assertEqual(len(download_indexes), 4)
        self.assertTrue(all(i < boot_index for i in download_indexes))

    def test_partition_layout_classifier_accepts_only_known_layouts(self):
        legacy = self.partition_table(cli.LEGACY_SINGLE_APP_LAYOUT)
        current = self.partition_table(cli.CURRENT_OTA_LAYOUT)
        self.assertEqual(cli.classify_partition_layout(legacy), "legacy-single-app")
        self.assertEqual(cli.classify_partition_layout(current), "current-ota")
        unknown = self.partition_table((
            ("nvs", 0x01, 0x02, 0x9000, 0x6000),
            ("factory", 0x00, 0x00, 0x10000, 0x200000),
        ))
        with self.assertRaisesRegex(cli.SupportError, "unknown partition layout"):
            cli.classify_partition_layout(unknown)

    def test_partition_layout_rejects_flags_bad_md5_and_missing_terminator(self):
        flagged = list(cli.CURRENT_OTA_LAYOUT)
        flagged[0] = (*flagged[0][:-1], 1)
        with self.assertRaisesRegex(cli.SupportError, "unsupported partition flags"):
            cli.classify_partition_layout(self.partition_table(tuple(flagged)))

        bad_md5 = bytearray(self.partition_table(cli.CURRENT_OTA_LAYOUT))
        bad_md5[len(cli.CURRENT_OTA_LAYOUT) * 32 + 16] ^= 0xFF
        with self.assertRaisesRegex(cli.SupportError, "MD5 checksum"):
            cli.classify_partition_layout(bytes(bad_md5))

        missing_trailer = bytearray(self.partition_table(cli.CURRENT_OTA_LAYOUT))
        trailer = len(cli.CURRENT_OTA_LAYOUT) * 32
        missing_trailer[trailer:trailer + 32] = b"\xFF" * 32
        with self.assertRaisesRegex(cli.SupportError, "no ESP-IDF MD5 trailer"):
            cli.classify_partition_layout(bytes(missing_trailer))

    def test_runtime_reconnect_never_substitutes_location_for_known_serial(self):
        wrong_board = SimpleNamespace(
            device="/dev/cu.wrong", vid=0x303A, pid=0x4001,
            serial_number="TT-AABBCCDDEEFF", location="1-2",
        )
        list_ports = SimpleNamespace(comports=lambda: [wrong_board])
        fake_tools = SimpleNamespace(list_ports=list_ports)
        fake_serial = SimpleNamespace(tools=fake_tools)
        with (
            mock.patch.dict(__import__("sys").modules, {
                "serial": fake_serial, "serial.tools": fake_tools,
                "serial.tools.list_ports": list_ports,
            }),
            mock.patch.object(cli.time, "monotonic", side_effect=[0.0, 0.0, 2.0]),
            mock.patch.object(cli.time, "sleep"),
        ):
            with self.assertRaises(cli.ToolError):
                cli.wait_for_runtime_port(
                    location="1-2", serial_number="TT-001122334455", wait_seconds=1
                )

    def test_migration_refuses_rom_mac_mismatch_before_flash(self):
        payloads = {
            "bootloader.bin": b"boot",
            "partition-table.bin": self.partition_table(cli.CURRENT_OTA_LAYOUT),
            "tiny_touch_unified.bin": b"image",
            "ota_data_initial.bin": b"ota",
        }
        images = [
            {"file": name, "size": len(payload), "sha256": "0" * 64}
            for name, payload in payloads.items()
        ]
        manifest = {"firmware": {"factory": {"images": images}}}
        fake_esptool = SimpleNamespace(main=mock.Mock())
        partition_read = mock.Mock()
        with (
            mock.patch.dict(__import__("sys").modules, {"esptool": fake_esptool}),
            mock.patch.object(cli, "verified_release_asset", side_effect=lambda meta, _manifest: payloads[meta["file"]]),
            mock.patch.object(cli, "port_usb_location", return_value="1-2"),
            mock.patch.object(cli, "port_usb_serial", return_value="TT-001122334455"),
            mock.patch.object(cli, "port_is_download_mode", return_value=False),
            mock.patch.object(cli, "unlock_configuration"),
            mock.patch.object(cli, "serial_command", return_value=["OK BOOTLOADER"]),
            mock.patch.object(cli, "wait_for_download_port", return_value="/dev/cu.rom"),
            mock.patch.object(cli, "read_rom_mac", return_value="AABBCCDDEEFF"),
            mock.patch.object(cli, "read_rom_flash", partition_read),
        ):
            with self.assertRaisesRegex(cli.SupportError, "not the selected tinyTouch"):
                cli.migrate_partition_layout("/dev/cu.runtime", manifest)
        fake_esptool.main.assert_not_called()
        partition_read.assert_not_called()

    def test_staged_image_readback_failure_stops_before_layout_change(self):
        payloads = {
            "bootloader.bin": b"boot",
            "partition-table.bin": self.partition_table(cli.CURRENT_OTA_LAYOUT),
            "tiny_touch_unified.bin": b"image",
            "ota_data_initial.bin": b"ota",
        }
        images = [
            {"file": name, "size": len(payload), "sha256": "0" * 64}
            for name, payload in payloads.items()
        ]
        manifest = {"firmware": {"factory": {"images": images}}}
        calls = []
        fake_esptool = SimpleNamespace(main=lambda arguments: calls.append(arguments))
        with (
            mock.patch.dict(__import__("sys").modules, {"esptool": fake_esptool}),
            mock.patch.object(cli, "verified_release_asset", side_effect=lambda meta, _manifest: payloads[meta["file"]]),
            mock.patch.object(cli, "port_usb_location", return_value="1-2"),
            mock.patch.object(cli, "port_usb_serial", return_value="TT-001122334455"),
            mock.patch.object(cli, "port_is_download_mode", return_value=False),
            mock.patch.object(cli, "unlock_configuration"),
            mock.patch.object(cli, "serial_command", return_value=["OK BOOTLOADER"]),
            mock.patch.object(cli, "wait_for_download_port", return_value="/dev/cu.rom"),
            mock.patch.object(cli, "read_rom_mac", return_value="001122334455"),
            mock.patch.object(cli, "read_rom_flash", side_effect=[
                self.partition_table(cli.LEGACY_SINGLE_APP_LAYOUT), b"wrong",
            ]),
        ):
            with self.assertRaisesRegex(cli.SupportError, "readback verification"):
                cli.migrate_partition_layout("/dev/cu.runtime", manifest)
        write_calls = [call for call in calls if "write_flash" in call]
        self.assertEqual(len(write_calls), 1)
        self.assertIn("0x110000", write_calls[0])

    def test_firmware_transaction_lock_rejects_a_second_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            lock_path = Path(directory) / "firmware.lock"
            with mock.patch.object(cli, "FIRMWARE_LOCK", lock_path), mock.patch.object(
                cli, "SUPPORT_DIR", Path(directory)
            ):
                with cli.firmware_transaction():
                    with self.assertRaisesRegex(cli.ToolError, "already running"):
                        with cli.firmware_transaction():
                            self.fail("second writer acquired the firmware lock")

    def test_firmware_transaction_does_not_mask_update_io_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            lock_path = Path(directory) / "firmware.lock"
            with mock.patch.object(cli, "FIRMWARE_LOCK", lock_path), mock.patch.object(
                cli, "SUPPORT_DIR", Path(directory)
            ):
                with self.assertRaisesRegex(OSError, "actual update failure"):
                    with cli.firmware_transaction():
                        raise OSError("actual update failure")

    def test_helper_keychain_denial_recovers_without_setup(self):
        helper = (ROOT / "software" / "macos-helper" / "tinytouch_helper.py").read_text()
        manager = helper.split("def run_manager", 1)[1].split("def run(port", 1)[0]
        self.assertIn("BackoffPolicy", manager)
        self.assertIn('"keychain.unavailable"', manager)
        self.assertIn("retry_after", manager)
        self.assertNotIn("blocked_devices", manager)
        top = helper.split("def main()", 1)[1]
        self.assertNotIn("except BaseException", top)
        self.assertIn("backoff.delay(failures)", top)
        self.assertIn("time.sleep(delay)", top)
        self.assertNotIn("parked until it is restarted", top)

    def test_web_serial_picker_filters_espressif_and_manifest_is_bounded(self):
        source = (
            ROOT / "docs" / ".vitepress" / "theme" / "FlashTool.vue"
        ).read_text()
        self.assertIn("requestPort({ filters: [{ usbVendorId: 0x303a }] })", source)
        self.assertIn("FLASH_BYTES", source)
        self.assertIn("flash regions overlap", source.lower())

    def test_recovery_does_not_host_erase_before_sensor_check(self):
        packaging = (ROOT / "packaging" / "assemble-release.py").read_text()
        main = (ROOT / "firmware" / "tiny_touch_unified" / "main" / "main.c").read_text()
        self.assertIn('"eraseAll": False', packaging)
        self.assertIn("postpones any NVS erase until after", main)
        recovery = main.split("static void run_recovery_once", 1)[1].split("#endif", 1)[0]
        self.assertLess(recovery.index("fingerprint_delete_all"), recovery.index("nvs_flash_erase"))

    def test_console_rejects_overlong_lines(self):
        source = (ROOT / "firmware" / "tiny_touch_unified" / "main" / "config_console.c").read_text()
        self.assertIn("command_overflow", source)
        self.assertIn('send_line("ERR LINE_TOO_LONG")', source)

    def test_fingerprint_packets_are_checksum_validated(self):
        source = (ROOT / "firmware" / "tiny_touch_unified" / "main" / "fingerprint.c").read_text()
        self.assertIn("fp_response_checksum_valid", source)
        self.assertIn("return received == expected", source)
        self.assertIn("checksum mismatch", source)

    def test_ccid_bounds_are_subtraction_based_and_in_transfer_is_serialized(self):
        source = (ROOT / "firmware" / "tiny_touch_unified" / "main" / "usb_ccid.c").read_text()
        self.assertIn("len > msg_len - 10", source)
        self.assertIn("in_busy", source)
        self.assertNotIn("len + 10 > msg_len", source)

    def test_release_network_smoke_test_cannot_fail_open(self):
        source = (ROOT / "packaging" / "build-standalone-macos.sh").read_text()
        self.assertIn("network_ok=0", source)
        self.assertIn("exit 1", source)


if __name__ == "__main__":
    unittest.main()

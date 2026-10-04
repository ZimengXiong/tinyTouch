"""Verify acknowledged writes without waiting on an unbounded driver drain."""

import base64
from collections import deque
from contextlib import contextmanager
import hashlib
import importlib.util
from pathlib import Path
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("transport_cli", ROOT / "macos/cli.py")
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)


class AcknowledgedSerial:
    def __init__(self):
        self.responses = deque()
        self.commands = []
        self.write_timeout = 2

    def write(self, data):
        command = data.decode("ascii").strip()
        self.commands.append(command)
        parts = command.split()
        if command == "PING":
            response = "PONG 6"
        elif parts[:2] == ["OTA", "BEGIN"]:
            response = "OK OTA BEGIN next=0"
        elif parts[:2] == ["OTA", "WRITE"]:
            offset = int(parts[3]) + len(base64.b64decode(parts[4]))
            response = f"OK OTA WRITE next={offset}"
        elif parts[:2] == ["OTA", "COMMIT"]:
            response = "OK OTA STAGED power_cycle=required"
        else:
            response = "OK OTA ABORT"
        self.responses.append((response + "\n").encode("ascii"))
        return len(data)

    def readline(self):
        if not self.responses:
            raise AssertionError("No reply is available in this transport fixture.")
        return self.responses.popleft()

    def flush(self):
        raise OSError("USB driver cannot drain queued output")


class CliTransportTests(unittest.TestCase):
    def test_foreground_command_uses_the_device_reply_as_acknowledgement(self):
        device = AcknowledgedSerial()
        self.assertEqual(cli.exchange_serial(device, "PING", timeout=1), ["PONG 6"])
        self.assertEqual(device.commands, ["PING"])

    def test_ota_command_uses_the_device_reply_as_acknowledgement(self):
        device = AcknowledgedSerial()
        self.assertEqual(cli.serial_exchange(device, "OTA ABORT token"), ["OK OTA ABORT"])

    def test_windowed_upload_finishes_without_driver_drain(self):
        device = AcknowledgedSerial()
        image = bytes(range(256)) * 200

        @contextmanager
        def session(port):
            with mock.patch.object(cli, "_active_serial", device):
                yield port

        with (
            mock.patch.object(cli, "foreground_session", side_effect=session),
            mock.patch.object(cli, "serial_command", return_value=["OK"]),
            mock.patch.object(cli, "say"),
        ):
            cli.stage_ota("fake-usb", image, hashlib.sha256(image).hexdigest())
        writes = [command for command in device.commands if command.startswith("OTA WRITE ")]
        self.assertGreater(len(writes), cli.OTA_WRITE_WINDOW)
        self.assertEqual(
            b"".join(base64.b64decode(command.split()[4]) for command in writes), image,
        )
        self.assertTrue(device.commands[-1].startswith("OTA COMMIT "))
        self.assertEqual(device.write_timeout, 2)

    def test_write_timeout_is_reported_before_reading_a_response(self):
        device = mock.Mock()
        device.write.side_effect = TimeoutError("serial write deadline")
        for exchange in (cli.exchange_serial, cli.serial_exchange):
            with self.subTest(exchange=exchange.__name__):
                with self.assertRaisesRegex(TimeoutError, "write deadline"):
                    exchange(device, "PING", timeout=1)
        device.readline.assert_not_called()


if __name__ == "__main__":
    unittest.main()

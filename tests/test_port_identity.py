"""Re-enumeration must never silently switch to an unrelated USB serial device."""

import importlib.machinery
import importlib.util
import sys
import types
import unittest
from collections import namedtuple
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("tinytouch_cli_ports", str(ROOT / "macos" / "cli.py"))
spec = importlib.util.spec_from_loader(loader.name, loader)
cli = importlib.util.module_from_spec(spec)
loader.exec_module(cli)

SerialPort = namedtuple("SerialPort", "device vid pid serial_number location")
TINYTOUCH = SerialPort("/dev/cu.usbmodem14401", 0x303A, 0x4001, "TT-0123456789AB", "")
OTHER = SerialPort("/dev/cu.usbmodem011NT1", 0x1234, 0x5678, "011NT1", "")


def fake_ports(*ports):
    module = types.ModuleType("tinytouch_ports")
    module.comports = lambda: list(ports)
    return mock.patch.dict(sys.modules, {"tinytouch_ports": module})


class CurrentPortTests(unittest.TestCase):
    def test_existing_path_is_kept(self):
        with mock.patch.object(cli.Path, "exists", return_value=True):
            self.assertEqual(cli.current_port("/dev/cu.usbmodem14403"), "/dev/cu.usbmodem14403")

    def test_unrelated_device_is_not_selected_while_tinytouch_reconnects(self):
        # Issue #33: after PIV CREATE the selected tinyTouch node vanished and
        # setup opened the only remaining (unrelated) serial device.
        with (
            mock.patch.object(cli.Path, "exists", return_value=False),
            fake_ports(OTHER),
            mock.patch.object(cli, "choose_port", side_effect=AssertionError("prompted")),
        ):
            self.assertEqual(cli.current_port("/dev/cu.usbmodem14403"), "/dev/cu.usbmodem14403")

    def test_renamed_tinytouch_node_is_followed(self):
        with (
            mock.patch.object(cli.Path, "exists", return_value=False),
            fake_ports(OTHER, TINYTOUCH),
            mock.patch.object(cli, "choose_port", side_effect=AssertionError("prompted")),
        ):
            self.assertEqual(cli.current_port("/dev/cu.usbmodem14403"), TINYTOUCH.device)

    def test_without_usb_metadata_falls_back_to_port_selection(self):
        with (
            mock.patch.object(cli.Path, "exists", return_value=False),
            mock.patch.dict(sys.modules, {"tinytouch_ports": None}),
            mock.patch.object(cli, "choose_port", return_value="/dev/cu.chosen") as choose,
        ):
            self.assertEqual(cli.current_port("/dev/cu.gone"), "/dev/cu.chosen")
        choose.assert_called_once_with(None)


class WaitForReconnectTests(unittest.TestCase):
    def test_remaining_unrelated_device_is_not_treated_as_reconnect(self):
        scans = iter([
            ["/dev/cu.usbmodem14403", OTHER.device],  # still connected
            [OTHER.device],                           # unplugged
            [OTHER.device],                           # other adapter remains
            [OTHER.device, TINYTOUCH.device],         # tinyTouch returns renamed
        ])
        ports = {
            1: (OTHER,), 2: (OTHER,), 3: (OTHER, TINYTOUCH),
        }
        calls = {"scan": 0}

        def detect():
            calls["scan"] += 1
            return next(scans)

        def comports():
            return list(ports[calls["scan"] - 1])

        module = types.ModuleType("tinytouch_ports")
        module.comports = comports
        with (
            mock.patch.dict(sys.modules, {"tinytouch_ports": module}),
            mock.patch.object(cli, "detect_ports", side_effect=detect),
            mock.patch.object(cli, "say"),
            mock.patch.object(cli.time, "sleep"),
        ):
            self.assertEqual(cli.wait_for_reconnect("/dev/cu.usbmodem14403"), TINYTOUCH.device)


if __name__ == "__main__":
    unittest.main()

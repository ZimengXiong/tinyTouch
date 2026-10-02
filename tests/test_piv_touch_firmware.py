"""Run production USB descriptor/visibility code against a simulated host."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PivTouchFirmwareTests(unittest.TestCase):
    def test_visibility_descriptors_discovery_expiry_and_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            build = Path(directory)
            for name in (
                "tusb.h", "esp_mac.h", "esp_log.h", "esp_timer.h",
                "freertos/FreeRTOS.h", "freertos/task.h", "freertos/semphr.h",
                "tinyusb.h", "tinyusb_default_config.h", "device/usbd_pvt.h",
            ):
                header = build / name
                header.parent.mkdir(parents=True, exist_ok=True)
                header.write_text('#include "usb_stubs.h"\n')
            executable = build / "usb_test"
            result = subprocess.run([
                os.environ.get("CC", "cc"), "-std=c11", "-Wall", "-Wextra", "-Werror",
                "-I", str(build), "-I", str(ROOT / "tests/host"),
                str(ROOT / "tests/host/usb_test.c"), "-o", str(executable),
            ], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)

"""Exercise production LED commands and NVS compatibility against simulated hardware."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class LedFirmwareTests(unittest.TestCase):
    def test_saved_led_control_and_fingerprint_results(self):
        with tempfile.TemporaryDirectory() as directory:
            build = Path(directory)
            for name in (
                "freertos/FreeRTOS.h", "freertos/semphr.h", "freertos/task.h",
                "driver/uart.h", "driver/gpio.h", "esp_log.h", "nvs.h", "mbedtls/sha256.h",
            ):
                header = build / name
                header.parent.mkdir(parents=True, exist_ok=True)
                header.write_text('#include "led_stubs.h"\n')
            executable = build / "led_test"
            subprocess.run([
                os.environ.get("CC", "cc"), "-std=c11", "-Wall", "-Wextra", "-Werror",
                "-Wno-sign-compare", "-Wno-misleading-indentation",
                "-I", str(build), "-I", str(ROOT / "tests/host"),
                str(ROOT / "tests/host/led_test.c"), "-o", str(executable),
            ], check=True, capture_output=True, text=True)
            subprocess.run([str(executable)], check=True, capture_output=True, timeout=10)

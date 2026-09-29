"""Exercise production LED commands and NVS compatibility against simulated hardware."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class LedFirmwareTests(unittest.TestCase):
    def test_saved_led_control_and_fingerprint_results(self):
        self.run_firmware_case("led_test.c")

    def test_fixed_finger_blocks_and_interrupted_enrollment(self):
        self.run_firmware_case("finger_groups_test.c")

    def test_console_authorization_inventory_and_disconnect(self):
        self.run_firmware_case("console_groups_test.c")

    def run_firmware_case(self, filename):
        with tempfile.TemporaryDirectory() as directory:
            build = Path(directory)
            for name in (
                "freertos/FreeRTOS.h", "freertos/semphr.h", "freertos/task.h",
                "driver/uart.h", "driver/gpio.h", "esp_log.h", "nvs.h", "mbedtls/sha256.h",
            ):
                header = build / name
                header.parent.mkdir(parents=True, exist_ok=True)
                header.write_text('#include "led_stubs.h"\n')
            console = (ROOT / "firmware/tiny_touch_unified/main/config_console.c").read_text()
            # Compile the production command dispatcher and disconnect callback,
            # with serial output and the authorization clock supplied by the fixture.
            parsing = console[console.index("static bool parse_u32("):console.index("static void touch_prompt(")]
            groups = console[console.index("static bool enrollment_running;"):console.index("static void factory_reset(")]
            settings = console[console.index("static void set_value("):console.index("static void host_add(")]
            (build / "console_under_test.h").write_text(parsing + settings + groups)
            executable = build / "led_test"
            flags = ["-fsanitize=address,undefined", "-fno-omit-frame-pointer"] if os.environ.get("TINYTOUCH_TEST_SANITIZERS") else []
            subprocess.run([
                os.environ.get("CC", "cc"), "-std=c11", "-Wall", "-Wextra", "-Werror",
                "-Wno-sign-compare", "-Wno-misleading-indentation", *flags,
                "-I", str(build), "-I", str(ROOT / "tests/host"),
                str(ROOT / "tests/host" / filename), "-o", str(executable),
            ], check=True, text=True)
            subprocess.run([str(executable)], check=True, timeout=10)

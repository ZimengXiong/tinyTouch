"""Execute the firmware LED controller with a fake clock and sensor ACKs."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Final


ROOT: Final = Path(__file__).parents[1]
MAIN: Final = ROOT / "firmware" / "tiny_touch_unified" / "main"
CASES: Final = (
    "idle_delay",
    "held_finger",
    "fade_finishes",
    "wake_from_off",
    "interrupt_fade",
    "green_expires",
    "red_expires",
    "restore_retries",
    "blackout_retries",
    "lost_fade_ack",
    "wake_retries",
    "reassert_after_capture",
    "clock_wrap",
    "stale_result_retry",
)


class FingerprintLedTests(unittest.TestCase):
    def test_led_behavior_with_sensor_acknowledgements(self) -> None:
        compiler = shutil.which("cc")
        if compiler is None:
            self.fail("A C compiler is required for firmware tests")
        with tempfile.TemporaryDirectory(prefix="tinytouch-led-") as directory:
            binary = Path(directory) / "fingerprint-led-test"
            subprocess.run(
                [
                    compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", "-pedantic",
                    "-I", str(MAIN), str(MAIN / "fingerprint_led.c"),
                    str(ROOT / "tests" / "firmware" / "fingerprint_led_test.c"),
                    "-o", str(binary),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            for case in CASES:
                with self.subTest(case=case):
                    result = subprocess.run(
                        [str(binary), case], capture_output=True, text=True, timeout=5,
                    )
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_driver_serializes_touch_and_retries_rejected_led_commands(self) -> None:
        compiler = shutil.which("cc")
        if compiler is None:
            self.fail("A C compiler is required for firmware tests")
        with tempfile.TemporaryDirectory(prefix="tinytouch-driver-") as directory:
            binary = Path(directory) / "fingerprint-driver-test"
            subprocess.run(
                [
                    compiler, "-std=c11", "-Wall", "-Werror", "-pedantic",
                    "-I", str(ROOT / "tests" / "firmware" / "stubs"),
                    str(MAIN / "fingerprint.c"), str(MAIN / "fingerprint_led.c"),
                    str(ROOT / "tests" / "firmware" / "fingerprint_driver_test.c"),
                    "-o", str(binary),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            for case in ("touch_during_led_command", "rejected_restore"):
                with self.subTest(case=case):
                    result = subprocess.run(
                        [str(binary), case], capture_output=True, text=True, timeout=5,
                    )
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()

"""Run the firmware's UART polling code against a simulated sensor."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STUBS = ROOT / "tests" / "firmware_polling"


class FingerprintPollingTests(unittest.TestCase):
    def test_touch_task_behavior(self):
        compiler = shutil.which("cc")
        if compiler is None:
            self.skipTest("A C compiler is required for firmware behavior tests")
        source = (ROOT / "firmware/tiny_touch_unified/main/touch_pin_hid.c").read_text()
        # Compile the task and its helpers verbatim; stub only external services.
        start = source.index("typedef enum {\n  AUTH_STATE_IDLE")
        end = source.index("void touch_pin_hid_start(void)", start)
        with tempfile.TemporaryDirectory() as directory:
            generated = Path(directory) / "touch_task.inc"
            generated.write_text(source[start:end])
            binary = Path(directory) / "touch-task"
            compilation = subprocess.run(
                [compiler, "-std=c11", "-I", str(STUBS), "-I", directory,
                 str(STUBS / "touch_task_test.c"), "-o", str(binary)],
                capture_output=True, text=True,
            )
            self.assertEqual(compilation.returncode, 0, compilation.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_sensor_polling_behavior(self):
        compiler = shutil.which("cc")
        if compiler is None:
            self.skipTest("A C compiler is required for firmware behavior tests")
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "fingerprint-polling"
            subprocess.run(
                [compiler, "-std=c11", "-I", str(STUBS),
                 str(STUBS / "polling_test.c"), "-o", str(binary)],
                check=True, capture_output=True, text=True,
            )
            result = subprocess.run(
                [str(binary)], check=True, capture_output=True, text=True,
            )
            self.assertIn("Polling behavior checks passed", result.stdout)


if __name__ == "__main__":
    unittest.main()

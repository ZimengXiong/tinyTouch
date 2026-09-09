"""Run the firmware's UART polling code against a simulated sensor."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STUBS = ROOT / "tests" / "firmware_polling"


class FingerprintPollingTests(unittest.TestCase):
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

"""Check that release packaging rejects the dependency behind the startup crash."""

import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "check_python_runtime",
    Path(__file__).resolve().parents[1] / "release" / "check-python-runtime.py",
)
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


class PythonRuntimeTests(unittest.TestCase):
    @patch.object(runtime.subprocess, "check_output")
    def test_rejects_system_expat(self, output):
        output.return_value = "pyexpat.so:\n\t/usr/lib/libexpat.1.dylib (version 8.0.0)\n"
        with self.assertRaisesRegex(RuntimeError, "macOS system Expat"):
            runtime.check_binary(Path("pyexpat.so"))

    @patch.object(runtime.subprocess, "check_output")
    def test_accepts_bundled_expat(self, output):
        output.return_value = "pyexpat.so:\n\t@loader_path/libexpat.1.dylib (version 8.0.0)\n"
        runtime.check_binary(Path("pyexpat.so"))

    @patch.object(runtime.subprocess, "check_output")
    def test_accepts_statically_linked_expat(self, output):
        output.return_value = "Python:\n\t/usr/lib/libSystem.B.dylib (version 1.0.0)\n"
        runtime.check_binary(Path("Python"))


if __name__ == "__main__":
    unittest.main()

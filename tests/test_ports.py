"""Check discovery metadata and native resource ownership on every exit path."""

import sys
import subprocess
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "macos"))
from tinytouch_ports import MacSerialPorts, SerialPort, _location_string  # noqa: E402
import tinytouch_helper as helper  # noqa: E402


class NativeOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.ports = MacSerialPorts.__new__(MacSerialPorts)
        self.ports.cf = mock.Mock()
        self.ports.io = mock.Mock()
        self.ports.cf.CFStringCreateWithCString.return_value = 101
        self.ports.io.IORegistryEntrySearchCFProperty.return_value = 102
        self.ports.cf.CFGetTypeID.return_value = 1
        self.ports.cf.CFNumberGetTypeID.return_value = 1

    def test_property_releases_key_and_value(self):
        def convert(_value, _kind, output):
            output._obj.value = 0x303A
            return True

        self.ports.cf.CFNumberGetValue.side_effect = convert
        self.assertEqual(self.ports._property(1, "idVendor", numeric=True), 0x303A)
        self.assertEqual(self.ports.cf.CFRelease.call_args_list,
                         [mock.call(102), mock.call(101)])

    def test_absent_property_releases_key(self):
        self.ports.io.IORegistryEntrySearchCFProperty.return_value = None
        self.assertIsNone(self.ports._property(1, "missing"))
        self.ports.cf.CFRelease.assert_called_once_with(101)

    def test_conversion_failure_releases_key_and_value(self):
        self.ports.cf.CFNumberGetValue.side_effect = ValueError("invalid property")
        with self.assertRaises(ValueError):
            self.ports._property(1, "idVendor", numeric=True)
        self.assertEqual(self.ports.cf.CFRelease.call_args_list,
                         [mock.call(102), mock.call(101)])

    def test_wrong_property_type_is_ignored_and_released(self):
        self.ports.cf.CFGetTypeID.return_value = 999
        self.assertIsNone(self.ports._property(1, "idVendor", numeric=True))
        self.ports.cf.CFNumberGetValue.assert_not_called()
        self.assertEqual(self.ports.cf.CFRelease.call_count, 2)

    def scan(self, properties, *, status=0):
        released = []

        def matching(_port, _dictionary, output):
            output._obj.value = 10
            return status

        self.ports.io.IOServiceGetMatchingServices.side_effect = matching
        self.ports.io.IOIteratorNext.side_effect = [11, 12, 0]
        self.ports.io.IOObjectRelease.side_effect = lambda value: released.append(
            value if isinstance(value, int) else value.value
        )
        with mock.patch.object(self.ports, "_property", side_effect=properties):
            try:
                return self.ports.comports()
            finally:
                self.released = released

    def test_scan_preserves_usb_identity_and_releases_services(self):
        result = self.scan([
            "/dev/cu.usbmodem1", 0x303A, 0x4001, "TT-001122334455", 0x02130000,
            None,
        ])
        self.assertEqual(result, [SerialPort(
            "/dev/cu.usbmodem1", 0x303A, 0x4001, "TT-001122334455", "2-1.3"
        )])
        self.assertEqual(self.released, [11, 12, 10])

    def test_scan_failure_releases_current_service_and_iterator(self):
        with self.assertRaises(ValueError):
            self.scan(ValueError("invalid property"))
        self.assertEqual(self.released, [11, 10])

    def test_matching_failure_releases_iterator(self):
        with self.assertRaises(OSError):
            self.scan([], status=5)
        self.assertEqual(self.released, [10])


class HelperDiscoveryTests(unittest.TestCase):
    def test_signed_registry_location_preserves_usb_bus_number(self):
        self.assertEqual(_location_string(-2146369536), "128-1.1")

    def test_only_tinytouch_usb_identities_become_endpoints(self):
        ports = [
            SerialPort("/dev/cu.tt", 0x303A, 0x4001, "TT-001122aabbcc", "1-1"),
            SerialPort("/dev/cu.other", 0x1234, 0x4001, "TT-001122AABBCC", "1-2"),
            SerialPort("/dev/cu.fake", 0x303A, 0x4001, "invalid", "1-3"),
            SerialPort("/dev/cu.bt", None, None, None, ""),
        ]
        with mock.patch.object(helper, "comports", return_value=ports):
            self.assertEqual(helper.device_endpoints(), [
                helper.DeviceEndpoint("TT-001122AABBCC", "/dev/cu.tt", "1-1")
            ])
            self.assertEqual(helper.port_identity("/dev/cu.tt"), "TT-001122AABBCC")


class NativeMemoryTests(unittest.TestCase):
    def test_repeated_scans_have_bounded_native_memory(self):
        # Use a fresh process so earlier tests cannot hide growth behind their
        # peak RSS. Python allocation tracking cannot detect CF/IOKit leaks.
        script = """
import resource
from tinytouch_ports import comports
for _ in range(1000):
    comports()
baseline = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
for _ in range(100000):
    comports()
growth = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - baseline
assert growth < 4 * 1024 * 1024, f"Serial discovery grew by {growth} bytes"
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parents[1] / "macos",
            capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()

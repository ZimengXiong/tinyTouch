import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "firmware" / "tiny_touch_unified" / "main"


class FirmwareInvariantTests(unittest.TestCase):
    def test_version_file_reconfigures_firmware_build(self):
        source = (ROOT / "firmware" / "tiny_touch_unified" / "CMakeLists.txt").read_text()
        self.assertIn("CMAKE_CONFIGURE_DEPENDS", source)
        self.assertIn("TINYTOUCH_VERSION_FILE", source)

    def test_atr_tck_is_valid(self):
        source = (MAIN / "usb_ccid.c").read_text()
        match = re.search(r"const uint8_t atr\[\] = \{([^}]+)\}", source)
        self.assertIsNotNone(match)
        atr = bytes(int(item.strip(), 16) for item in match.group(1).split(","))
        self.assertEqual(atr[0], 0x3B)
        self.assertEqual(0, self._xor(atr[1:]))

    @staticmethod
    def _xor(values):
        result = 0
        for value in values:
            result ^= value
        return result

    def test_piv_pin_is_layout_independent_and_always_submitted(self):
        source = (MAIN / "touch_pin_hid.c").read_text()
        body = source.split("static bool type_dummy_pin", 1)[1].split(
            "static bool type_ascii", 1
        )[0]
        self.assertIn("HID_KEY_KEYPAD_1", body)
        self.assertIn("send_key(0, HID_KEY_ENTER)", body)
        self.assertNotIn("device_config_submit_enter", body)
        self.assertNotIn("HID_KEY_0", body)

    def test_hid_payload_is_preflighted_before_first_key(self):
        source = (MAIN / "touch_pin_hid.c").read_text()
        body = source.split("static bool type_ascii", 1)[1].split(
            "static void bytes_to_hex", 1
        )[0]
        self.assertLess(body.index("for (size_t i = 0; i < length; i++)"),
                        body.index("send_key(modifier"))
        self.assertIn("device_config_submit_enter", body)

    def test_long_suspend_never_reconnects_until_resume(self):
        source = (MAIN / "touch_pin_hid.c").read_text()
        suspend = source.split("void tud_suspend_cb", 1)[1].split("void tud_resume_cb", 1)[0]
        self.assertNotIn("tud_disconnect", suspend)
        self.assertNotIn("tud_connect", suspend)
        self.assertIn("hid_suspended &&", source)

    def test_remote_wake_is_advertised_and_guarded_by_host_permission(self):
        descriptor = (MAIN / "usb_descriptors.c").read_text()
        hid = (MAIN / "touch_pin_hid.c").read_text()
        self.assertIn("TUSB_DESC_CONFIG_ATT_REMOTE_WAKEUP", descriptor)
        self.assertIn("hid_remote_wakeup_enabled", hid)
        self.assertIn("tud_remote_wakeup()", hid)

    def test_tinyusb_has_one_service_task(self):
        sources = "\n".join(path.read_text() for path in MAIN.glob("*.c"))
        self.assertNotIn("tud_task();", sources)
        self.assertIn("tinyusb_driver_install", sources)

    def test_enrollment_polls_sensor_instead_of_requiring_interrupt(self):
        source = (MAIN / "fingerprint.c").read_text()
        enrollment = source.split("bool fingerprint_enroll", 1)[1]
        self.assertIn("wait_capture_template", enrollment)
        self.assertNotIn("wait_finger_state", enrollment)

    def test_supported_board_wiring_is_stable(self):
        firmware = (MAIN / "fingerprint.c").read_text()
        for name, gpio in (("FP_TX_PIN", "43"), ("FP_RX_PIN", "44"),
                           ("FP_INT_PIN", "2")):
            self.assertRegex(firmware, rf"{name}\s*=\s*{gpio};")


if __name__ == "__main__":
    unittest.main()

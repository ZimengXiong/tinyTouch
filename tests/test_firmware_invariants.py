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

    def test_piv_private_key_slots_require_user_presence(self):
        source = (MAIN / "piv.c").read_text()
        body = source.split("static bool handle_general_authenticate", 1)[1].split(
            "void piv_init", 1
        )[0]

        self.assertIn("apdu[3] == 0x9a || apdu[3] == 0x9d", body)
        self.assertNotRegex(body, r"if\s*\(\s*apdu\[3\]\s*==\s*0x9a\s*\)")
        self.assertIn("deadline_active(user_presence_until", body)
        self.assertIn("if (!user_presence_valid && !pairing_mode_valid)", body)

        presence_gate = body.index("if (!user_presence_valid && !pairing_mode_valid)")
        denied_path = body[presence_gate:body.index("}", presence_gate)]
        self.assertIn("0x6982", denied_path)
        self.assertLess(presence_gate, body.index("mbedtls_rsa_private"))
        self.assertLess(presence_gate, body.index("mbedtls_pk_sign"))

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

    def test_ota_status_exposes_boot_and_slot_diagnostics(self):
        source = (MAIN / "config_console.c").read_text()
        for field in ("ota_running=%s", "ota_boot=%s", "ota_next=%s", "ota_slot0=%s", "ota_slot1=%s"):
            self.assertIn(field, source)
        self.assertIn("esp_ota_get_partition_description", source)
        self.assertIn("esp_ota_get_state_partition", source)

    def test_ota_session_reports_resumable_and_terminal_state(self):
        update = (MAIN / "firmware_update.c").read_text()
        console = (MAIN / "config_console.c").read_text()
        cmake = (MAIN / "CMakeLists.txt").read_text()
        self.assertIn("TINYTOUCH_PROTOCOL_VERSION=5", cmake)
        self.assertIn("firmware_update_active", update)
        self.assertIn("firmware_update_expected", update)
        self.assertIn("running_pending", update)
        self.assertIn("length > FIRMWARE_UPDATE_CHUNK_MAX", update)
        for field in ("update_expected=%u", "update_commit_stack_free=%u",
                      "update_last_reason=%s", "update_error=%s"):
            self.assertIn(field, console)
        self.assertIn('"OK UPDATE_STATUS next=%u"', console)
        self.assertIn('"ERR UPDATE_STATUS active=0 error=%s"', console)

    def test_ccid_rearms_failed_out_and_validates_descriptor_length(self):
        source = (MAIN / "usb_ccid.c").read_text()
        failure = source.split("if (result != XFER_RESULT_SUCCESS)", 1)[1].split(
            "if (ep_addr == CCID_EP_OUT)", 1
        )[0]
        self.assertIn("CCID_EP_OUT", failure)
        self.assertIn("usbd_edpt_xfer", failure)
        self.assertIn("max_len < required_len", source)

    def test_piv_chaining_and_key_reload_are_serialized(self):
        source = (MAIN / "piv.c").read_text()
        self.assertIn("piv_mutex", source)
        self.assertIn("data_len > sizeof(chained_apdu_data) - chained_apdu_data_len", source)
        self.assertIn("lc > apdu_len - 7", source)

    def test_piv_provisioning_is_all_or_nothing(self):
        source = (MAIN / "piv.c").read_text()
        clear = source.split("static void clear_provisioned_identity", 1)[1].split(
            "bool piv_uses_provisioned_keys", 1
        )[0]
        self.assertIn("reset_key_contexts()", clear)
        self.assertIn("cert_9a_der_len = 0", clear)
        self.assertIn("cert_9d_der_len = 0", clear)
        failure = source.split('"provisioned PIV material is incomplete or unusable"', 1)[1]
        self.assertIn("clear_provisioned_identity()", failure)
        self.assertGreaterEqual(source.count("!using_provisioned_keys"), 4)

    def test_fingerprint_drains_buffer_before_another_uart_read(self):
        source = (MAIN / "fingerprint.c").read_text()
        receive = source.split("while ((xTaskGetTickCount() - start) < deadline)", 1)[1].split(
            "return saw_ack", 1
        )[0]
        self.assertLess(receive.index("while (true)"), receive.index("uart_read_bytes"))
        self.assertIn("if (pos < expected) break", receive)


if __name__ == "__main__":
    unittest.main()

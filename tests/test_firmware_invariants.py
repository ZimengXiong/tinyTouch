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
        self.assertIn(
            "if ((!user_presence_valid || slot_already_used) && !pairing_mode_valid)",
            body,
        )

        presence_gate = body.index(
            "if ((!user_presence_valid || slot_already_used) && !pairing_mode_valid)"
        )
        denied_path = body[presence_gate:body.index("uint8_t sig", presence_gate)]
        self.assertIn("0x6982", denied_path)
        self.assertLess(presence_gate, body.index("mbedtls_rsa_private"))

    def test_one_touch_allows_keychain_unlock_without_reusing_a_slot(self):
        source = (MAIN / "piv.c").read_text()
        body = source.split("static bool handle_general_authenticate", 1)[1].split(
            "void piv_init", 1
        )[0]
        self.assertIn("user_presence_slots_used", body)
        self.assertIn("slot_already_used", body)
        self.assertIn("user_presence_slots_used |= slot_bit", body)
        self.assertIn("user_presence_slots_used == 0x03", body)
        self.assertIn("apdu[3] == 0x9d ? 0x02 : 0x01", body)

    def test_piv_command_chaining_accumulates_every_segment(self):
        source = (MAIN / "piv.c").read_text()
        body = source.split("if ((cla & 0x10) && ins == 0x87)", 1)[1].split(
            "uint8_t chained_apdu[", 1
        )[0]
        self.assertIn(
            "sizeof(chained_apdu_data) - chained_apdu_data_len", body
        )
        self.assertIn(
            "chained_apdu_data + chained_apdu_data_len", body
        )
        self.assertIn("chained_apdu_data_len += data_len", body)

    def test_piv_provisioning_secrets_are_wiped(self):
        source = (MAIN / "config_console.c").read_text()
        reset = source.split("static void reset_provisioning", 1)[1].split(
            "static provision_buffer_t", 1
        )[0]
        commit = source.split("static bool commit_provisioning", 1)[1].split(
            "static bool factory_reset", 1
        )[0]
        self.assertIn("secure_wipe(&provision_key9a", reset)
        self.assertIn("secure_wipe(&provision_key9d", reset)
        self.assertIn("reset_provisioning();", commit)

    def test_hid_payload_is_preflighted_before_first_key(self):
        source = (MAIN / "touch_pin_hid.c").read_text()
        body = source.split("static bool type_ascii", 1)[1].split(
            "static void bytes_to_hex", 1
        )[0]
        self.assertLess(body.index("for (size_t i = 0; i < length; i++)"),
                        body.index("send_key(modifier"))
        self.assertIn("device_config_submit_enter", body)

    def test_long_suspend_never_reconnects_until_resume(self):
        source = (MAIN / "usb_runtime.c").read_text()
        suspend = source.split("void tud_suspend_cb", 1)[1].split("void tud_resume_cb", 1)[0]
        self.assertNotIn("tud_disconnect", suspend)
        self.assertNotIn("tud_connect", suspend)
        self.assertIn("state.suspended &&", source)

    def test_remote_wake_is_advertised_and_guarded_by_host_permission(self):
        descriptor = (MAIN / "usb_descriptors.c").read_text()
        usb = (MAIN / "usb_runtime.c").read_text()
        self.assertIn("TUSB_DESC_CONFIG_ATT_REMOTE_WAKEUP", descriptor)
        self.assertIn("remote_wakeup_enabled", usb)
        self.assertIn("tud_remote_wakeup()", usb)

    def test_hid_mode_does_not_advertise_a_smart_card(self):
        descriptors = (MAIN / "usb_descriptors.c").read_text()
        usb_start = (MAIN / "usb_ccid.c").read_text()
        hid_descriptor = descriptors.split(
            "tiny_touch_hid_configuration_descriptor[]", 1
        )[1].split("static char tiny_touch_serial", 1)[0]
        self.assertNotIn("TUSB_DESC_INTERFACE, PIV_ITF_NUM_CCID", hid_descriptor)
        self.assertNotIn("0x0b, 0x00, 0x00", hid_descriptor)
        self.assertIn("TUD_HID_DESCRIPTOR", hid_descriptor)
        self.assertIn("TUD_CDC_DESCRIPTOR", hid_descriptor)
        self.assertIn("device_config_mode() == DEVICE_MODE_HID", usb_start)

    def test_remote_wake_requires_a_fingerprint_match(self):
        source = (MAIN / "touch_pin_hid.c").read_text()
        task = source.split("static void touch_hid_task", 1)[1].split(
            "void touch_pin_hid_start", 1
        )[0]
        self.assertEqual(1, task.count("usb_runtime_request_remote_wakeup()"))
        self.assertLess(
            task.index("fingerprint_authorize_poll_match()"),
            task.index("usb_runtime_request_remote_wakeup()"),
        )

    def test_fingerprint_uart_rejects_corrupt_packets(self):
        source = (MAIN / "fingerprint.c").read_text()
        command = source.split("static bool fp_command", 1)[1].split(
            "static bool fp_take", 1
        )[0]
        self.assertIn("fp_response_checksum_valid(response, expected)", command)

    def test_suspended_touch_does_not_require_sensor_interrupt(self):
        source = (MAIN / "touch_pin_hid.c").read_text()
        availability = source.split(
            "static bool suspended_sensor_poll_available", 1
        )[1].split("static void handle_fingerprint_match", 1)[0]
        task = source.split("static void touch_hid_task", 1)[1].split(
            "void touch_pin_hid_start", 1
        )[0]
        self.assertIn("usb_runtime_can_poll_sensor", availability)
        self.assertNotIn("fingerprint_present_hint", availability)
        self.assertIn(
            "tud_hid_ready() || suspended_sensor_poll_available()", task
        )

    def test_authentication_runtime_uses_explicit_states(self):
        source = (MAIN / "touch_pin_hid.c").read_text()
        for state in (
            "AUTH_STATE_IDLE",
            "AUTH_STATE_WAITING_FOR_HOST",
            "AUTH_STATE_WAITING_FOR_LIFT",
        ):
            self.assertIn(state, source)
        task = source.split("static void touch_hid_task", 1)[1].split(
            "void touch_pin_hid_start", 1
        )[0]
        self.assertNotIn("bool wait_for_lift", task)
        self.assertNotIn("TickType_t pending_since", task)
        self.assertLess(
            task.index("fingerprint_authorize_poll_match()"),
            task.index("usb_runtime_request_remote_wakeup()"),
        )

    def test_tinyusb_has_one_service_task(self):
        sources = "\n".join(path.read_text() for path in MAIN.glob("*.c"))
        self.assertNotIn("tud_task();", sources)
        self.assertIn("tinyusb_driver_install", sources)

    def test_runtime_health_is_exposed_in_status(self):
        health = (MAIN / "runtime_health.c").read_text()
        console = (MAIN / "config_console.c").read_text()
        for field in (
            "runtime_usb=%s",
            "runtime_uptime_ms=%",
            "runtime_reconnects=%",
            "runtime_auth_fail=%",
            "runtime_sensor_errors=%",
        ):
            self.assertIn(field, health)
        self.assertIn("runtime_health_format", console)

    def test_auth_task_is_supervised_and_allocations_fail_fast(self):
        touch = (MAIN / "touch_pin_hid.c").read_text()
        fingerprint = (MAIN / "fingerprint.c").read_text()
        console = (MAIN / "config_console.c").read_text()
        defaults = (ROOT / "firmware" / "tiny_touch_unified" / "sdkconfig.defaults").read_text()

        self.assertIn("esp_task_wdt_add(NULL)", touch)
        self.assertIn("esp_task_wdt_reset()", touch)
        self.assertIn("configASSERT(password_responses != NULL)", touch)
        self.assertIn("configASSERT(fp_mutex != NULL)", fingerprint)
        self.assertIn("configASSERT(cdc_write_mutex != NULL)", console)
        self.assertIn("CONFIG_ESP_TASK_WDT_PANIC=y", defaults)
        self.assertIn("CONFIG_ESP_TASK_WDT_TIMEOUT_S=30", defaults)

    def test_legacy_hid_credentials_migrate_and_corruption_fails_closed(self):
        source = (MAIN / "device_config.c").read_text()
        validation = source.split("static bool hid_hosts_valid", 1)[1].split(
            "static bool save_hid_hosts", 1
        )[0]
        reload = source.split("static void reload_locked", 1)[1].split(
            "void device_config_init", 1
        )[0]

        self.assertIn("derive_key_id(hosts[i].key, expected_id)", validation)
        self.assertIn("memcmp(hosts[i].id, expected_id", validation)
        self.assertIn("memcmp(hosts[i].id, hosts[previous].id", validation)
        self.assertIn("migrated_legacy_key = true", reload)
        self.assertIn("save_hid_hosts()", reload)
        self.assertIn("current_mode == DEVICE_MODE_HID && hid_host_count == 0", reload)
        self.assertIn("current_mode = DEVICE_MODE_PIV", reload)

    def test_sensor_transport_degrades_and_recovers_without_blocking_usb(self):
        fingerprint = (MAIN / "fingerprint.c").read_text()
        touch = (MAIN / "touch_pin_hid.c").read_text()
        health = fingerprint.split("void fingerprint_service_health", 1)[1].split(
            "bool fingerprint_authorize_once", 1
        )[0]

        self.assertIn("MAX_TRANSPORT_FAILURES", fingerprint)
        self.assertIn("RECOVERY_INTERVAL_MS", fingerprint)
        self.assertIn("sensor_ready = false", fingerprint)
        self.assertIn("fingerprint sensor recovered", health)
        self.assertIn("fingerprint_service_health()", touch)
        self.assertIn("if (!fingerprint_is_ready())", touch)
        self.assertLess(touch.index("usb_runtime_service_reconnect()"),
                        touch.index("fingerprint_service_health()"))

    def test_sensor_health_state_is_synchronized_across_cores(self):
        source = (MAIN / "fingerprint.c").read_text()

        self.assertIn("sensor_state_lock = portMUX_INITIALIZER_UNLOCKED", source)
        self.assertIn("static bool sensor_ready_snapshot(void)", source)
        self.assertIn("static void set_sensor_ready(bool ready)", source)
        getter = source.split("bool fingerprint_is_ready(void)", 1)[1].split(
            "void fingerprint_service_health", 1
        )[0]
        self.assertIn("sensor_ready_snapshot()", getter)

    def test_piv_parser_rejects_ambiguous_or_replayable_authentication(self):
        source = (MAIN / "piv.c").read_text()
        verify = source.split("static bool handle_verify", 1)[1].split(
            "static bool handle_general_authenticate", 1
        )[0]
        authenticate = source.split("static bool handle_general_authenticate", 1)[1].split(
            "void piv_init", 1
        )[0]
        parser = source.split("static bool parse_dynamic_auth", 1)[1].split(
            "static int piv_rng", 1
        )[0]

        self.assertIn("data_len != sizeof(expected_pin)", verify)
        self.assertIn("memcmp(data, expected_pin", verify)
        self.assertIn("apdu[2] == 0xff && apdu_len == 4", verify)
        self.assertIn("saw_challenge && saw_empty_response", parser)
        self.assertIn("outer_off + outer_len != data_len", authenticate)
        self.assertIn("challenge_len != sig_len", authenticate)
        self.assertIn("mbedtls_rsa_private", authenticate)
        self.assertNotIn("mbedtls_pk_sign", authenticate)
        self.assertLess(authenticate.index("pin_verified_until = 0"),
                        authenticate.index("parse_dynamic_auth"))
        self.assertIn("required > response_cap", authenticate)

        validation = source.split("static bool validate_identity_pair", 1)[1].split(
            "bool piv_validate_identity", 1
        )[0]
        self.assertIn("mbedtls_pk_get_bitlen(&key) == 2048", validation)

    def test_piv_transport_rejects_unsupported_cla_and_stale_responses(self):
        source = (MAIN / "piv.c").read_text()
        dispatch = source.split("static bool piv_handle_apdu_locked", 1)[1].split(
            "bool piv_handle_apdu", 1
        )[0]
        wrapper = source.split("bool piv_handle_apdu", 1)[1]

        self.assertIn("cla != 0x00 && cla != 0x10", dispatch)
        self.assertIn("(cla & 0x10) && ins != 0x87", dispatch)
        self.assertIn("if (ins != 0xc0)", dispatch)
        self.assertIn("if (!apdu || !response || response_cap < 2)", wrapper)

    def test_piv_apdu_lengths_are_exact_for_short_and_extended_cases(self):
        source = (MAIN / "piv.c").read_text()
        parser = source.split("static bool read_lc_data", 1)[1].split(
            "static bool tlv_read_len", 1
        )[0]
        response_length = source.split("static size_t apdu_le", 1)[1].split(
            "static bool respond_maybe_chunked", 1
        )[0]

        self.assertIn("apdu_len != data_end && apdu_len != data_end + 2", parser)
        self.assertIn("apdu_len != data_end && apdu_len != data_end + 1", parser)
        self.assertIn("lc == 0", parser)
        self.assertIn("apdu_len == data_end + 2", response_length)
        self.assertIn("apdu_len == data_end + 1", response_length)

    def test_console_rejects_wrapped_numbers_and_reopened_first_setup(self):
        source = (MAIN / "config_console.c").read_text()
        parser = source.split("static bool parse_unsigned", 1)[1].split(
            "static bool first_setup_allowed", 1
        )[0]
        first_setup = source.split("static bool first_setup_allowed", 1)[1].split(
            "static bool valid_update_token", 1
        )[0]

        self.assertIn("text[0] < '0' || text[0] > '9'", parser)
        self.assertIn("*end != '\\0'", parser)
        self.assertIn("parsed > maximum", parser)
        self.assertIn("!piv_uses_provisioned_keys()", first_setup)
        self.assertIn("!device_config_hid_key_configured()", first_setup)
        for command in ("ENROLL ", "PROFILE_COMPLETE ", "DELETE "):
            body = source.split(f'strncmp(command, "{command}', 1)[1].split(
                "} else if", 1
            )[0]
            self.assertIn("parse_unsigned", body)
        self.assertIn("decode_hex(arguments, token_bytes", source)

    def test_piv_provisioning_validates_key_pairs_before_nvs_commit(self):
        piv = (MAIN / "piv.c").read_text()
        console = (MAIN / "config_console.c").read_text()
        validation = piv.split("bool piv_validate_identity", 1)[1].split(
            "static bool load_nvs_string", 1
        )[0]
        commit = console.split("static bool commit_provisioning", 1)[1].split(
            "static bool factory_reset", 1
        )[0]

        self.assertIn("mbedtls_x509_crt_parse", piv)
        self.assertIn("mbedtls_pk_check_pair", piv)
        self.assertEqual(2, validation.count("validate_identity_pair"))
        self.assertLess(commit.index("piv_validate_identity"),
                        commit.index('nvs_open("piv_keys"'))
        self.assertIn("reset_provisioning();", commit)
        self.assertIn("piv_uses_provisioned_keys()", commit)
        self.assertIn("wipe_stored_identity();", piv)

    def test_mode_reconnect_reboots_to_select_the_persisted_descriptor(self):
        source = (MAIN / "config_console.c").read_text()
        reconnect = source.split('strcmp(command, "USB_RECONNECT")', 1)[1].split(
            '} else if (strcmp(command, "REBOOT")', 1
        )[0]
        reboot = source.split('strcmp(command, "REBOOT")', 1)[1].split(
            '} else if (strcmp(command, "BOOTLOADER")', 1
        )[0]
        reset = source.split('strcmp(command, "FACTORY_RESET")', 1)[1].split(
            '} else if (strcmp(command, "USB_RECONNECT")', 1
        )[0]

        self.assertIn("esp_restart()", reconnect)
        self.assertIn("require_config_authorization()", reconnect)
        self.assertIn("require_config_authorization()", reboot)
        self.assertIn("esp_restart()", reset)
        self.assertNotIn("tud_disconnect()", reconnect)
        self.assertNotIn("tud_connect()", reconnect)

    def test_ccid_transport_rejects_nonzero_slots_and_ambiguous_lengths(self):
        source = (MAIN / "usb_ccid.c").read_text()
        handler = source.split("static void handle_message", 1)[1].split(
            "static void ccid_init", 1
        )[0]

        self.assertIn("if (slot != 0)", handler)
        self.assertIn("len != msg_len - 10", handler)

    def test_ota_validation_worker_is_watchdog_supervised(self):
        source = (MAIN / "firmware_update.c").read_text()
        worker = source.split("static void firmware_update_commit_task", 1)[1].split(
            "bool firmware_update_commit", 1
        )[0]

        self.assertIn("esp_task_wdt_add(NULL)", worker)
        self.assertIn("esp_task_wdt_delete(NULL)", worker)
        self.assertLess(worker.index("esp_task_wdt_add(NULL)"),
                        worker.index("esp_ota_end"))

    def test_device_configuration_is_serialized_and_hid_hosts_are_snapshotted(self):
        config = (MAIN / "device_config.c").read_text()
        hid = (MAIN / "touch_pin_hid.c").read_text()

        self.assertIn("xSemaphoreCreateMutex()", config)
        self.assertIn("static void config_lock(void)", config)
        self.assertIn("device_config_copy_hid_hosts", config)
        self.assertIn("device_config_copy_hid_hosts(hosts)", hid)
        request = hid.split("static bool request_and_type_password", 1)[1]
        self.assertNotIn("device_config_get_hid_host(i", request)

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
        self.assertIn("apdu_len != data_end && apdu_len != data_end + 2", source)

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

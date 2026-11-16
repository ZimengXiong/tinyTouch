"""Optional PIV PIN policy: retry limit, fingerprint fallback, and enforcement."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "firmware" / "tiny_touch_unified" / "main"


def section(source: str, start: str, end: str) -> str:
    return source[source.index(start):source.index(end, source.index(start))]


class PivPinFirmwareTests(unittest.TestCase):
    def test_policy_state_machine_against_simulated_flash(self):
        with tempfile.TemporaryDirectory() as directory:
            build = Path(directory)
            for name in ("esp_random.h", "freertos/FreeRTOS.h", "freertos/semphr.h", "freertos/task.h",
                         "mbedtls/sha256.h", "nvs.h"):
                header = build / name
                header.parent.mkdir(parents=True, exist_ok=True)
                header.write_text('#include "piv_pin_stubs.h"\n')
            executable = build / "piv_pin_test"
            flags = ["-fsanitize=address,undefined", "-fno-omit-frame-pointer"] if os.environ.get("TINYTOUCH_TEST_SANITIZERS") else []
            subprocess.run([
                os.environ.get("CC", "cc"), "-std=c11", "-Wall", "-Wextra", "-Werror", *flags,
                "-I", str(build), "-I", str(ROOT / "tests/host"), "-I", str(MAIN),
                str(ROOT / "tests/host/piv_pin_test.c"), "-o", str(executable),
            ], check=True, text=True)
            subprocess.run([str(executable)], check=True, timeout=10)

    def test_pending_fallback_blocks_signing_before_any_presence_window(self):
        authenticate = section((MAIN / "piv.c").read_text(),
                               "static bool handle_general_authenticate(", "void piv_init(")
        required = authenticate.index("if (piv_pin_required())")
        self.assertLess(required, authenticate.index("deadline_active(user_presence_until"))
        self.assertIn("0x6982", authenticate[required:required + 300])

    def test_fallback_needs_both_the_pin_and_a_fingerprint(self):
        source = (MAIN / "piv.c").read_text()
        verify = section(source, "static bool handle_verify(", "static bool handle_general_authenticate(")
        # A correct PIN never grants presence; only a finger after it does.
        self.assertNotIn("user_presence", verify)
        self.assertNotIn("piv_note_user_presence", verify)
        # Outside the fallback, only the dummy reaches 9000 and no retry is spent.
        normal = verify.index("pin_state == PIV_PIN_STATE_ON)")
        self.assertLess(normal, verify.index("piv_pin_verify_piv(data)"))
        self.assertIn("0x6a80", verify[normal:normal + 200])
        touch = (MAIN / "touch_pin_hid.c").read_text()
        match = section(touch, "static void handle_fingerprint_match(", "static void auth_wait_for_lift(")
        hid, piv = match.split("} else {", 1)
        # A match after a correct PIN completes the fallback before the checks.
        self.assertLess(match.index("piv_pin_note_match()"), match.index("if (piv_pin_required())"))
        # Otherwise HID typing stops, and a PIV match grants nothing.
        self.assertLess(hid.index("if (piv_pin_required())"), hid.index("request_and_type_password"))
        required = piv.index("if (piv_pin_required())")
        self.assertLess(required, piv.index("piv_note_user_presence();"))
        self.assertLess(required, piv.index("type_ascii(piv_pin"))
        self.assertIn("return;", piv[required:piv.index("}", required)])

    def test_pin_fallback_alert_flashes_the_sensor_ring(self):
        fingerprint = (MAIN / "fingerprint.c").read_text()
        worker = section(fingerprint, "static void result_led_worker(", "static void schedule_result_led(")
        self.assertLess(worker.index("service_result_led()"), worker.index("service_alert_led()"))
        self.assertIn("fingerprint_set_alert_source(pin_alert)", (MAIN / "piv.c").read_text())

    def test_every_search_is_saved_as_failed_before_it_runs(self):
        fingerprint = (MAIN / "fingerprint.c").read_text()
        captured = section(fingerprint, "static fingerprint_match_t fingerprint_match_captured(",
                           "bool fingerprint_try_poll_match(")
        self.assertLess(captured.index("search_hook()"), captured.index("fp_command(0x04"))
        self.assertIn("fingerprint_set_search_hook(piv_pin_note_attempt)", (MAIN / "piv.c").read_text())

    def test_fallback_blocks_firmware_replacement(self):
        console = (MAIN / "config_console.c").read_text()
        ota = section(console, "static void ota_begin(", "firmware_update_begin(")
        self.assertIn('if (piv_pin_required()) { reply("ERR OTA pin_required"); return; }', ota)

    def test_new_identity_never_inherits_a_stale_pin(self):
        create = section((MAIN / "piv.c").read_text(), "bool piv_create_identity(", "bool piv_uses_provisioned_keys(")
        self.assertLess(create.index("piv_pin_reset()"), create.index('nvs_open("piv_keys"'))


if __name__ == "__main__":
    unittest.main()

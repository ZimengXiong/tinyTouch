"""Run the actual firmware hot paths against deterministic UART/USB clocks."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "firmware/tiny_touch_unified/main"


def section(source, start, end):
    return source[source.index(start):source.index(end, source.index(start))]


@unittest.skipUnless(shutil.which("cc"), "host C compiler unavailable")
class FirmwareLatencyTests(unittest.TestCase):
    def run_harness(self, name, replacements):
        source = (ROOT / "tests/host" / name).read_text()
        for marker, implementation in replacements.items():
            source = source.replace(f"/* {marker} */", implementation)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "test.c").write_text(source)
            built = subprocess.run(
                ["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", str(path / "test.c"),
                 "-o", str(path / "test")], capture_output=True, text=True,
            )
            self.assertEqual(built.returncode, 0, built.stderr)
            ran = subprocess.run([str(path / "test")], capture_output=True, text=True, timeout=10)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)

    def test_keyboard_pacing_completion_failure_and_release(self):
        source = (MAIN / "touch_pin_hid.c").read_text()
        self.run_harness("hid_latency_test.c", {
            "globals": section(source, "static SemaphoreHandle_t hid_signal;", "static uint32_t event_counter;"),
            "implementation": section(source, "static hid_transfer_t hid_transfer_status(", "static void bytes_to_hex("),
            "callbacks": section(source, "void touch_pin_hid_usb_attached(", "bool touch_pin_hid_submit_response("),
        })

    def test_helper_requests_end_when_usb_session_changes(self):
        source = (MAIN / "touch_pin_hid.c").read_text()
        self.run_harness("hid_request_test.c", {
            "request": section(source, "static bool begin_password_request(", "typedef enum {\n  AUTH_STATE_IDLE"),
            "submit": section(source, "bool touch_pin_hid_submit_response(", "uint8_t const *tud_hid_descriptor_report_cb("),
        })

    def test_touch_edges_capture_retries_and_foreground_pause(self):
        source = (MAIN / "touch_pin_hid.c").read_text()
        self.run_harness("touch_polling_test.c", {
            "state": section(source, "typedef enum {\n  AUTH_STATE_IDLE", "static void handle_fingerprint_match("),
            "task": section(source, "static void auth_wait_for_lift(", "void touch_pin_hid_start("),
        })

    def test_uart_framing_and_independent_result_feedback(self):
        source = (MAIN / "fingerprint.c").read_text()
        self.run_harness("fingerprint_latency_test.c", {
            "uart": section(source, "static uint16_t fp_checksum(", "static bool fp_take("),
            "led": section(source, "static bool set_aura(", "void fingerprint_led_service(") + section(source, "static bool apply_aura(void) {", "static void result_led_worker("),
            "schedule": section(source, "static void schedule_result_led(", "static void show_result("),
            "matcher": section(source, "bool fingerprint_try_poll_match(", "void fingerprint_init("),
        })

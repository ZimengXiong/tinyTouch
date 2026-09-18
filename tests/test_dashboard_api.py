import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "firmware" / "tiny_touch_unified" / "main"
HOST_TEST = ROOT / "tests" / "host" / "dashboard_api_test.c"
UI = MAIN / "web" / "index.html"


class DashboardApiHostTests(unittest.TestCase):
    def test_host_api_behaviors(self):
        binary = Path("/tmp/tinytouch-dashboard-api-test")
        result = subprocess.run(
            [
                "cc",
                "-std=c11",
                "-Wall",
                "-Werror",
                f"-I{MAIN}",
                str(HOST_TEST),
                str(MAIN / "dashboard_api.c"),
                "-o",
                str(binary),
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        ran = subprocess.run([str(binary)], capture_output=True, text=True)
        self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
        self.assertEqual(ran.stdout.strip(), "ok")


class DashboardUiTests(unittest.TestCase):
    def test_ui_has_no_mac_password_fields(self):
        html = UI.read_text(encoding="utf-8")
        self.assertNotRegex(html, r'type=["\']password["\']')
        self.assertNotRegex(html.lower(), r"keychain")
        self.assertIn("not stored on the dongle", html)
        self.assertIn("tinytouch setup --mode hid", html)
        self.assertIn("add-computer", html)
        self.assertIn('id="slots"', html)
        self.assertIn("/api/pause", html)
        self.assertIn("Submit Enter", html)
        self.assertIn("idle-led", html)
        self.assertIn("Idle ring light", html)
        self.assertIn('id="log"', html)
        self.assertIn("credentials: 'same-origin'", html)
        self.assertIn("editingLabel()", html)
        self.assertIn("setBusy(", html)

    def test_hid_mode_usb_descriptor_adds_ecm_and_drops_ccid(self):
        src = (MAIN / "usb_descriptors.c").read_text(encoding="utf-8")
        self.assertIn("TUD_CDC_ECM_DESCRIPTOR", src)
        self.assertNotIn("TUD_CDC_NCM_DESCRIPTOR", src)
        self.assertIn("hid_configuration_descriptor", src)
        hid_start = src.index("hid_configuration_descriptor")
        hid_body = src[hid_start:src.index("};", hid_start)]
        self.assertNotIn("0x0b", hid_body)
        self.assertIn("ITF_NUM_HID_NCM", hid_body)
        # ESP32-S3 only has 5 IN endpoints including EP0; omit CDC notif in HID mode.
        self.assertIn("TUD_CDC_DESCRIPTOR_NO_NOTIF", hid_body)
        self.assertNotIn("EPNUM_HID_CDC_NOTIF", hid_body)

    def test_usb_ethernet_dhcp_does_not_offer_a_default_gateway(self):
        src = (MAIN / "usb_ncm.c").read_text(encoding="utf-8")
        self.assertRegex(src, r"s_usb_ip\.gw\.addr\s*=\s*0")
        self.assertNotRegex(
            src,
            r"s_usb_ip\.gw\.addr\s*=\s*ESP_IP4TOADDR\(DASHBOARD_IP_A",
        )
        self.assertIn("ROUTER_SOLICITATION_ADDRESS", src)
        self.assertRegex(src, r"offer_router\s*=\s*0")

import unittest
from pathlib import Path


MAIN = Path(__file__).parents[1] / "firmware" / "tiny_touch_unified" / "main"


class ProtocolSixFirmwareTests(unittest.TestCase):
    def source(self, name: str) -> str:
        return (MAIN / name).read_text()

    def test_no_firmware_software_restart_path(self) -> None:
        source = "\n".join(path.read_text() for path in MAIN.glob("*.c"))
        self.assertNotIn("esp_restart", source)
        self.assertNotIn("RTC_CNTL_FORCE_DOWNLOAD_BOOT", source)

    def test_protocol_six_has_one_stable_usb_descriptor(self) -> None:
        cmake = self.source("CMakeLists.txt")
        usb = self.source("usb_ccid.c")
        descriptors = self.source("usb_descriptors.c")
        self.assertIn("TINYTOUCH_PROTOCOL_VERSION=6", cmake)
        self.assertIn("tiny_touch_configuration_descriptor", usb)
        self.assertNotIn("tiny_touch_hid_configuration_descriptor", descriptors)
        self.assertNotIn("tiny_touch_piv_configuration_descriptor", descriptors)

    def test_persistence_swaps_one_live_config_blob(self) -> None:
        source = self.source("device_config.c")
        self.assertIn('CONFIG_NAMESPACE "tt6"', source)
        self.assertIn("replace_locked", source)
        self.assertIn("device_config_factory_reset", source)
        self.assertNotIn('"hid_key"', source)
        self.assertNotIn('"hid_hosts"', source)

    def test_ota_stages_without_changing_the_current_runtime(self) -> None:
        console = self.source("config_console.c")
        update = self.source("firmware_update.c")
        self.assertIn("OK OTA STAGED power_cycle=required", console)
        self.assertIn("esp_ota_set_boot_partition", update)
        self.assertIn("firmware_update_staged", update)
        self.assertNotIn("fingerprint_prepare_for_restart", console)

    def test_fingerprint_auth_requires_presence(self) -> None:
        source = self.source("touch_pin_hid.c")
        self.assertIn("if (!present || !fingerprint_is_ready() || !tud_hid_ready())", source)
        self.assertNotIn("fingerprint_service_health", source)
        self.assertNotIn("usb_runtime", source)


if __name__ == "__main__":
    unittest.main()

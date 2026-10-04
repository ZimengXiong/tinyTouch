"""Explain recovery when fingerprint approval cannot succeed."""

import unittest

from test_tinytouch_cli import cli


class RecoveryGuidanceTests(unittest.TestCase):
    def test_no_match_reports_a_failed_match_and_a_destructive_recovery_path(self):
        for prompted in (False, True):
            with self.subTest(prompted=prompted):
                message = cli.human_error("ERR AUTH no_match", touch_prompted=prompted)
                self.assertIn("No enrolled fingerprint matched.", message)
                self.assertIn("try an enrolled finger", message)
                self.assertIn(f"{cli.FACTORY_FLASH_URL}?firmware=recovery", message)
                self.assertIn("Recovery erases fingerprints, device keys", message)
                self.assertNotIn("timed out", message)

    def test_offline_sensor_requests_a_reconnect(self):
        self.assertEqual(
            cli.human_error("ERR AUTH sensor=offline", touch_prompted=True),
            "Fingerprint sensor unavailable. Please reconnect tinyTouch.",
        )

    def test_generic_authorization_timeout_retains_its_retry_instruction(self):
        self.assertEqual(
            cli.human_error("ERR AUTH", touch_prompted=True),
            "Fingerprint authentication timed out. Please try again.",
        )


if __name__ == "__main__":
    unittest.main()

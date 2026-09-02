import importlib.util
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "tinytouch_keychain_runtime_test",
    ROOT / "software" / "macos-helper" / "tinytouch_keychain.py",
)
keychain = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(keychain)


class KeychainRuntimeTests(unittest.TestCase):
    def test_background_mode_disables_unattended_prompts(self):
        with mock.patch.object(
            keychain._SECURITY,
            "SecKeychainSetUserInteractionAllowed",
            return_value=0,
        ) as interaction:
            keychain.set_background_mode()
        interaction.assert_called_once_with(False)

    def test_locked_keychain_status_is_classified_as_transient(self):
        error = keychain.KeychainError("read", -25308)
        self.assertTrue(error.transient)
        self.assertEqual(error.status_name, "interaction_not_allowed")

    def test_acl_denial_is_not_misclassified_as_transient(self):
        error = keychain.KeychainError("read", -25293)
        self.assertFalse(error.transient)
        self.assertEqual(error.status_name, "authentication_failed")

    def test_text_password_wrapper_wipes_raw_copy(self):
        raw = bytearray(b"secret")
        with mock.patch.object(keychain, "get_password_bytes", return_value=raw):
            self.assertEqual(keychain.get_password("service", "account"), "secret")
        self.assertEqual(raw, bytearray(b"\x00" * 6))


if __name__ == "__main__":
    unittest.main()

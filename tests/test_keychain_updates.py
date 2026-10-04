"""Check credential updates without accessing the user's Keychain."""

import ctypes
import importlib.util
from pathlib import Path
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "keychain_updates", ROOT / "macos/tinytouch_keychain.py"
)
keychain = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(keychain)


class KeychainUpdateTests(unittest.TestCase):
    def setUp(self):
        self.saved = b"old password"
        self.write_status = 0
        self.buffer = None
        self.item = ctypes.c_void_p(123)
        self.find_status = 0
        self.patchers = [
            mock.patch.object(keychain, "_find", side_effect=self.find),
            mock.patch.object(
                keychain._SECURITY,
                "SecKeychainItemModifyAttributesAndData",
                side_effect=self.modify,
            ),
            mock.patch.object(
                keychain._SECURITY, "SecKeychainAddGenericPassword", side_effect=self.add
            ),
            mock.patch.object(
                keychain._SECURITY, "SecKeychainItemDelete", side_effect=self.delete
            ),
            mock.patch.object(keychain._CORE_FOUNDATION, "CFRelease"),
            mock.patch.object(keychain.subprocess, "run"),
        ]
        self.find_mock, self.modify_mock, self.add_mock, self.delete_mock, \
            self.release_mock, self.process_mock = [p.start() for p in self.patchers]
        for patcher in self.patchers:
            self.addCleanup(patcher.stop)

    def find(self, service, account, *, include_secret):
        return self.find_status, self.item, 0, ctypes.c_void_p()

    def modify(self, item, attributes, length, buffer):
        self.assertEqual(item.value, self.item.value)
        self.assertIsNone(attributes)
        self.buffer = buffer
        if not self.write_status:
            self.saved = ctypes.string_at(buffer, length)
        return self.write_status

    def add(self, keychain_ref, service_length, service, account_length, account,
            length, buffer, output):
        self.buffer = buffer
        if not self.write_status:
            self.saved = ctypes.string_at(buffer, length)
            output._obj.value = 456
        return self.write_status

    def delete(self, item):
        self.saved = None
        return 0

    def test_update_preserves_existing_item_and_acl(self):
        keychain.set_password("service", "account", "new password")
        self.assertEqual(self.saved, b"new password")
        self.modify_mock.assert_called_once()
        self.delete_mock.assert_not_called()
        self.add_mock.assert_not_called()
        self.release_mock.assert_called_once_with(self.item)

    def test_write_failure_keeps_previous_credential(self):
        self.write_status = -25308
        with self.assertRaises(keychain.KeychainError) as raised:
            keychain.set_password("service", "account", "new password")
        self.assertEqual(raised.exception.status, -25308)
        self.assertEqual(self.saved, b"old password")
        self.delete_mock.assert_not_called()
        self.release_mock.assert_called_once_with(self.item)

    def test_acl_denial_does_not_delete_with_the_security_tool(self):
        self.write_status = -25293
        with self.assertRaises(keychain.KeychainError):
            keychain.set_password("service", "account", "new password")
        self.assertEqual(self.saved, b"old password")
        self.delete_mock.assert_not_called()
        self.process_mock.assert_not_called()

    def test_missing_item_is_created(self):
        self.find_status = keychain._NOT_FOUND
        self.item = ctypes.c_void_p()
        keychain.set_password("service", "account", "é")
        self.assertEqual(self.saved, "é".encode())
        self.modify_mock.assert_not_called()
        self.add_mock.assert_called_once()
        self.assertEqual(self.release_mock.call_args.args[0].value, 456)

    def test_lookup_failure_does_not_change_existing_credential(self):
        self.find_status = -25308
        with self.assertRaises(keychain.KeychainError):
            keychain.set_password("service", "account", "new password")
        self.assertEqual(self.saved, b"old password")
        self.modify_mock.assert_not_called()
        self.add_mock.assert_not_called()
        self.delete_mock.assert_not_called()

    def test_native_password_buffer_is_wiped_after_success_or_failure(self):
        for status in (0, -25308):
            with self.subTest(status=status):
                self.write_status = status
                if status:
                    with self.assertRaises(keychain.KeychainError):
                        keychain.set_password("service", "account", "new password")
                else:
                    keychain.set_password("service", "account", "new password")
                self.assertEqual(self.buffer.raw, bytes(len(self.buffer)))


if __name__ == "__main__":
    unittest.main()

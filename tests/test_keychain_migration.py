"""Exercise legacy partition migration against a disposable macOS Keychain."""

import ctypes
import importlib.util
from pathlib import Path
import secrets
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "keychain_migration", ROOT / "macos/tinytouch_keychain.py"
)
keychain = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(keychain)


class LegacyMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="tinytouch-migration-")
        cls.probe = str(Path(cls.directory.name) / "legacy")
        subprocess.run(
            [
                "clang",
                "-Wno-deprecated-declarations",
                str(ROOT / "tests/host/keychain_identity_probe.c"),
                "-framework",
                "Security",
                "-framework",
                "CoreFoundation",
                "-o",
                cls.probe,
            ],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["codesign", "-s", "-", cls.probe], check=True, capture_output=True
        )

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def setUp(self):
        # Home Keychains use partition ACLs. Keychains in /tmp use the older format.
        self.path = str(
            Path.home()
            / "Library/Keychains"
            / ("tinytouch-migration-" + secrets.token_hex(8) + ".keychain")
        )
        self.password = secrets.token_hex(24)
        # This password belongs only to the disposable test Keychain.
        subprocess.run(
            ["security", "create-keychain", "-p", self.password, self.path],
            check=True,
            capture_output=True,
        )
        self.addCleanup(
            subprocess.run,
            ["security", "delete-keychain", self.path],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [self.probe, "store", "migration", self.path],
            check=True,
            capture_output=True,
        )
        # Keychain creates the partition ACL on the first successful read.
        self.assert_legacy_can_read()
        self.reference = ctypes.c_void_p()
        keychain._SECURITY.SecKeychainOpen.argtypes = [
            ctypes.c_char_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        self.assertEqual(
            keychain._SECURITY.SecKeychainOpen(
                self.path.encode(), ctypes.byref(self.reference)
            ),
            0,
        )
        self.addCleanup(keychain._CORE_FOUNDATION.CFRelease, self.reference)

    def find_fixture(self, service, account, *, include_secret):
        self.assertFalse(include_secret)
        item = ctypes.c_void_p()
        status = keychain._SECURITY.SecKeychainFindGenericPassword(
            self.reference,
            len(service),
            service.encode(),
            len(account),
            account.encode(),
            None,
            None,
            ctypes.byref(item),
        )
        return status, item, 0, None

    def repair(self, password_provider, application_update):
        # Signature checks are covered by the release gate. This fixture uses an
        # ad hoc probe so tests do not require a developer's signing certificate.
        with (
            mock.patch.object(keychain, "_find", side_effect=self.find_fixture),
            mock.patch.object(
                keychain.subprocess,
                "run",
                return_value=mock.Mock(stderr="TeamIdentifier=TESTTEAM01\n"),
            ),
            mock.patch.object(
                keychain._SECURITY,
                "SecKeychainItemSetAccess",
                side_effect=application_update,
            ),
        ):
            keychain.authorize_executable(
                "tinyTouch-signing-test",
                "migration",
                self.probe,
                password_provider=password_provider,
            )

    def assert_legacy_can_read(self):
        subprocess.run(
            [self.probe, "read", "migration", self.path],
            check=True,
            capture_output=True,
        )

    def test_partition_commit_precedes_application_approval_and_survives_retry(self):
        password = bytearray(self.password.encode())
        provider = mock.Mock(return_value=password)
        approval = mock.Mock(return_value=-25293)
        with self.assertRaises(keychain.KeychainError):
            self.repair(provider, approval)
        provider.assert_called_once_with()
        approval.assert_called_once()
        self.assertEqual(password, bytearray(len(password)))
        self.assert_legacy_can_read()
        # A declined application dialog must not repeat the completed partition
        # migration. Read the persisted ACL again from Security.framework.
        provider.reset_mock()
        with self.assertRaises(keychain.KeychainError):
            self.repair(provider, approval)
        provider.assert_not_called()
        self.assert_legacy_can_read()

    def test_wrong_password_does_not_attempt_application_update(self):
        password = bytearray(b"wrong-test-password")
        approval = mock.Mock(return_value=0)
        with self.assertRaisesRegex(
            keychain.KeychainError, "authorize signing identity"
        ):
            self.repair(lambda: password, approval)
        approval.assert_not_called()
        self.assertEqual(password, bytearray(len(password)))
        self.assert_legacy_can_read()


if __name__ == "__main__":
    unittest.main()

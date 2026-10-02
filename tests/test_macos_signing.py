"""Check that release signing cannot silently revert to a changing identity."""

import os
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class MacOSSigningTests(unittest.TestCase):
    def reject_signing(self, **settings):
        environment = os.environ.copy()
        environment.update(settings)
        result = subprocess.run(
            ["zsh", str(ROOT / "release/sign-macos-cli.sh"), "/nonexistent/cli"],
            env=environment,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        return result.stderr

    def test_ad_hoc_signature_requires_explicit_test_opt_in(self):
        error = self.reject_signing(
            TINYTOUCH_SIGNING_IDENTITY="-", TINYTOUCH_ALLOW_ADHOC="0"
        )
        self.assertIn("Ad hoc signing changes Keychain identity", error)

    def test_release_forbids_ad_hoc_even_with_test_opt_in(self):
        error = self.reject_signing(
            TINYTOUCH_SIGNING_IDENTITY="-",
            TINYTOUCH_ALLOW_ADHOC="1",
            TINYTOUCH_REQUIRE_STABLE_IDENTITY="1",
        )
        self.assertIn("require a stable certificate signature", error)

    def test_release_requires_an_explicit_team_pin(self):
        error = self.reject_signing(
            TINYTOUCH_SIGNING_IDENTITY="certificate",
            TINYTOUCH_SIGNING_TEAM_ID="",
            TINYTOUCH_REQUIRE_STABLE_IDENTITY="1",
        )
        self.assertIn("require a pinned", error)


if __name__ == "__main__":
    unittest.main()

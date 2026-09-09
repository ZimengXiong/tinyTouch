"""Exercise release installers with local downloads and isolated home folders."""

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packaging"))
SPEC = importlib.util.spec_from_file_location("finalizer", ROOT / "packaging/finalize-release.py")
finalizer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(finalizer)


class BetaPackagingTests(unittest.TestCase):
    def run_installer(self, version, installer_version=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        home = root / "home"
        install = home / ".local/bin"
        install.mkdir(parents=True)
        stable = home / "Library/Application Support/tinyTouch"
        stable.mkdir(parents=True)
        (stable / "helper.json").write_text("stable helper state")
        stable_executable = stable / "tinytouch"
        stable_executable.write_text("stable executable")
        (install / "tinytouch").symlink_to(stable_executable)
        fixture = root / "download"
        fixture.mkdir()
        archive_path = fixture / "tinytouch-macos-arm64.tar.gz"
        with tarfile.open(archive_path, "w:gz") as archive:
            for name, contents in (
                ("tinytouch/tinytouch", b"#!/bin/sh\nexit 0\n"),
                ("tinytouch/_internal/runtime", b"runtime"),
            ):
                info = tarfile.TarInfo(name)
                info.size = len(contents)
                info.mode = 0o755
                archive.addfile(info, io.BytesIO(contents))
        digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
        (fixture / "release-manifest.json").write_text(json.dumps({
            "version": version,
            "cli": {"macos-arm64": {"file": archive_path.name, "sha256": digest}},
        }))
        fakebin = root / "fakebin"
        fakebin.mkdir()
        scripts = {
            "uname": "#!/bin/sh\n[ \"$1\" = -s ] && echo Darwin || echo arm64\n",
            "xattr": "#!/bin/sh\nexit 0\n",
            "curl": "#!/bin/sh\ncp \"$FIXTURE/${2##*/}\" \"$4\"\n",
            "plutil": f"#!{sys.executable}\n" + """import json, sys
value = json.load(open(sys.argv[-1]))
for key in sys.argv[2].split('.'):
    value = value[key]
print(value)
""",
            # The installer must never operate on LaunchAgents or Keychain.
            "launchctl": "#!/bin/sh\necho unexpected-launchctl >&2\nexit 99\n",
            "security": "#!/bin/sh\necho unexpected-keychain >&2\nexit 99\n",
        }
        for name, contents in scripts.items():
            path = fakebin / name
            path.write_text(contents)
            path.chmod(0o755)
        script = root / "install.sh"
        finalizer.write_installer(script, installer_version or version)
        environment = dict(os.environ, HOME=str(home), FIXTURE=str(fixture),
                           PATH=f"{fakebin}:{install}:/usr/bin:/bin",
                           TINYTOUCH_INSTALL_DIR=str(install))
        environment.pop("TINYTOUCH_RELEASE_ROOT", None)
        result = subprocess.run(["/bin/sh", str(script)], env=environment,
                                capture_output=True, text=True)
        self.assertEqual((stable / "helper.json").read_text(), "stable helper state")
        self.assertNotIn("unexpected-", result.stderr)
        return result, home, script, stable_executable

    def test_beta_installs_alongside_stable(self):
        result, home, script, stable_executable = self.run_installer("0.1.25-beta.1")
        self.assertEqual(result.returncode, 0, result.stderr)
        install = home / ".local/bin"
        self.assertEqual((install / "tinytouch").resolve(), stable_executable)
        self.assertEqual(stable_executable.read_text(), "stable executable")
        beta = (install / "tinytouch-beta").resolve()
        self.assertIn("tinyTouch-beta", beta.parts)
        self.assertTrue(beta.is_file())
        self.assertIn("tinytouch-beta setup", result.stdout)
        self.assertIn("releases/download/v0.1.25-beta.1", script.read_text())
        self.assertNotIn("releases/latest/download", script.read_text())

    def test_stable_install_uses_existing_command_and_support_directory(self):
        result, home, script, _ = self.run_installer("0.1.24-prod")
        self.assertEqual(result.returncode, 0, result.stderr)
        install = home / ".local/bin"
        self.assertFalse((install / "tinytouch-beta").exists())
        self.assertIn("tinyTouch", (install / "tinytouch").resolve().parts)
        self.assertIn("tinytouch setup", result.stdout)
        self.assertEqual(script.read_bytes(), (ROOT / "packaging/install.sh").read_bytes())

    def test_beta_installer_rejects_stable_manifest_before_installing(self):
        result, home, _, stable_executable = self.run_installer(
            "0.1.24-prod", installer_version="0.1.25-beta.1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not match", result.stderr)
        self.assertEqual((home / ".local/bin/tinytouch").resolve(), stable_executable)
        self.assertFalse((home / ".local/bin/tinytouch-beta").exists())


if __name__ == "__main__":
    unittest.main()

import hashlib
import importlib.util
import io
import json
import os
import struct
import subprocess
import tarfile
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "release_integrity", ROOT / "release" / "release_integrity.py"
)
integrity = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(integrity)


def metadata(path: Path) -> dict:
    return {
        "file": path.name,
        "size": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


class ReleasePipelineTests(unittest.TestCase):
    commit = "1234567890ab" + "c" * 28

    def test_release_version_policy_preserves_stable_and_dev_formats(self):
        for version in ("0.1.31", "1.0.0", "12.34.567", "0.1.31-dev.1", "0.1.100-dev.12"):
            with self.subTest(version=version):
                self.assertEqual(integrity.checked_version(version), version)
        for version in (
            "0.1.31-prod", "0.1.31-beta.1", "0.1.31-dev", "0.1.31-dev.x",
            "0.1.31+build", "v0.1.31", "0.1", "0.1.31.1", "0.1.31\n",
            "../0.1.31", "", None, 31,
        ):
            with self.subTest(version=version):
                with self.assertRaisesRegex(integrity.IntegrityError, "version must be"):
                    integrity.checked_version(version)

    def test_version_guard_checks_tag_and_manifest_before_publishing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            version_file = root / "VERSION"
            manifest_file = root / "release-manifest.json"
            for version in ("0.1.31", "0.1.31-dev.1", "0.1.100"):
                version_file.write_text(version + "\n")
                manifest_file.write_text(json.dumps({"version": version}))
                command = [
                    "python3", str(ROOT / "release" / "check-version.py"),
                    "--version-file", str(version_file), "--tag", f"v{version}",
                    "--manifest", str(manifest_file),
                ]
                with self.subTest(version=version):
                    self.assertEqual(subprocess.run(command, capture_output=True).returncode, 0)
                    manifest_file.write_text(json.dumps({"version": "0.1.30"}))
                    result = subprocess.run(command, capture_output=True, text=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("manifest version does not match VERSION", result.stderr)
                    command[command.index("--tag") + 1] = "v0.1.30-prod"
                    result = subprocess.run(command, capture_output=True, text=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("release tag must be", result.stderr)

            version_file.write_text("0.1.31-prod\n")
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("version must be", result.stderr)

    def test_integrity_rejects_invalid_version_even_when_it_matches_version_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "release-manifest.json").write_text(json.dumps({"version": "0.1.31-prod"}))
            with mock.patch.object(integrity, "VERSION", "0.1.31-prod"):
                with self.assertRaisesRegex(integrity.IntegrityError, "version must be"):
                    integrity.validate_release(root, self.commit)

    def test_publishing_dev_release_explicitly_excludes_it_from_latest(self):
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text()
        publish = workflow.split("      - name: Publish release\n", 1)[1]
        script = textwrap.dedent(publish.split("        run: |\n", 1)[1].split("      - name:", 1)[0])
        # Execute the actual publish command with gh replaced by an argument recorder.
        for version, prerelease in (("0.1.31", "false"), ("0.1.31-dev.1", "true")):
            with self.subTest(version=version):
                result = subprocess.run(
                    ["bash", "-e", "-c", 'gh() { printf "%s\\n" "$@"; }\n' + script],
                    env={**os.environ, "RELEASE_TAG": f"v{version}", "RELEASE_PRERELEASE": prerelease},
                    capture_output=True, text=True, check=True,
                )
                flags = result.stdout.splitlines()
                self.assertIn(f"v{version}", flags)
                if prerelease == "true":
                    self.assertIn("--prerelease", flags)
                    self.assertIn("--latest=false", flags)
                else:
                    self.assertNotIn("--prerelease", flags)
                    self.assertNotIn("--latest=false", flags)

    def test_publish_uses_written_notes_and_keeps_a_generated_fallback(self):
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text()
        publish = workflow.split("      - name: Publish release\n", 1)[1]
        script = textwrap.dedent(publish.split("        run: |\n", 1)[1].split("      - name:", 1)[0])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            notes = root / "release/notes/0.1.35.md"
            notes.parent.mkdir(parents=True)
            for written in (True, False):
                with self.subTest(written=written):
                    if written:
                        notes.write_text("- fixed helper reconnects\n")
                    else:
                        notes.unlink()
                    result = subprocess.run(
                        ["bash", "-e", "-c", 'gh() { printf "%s\\n" "$@"; }\n' + script],
                        cwd=root,
                        env={**os.environ, "RELEASE_TAG": "v0.1.35", "RELEASE_PRERELEASE": "false"},
                        capture_output=True, text=True, check=True,
                    )
                    flags = result.stdout.splitlines()
                    if written:
                        index = flags.index("--notes-file")
                        self.assertEqual(flags[index + 1], "release/notes/0.1.35.md")
                        self.assertNotIn("--generate-notes", flags)
                    else:
                        self.assertIn("--generate-notes", flags)
                        self.assertNotIn("--notes-file", flags)

    def make_app(self, path: Path, kind: str) -> None:
        payload = bytearray(512)
        offset = 32
        struct.pack_into("<I", payload, offset, integrity.APP_DESCRIPTION_MAGIC)
        struct.pack_into("<I", payload, offset + 4, 0)
        version = (ROOT / "VERSION").read_text().strip().encode()
        payload[offset + 16:offset + 16 + len(version)] = version
        project = b"tiny_touch_unified"
        payload[offset + 48:offset + 48 + len(project)] = project
        idf = b"v5.3.2"
        payload[offset + 112:offset + 112 + len(idf)] = idf
        payload[256:268] = self.commit[:12].encode()
        if kind == "recovery":
            payload[300:317] = b"RECOVERY COMPLETE"
        path.write_bytes(payload)

    def make_cli(self, path: Path) -> None:
        with tarfile.open(path, "w:gz") as archive:
            for name, value in (
                ("tinytouch/tinytouch", b"executable"),
                ("tinytouch/_internal/runtime", b"runtime"),
            ):
                info = tarfile.TarInfo(name)
                info.size = len(value)
                info.mode = 0o755
                archive.addfile(info, io.BytesIO(value))

    def make_release(self, root: Path) -> None:
        version = (ROOT / "VERSION").read_text().strip()
        layouts = {}
        for kind, images in integrity.EXPECTED_IMAGES.items():
            directory = root / kind
            directory.mkdir(parents=True)
            entries = []
            for address, name in images.items():
                path = directory / name
                if address == 0x10000:
                    self.make_app(path, kind)
                elif name == "recovery-request.bin":
                    request = integrity.RECOVERY_REQUEST
                    path.write_bytes(request + b"\xff" * (4096 - len(request)))
                elif name == "ota_data_initial.bin":
                    path.write_bytes(b"ota" * 32)
                elif name == "partition-table.bin":
                    path.write_bytes(b"partition")
                else:
                    path.write_bytes(kind.encode() + name.encode())
                entries.append({"name": name, "address": address, **metadata(path)})
            full = directory / integrity.EXPECTED_FULL_IMAGES[kind]
            full.write_bytes(b"full" + kind.encode())
            layouts[kind] = {
                "version": version,
                "protocol": integrity.PROTOCOL,
                "secureVersion": integrity.SECURE_VERSION,
                "flashSize": "4MB",
                "eraseAll": False,
                "compress": False,
                "images": entries,
                "fullImage": metadata(full),
            }
            (directory / "manifest.json").write_text(json.dumps(layouts[kind]))
        factory_app = root / "factory" / "tiny_touch_unified.bin"
        (root / "tiny_touch_unified.bin").write_bytes(factory_app.read_bytes())
        cli = {}
        for key, name in (
            ("macos-arm64", "tinytouch-macos-arm64.tar.gz"),
            ("macos-x86_64", "tinytouch-macos-x86_64.tar.gz"),
        ):
            path = root / name
            self.make_cli(path)
            cli[key] = {**metadata(path), "format": "tar.gz"}
        manifest = {
            "version": version,
            "build": self.commit[:12],
            "protocol": integrity.PROTOCOL,
            "secureVersion": integrity.SECURE_VERSION,
            "boards": ["esp32s3-super-mini", "seeed-xiao-esp32s3"],
            "firmware": layouts,
            "ota": metadata(root / "tiny_touch_unified.bin"),
            "cli": cli,
        }
        (root / "release-manifest.json").write_text(json.dumps(manifest))

    def test_finalizer_produces_complete_flat_release(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release = root / "release"
            release.mkdir()
            self.make_release(release)
            output = root / "publish"
            subprocess.run(
                [
                    "python3", str(ROOT / "release" / "finalize-release.py"),
                    str(release), "--output", str(output), "--commit", self.commit,
                ],
                check=True,
            )
            integrity.validate_release(output, self.commit, flat=True)
            integrity.validate_checksums(output)
            self.assertTrue((output / "ota_data_initial.bin").is_file())
            self.assertTrue((output / "tiny_touch_recovery.bin").is_file())
            self.assertTrue((output / "tiny_touch_recovery_full.bin").is_file())
            self.assertFalse((output / "ota_slot1.bin").exists())
            self.assertFalse((output / "tinytouch-web-flashers.tar.gz").exists())

            (output / "unexpected.bin").write_bytes(b"unexpected")
            with self.assertRaisesRegex(integrity.IntegrityError, "published asset set mismatch"):
                integrity.validate_release(output, self.commit, flat=True)

    def test_descriptor_version_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_release(root)
            path = root / "factory" / "tiny_touch_unified.bin"
            data = bytearray(path.read_bytes())
            offset = data.find(struct.pack("<I", integrity.APP_DESCRIPTION_MAGIC))
            data[offset + 16:offset + 48] = b"stale\0" + b"\0" * 26
            path.write_bytes(data)
            manifest_path = root / "release-manifest.json"
            manifest = json.loads(manifest_path.read_text())
            image = manifest["firmware"]["factory"]["images"][2]
            image.update(metadata(path))
            (root / "tiny_touch_unified.bin").write_bytes(path.read_bytes())
            manifest["ota"].update(metadata(root / "tiny_touch_unified.bin"))
            manifest_path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(integrity.IntegrityError, "embedded version mismatch"):
                integrity.validate_release(root, self.commit)

    def test_candidate_extraction_rejects_links(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_path = root / "candidate.tar.gz"
            with tarfile.open(archive_path, "w:gz") as archive:
                info = tarfile.TarInfo("publish/link")
                info.type = tarfile.SYMTYPE
                info.linkname = "../../outside"
                archive.addfile(info)
            with self.assertRaisesRegex(integrity.IntegrityError, "links are not allowed"):
                integrity.safe_extract(archive_path, root / "output")

    def test_release_workflow_is_ci_and_tag_driven(self):
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text()
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("workflow_run:", workflow)
        self.assertNotIn("push:", workflow)
        self.assertIn("Existing annotated release tag", workflow)
        self.assertIn('git cat-file -t "refs/tags/$RELEASE_TAG"', workflow)
        self.assertIn("idf.py -C firmware/tiny_touch_unified build", workflow)
        self.assertIn("TINYTOUCH_RECOVERY_BUILD=ON", workflow)
        self.assertIn("-B firmware/tiny_touch_unified/build-recovery", workflow)
        self.assertIn("--recovery-build", workflow)
        self.assertIn("release/build-standalone-macos.sh", workflow)
        self.assertIn("environment: release-signing", workflow)
        self.assertNotIn("beta-signing", workflow)
        self.assertIn("release-publishing", workflow)
        self.assertIn("attest-build-provenance", workflow)
        self.assertIn('gh release create "$RELEASE_TAG"', workflow)
        self.assertFalse((ROOT / ".github" / "workflows" / "release-candidate.yml").exists())

        build_script = (ROOT / "release" / "build-standalone-macos.sh").read_text()
        self.assertIn("--require-hashes", build_script)
        self.assertIn("--no-build-isolation", build_script)
        self.assertIn("requirements-bootstrap.txt", build_script)
        self.assertIn("requirements-release.txt", build_script)
        self.assertNotIn("_network_test", build_script)
        self.assertFalse((ROOT / "release" / "release-local").exists())
        self.assertFalse((ROOT / "release" / "tag-release").exists())
        self.assertFalse((ROOT / "release" / "release").exists())

    def test_recovery_clears_sensor_before_device_state(self):
        source = (ROOT / "firmware" / "tiny_touch_unified" / "main" / "main.c").read_text()
        recovery = source.split("static void recover_device(void)", 1)[1].split("#endif", 1)[0]
        self.assertLess(recovery.index("fingerprint_delete_all()"), recovery.index("nvs_flash_erase()"))
        self.assertLess(recovery.index("fingerprint_count() == 0"), recovery.index("nvs_flash_erase()"))
        self.assertIn("esp_partition_erase_range(partition", recovery)
        partitions = (ROOT / "firmware" / "tiny_touch_unified" / "partitions.csv").read_text()
        self.assertIn("recovery,   data, 0x40", partitions)

    def test_browser_requires_protocol_six_and_prefetches_before_usb(self):
        source = (ROOT / "docs" / ".vitepress" / "theme" / "FlashTool.vue").read_text()
        self.assertIn("const UPDATE_PROTOCOL = 6", source)
        self.assertNotIn("await loader.eraseFlash()", source)
        self.assertIn("release.firmware?.recovery", source)
        self.assertIn("function releaseAsset(file: string, tag?: string)", source)
        self.assertNotIn("/firmware/${image.file}", source)
        self.assertIn(
            '<option value="dev">Development firmware</option>', source
        )
        self.assertIn("release.prerelease", source)
        proxy = (ROOT / "docs" / "api" / "github-release.js").read_text()
        self.assertIn("redirect: 'follow'", proxy)
        self.assertIn("RELEASE_ASSETS.has(file)", proxy)
        self.assertIn("'tiny_touch_recovery.bin'", proxy)
        self.assertNotIn('"rewrites"', (ROOT / "docs" / "vercel.json").read_text())
        self.assertNotIn("/flash/recovery", source)
        self.assertIn("nextManifest.eraseAll !== false", source)
        self.assertIn("nextManifest.compress !== false", source)
        self.assertIn("requestPort({ filters: [{ usbVendorId: 0x303a }] })", source)
        flash = source.split("async function flash()", 1)[1].split(
            "async function selectTool()", 1
        )[0]
        self.assertLess(
            flash.index("const fileArray = firmwareFiles.value"),
            flash.index("navigator.serial.requestPort"),
        )


if __name__ == "__main__":
    unittest.main()

import hashlib
import importlib.util
import io
import json
import struct
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path


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

    def make_app(self, path: Path) -> None:
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
                    self.make_app(path)
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
        self.assertFalse((ROOT / "release" / "release-local").exists())
        self.assertFalse((ROOT / "release" / "tag-release").exists())
        self.assertFalse((ROOT / "release" / "release").exists())

    def test_browser_requires_protocol_six_and_prefetches_before_usb(self):
        source = (ROOT / "docs" / ".vitepress" / "theme" / "FlashTool.vue").read_text()
        self.assertIn("const UPDATE_PROTOCOL = 6", source)
        self.assertIn("await loader.eraseFlash()", source)
        self.assertIn("function releaseAsset(file: string, tag?: string)", source)
        self.assertNotIn("/firmware/${image.file}", source)
        self.assertIn(
            '<option value="dev">Development firmware</option>', source
        )
        self.assertIn("release.prerelease", source)
        proxy = (ROOT / "docs" / "api" / "github-release.js").read_text()
        self.assertIn("redirect: 'follow'", proxy)
        self.assertIn("RELEASE_ASSETS.has(file)", proxy)
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

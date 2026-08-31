#!/usr/bin/env python3
"""Create the complete, flat set of assets promoted by the tag workflow."""

from __future__ import annotations

import argparse
import json
import shutil
import tarfile
import tempfile
from pathlib import Path

from release_integrity import digest, validate_checksums, validate_release


ROOT = Path(__file__).resolve().parent.parent
WEB_FILES = ("index.html", "styles.css", "app.js")


def copy_once(source: Path, destination: Path) -> None:
    if destination.exists():
        if digest(source) != digest(destination):
            raise SystemExit(f"public filename has conflicting contents: {destination.name}")
        return
    shutil.copy2(source, destination)


def add_tree(archive: tarfile.TarFile, source: Path, name: str) -> None:
    archive.add(source, arcname=name, recursive=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("release", type=Path)
    parser.add_argument("--web-source", type=Path, default=ROOT / "web")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    args = parser.parse_args()
    release = args.release.resolve()
    output = args.output.resolve()
    validate_release(release, args.commit)
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    manifest = json.loads((release / "release-manifest.json").read_text(encoding="utf-8"))
    copy_once(release / "release-manifest.json", output / "release-manifest.json")
    for kind in ("factory", "recovery"):
        layout = manifest["firmware"][kind]
        for metadata in [*layout["images"], layout["fullImage"]]:
            copy_once(release / kind / metadata["file"], output / metadata["file"])
    for metadata in (manifest["ota"], manifest["migration"]["otaState"], *manifest["cli"].values()):
        copy_once(release / metadata["file"], output / metadata["file"])

    with tempfile.TemporaryDirectory(prefix="tinytouch-release-") as temporary:
        staging = Path(temporary)
        site = staging / "site"
        for kind, site_name in (("factory", "flasher"), ("recovery", "recovery")):
            destination = site / site_name
            firmware = destination / "firmware"
            firmware.mkdir(parents=True)
            for name in WEB_FILES:
                copy_once(args.web_source / site_name / name, destination / name)
            shutil.copytree(args.web_source / site_name / "vendor", destination / "vendor")
            shutil.copy2(release / kind / "manifest.json", destination / "manifest.json")
            for metadata in [*manifest["firmware"][kind]["images"],
                             manifest["firmware"][kind]["fullImage"]]:
                copy_once(release / kind / metadata["file"], firmware / metadata["file"])
        shutil.copy2(release / "release-manifest.json", site / "release.json")
        with tarfile.open(output / "tinytouch-web-flashers.tar.gz", "w:gz") as archive:
            for name in ("flasher", "recovery", "release.json"):
                add_tree(archive, site / name, name)
        with tarfile.open(output / "tinytouch-firmware.tar.gz", "w:gz") as archive:
            for name in ("factory", "recovery", "release-manifest.json"):
                add_tree(archive, release / name, name)

    lines = [
        f"{digest(path)}  {path.name}"
        for path in sorted(output.iterdir(), key=lambda item: item.name)
        if path.is_file() and path.name != "checksums.txt"
    ]
    (output / "checksums.txt").write_text("\n".join(lines) + "\n", encoding="ascii")
    validate_release(output, args.commit, flat=True)
    validate_checksums(output)


if __name__ == "__main__":
    main()

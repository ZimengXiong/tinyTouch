#!/usr/bin/env python3
"""Create the complete, flat set of assets promoted by the tag workflow."""

from __future__ import annotations

import argparse
import re
import shutil
import tarfile
from pathlib import Path

from release_integrity import digest, validate_checksums, validate_release


ROOT = Path(__file__).resolve().parent.parent


def copy_once(source: Path, destination: Path) -> None:
    if destination.exists():
        if digest(source) != digest(destination):
            raise SystemExit(f"public filename has conflicting contents: {destination.name}")
        return
    shutil.copy2(source, destination)


def write_installer(destination: Path, version: str) -> None:
    source = ROOT / "packaging" / "install.sh"
    if "-beta." not in version:
        copy_once(source, destination)
        return
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+-beta\.[0-9]+", version):
        raise SystemExit("invalid beta installer version")
    installer = source.read_text(encoding="utf-8")
    installer = installer.replace("installer_version=''", f"installer_version='{version}'")
    installer = installer.replace(
        "https://github.com/ZimengXiong/tinyTouch/releases/latest/download",
        f"https://github.com/ZimengXiong/tinyTouch/releases/download/v{version}",
    )
    destination.write_text(installer, encoding="utf-8")
    shutil.copymode(source, destination)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("release", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    args = parser.parse_args()
    release = args.release.resolve()
    output = args.output.resolve()
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    manifest = validate_release(release, args.commit)
    copy_once(release / "release-manifest.json", output / "release-manifest.json")
    for kind in ("factory",):
        layout = manifest["firmware"][kind]
        for metadata in [*layout["images"], layout["fullImage"]]:
            copy_once(release / kind / metadata["file"], output / metadata["file"])
    for metadata in (manifest["ota"], *manifest["cli"].values()):
        copy_once(release / metadata["file"], output / metadata["file"])
    write_installer(output / "install.sh", manifest["version"])

    with tarfile.open(output / "tinytouch-firmware.tar.gz", "w:gz") as archive:
        for name in ("factory", "release-manifest.json"):
            archive.add(release / name, arcname=name, recursive=True)

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

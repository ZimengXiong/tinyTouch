#!/usr/bin/env python3
"""Publish verified release assets into the VitePress public directory."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from release_integrity import validate_release


def copy_asset(source: Path, destination: Path, metadata: dict) -> None:
    filename = metadata["file"]
    shutil.copy2(source / filename, destination / filename)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("release", type=Path)
    parser.add_argument("public", type=Path)
    parser.add_argument("--commit", required=True)
    args = parser.parse_args()

    release = args.release.resolve()
    public = args.public.resolve()
    manifest = validate_release(release, args.commit, flat=True)

    flash_root = public / "flash"
    for kind in ("factory", "recovery"):
        destination = flash_root / kind
        if destination.exists():
            shutil.rmtree(destination)
        firmware = destination / "firmware"
        firmware.mkdir(parents=True)
        layout = manifest["firmware"]["factory"]
        (destination / "manifest.json").write_text(
            json.dumps(layout, indent=2) + "\n", encoding="utf-8"
        )
        for metadata in [*layout["images"], layout["fullImage"]]:
            copy_asset(release, firmware, metadata)

    cli = public / "cli"
    if cli.exists():
        shutil.rmtree(cli)
    cli.mkdir(parents=True)
    for metadata in manifest["cli"].values():
        copy_asset(release, cli, metadata)

    shutil.copy2(release / "release-manifest.json", public / "release.json")


if __name__ == "__main__":
    main()

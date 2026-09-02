#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
PROTOCOL = 6
SECURE_VERSION = 0
FLASH_SIZE = 4 * 1024 * 1024


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def copy_image(source: Path, destination: Path, name: str, address: int) -> dict:
    if not source.is_file():
        raise SystemExit(f"missing build artifact: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    size = destination.stat().st_size
    if address + size > FLASH_SIZE:
        raise SystemExit(f"{name} does not fit in 4 MB flash at {address:#x}")
    return {
        "name": name,
        "file": destination.name,
        "address": address,
        "size": size,
        "sha256": digest(destination),
    }


def merge(images: list[dict], directory: Path, output: Path) -> None:
    command = [
        sys.executable, "-m", "esptool", "--chip", "esp32s3", "merge_bin",
        "--output", str(output), "--flash_mode", "dio", "--flash_freq", "80m",
        "--flash_size", "4MB",
    ]
    for image in images:
        command.extend([hex(image["address"]), str(directory / image["file"])])
    subprocess.run(command, check=True)


def require_consistent_asset_names(value: object, seen: dict[str, str] | None = None) -> None:
    """Reject manifests where one public filename refers to different bytes."""
    if seen is None:
        seen = {}
    if isinstance(value, dict):
        if isinstance(value.get("file"), str) and isinstance(value.get("sha256"), str):
            filename = value["file"]
            checksum = value["sha256"]
            if filename in seen and seen[filename] != checksum:
                raise SystemExit(f"public asset name has conflicting contents: {filename}")
            seen[filename] = checksum
        for child in value.values():
            require_consistent_asset_names(child, seen)
    elif isinstance(value, list):
        for child in value:
            require_consistent_asset_names(child, seen)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--firmware-build", type=Path, required=True)
    parser.add_argument("--cli", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / "release")
    parser.add_argument("--build-id")
    args = parser.parse_args()

    output = args.output.resolve()
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)

    layouts = {}
    specifications = {
        "factory": (
            args.firmware_build,
            [
                ("Bootloader", "bootloader/bootloader.bin", "bootloader.bin", 0x0),
                ("Partition table", "partition_table/partition-table.bin", "partition-table.bin", 0x8000),
                ("Unified firmware", "tiny_touch_unified.bin", "tiny_touch_unified.bin", 0x10000),
                ("OTA state", "ota_data_initial.bin", "ota_data_initial.bin", 0x210000),
            ],
        ),
    }
    for kind, (build, files) in specifications.items():
        directory = output / kind
        images = [
            copy_image(build / source, directory / destination, name, address)
            for name, source, destination, address in files
        ]
        full_name = "tiny_touch_factory_full.bin"
        merge(images, directory, directory / full_name)
        layouts[kind] = {
            "version": VERSION,
            "protocol": PROTOCOL,
            "secureVersion": SECURE_VERSION,
            "flashSize": "4MB",
            "eraseAll": False,
            "compress": False,
            "images": images,
            "fullImage": {
                "file": full_name,
                "size": (directory / full_name).stat().st_size,
                "sha256": digest(directory / full_name),
            },
        }
        (directory / "manifest.json").write_text(
            json.dumps(layouts[kind], indent=2) + "\n", encoding="utf-8"
        )

    built_app = output / "factory" / "tiny_touch_unified.bin"
    ota_image = output / "tiny_touch_unified.bin"
    shutil.copy2(built_app, ota_image)
    build_id = args.build_id or os.environ.get("GITHUB_SHA", "")[:12]
    if not build_id:
        build_id = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"], cwd=ROOT,
            check=True, capture_output=True, text=True,
        ).stdout.strip()
    if len(build_id) != 12 or any(character not in "0123456789abcdef" for character in build_id):
        raise SystemExit("build ID must be the first 12 lowercase hex characters of the commit SHA")
    release = {
        "version": VERSION,
        "build": build_id,
        "protocol": PROTOCOL,
        "secureVersion": SECURE_VERSION,
        "boards": ["esp32s3-super-mini", "seeed-xiao-esp32s3"],
        "firmware": layouts,
        "ota": {
            "file": ota_image.name,
            "size": ota_image.stat().st_size,
            "sha256": digest(ota_image),
        },
    }
    if args.cli:
        cli_target = output / "tinytouch-macos-arm64.tar.gz"
        shutil.copy2(args.cli, cli_target)
        release["cli"] = {
            "macos-arm64": {
                "file": cli_target.name,
                "size": cli_target.stat().st_size,
                "sha256": digest(cli_target),
                "format": "tar.gz",
            }
        }
    require_consistent_asset_names(release)
    (output / "release-manifest.json").write_text(
        json.dumps(release, indent=2) + "\n", encoding="utf-8"
    )

if __name__ == "__main__":
    main()

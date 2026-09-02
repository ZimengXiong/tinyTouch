#!/usr/bin/env python3
"""Validate and safely unpack immutable tinyTouch release candidates."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import tarfile
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parent.parent
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
PROTOCOL = 6
SECURE_VERSION = 0
FLASH_BYTES = 4 * 1024 * 1024
APP_DESCRIPTION_MAGIC = 0xABCD5432
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
BUILD_PATTERN = re.compile(r"[0-9a-f]{12}")
NAME_PATTERN = re.compile(r"[A-Za-z0-9._-]+")
EXPECTED_IMAGES = {
    "factory": {
        0x0: "bootloader.bin",
        0x8000: "partition-table.bin",
        0x10000: "tiny_touch_unified.bin",
        0x210000: "ota_data_initial.bin",
    },
}
EXPECTED_FULL_IMAGES = {
    "factory": "tiny_touch_factory_full.bin",
}


class IntegrityError(RuntimeError):
    """Release data is incomplete, inconsistent, or unsafe."""


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise IntegrityError(message)


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IntegrityError(f"invalid JSON file: {path}") from exc
    require(isinstance(value, dict), f"JSON root must be an object: {path}")
    return value


def checked_name(value: object, field: str) -> str:
    require(isinstance(value, str) and NAME_PATTERN.fullmatch(value) is not None,
            f"invalid {field}")
    return value


def checked_asset(metadata: object, path: Path, label: str) -> dict:
    require(isinstance(metadata, dict), f"invalid {label} metadata")
    name = checked_name(metadata.get("file"), f"{label} filename")
    size = metadata.get("size")
    checksum = metadata.get("sha256")
    require(isinstance(size, int) and not isinstance(size, bool) and 0 < size <= 256 * 1024 * 1024,
            f"invalid {label} size")
    require(isinstance(checksum, str) and SHA256_PATTERN.fullmatch(checksum) is not None,
            f"invalid {label} SHA-256")
    require(path.name == name and path.is_file(), f"missing {label}: {name}")
    require(path.stat().st_size == size, f"wrong size for {label}: {name}")
    require(digest(path) == checksum, f"wrong SHA-256 for {label}: {name}")
    return metadata


def app_description(path: Path) -> dict[str, object]:
    data = path.read_bytes()
    offset = data.find(struct.pack("<I", APP_DESCRIPTION_MAGIC))
    require(offset == 0x20 and offset + 144 <= len(data),
            f"missing ESP app descriptor at offset 0x20: {path.name}")

    def text(start: int, size: int) -> str:
        try:
            return data[offset + start:offset + start + size].split(b"\0", 1)[0].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise IntegrityError(f"invalid ESP app descriptor text: {path.name}") from exc

    return {
        "secure_version": struct.unpack_from("<I", data, offset + 4)[0],
        "version": text(16, 32),
        "project": text(48, 32),
        "idf": text(112, 32),
    }


def validate_app(path: Path, version: str, build: str, kind: str) -> None:
    description = app_description(path)
    require(description["version"] == version,
            f"embedded version mismatch in {path.name}: {description['version']}")
    require(description["project"] == "tiny_touch_unified",
            f"embedded project mismatch in {path.name}: {description['project']}")
    require(description["idf"].startswith("v5.3."),
            f"unexpected ESP-IDF version in {path.name}: {description['idf']}")
    require(description["secure_version"] == SECURE_VERSION,
            f"embedded secure version mismatch in {path.name}")
    require(build.encode("ascii") in path.read_bytes(),
            f"build ID {build} is not embedded in {path.name}")


def asset_path(root: Path, kind: str, name: str, flat: bool) -> Path:
    return root / name if flat else root / kind / name


def validate_layout(root: Path, kind: str, layout: object, version: str,
                    protocol: int, build: str, flat: bool) -> dict[str, str]:
    require(isinstance(layout, dict), f"missing {kind} layout")
    require(layout.get("version") == version, f"{kind} version mismatch")
    require(layout.get("protocol") == protocol, f"{kind} protocol mismatch")
    require(layout.get("secureVersion") == SECURE_VERSION,
            f"{kind} secure version mismatch")
    require(layout.get("flashSize") == "4MB", f"{kind} flash size mismatch")
    require(layout.get("eraseAll") is False, f"{kind} must not use host-side eraseAll")
    require(layout.get("compress") is False, f"{kind} compression must be disabled")
    images = layout.get("images")
    require(isinstance(images, list) and len(images) == len(EXPECTED_IMAGES[kind]),
            f"{kind} must contain exactly four flash images")
    seen: dict[int, tuple[int, str]] = {}
    public: dict[str, str] = {}
    for index, image in enumerate(images):
        require(isinstance(image, dict), f"invalid {kind} image {index}")
        address = image.get("address")
        require(isinstance(address, int) and not isinstance(address, bool),
                f"invalid {kind} image address")
        name = checked_name(image.get("file"), f"{kind} image filename")
        require(EXPECTED_IMAGES[kind].get(address) == name,
                f"unexpected {kind} image {name} at {address!r}")
        path = asset_path(root, kind, name, flat)
        checked_asset(image, path, f"{kind} image")
        end = address + image["size"]
        require(end <= FLASH_BYTES, f"{kind} image exceeds 4 MB flash: {name}")
        seen[address] = (end, name)
        previous = sorted(seen.items())
        for (_, (left_end, left_name)), (right_address, (_, right_name)) in zip(previous, previous[1:]):
            require(left_end <= right_address,
                    f"{kind} flash images overlap: {left_name} and {right_name}")
        checksum = image["sha256"]
        if name in public:
            require(public[name] == checksum, f"conflicting public asset: {name}")
        public[name] = checksum
    full = layout.get("fullImage")
    full_name = EXPECTED_FULL_IMAGES[kind]
    require(isinstance(full, dict) and full.get("file") == full_name,
            f"invalid {kind} full image metadata")
    checked_asset(full, asset_path(root, kind, full_name, flat), f"{kind} full image")
    public[full_name] = full["sha256"]
    app_name = EXPECTED_IMAGES[kind][0x10000]
    validate_app(asset_path(root, kind, app_name, flat), version, build, kind)
    return public


def validate_cli_archive(path: Path) -> None:
    try:
        with tarfile.open(path, "r:gz") as archive:
            members = archive.getmembers()
    except (OSError, tarfile.TarError) as exc:
        raise IntegrityError(f"invalid CLI archive: {path.name}") from exc
    require(members, f"empty CLI archive: {path.name}")
    names = set()
    for member in members:
        pure = PurePosixPath(member.name)
        require(not pure.is_absolute() and ".." not in pure.parts,
                f"unsafe path in {path.name}: {member.name}")
        names.add(pure.as_posix().rstrip("/"))
    require("tinytouch/tinytouch" in names, f"CLI executable missing from {path.name}")
    require(any(name == "tinytouch/_internal" or name.startswith("tinytouch/_internal/") for name in names),
            f"CLI runtime missing from {path.name}")


def validate_release(root: Path, commit: str, *, flat: bool = False,
                     require_cli: bool = True) -> dict:
    require(re.fullmatch(r"[0-9a-f]{40}", commit) is not None, "commit must be a full SHA")
    manifest = load_json(root / "release-manifest.json")
    version = manifest.get("version")
    protocol = manifest.get("protocol")
    build = manifest.get("build")
    require(version == VERSION, f"release version {version!r} does not match VERSION {VERSION!r}")
    require(protocol == PROTOCOL, f"release protocol must be {PROTOCOL}")
    require(manifest.get("secureVersion") == SECURE_VERSION,
            f"release secure version must be {SECURE_VERSION}")
    require(isinstance(build, str) and BUILD_PATTERN.fullmatch(build) is not None,
            "invalid release build ID")
    require(build == commit[:12], "release build ID does not match candidate commit")
    require(manifest.get("boards") == ["esp32s3-super-mini", "seeed-xiao-esp32s3"],
            "unexpected board compatibility list")
    firmware = manifest.get("firmware")
    require(isinstance(firmware, dict) and set(firmware) == {"factory"},
            "release must contain one factory layout")
    public: dict[str, str] = {}
    for kind in ("factory",):
        for name, checksum in validate_layout(
            root, kind, firmware[kind], version, protocol, build, flat
        ).items():
            if name in public:
                require(public[name] == checksum, f"conflicting public asset: {name}")
            public[name] = checksum
    factory_app = asset_path(root, "factory", "tiny_touch_unified.bin", flat)
    ota = checked_asset(manifest.get("ota"), root / "tiny_touch_unified.bin",
                        "OTA image")
    require(ota["sha256"] == digest(factory_app), "OTA image differs from factory application")
    cli = manifest.get("cli")
    if not require_cli:
        require(cli is None, "firmware-only release unexpectedly contains CLI metadata")
        return manifest
    require(isinstance(cli, dict) and set(cli) == {"macos-arm64", "macos-x86_64"},
            "release must contain both macOS CLI architectures")
    for key, name in (
        ("macos-arm64", "tinytouch-macos-arm64.tar.gz"),
        ("macos-x86_64", "tinytouch-macos-x86_64.tar.gz"),
    ):
        metadata = cli[key]
        require(isinstance(metadata, dict) and metadata.get("file") == name and
                metadata.get("format") == "tar.gz", f"invalid {key} CLI metadata")
        path = root / name
        checked_asset(metadata, path, f"{key} CLI")
        validate_cli_archive(path)
        public[name] = metadata["sha256"]
    if flat:
        expected = set(public) | {
            "release-manifest.json",
            "tinytouch-firmware.tar.gz",
            "checksums.txt",
        }
        actual = {path.name for path in root.iterdir() if path.is_file()}
        require(actual == expected,
                f"published asset set mismatch; missing={sorted(expected - actual)}, "
                f"extra={sorted(actual - expected)}")
    return manifest


def validate_checksums(root: Path) -> None:
    checksum_path = root / "checksums.txt"
    try:
        lines = checksum_path.read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise IntegrityError("missing or invalid checksums.txt") from exc
    expected = {path.name for path in root.iterdir() if path.is_file() and path.name != "checksums.txt"}
    actual: set[str] = set()
    for line in lines:
        parts = line.split("  ", 1)
        require(len(parts) == 2 and SHA256_PATTERN.fullmatch(parts[0]) is not None,
                "invalid checksums.txt line")
        name = checked_name(parts[1], "checksum filename")
        require(name not in actual, f"duplicate checksum entry: {name}")
        path = root / name
        require(path.is_file() and digest(path) == parts[0], f"checksum mismatch: {name}")
        actual.add(name)
    require(actual == expected,
            f"checksum closure mismatch; missing={sorted(expected - actual)}, extra={sorted(actual - expected)}")


def safe_extract(candidate: Path, destination: Path) -> None:
    require(not destination.exists(), f"extraction destination already exists: {destination}")
    try:
        with tarfile.open(candidate, "r:gz") as archive:
            members = archive.getmembers()
            for member in members:
                pure = PurePosixPath(member.name)
                require(not pure.is_absolute() and ".." not in pure.parts,
                        f"unsafe candidate path: {member.name}")
                require(not member.issym() and not member.islnk(),
                        f"candidate archive links are not allowed: {member.name}")
            archive.extractall(destination, filter="data")
    except (OSError, tarfile.TarError) as exc:
        raise IntegrityError(f"invalid candidate archive: {candidate}") from exc


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("firmware", "assembled", "publish"):
        command = subparsers.add_parser(name)
        command.add_argument("directory", type=Path)
        command.add_argument("--commit", required=True)
    extract = subparsers.add_parser("extract")
    extract.add_argument("archive", type=Path)
    extract.add_argument("destination", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "extract":
            safe_extract(args.archive.resolve(), args.destination.resolve())
        else:
            directory = args.directory.resolve()
            validate_release(
                directory,
                args.commit,
                flat=args.command == "publish",
                require_cli=args.command != "firmware",
            )
            if args.command == "publish":
                validate_checksums(directory)
    except IntegrityError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

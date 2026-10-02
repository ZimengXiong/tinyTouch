#!/usr/bin/env python3
"""Require different release-signed builds to retain unattended Keychain access."""

import argparse
import hashlib
from pathlib import Path
import subprocess
import tempfile
import uuid


ROOT = Path(__file__).resolve().parent.parent


def requirement(executable: Path) -> str:
    result = subprocess.run(
        ["codesign", "-d", "-r-", str(executable)],
        capture_output=True, text=True, check=True,
    )
    return (result.stdout + result.stderr).split("designated => ", 1)[1].splitlines()[0]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cli", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="tinytouch-keychain-upgrade-") as directory:
        probes = [Path(directory) / f"build-{number}" for number in (1, 2)]
        for number, executable in enumerate(probes, 1):
            subprocess.run([
                "clang", "-Wno-deprecated-declarations", f"-DPROBE_BUILD={number}",
                str(ROOT / "tests/host/keychain_identity_probe.c"),
                "-framework", "Security", "-framework", "CoreFoundation",
                "-o", str(executable),
            ], check=True)
            subprocess.run([
                "zsh", str(ROOT / "release/sign-macos-cli.sh"), str(executable)
            ], check=True)
        expected = requirement(args.cli)
        if "cdhash" in expected or any(requirement(probe) != expected for probe in probes):
            raise RuntimeError("Release builds do not share a stable signing requirement")
        if hashlib.sha256(probes[0].read_bytes()).digest() == hashlib.sha256(probes[1].read_bytes()).digest():
            raise RuntimeError("The upgrade probe must use two different binaries")
        account = "upgrade-" + uuid.uuid4().hex
        subprocess.run([str(probes[0]), "store", account], check=True)
        try:
            subprocess.run([str(probes[1]), "read", account], check=True)
        finally:
            subprocess.run([str(probes[0]), "delete", account], check=True)
    print("Different release builds share unattended Keychain access.")


if __name__ == "__main__":
    main()

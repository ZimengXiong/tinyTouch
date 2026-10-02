#!/usr/bin/env python3
"""Check release naming before CI builds or release publishing."""

from __future__ import annotations

import argparse
from pathlib import Path

from release_integrity import IntegrityError, checked_version, load_json


ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version-file", type=Path, default=ROOT / "VERSION")
    parser.add_argument("--tag")
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    try:
        version = checked_version(args.version_file.read_text(encoding="utf-8").strip())
        if args.tag is not None and args.tag != f"v{version}":
            raise IntegrityError(f"release tag must be v{version}, got {args.tag!r}")
        if args.manifest is not None:
            manifest_version = checked_version(load_json(args.manifest).get("version"))
            if manifest_version != version:
                raise IntegrityError("release manifest version does not match VERSION")
    except (OSError, UnicodeDecodeError, IntegrityError) as exc:
        parser.error(str(exc))
    channel = "development prerelease" if "-dev." in version else "stable release"
    print(f"Version ok: {version} ({channel})")


if __name__ == "__main__":
    main()

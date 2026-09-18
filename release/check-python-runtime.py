#!/usr/bin/env python3
"""Reject macOS release runtimes that depend on the host's Expat library."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys


def check_binary(path: Path) -> None:
    dependencies = subprocess.check_output(["otool", "-L", str(path)], text=True)
    if "/usr/lib/libexpat" in dependencies:
        raise RuntimeError(
            f"{path} depends on macOS system Expat. Use a Python distribution "
            "with bundled Expat, such as a uv-managed Python 3.13 runtime."
        )


def main() -> None:
    if sys.version_info[:2] != (3, 13):
        raise RuntimeError(
            "Release builds require Python 3.13. Set TINYTOUCH_PYTHON to a "
            "compatible interpreter and use a fresh TINYTOUCH_VENV_DIR."
        )
    import pyexpat

    # Standalone Python can compile Expat into the interpreter itself.
    check_binary(Path(getattr(pyexpat, "__file__", sys.executable)))
    if len(sys.argv) > 1:
        bundle = Path(sys.argv[1])
        for path in bundle.rglob("*"):
            if path.is_file() and (
                path.name.startswith(("pyexpat", "libpython")) or path.name == "Python"
            ):
                check_binary(path)
    print(f"Release Python runtime ok (Expat {pyexpat.EXPAT_VERSION})")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ImportError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f"Unsafe release runtime: {exc}") from exc

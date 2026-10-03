"""Arrow-key menus that replace their own terminal output."""

from __future__ import annotations

import os
import select
import sys
import termios
import textwrap
import tty
from collections.abc import Callable

INSTRUCTIONS = "Use the up and down arrows to navigate this menu."


def supports_arrows() -> bool:
    if (
        not sys.stdin.isatty()
        or not sys.stdout.isatty()
        or os.environ.get("TERM") == "dumb"
    ):
        return False
    try:
        return os.isatty(sys.stdin.fileno())
    except (AttributeError, OSError, ValueError):
        return False


def read_key(descriptor: int) -> bytes:
    key = os.read(descriptor, 1)
    if not key or key == b"\x04":
        raise EOFError
    if key != b"\x1b":
        return key
    while len(key) < 8 and select.select([descriptor], [], [], 0.05)[0]:
        next_byte = os.read(descriptor, 1)
        if not next_byte:
            break
        key += next_byte
        if len(key) > 1 and (next_byte.isalpha() or next_byte == b"~"):
            break
    return key


def select_menu(
    title: str,
    options: list[tuple[str, str]],
    *,
    back: str,
    width: int,
    style: Callable[[str, str], str],
) -> str | None:
    descriptor = sys.stdin.fileno()
    attributes = termios.tcgetattr(descriptor)
    choices = [*options, (None, back)]
    selected = 0
    rendered_lines = 0

    def clear() -> None:
        nonlocal rendered_lines
        if rendered_lines:
            sys.stdout.write(f"\033[{rendered_lines}F\033[J")
            rendered_lines = 0

    def render() -> None:
        nonlocal rendered_lines
        clear()
        lines = [""]
        if title:
            lines.extend([style(title, "1;36"), ""])
        for index, (_key, label) in enumerate(choices):
            prefix = "  › " if index == selected else "    "
            text = textwrap.fill(
                label.removesuffix("."),
                width=width,
                initial_indent=prefix,
                subsequent_indent=" " * len(prefix),
            )
            if index == selected:
                text = style(text, "1;36")
            lines.extend(text.splitlines())
        lines.append("")
        for instruction in (INSTRUCTIONS, "Enter to select."):
            lines.extend(
                style(textwrap.fill(instruction, width=width), "2").splitlines()
            )
        sys.stdout.write("\n".join(lines) + "\n")
        rendered_lines = len(lines)
        sys.stdout.flush()

    try:
        tty.setcbreak(descriptor)
        sys.stdout.write("\033[?25l")
        while True:
            render()
            key = read_key(descriptor)
            if key in {b"\x1b[A", b"\x1bOA", b"\x1b[Z"}:
                selected = (selected - 1) % len(choices)
            elif key in {b"\x1b[B", b"\x1bOB", b"\t"}:
                selected = (selected + 1) % len(choices)
            elif key in {b"\x1b[H", b"\x1b[1~"}:
                selected = 0
            elif key in {b"\x1b[F", b"\x1b[4~"}:
                selected = len(choices) - 1
            elif key in {b"\r", b"\n"}:
                return choices[selected][0]
            elif key in {b"\x1b", b"q", b"Q"}:
                return None
    finally:
        clear()
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()
        termios.tcsetattr(descriptor, termios.TCSADRAIN, attributes)

"""Arrow selection, terminal cleanup, and key-sequence tests."""

import importlib.util
import io
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "arrow_tinytouch_menu", ROOT / "macos" / "tinytouch_menu.py"
)
menu = importlib.util.module_from_spec(spec)
spec.loader.exec_module(menu)


class ArrowMenuTests(unittest.TestCase):
    def choose(self, keys):
        self.output = io.StringIO()
        self.attributes = ["original terminal attributes"]
        self.restore = self.enterContext(mock.patch.object(menu.termios, "tcsetattr"))
        self.enterContext(
            mock.patch.object(menu.termios, "tcgetattr", return_value=self.attributes)
        )
        self.enterContext(mock.patch.object(menu.tty, "setcbreak"))
        self.enterContext(mock.patch.object(menu.sys, "stdout", self.output))
        self.enterContext(mock.patch.object(menu.sys.stdin, "fileno", return_value=42))
        self.enterContext(mock.patch.object(menu, "read_key", side_effect=keys))
        return menu.select_menu(
            "Actions",
            [("setup", "Setup"), ("status", "Status")],
            back="Back",
            width=64,
            style=lambda text, _code: text,
        )

    def test_down_arrow_selects_next_action_and_clears_menu(self):
        self.assertEqual(self.choose([b"\x1b[B", b"\r"]), "status")
        text = self.output.getvalue()
        self.assertIn(menu.INSTRUCTIONS, text)
        self.assertIn("› Status", text)
        self.assertIn("\033[J", text)
        self.assertTrue(text.endswith("\033[?25h"))
        self.restore.assert_called_once_with(
            42, menu.termios.TCSADRAIN, self.attributes
        )

    def test_up_arrow_wraps_to_back(self):
        self.assertIsNone(self.choose([b"\x1b[A", b"\r"]))

    def test_escape_cancels_and_restores_terminal(self):
        self.assertIsNone(self.choose([b"\x1b"]))
        self.restore.assert_called_once()

    def test_interrupt_restores_terminal_and_cursor(self):
        with self.assertRaises(KeyboardInterrupt):
            self.choose([KeyboardInterrupt])
        self.restore.assert_called_once()
        self.assertTrue(self.output.getvalue().endswith("\033[?25h"))

    def test_eof_restores_terminal_and_cursor(self):
        with self.assertRaises(EOFError):
            self.choose([EOFError])
        self.restore.assert_called_once()
        self.assertTrue(self.output.getvalue().endswith("\033[?25h"))

    def test_reads_arrow_sequence_as_one_key(self):
        with (
            mock.patch.object(menu.os, "read", side_effect=[b"\x1b", b"[", b"B"]),
            mock.patch.object(menu.select, "select", return_value=([42], [], [])),
        ):
            self.assertEqual(menu.read_key(42), b"\x1b[B")

    def test_escape_without_sequence_is_back(self):
        with (
            mock.patch.object(menu.os, "read", return_value=b"\x1b"),
            mock.patch.object(menu.select, "select", return_value=([], [], [])),
        ):
            self.assertEqual(menu.read_key(42), b"\x1b")


if __name__ == "__main__":
    unittest.main()

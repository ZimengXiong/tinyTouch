"""Completion stays in sync with argparse and never dispatches device commands."""

import contextlib
import importlib.util
import io
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("completion_cli", ROOT / "macos/cli.py")
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


class ShellCompletionTests(unittest.TestCase):
    def test_command_context_and_values(self):
        cases = [
            (["sta"], ["status"]),
            (["--verbose", "sta"], ["status"]),
            (["--port", "/dev/cu.test", "sta"], ["status"]),
            (["--port=/dev/cu.test", "sta"], ["status"]),
            (["setup", "--m"], ["--mode"]),
            (["setup", "--mode", "p"], ["piv"]),
            (["setup", "--mode=p"], ["--mode=piv"]),
            (["setup", "--mode", "=", "p"], ["piv"]),
            (["setup", "--mode", "="], ["hid", "piv"]),
            (["mode", "--port", "/dev/cu.test", "p"], ["piv"]),
            (["led", "co"], ["color"]),
            (["led", "color", "idle", "p"], ["purple"]),
            (["led", "preview", "blue", "--effect", "f"], ["flash", "fade-in", "fade-out"]),
            (["enroll", "1"], ["1", "10"]),
            (["enroll", "1", "--r"], ["--replace"]),
            (["password", "--finger", "1"], ["1", "10"]),
            (["config", "submit_"], ["submit_enter"]),
            (["settings", "--json", "submit-enter", "o"], ["off", "on"]),
            (["config", "led_mode", "only"], ["only-auth"]),
            (["help", "sta"], ["status"]),
            (["completion", "f"], ["fish"]),
            (["update", "--file", ""], ["__tinytouch_files__"]),
            (["update", "--file=fw"], ["__tinytouch_files__"]),
            (["--port", ""], ["__tinytouch_files__"]),
            (["unknown", ""], []),
            (["--unknown", ""], []),
            (["config", "unknown", ""], ["-h", "--help", "--json", "--port"]),
            (["led", "preview", "blue", "--duration-ms", ""], []),
            (["mode", "--", "--p"], []),
            (["mode", "--", "p"], ["piv"]),
            (["status", "extra", ""], []),
        ]
        for words, expected in cases:
            with self.subTest(words=words):
                self.assertEqual(cli.completion_candidates(words), expected)
        self.assertNotIn("_upgrade-helper", cli.completion_candidates([""]))
        self.assertNotIn("--firmware-only", cli.completion_candidates(["update", "--"]))

    def test_completion_does_not_dispatch_or_print_banner(self):
        for args in (["_complete", "--", "factory-reset", ""],
                     ["completion", "bash"]):
            with (
                self.subTest(args=args),
                mock.patch.object(sys, "argv", ["tinytouch", *args]),
                mock.patch.object(cli, "command_factory_reset") as reset,
                mock.patch.object(cli, "choose_port") as port,
                mock.patch.object(cli, "show_startup_mark") as banner,
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(cli.main(), 0)
                reset.assert_not_called()
                port.assert_not_called()
                banner.assert_not_called()

    def test_generated_scripts_in_shells(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wrapper = root / "tinytouch"
            wrapper.write_text(
                f"#!/bin/sh\nexec {shlex.quote(sys.executable)} "
                f"{shlex.quote(str(ROOT / 'macos/cli.py'))} \"$@\"\n"
            )
            wrapper.chmod(0o755)
            (root / "firmware image.bin").touch()
            env = {**os.environ, "PATH": directory + os.pathsep + os.environ["PATH"]}
            for shell, script in cli.COMPLETION_SCRIPTS.items():
                with self.subTest(shell=shell):
                    executable = shutil.which(shell)
                    if executable is None:
                        self.skipTest(f"{shell} is not installed")
                    completion = root / f"completion.{shell}"
                    completion.write_text(script)
                    subprocess.run([executable, "-n", str(completion)], check=True)
                    source = f"source {shlex.quote(str(completion))}\n"
                    if shell == "bash":
                        program = source + '''
COMP_WORDS=(tinytouch led color idle p); COMP_CWORD=4
_tinytouch; printf '%s\n' "${COMPREPLY[@]}"
COMP_WORDS=(tinytouch setup --mode = p); COMP_CWORD=4
_tinytouch; printf '%s\n' "${COMPREPLY[@]}"
COMP_WORDS=(tinytouch setup --mode=p); COMP_CWORD=2; COMP_WORDBREAKS='='
_tinytouch; printf '%s\n' "${COMPREPLY[@]}"
COMP_WORDS=(tinytouch update --file firm); COMP_CWORD=3
_tinytouch; printf '%s\n' "${COMPREPLY[@]}"
COMP_WORDS=(tinytouch '"mode"' '"p'); COMP_CWORD=2
_tinytouch; printf '%s\n' "${COMPREPLY[@]}"
COMP_WORDS=(tinytouch update --file '"firmware i'); COMP_CWORD=3
_tinytouch; printf '%s\n' "${COMPREPLY[@]}"
'''
                    elif shell == "zsh":
                        program = "autoload -Uz compinit; compinit -D\n" + source + '''
compadd() { shift; print -rl -- "$@"; }
words=(tinytouch led color idle p ignored); CURRENT=5; PREFIX=p
_tinytouch
words=(tinytouch setup --mode=p); CURRENT=3; PREFIX=--mode=p
_tinytouch
'''
                    else:
                        program = source + '''
complete -C 'tinytouch led color idle p'
complete -C 'tinytouch setup --mode=p'
complete -C 'tinytouch update --file firm'
complete -C 'tinytouch update --file=firm'
complete -C 'tinytouch mode "p'
'''
                    flags = ["--no-config"] if shell == "fish" else ["-f"] if shell == "zsh" else ["--noprofile", "--norc"]
                    result = subprocess.run([executable, *flags, "-c", program], env=env,
                                            cwd=root, text=True, capture_output=True, check=True)
                    self.assertEqual(result.stderr, "")
                    lines = [line.split("\t")[0] for line in result.stdout.splitlines()]
                    self.assertIn("purple", lines)
                    self.assertIn("piv" if shell == "bash" else "--mode=piv", lines)
                    if shell == "bash":
                        self.assertEqual(lines.count("piv"), 3)
                        self.assertNotIn("--mode=piv", lines)
                    if shell != "zsh":
                        self.assertIn("firmware image.bin", lines)
                    if shell == "fish":
                        self.assertIn("piv", lines)
                        self.assertIn("--file=firmware image.bin", lines)


if __name__ == "__main__":
    unittest.main()

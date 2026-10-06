"""Exercise TinyTouch's argcomplete integration without dispatching commands."""

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
    def test_completions_exit_before_dispatch(self):
        for line, expected in (
            ("sta", ["status"]),
            ("--verbose --port /dev/cu.test sta", ["status"]),
            ("setup --m", ["--mode"]),
            ("setup --mode=p", ["--mode=piv"]),
            ("led color idle p", ["purple"]),
            ("password --finger 1", ["1", "10"]),
            ("config submit_", ["submit_enter"]),
            ("config submit_enter o", ["off", "on"]),
            ("settings led_mode only", ["only-auth"]),
            ("settings submit-enter o", ["off", "on"]),
            ("config unknown z", []),
            ("config typing_delay_ms z", []),
            ("help sta", ["status"]),
            ("completion f", ["fish"]),
            ("_upgrade", []),
            ("update --firmware", []),
            ("factory-reset --p", ["--port"]),
        ):
            command = "tinytouch " + line
            output = io.StringIO()
            complete = cli.argcomplete.CompletionFinder()
            with (
                self.subTest(line=line),
                mock.patch.dict(os.environ, {"_ARGCOMPLETE": "1", "COMP_LINE": command,
                                             "COMP_POINT": str(len(command)), "_ARGCOMPLETE_IFS": "\n"}),
                mock.patch.object(sys, "argv", ["tinytouch"]),
                mock.patch.object(cli.argcomplete, "autocomplete", side_effect=lambda parser, **kwargs:
                                  complete(parser, output_stream=output, exit_method=sys.exit,
                                           append_space=False, **kwargs)),
                mock.patch.object(cli, "command_factory_reset") as reset,
                mock.patch.object(cli, "choose_port") as port,
                mock.patch.object(cli, "show_startup_mark") as banner,
                self.assertRaises(SystemExit) as exit,
            ):
                cli.main()
            self.assertEqual(exit.exception.code, 0)
            self.assertEqual(output.getvalue().splitlines(), expected)
            reset.assert_not_called()
            port.assert_not_called()
            banner.assert_not_called()

    def test_generated_scripts_in_shells(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wrapper = root / "tinytouch"
            wrapper.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} "
                               f"{shlex.quote(str(ROOT / 'macos/cli.py'))} \"$@\"\n")
            wrapper.chmod(0o755)
            (root / "firmware image.bin").touch()
            env = {**os.environ, "PATH": directory + os.pathsep + os.environ["PATH"]}
            programs = {
                "bash": '''
COMP_LINE='tinytouch led color idle p'; COMP_POINT=${#COMP_LINE}; COMP_TYPE=9
_python_argcomplete tinytouch; printf '%s\\n' "${COMPREPLY[@]}"
COMP_LINE='tinytouch update --file "firmware i'; COMP_POINT=${#COMP_LINE}
_python_argcomplete tinytouch; printf '%s\\n' "${COMPREPLY[@]}"
COMP_LINE='tinytouch setup --mode="p'; COMP_POINT=${#COMP_LINE}
COMP_WORDS=(tinytouch setup '--mode="p'); COMP_CWORD=2
_tinytouch tinytouch; printf '%s\\n' "${COMPREPLY[@]}"
''',
                "zsh": '''
_describe() { print -rl -- "${completions[@]}"; }
words=(tinytouch); BUFFER='tinytouch led color idle p'; CURSOR=${#BUFFER}
_python_argcomplete
''',
                "fish": '''
complete -C 'tinytouch led color idle p'
complete -C 'tinytouch update --file=firm'
complete -C 'tinytouch mode "p'
complete -C 'tinytouch config submit_enter o'
''',
            }
            for shell, program in programs.items():
                with self.subTest(shell=shell):
                    executable = shutil.which(shell)
                    if executable is None:
                        self.skipTest(f"{shell} is not installed")
                    completion = root / f"completion.{shell}"
                    with contextlib.redirect_stdout(io.StringIO()) as output:
                        with mock.patch.object(sys, "argv", ["tinytouch", "completion", shell]):
                            self.assertEqual(cli.main(), 0)
                    completion.write_text(output.getvalue())
                    source = f"source {shlex.quote(str(completion))}\n"
                    if shell == "zsh":
                        source = "autoload -Uz compinit; compinit -D\n" + source
                    flags = ["--no-config"] if shell == "fish" else ["-f"] if shell == "zsh" else ["--noprofile", "--norc"]
                    result = subprocess.run([executable, *flags, "-c", source + program], env=env,
                                            cwd=root, text=True, capture_output=True, check=True)
                    self.assertEqual(result.stderr, "")
                    self.assertIn("purple", result.stdout)
                    if shell == "bash":
                        self.assertIn("firmware image.bin", result.stdout)
                        self.assertIn("\npiv\n", result.stdout)
                    if shell == "fish":
                        self.assertIn("--file=firmware image.bin", result.stdout)
                        self.assertIn("piv", result.stdout)
                        self.assertIn("off\non\n", result.stdout)


if __name__ == "__main__":
    unittest.main()

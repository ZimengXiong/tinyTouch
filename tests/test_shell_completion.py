"""Exercise TinyTouch's argcomplete integration without dispatching commands."""

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import pty
import select
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
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
            python = shlex.quote(sys.executable)
            # Real completion invokes the CLI; pressing Enter only records argv.
            capture = "import json,sys; print('TTARGS:' + json.dumps(sys.argv[1:])); print('TTDONE')"
            wrapper.write_text("#!/bin/sh\nif [ -n \"${_ARGCOMPLETE-}\" ]; then\n"
                               f"exec {python} {shlex.quote(str(ROOT / 'macos/cli.py'))} \"$@\"\nfi\n"
                               f"exec {python} -c {shlex.quote(capture)} \"$@\"\n")
            wrapper.chmod(0o755)
            (root / "firmware=one.bin").touch()
            (root / "image with spaces.bin").touch()
            (root / "images").mkdir()
            (root / "images" / "firmware.bin").touch()
            env = {**os.environ, "PATH": directory + os.pathsep + os.environ["PATH"],
                   "TERM": "xterm", "INPUTRC": "/dev/null", "ZDOTDIR": directory,
                   "TT_COMPLETION_DIR": directory}
            for shell in ("bash", "zsh", "fish"):
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
                    if shell == "fish":
                        result = subprocess.run([executable, "--no-config", "-c", source +
                            "complete -C 'tinytouch led color idle p'; "
                            "complete -C 'tinytouch update --file=firm'; "
                            "complete -C 'tinytouch config submit_enter o'"],
                            env=env, cwd=root, text=True, capture_output=True, check=True)
                        self.assertEqual(result.stderr, "")
                        self.assertIn("purple", result.stdout)
                        self.assertIn("--file=firmware=one.bin", result.stdout)
                        self.assertIn("off\non\n", result.stdout)
                        for line, expected in (
                            ("update --file $TT_COMPLETION_DIR/firmw", "$TT_COMPLETION_DIR/firmware=one.bin"),
                            ("update --file=$TT_COMPLETION_DIR/firmw", "--file=$TT_COMPLETION_DIR/firmware=one.bin"),
                            ("--port $TT_COMPLETION_DIR/firmw", "$TT_COMPLETION_DIR/firmware=one.bin"),
                            ("--port=$TT_COMPLETION_DIR/firmw", "--port=$TT_COMPLETION_DIR/firmware=one.bin"),
                            ("update --file $TT_COMPLETION_DIR/images/firmw", "$TT_COMPLETION_DIR/images/firmware.bin"),
                            ("led color idle firmw", ""),
                            ("--port /dev/test firmw", ""),
                            ("config --fi", ""),
                        ):
                            with self.subTest(line=line):
                                result = subprocess.run([executable, "--no-config", "-c", source +
                                    "complete -C " + shlex.quote("tinytouch " + line)],
                                    env=env, cwd=root, text=True, capture_output=True, check=True)
                                self.assertEqual(result.stderr, "")
                                self.assertEqual(result.stdout.strip(), expected)
                        continue
                    if shell == "zsh":
                        source = "autoload -Uz compinit; compinit -D\n" + source
                        registration = '[[ ${_comps[tinytouch]} == _tinytouch ]]'
                        flags = ["-f"]
                    else:
                        registration = '[[ $(complete -p tinytouch) == *"-F _tinytouch tinytouch" ]]'
                        flags = ["--noprofile", "--norc"]
                    subprocess.run([executable, *flags, "-c", source + registration],
                                   env=env, cwd=root, capture_output=True, check=True)
                    master, slave = pty.openpty()
                    process = subprocess.Popen([executable, *flags, "-i"], cwd=root, env=env,
                                               stdin=slave, stdout=slave, stderr=slave,
                                               start_new_session=True)
                    try:
                        os.write(master, (source + "printf '__TT_%s__\\n' READY\n").encode())
                        self.read_until(master, b"__TT_READY__")
                        for line, expected in (
                            ("tinytouch led color idle p", ["led", "color", "idle", "purple"]),
                            ('tinytouch setup --mode="p', ["setup", "--mode=piv"]),
                            ('tinytouch update --file="firmw"', ["update", "--file=firmware=one.bin"]),
                            ('tinytouch update --file=./"firmw', ["update", "--file=./firmware=one.bin"]),
                            ('tinytouch --port=./"firmw', ["--port=./firmware=one.bin"]),
                            ('tinytouch update --file "image w', ["update", "--file", "image with spaces.bin"]),
                            ("tinytouch update --file firmw\t--port /safe", ["update", "--file", "firmware=one.bin", "--port", "/safe"]),
                            ("tinytouch update --file=firmw\t--port /safe", ["update", "--file=firmware=one.bin", "--port", "/safe"]),
                            ("tinytouch --port firmw\tstatus", ["--port", "firmware=one.bin", "status"]),
                            ("tinytouch --port=firmw\tstatus", ["--port=firmware=one.bin", "status"]),
                            ("tinytouch update --file images\tfirmw", ["update", "--file", "images/firmware.bin"]),
                        ):
                            with self.subTest(line=line):
                                os.write(master, (line + "\t\n").encode())
                                output = self.read_until(master, b"TTDONE")
                                arguments = output.split(b"TTARGS:", 1)[1].splitlines()[0]
                                self.assertEqual(json.loads(arguments), expected)
                    finally:
                        process.kill()
                        process.wait(timeout=5)
                        os.close(master)
                        os.close(slave)

    def read_until(self, fd, marker):
        output = b""
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if select.select([fd], [], [], 0.1)[0]:
                output += os.read(fd, 65536)
                if marker in output:
                    return output
        self.fail(f"Shell did not produce {marker!r}: {output!r}")


if __name__ == "__main__":
    unittest.main()

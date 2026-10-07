"""Check documented shell setup and real Tab insertion without dispatching commands.

Commands/choices and representative native paths run in Bash, Zsh, and Fish;
candidate checks cover hidden/invalid contexts and completion exits before I/O.
"""

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
import termios
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
            ("update --release", []),
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
                mock.patch.object(cli, "detect_ports", side_effect=AssertionError("device discovery")) as devices,
                mock.patch("serial.Serial", side_effect=AssertionError("serial access")) as serial,
                mock.patch.object(cli, "_keychain", side_effect=AssertionError("credential access")) as keychain,
                mock.patch.object(cli, "show_startup_mark") as banner,
                self.assertRaises(SystemExit) as exit,
            ):
                cli.main()
            self.assertEqual(exit.exception.code, 0)
            self.assertEqual(output.getvalue().splitlines(), expected)
            reset.assert_not_called()
            port.assert_not_called()
            devices.assert_not_called()
            serial.assert_not_called()
            keychain.assert_not_called()
            banner.assert_not_called()

    def test_generated_scripts_in_shells(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wrapper = root / "tinytouch"
            python = shlex.quote(sys.executable)
            command = ([os.environ["TINYTOUCH_TEST_CLI"]] if os.environ.get("TINYTOUCH_TEST_CLI")
                       else [sys.executable, str(ROOT / "macos/cli.py")])
            # Real completion invokes the CLI; pressing Enter only records argv.
            capture = "import json,sys; print('TTARGS:' + json.dumps(sys.argv[1:])); print('TTDONE')"
            wrapper.write_text("#!/bin/sh\nif [ -n \"${_ARGCOMPLETE-}\" ] || [ \"${1-}\" = completion ]; then\n"
                               f"exec {shlex.join(command)} \"$@\"\nfi\n"
                               f"exec {python} -c {shlex.quote(capture)} \"$@\"\n")
            wrapper.chmod(0o755)
            (root / "firmware=one.bin").touch()
            (root / "image with spaces.bin").touch()
            (root / "images").mkdir()
            (root / "images" / "firmware.bin").touch()
            env = {**os.environ, "PATH": directory + os.pathsep + os.environ["PATH"],
                   "TERM": "xterm", "INPUTRC": "/dev/null", "ZDOTDIR": directory,
                   "XDG_CONFIG_HOME": str(root / "config"),
                   "TT_COMPLETION_DIR": directory}
            for shell in ("bash", "zsh", "fish"):
                with self.subTest(shell=shell):
                    executable = shutil.which(shell)
                    if executable is None:
                        self.skipTest(f"{shell} is not installed")
                    if shell == "fish":
                        source = ("mkdir -p $XDG_CONFIG_HOME/fish/completions\n"
                                  "tinytouch completion fish > $XDG_CONFIG_HOME/fish/completions/tinytouch.fish\n"
                                  "source $XDG_CONFIG_HOME/fish/completions/tinytouch.fish\n")
                        registration = "complete -c tinytouch | string match -q '*__fish_tinytouch_complete*'"
                        flags = ["--no-config"]
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
                    elif shell == "zsh":
                        source = "autoload -Uz compinit\ncompinit\nsource <(tinytouch completion zsh)\n"
                        registration = '[[ ${_comps[tinytouch]} == _tinytouch ]]'
                        flags = ["-f"]
                    else:
                        source = 'eval "$(tinytouch completion bash)"\n'
                        registration = '[[ $(complete -p tinytouch) == *"-F _tinytouch tinytouch" ]]'
                        flags = ["--noprofile", "--norc"]
                    subprocess.run([executable, *flags, "-c", source + registration],
                                   env=env, cwd=root, capture_output=True, check=True)
                    master, slave = pty.openpty()
                    termios.tcsetwinsize(slave, (24, 120))
                    process = subprocess.Popen([executable, *flags, "-i"], cwd=root, env=env,
                                               stdin=slave, stdout=slave, stderr=slave,
                                               preexec_fn=lambda: os.login_tty(0))
                    try:
                        os.write(master, (source + "printf '__TT_%s__\\n' READY\n").encode())
                        self.read_until(master, b"__TT_READY__")
                        for line, expected in (
                            ("tinytouch sta", ["status"]),
                            ("tinytouch led col", ["led", "color"]),
                            ("tinytouch led color idle p", ["led", "color", "idle", "purple"]),
                            ("tinytouch setup --m", ["setup", "--mode"]),
                            ("tinytouch setup --mode p", ["setup", "--mode", "piv"]),
                            ("tinytouch setup --mode=p", ["setup", "--mode=piv"]),
                            ('tinytouch setup --mode="p', ["setup", "--mode=piv"]),
                            ("tinytouch password --finger 2", ["password", "--finger", "2"]),
                            ("tinytouch config submit_", ["config", "submit_enter"]),
                            ("tinytouch config submit_enter of", ["config", "submit_enter", "off"]),
                            ("tinytouch settings led_mode only", ["settings", "led_mode", "only-auth"]),
                            ("tinytouch settings submit-enter of", ["settings", "submit-enter", "off"]),
                            ("tinytouch help sta", ["help", "status"]),
                            ("tinytouch --verbose --port /safe sta", ["--verbose", "--port", "/safe", "status"]),
                            ('tinytouch update --file="firmw"', ["update", "--file=firmware=one.bin"]),
                            ('tinytouch update --file=./"firmw', ["update", "--file=./firmware=one.bin"]),
                            ('tinytouch --port=./"firmw', ["--port=./firmware=one.bin"]),
                            ('tinytouch update --file "image w', ["update", "--file", "image with spaces.bin"]),
                            (f'tinytouch update --file "{root}/image w"', ["update", "--file", str(root / "image with spaces.bin")]),
                            ('tinytouch --port="image w"', ["--port=image with spaces.bin"]),
                            (f"tinytouch update --file {root}/firmw", ["update", "--file", str(root / "firmware=one.bin")]),
                            (f"tinytouch --port={root}/firmw", ["--port=" + str(root / "firmware=one.bin")]),
                            ("tinytouch update --file firmw\t--port /safe", ["update", "--file", "firmware=one.bin", "--port", "/safe"]),
                            ("tinytouch update --file=firmw\t--port /safe", ["update", "--file=firmware=one.bin", "--port", "/safe"]),
                            ("tinytouch --port firmw\tstatus", ["--port", "firmware=one.bin", "status"]),
                            ("tinytouch --port=firmw\tstatus", ["--port=firmware=one.bin", "status"]),
                            ("tinytouch update --file images\tfirmw", ["update", "--file", "images/firmware.bin"]),
                            ("tinytouch --port=images\tfirmw\tstatus", ["--port=images/firmware.bin", "status"]),
                            ("tinytouch update --file $TT_COMPLETION_DIR/firmw", ["update", "--file", str(root / "firmware=one.bin")]),
                            ("tinytouch update --file=$TT_COMPLETION_DIR/firmw", ["update", "--file=" + str(root / "firmware=one.bin")]),
                            ("tinytouch --port $TT_COMPLETION_DIR/firmw", ["--port", str(root / "firmware=one.bin")]),
                            ("tinytouch --port=$TT_COMPLETION_DIR/firmw", ["--port=" + str(root / "firmware=one.bin")]),
                        ):
                            with self.subTest(line=line):
                                os.write(master, (line + "\t\n").encode())
                                output = self.read_until(master, b"TTDONE")
                                arguments = output.split(b"TTARGS:", 1)[1].splitlines()[0]
                                self.assertEqual(json.loads(arguments), expected)
                        if shell == "bash":
                            os.write(master, b"COMP_WORDBREAKS=${COMP_WORDBREAKS//=/}\n"
                                             b"printf '__TT_%s__\\n' WORDBREAKS\n")
                            self.read_until(master, b"__TT_WORDBREAKS__")
                            for line in ("tinytouch setup --mode=p", 'tinytouch setup --mode="p',
                                         "tinytouch setup --mode='p"):
                                with self.subTest(line=line, wordbreaks="without equals"):
                                    os.write(master, (line + "\t\n").encode())
                                    output = self.read_until(master, b"TTDONE")
                                    arguments = output.split(b"TTARGS:", 1)[1].splitlines()[0]
                                    self.assertEqual(json.loads(arguments), ["setup", "--mode=piv"])
                    finally:
                        process.kill()
                        os.close(master)
                        os.close(slave)
                        process.wait(timeout=5)

    def read_until(self, fd, marker):
        output = b""
        answered_queries = 0
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if select.select([fd], [], [], 0.1)[0]:
                output += os.read(fd, 65536)
                # Fish queries primary device attributes before enabling editing.
                queries = output.count(b"\x1b[0c")
                if queries > answered_queries:
                    os.write(fd, b"\x1b[?1;2c" * (queries - answered_queries))
                    answered_queries = queries
                if marker in output:
                    return output
        self.fail(f"Shell did not produce {marker!r}: {output!r}")


if __name__ == "__main__":
    unittest.main()

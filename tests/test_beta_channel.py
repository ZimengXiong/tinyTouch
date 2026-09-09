"""Verify beta isolation and release selection without accessing real services."""

import hashlib
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "software/macos-helper"))
import tinytouch_channel as channel
import tinytouch_helper as helper

loader = importlib.machinery.SourceFileLoader("beta_channel_cli", str(ROOT / "tinytouch"))
spec = importlib.util.spec_from_loader(loader.name, loader)
cli = importlib.util.module_from_spec(spec)
loader.exec_module(cli)


class BetaChannelTests(unittest.TestCase):
    def setUp(self):
        self.beta = channel.Channel("0.1.25-beta.1")
        self.production = channel.Channel("0.1.24-prod")

    def test_channel_identities_are_separate(self):
        for actual, expected in (
            (self.beta.command, "tinytouch-beta"),
            (self.beta.label, "com.tinytouch.beta.helper"),
            (self.beta.name, "tinyTouch-beta"),
            (self.beta.password_service, "tinyTouch-beta"),
            (self.beta.pairing_service, "tinyTouch-beta-pairing"),
            (self.production.command, "tinytouch"),
            (self.production.label, "com.tinytouch.helper"),
            (self.production.name, "tinyTouch"),
            (self.production.password_service, "tinyTouch"),
            (self.production.pairing_service, "tinyTouch-pairing"),
        ):
            self.assertEqual(actual, expected)

    def test_frozen_version_comes_from_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "VERSION").write_text("0.1.25-beta.1\n")
            with mock.patch.object(channel.sys, "_MEIPASS", directory, create=True):
                self.assertEqual(channel.bundled_version(), "0.1.25-beta.1")

    def test_release_roots_cannot_cross_channels(self):
        for identity, valid, invalid in (
            (self.beta, "0.1.25-beta.1", "0.1.25-prod"),
            (self.production, "0.1.25-prod", "0.1.25-beta.1"),
        ):
            with mock.patch.object(cli, "CHANNEL", identity):
                self.assertTrue(cli.release_root(valid).endswith("/v" + valid))
                for rejected in (invalid, "../0.1.25-prod", "0.1.25-beta", "0.1.25-beta.1/x"):
                    with self.assertRaises(cli.ToolError):
                        cli.release_root(rejected)

    def test_beta_feed_filters_drafts_stable_and_invalid_tags(self):
        releases = [
            {"tag_name": "v0.1.25-beta.2", "draft": False, "prerelease": True},
            {"tag_name": "v0.1.25-beta.10", "draft": False, "prerelease": True},
            {"tag_name": "v9.0.0-beta.1", "draft": True, "prerelease": True},
            {"tag_name": "v8.0.0-prod", "draft": False, "prerelease": False},
            {"tag_name": "v7.0.0-beta.1", "draft": False, "prerelease": False},
            {"tag_name": "v6.0.0-beta.1/evil", "draft": False, "prerelease": True},
        ]
        manifest = {"version": "0.1.25-beta.10"}
        with mock.patch.object(cli, "CHANNEL", self.beta), mock.patch.object(
            cli, "download", side_effect=[json.dumps(releases).encode(), json.dumps(manifest).encode()]
        ) as download:
            root, actual = cli.update_release()
        self.assertEqual(actual, manifest)
        self.assertTrue(root.endswith("v0.1.25-beta.10"))
        self.assertIn("api.github.com", download.call_args_list[0].args[0])
        self.assertEqual(download.call_args_list[1].args[0], root + "/release-manifest.json")

    def test_beta_does_not_fall_back_to_stable_when_feed_is_empty(self):
        with mock.patch.object(cli, "CHANNEL", self.beta), mock.patch.object(
            cli, "download", return_value=b"[]"
        ) as download:
            with self.assertRaisesRegex(cli.ToolError, "No published beta"):
                cli.update_release()
        self.assertEqual(download.call_count, 1)

    def test_selected_version_requires_matching_immutable_manifest(self):
        with mock.patch.object(cli, "CHANNEL", self.beta), mock.patch.object(
            cli, "download", return_value=b'{"version":"0.1.25-prod"}'
        ):
            with self.assertRaisesRegex(cli.ToolError, "do not match"):
                cli.update_release("0.1.25-beta.1")

    def test_production_helper_blocks_beta_without_mutation(self):
        with mock.patch.object(channel.subprocess, "run") as run:
            run.return_value.returncode = 0
            with self.assertRaisesRegex(RuntimeError, "production tinyTouch helper") as error:
                self.beta.require_device_access()
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0][0:2], ["launchctl", "print"])
        self.assertIn("launchctl bootstrap", str(error.exception))
        with mock.patch.object(channel.subprocess, "run") as run:
            self.production.require_device_access()
            run.assert_not_called()

    def test_beta_continues_only_when_production_helper_is_unloaded(self):
        with mock.patch.object(channel.subprocess, "run") as run:
            run.return_value.returncode = 113
            self.beta.require_device_access()
        with mock.patch.object(channel.subprocess, "run", side_effect=OSError):
            with self.assertRaisesRegex(RuntimeError, "Could not check"):
                self.beta.require_device_access()

    def test_helper_does_not_open_serial_while_production_is_loaded(self):
        with mock.patch.object(helper, "CHANNEL", self.beta), mock.patch.object(
            channel.subprocess, "run"
        ) as run, mock.patch.object(helper.serial, "Serial") as serial:
            run.return_value.returncode = 0
            with self.assertRaises(helper.serial.SerialException):
                helper.open_serial("/dev/fake")
            serial.assert_not_called()

    def test_beta_device_commands_refuse_before_side_effects(self):
        for command in (cli.command_setup, cli.command_mode, cli.command_update):
            with mock.patch.object(cli, "require_device_access", side_effect=cli.ToolError("blocked")):
                with self.assertRaisesRegex(cli.ToolError, "blocked"):
                    command(None)
        with mock.patch.object(cli, "require_device_access", side_effect=cli.ToolError("blocked")):
            with self.assertRaisesRegex(cli.ToolError, "blocked"):
                with cli.foreground_session("/dev/fake"):
                    self.fail("The device must not open")

    def test_network_smoke_can_run_before_first_beta_release(self):
        image = b"firmware"
        manifest = {"version": "0.1.24-prod", "ota": {
            "file": "firmware.bin", "size": len(image), "sha256": hashlib.sha256(image).hexdigest(),
        }}
        with mock.patch.object(cli, "CHANNEL", self.beta), mock.patch.object(
            cli, "download", side_effect=[json.dumps(manifest).encode(), json.dumps(manifest).encode(), image]
        ), mock.patch.object(cli, "update_release") as update, mock.patch.object(cli, "say"):
            cli.network_test()
            update.assert_not_called()

    def test_cli_and_helper_use_the_same_bundled_identity(self):
        self.assertEqual(cli.CHANNEL.beta, helper.CHANNEL.beta)
        self.assertEqual(cli.PASSWORD_SERVICE, helper.SERVICE)
        self.assertEqual(cli.PAIRING_SERVICE, helper.PAIRING_SERVICE)
        self.assertEqual(cli.SUPPORT_DIR, helper.STATE_DIR)
        self.assertEqual(cli.LAUNCH_AGENT.name, cli.CHANNEL.label + ".plist")
        self.assertEqual(cli.LOG_DIR.name, cli.CHANNEL.name)
        self.assertEqual(cli.parser().prog, cli.CHANNEL.command)


if __name__ == "__main__":
    unittest.main()

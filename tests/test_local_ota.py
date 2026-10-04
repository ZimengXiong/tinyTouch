"""Use the authenticated OTA path for local images without release downloads."""

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_tinytouch_cli import cli


class LocalOtaTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.image = self.root / "signed firmware.bin"
        self.payload = b"local signed firmware fixture"
        self.image.write_bytes(self.payload)
        self.args = cli.parser().parse_args(["update", "--file", str(self.image)])
        self.port = self.enterContext(mock.patch.object(cli, "choose_port", return_value="usb-test"))
        self.status = self.enterContext(mock.patch.object(
            cli, "status", return_value={"protocol": "6", "firmware": "test"},
        ))
        self.stage = self.enterContext(mock.patch.object(cli, "stage_ota"))
        self.output = self.enterContext(mock.patch.object(cli, "say"))
        self.enterContext(mock.patch.object(cli, "notify"))
        for name in ("update_release", "download", "command_upgrade_helper"):
            self.enterContext(mock.patch.object(
                cli, name, side_effect=AssertionError(f"Local update called {name}"),
            ))

    def test_file_bytes_use_authenticated_ota_with_their_actual_digest(self):
        self.args.func(self.args)
        self.stage.assert_called_once_with(
            "usb-test", self.payload, hashlib.sha256(self.payload).hexdigest(),
        )
        self.output.assert_called_once_with(
            "Update ready. Unplug and reconnect tinyTouch to finish."
        )

    def test_explicit_port_is_preserved(self):
        args = cli.parser().parse_args([
            "update", "--file", str(self.image), "--port", "selected-usb",
        ])
        args.func(args)
        self.port.assert_called_once_with("selected-usb")

    def test_missing_file_fails_before_opening_usb(self):
        self.image.unlink()
        with self.assertRaisesRegex(cli.ToolError, "Could not read"):
            self.args.func(self.args)
        self.port.assert_not_called()
        self.stage.assert_not_called()

    def test_empty_file_fails_before_opening_usb(self):
        self.image.write_bytes(b"")
        with self.assertRaisesRegex(cli.ToolError, "empty"):
            self.args.func(self.args)
        self.port.assert_not_called()
        self.stage.assert_not_called()

    def test_incompatible_protocol_never_starts_an_upload(self):
        self.status.return_value = {"protocol": "3", "firmware": "old"}
        with self.assertRaisesRegex(cli.ToolError, "requires protocol 6"):
            self.args.func(self.args)
        self.stage.assert_not_called()
        self.output.assert_not_called()

    def test_rejected_upload_cannot_report_success(self):
        self.stage.side_effect = cli.ToolError("ERR OTA COMMIT")
        with self.assertRaisesRegex(cli.ToolError, "OTA COMMIT"):
            self.args.func(self.args)
        self.output.assert_not_called()

    def test_local_image_and_remote_release_are_mutually_exclusive(self):
        with mock.patch.object(cli.sys, "stderr"):
            with self.assertRaises(SystemExit) as error:
                cli.parser().parse_args([
                    "update", "--file", str(self.image),
                    "--release-version", "0.1.35-dev.1",
                ])
        self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
    unittest.main()

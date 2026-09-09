"""Exercise helper switching with a simulated launchctl state machine."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "software/macos-helper"))
import tinytouch_session as session


class BetaSessionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.home = Path(directory.name)
        patch = mock.patch.object(session.Path, "home", return_value=self.home)
        patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch.object(session, "CHANNEL", mock.Mock(beta=True))
        patch.start()
        self.addCleanup(patch.stop)
        self.loaded = {session.PRODUCTION_LABEL: True, session.BETA_LABEL: False}
        self.disabled = {session.PRODUCTION_LABEL: False, session.BETA_LABEL: True}
        self.calls = []
        self.failure = None
        self.both_loaded = False
        for label in self.loaded:
            path = session._plist(label)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("test plist")
        patch = mock.patch.object(session.subprocess, "run", side_effect=self.launchctl)
        patch.start()
        self.addCleanup(patch.stop)
        self.record = self.home / "Library/Application Support/tinyTouch-beta/helper-session.json"

    def launchctl(self, arguments, **kwargs):
        self.calls.append(arguments)
        operation, target = arguments[1], arguments[-1]
        label = target.rsplit("/", 1)[-1]
        if self.failure == operation:
            return subprocess.CompletedProcess(arguments, 1, "", "simulated failure")
        if operation == "print":
            return subprocess.CompletedProcess(arguments, 0 if self.loaded[label] else 113, "", "")
        if operation == "print-disabled":
            output = "disabled services = {\n" + "\n".join(
                f'"{key}" => {str(value).lower()}' for key, value in self.disabled.items()
            ) + "\n}"
            return subprocess.CompletedProcess(arguments, 0, output, "")
        if operation == "bootstrap":
            label = Path(target).stem
            self.assertFalse(self.disabled[label])
            self.loaded[label] = True
        elif operation == "bootout":
            self.loaded[label] = False
        elif operation in ("disable", "enable"):
            # Production cannot change before the durable snapshot exists.
            if label == session.PRODUCTION_LABEL:
                self.assertTrue(self.record.is_file())
            self.disabled[label] = operation == "disable"
        else:
            self.fail(f"Unexpected operation: {operation}")
        self.both_loaded |= all(self.loaded.values())
        return subprocess.CompletedProcess(arguments, 0, "", "")

    def test_activate_and_exit_restore_running_production(self):
        session.activate_beta()
        self.assertEqual(self.loaded, {session.PRODUCTION_LABEL: False, session.BETA_LABEL: True})
        self.assertTrue(self.disabled[session.PRODUCTION_LABEL])
        self.assertFalse(self.disabled[session.BETA_LABEL])
        self.assertTrue(self.record.exists())
        session.exit_beta()
        self.assertEqual(self.loaded, {session.PRODUCTION_LABEL: True, session.BETA_LABEL: False})
        self.assertFalse(self.disabled[session.PRODUCTION_LABEL])
        self.assertTrue(self.disabled[session.BETA_LABEL])
        self.assertFalse(self.record.exists())
        self.assertFalse(self.both_loaded)

    def test_repeat_preserves_snapshot_and_repairs_restarted_production(self):
        session.activate_beta()
        original = self.record.read_bytes()
        self.loaded[session.BETA_LABEL] = False
        self.disabled[session.BETA_LABEL] = True
        self.loaded[session.PRODUCTION_LABEL] = True
        self.disabled[session.PRODUCTION_LABEL] = False
        session.activate_beta()
        self.assertEqual(self.record.read_bytes(), original)
        self.assertTrue(self.loaded[session.BETA_LABEL])
        self.assertFalse(self.loaded[session.PRODUCTION_LABEL])
        session.exit_beta()
        self.assertTrue(self.loaded[session.PRODUCTION_LABEL])

    def test_restore_all_original_enablement_and_load_combinations(self):
        for loaded in (False, True):
            for disabled in (False, True):
                self.loaded[session.PRODUCTION_LABEL] = loaded
                self.disabled[session.PRODUCTION_LABEL] = disabled
                session.activate_beta()
                session.exit_beta()
                self.assertEqual(self.loaded[session.PRODUCTION_LABEL], loaded)
                self.assertEqual(self.disabled[session.PRODUCTION_LABEL], disabled)

    def test_activation_without_beta_plist_supports_first_setup(self):
        session._plist(session.BETA_LABEL).unlink()
        session.activate_beta()
        self.assertFalse(self.loaded[session.BETA_LABEL])
        self.assertFalse(self.disabled[session.BETA_LABEL])
        session.exit_beta()
        self.assertTrue(self.loaded[session.PRODUCTION_LABEL])

    def test_exit_without_snapshot_stops_only_beta(self):
        self.loaded[session.BETA_LABEL] = True
        session.exit_beta()
        self.assertTrue(self.loaded[session.PRODUCTION_LABEL])
        self.assertFalse(self.disabled[session.PRODUCTION_LABEL])
        self.assertTrue(self.disabled[session.BETA_LABEL])
        self.assertFalse(any(session.PRODUCTION_LABEL in call[-1] for call in self.calls))

    def test_invalid_snapshot_blocks_all_mutations(self):
        self.record.parent.mkdir(parents=True)
        for content in ("broken", "{}", '{"schema":1,"uid":false,"loaded":true,"disabled":false}'):
            self.record.write_text(content)
            for operation in (session.activate_beta, session.exit_beta):
                with self.assertRaises(session.SessionError):
                    operation()
                self.assertEqual(self.calls, [])
                self.assertEqual(self.record.read_text(), content)

    def test_failed_activation_keeps_snapshot_and_never_starts_beta(self):
        self.failure = "bootout"
        with self.assertRaisesRegex(session.SessionError, "simulated failure"):
            session.activate_beta()
        self.assertTrue(self.record.exists())
        self.assertFalse(self.loaded[session.BETA_LABEL])
        self.failure = None
        session.exit_beta()
        self.assertTrue(self.loaded[session.PRODUCTION_LABEL])
        self.assertFalse(self.disabled[session.PRODUCTION_LABEL])

    def test_failed_restore_can_be_retried(self):
        session.activate_beta()
        self.failure = "bootstrap"
        with self.assertRaises(session.SessionError):
            session.exit_beta()
        self.assertTrue(self.record.exists())
        self.assertFalse(self.loaded[session.BETA_LABEL])
        self.failure = None
        session.exit_beta()
        self.assertTrue(self.loaded[session.PRODUCTION_LABEL])
        self.assertFalse(self.record.exists())

    def test_missing_production_plist_preserves_snapshot_for_repair(self):
        session.activate_beta()
        session._plist(session.PRODUCTION_LABEL).unlink()
        with self.assertRaisesRegex(session.SessionError, "saved helper file is missing"):
            session.exit_beta()
        self.assertTrue(self.record.exists())
        self.assertFalse(self.loaded[session.BETA_LABEL])

    def test_failed_state_read_leaves_no_snapshot_or_mutations(self):
        self.failure = "print"
        with self.assertRaises(session.SessionError):
            session.activate_beta()
        self.assertFalse(self.record.exists())
        self.assertEqual([call[1] for call in self.calls], ["print"])

    def test_command_lock_rejects_overlap_and_releases_on_error(self):
        with self.assertRaisesRegex(ValueError, "command failed"):
            with session.command_lock():
                with self.assertRaisesRegex(session.SessionError, "Another beta command"):
                    with session.command_lock():
                        self.fail("Overlapping command acquired the lock")
                raise ValueError("command failed")
        with session.command_lock():
            session.activate_beta()
        with session.command_lock():
            session.exit_beta()

    def test_snapshot_write_failure_prevents_helper_changes(self):
        with mock.patch.object(session, "atomic_write_json", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                session.activate_beta()
        self.assertEqual([call[1] for call in self.calls], ["print", "print-disabled"])
        self.assertTrue(self.loaded[session.PRODUCTION_LABEL])
        self.assertFalse(self.disabled[session.PRODUCTION_LABEL])

    def test_omitted_override_means_enabled(self):
        with mock.patch.object(session, "_launchctl", return_value=subprocess.CompletedProcess(
            [], 0, 'disabled services = {\n"some.other.service" => true\n}', ""
        )):
            self.assertFalse(session._disabled(session.PRODUCTION_LABEL))

    def test_unrecognized_enablement_output_fails_safely(self):
        with mock.patch.object(session, "_launchctl", return_value=subprocess.CompletedProcess(
            [], 0, "unexpected output", ""
        )):
            with self.assertRaisesRegex(session.SessionError, "Could not read helper enablement"):
                session._disabled(session.PRODUCTION_LABEL)


if __name__ == "__main__":
    unittest.main()

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "software" / "macos-helper"))

from tinytouch_runtime import (  # noqa: E402
    BackoffPolicy,
    ForegroundLease,
    LeaseBusyError,
    LeaseObserver,
    LeaseProtocolError,
    LeaseRecord,
    SerialFrameDecoder,
    atomic_write_json,
    diagnostic,
)
import tinytouch_runtime as runtime  # noqa: E402


class DurableStateTests(unittest.TestCase):
    def test_atomic_json_replaces_content_with_private_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            atomic_write_json(path, {"generation": 1})
            atomic_write_json(path, {"generation": 2})
            self.assertEqual(json.loads(path.read_text()), {"generation": 2})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(list(path.parent.glob(f".{path.name}.*")), [])


class LeaseTests(unittest.TestCase):
    def test_live_lease_is_acknowledged_with_matching_nonce(self):
        with tempfile.TemporaryDirectory() as directory:
            lease_path = Path(directory) / "lease.json"
            acknowledgement = Path(directory) / "ack.json"
            lease = ForegroundLease(lease_path, acknowledgement).acquire(wait_for_ack=False)
            try:
                observer = LeaseObserver(lease_path, acknowledgement)
                record = observer.active()
                self.assertEqual(record, lease.record)
                observer.acknowledge(record)
                self.assertEqual(
                    json.loads(acknowledgement.read_text())["nonce"], record.nonce
                )
            finally:
                lease.release()
            self.assertFalse(lease_path.exists())
            self.assertFalse(acknowledgement.exists())

    def test_process_exit_makes_lease_recoverable(self):
        with tempfile.TemporaryDirectory() as directory:
            lease_path = Path(directory) / "lease.json"
            acknowledgement = Path(directory) / "ack.json"
            atomic_write_json(
                lease_path,
                LeaseRecord(999999, "a" * 32, 1).as_json(),
            )
            acknowledgement.write_text("stale")
            self.assertIsNone(LeaseObserver(lease_path, acknowledgement).active())
            self.assertFalse(lease_path.exists())
            self.assertFalse(acknowledgement.exists())

    def test_only_one_foreground_owner_can_hold_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lease.json"
            first = ForegroundLease(path, Path(directory) / "ack.json").acquire(
                wait_for_ack=False
            )
            try:
                with self.assertRaises(LeaseBusyError):
                    ForegroundLease(path, Path(directory) / "ack.json").acquire(
                        wait_for_ack=False
                    )
            finally:
                first.release()

    def test_acknowledgement_timeout_releases_lease(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lease.json"
            with self.assertRaises(LeaseProtocolError):
                ForegroundLease(path, Path(directory) / "ack.json").acquire(
                    wait_for_ack=True, timeout=0.01
                )
            self.assertFalse(path.exists())


class BackoffTests(unittest.TestCase):
    def test_backoff_is_exponential_bounded_and_jittered(self):
        policy = BackoffPolicy(initial=1, maximum=8, jitter=0.25)
        self.assertEqual(policy.delay(1, random_value=lambda: 0.5), 1)
        self.assertEqual(policy.delay(2, random_value=lambda: 0.5), 2)
        self.assertEqual(policy.delay(20, random_value=lambda: 0.5), 8)
        self.assertEqual(policy.delay(1, random_value=lambda: 0), 0.75)
        self.assertEqual(policy.delay(1, random_value=lambda: 1), 1.25)


class StreamDecoderTests(unittest.TestCase):
    def test_split_and_coalesced_frames_are_preserved(self):
        decoder = SerialFrameDecoder(64)
        self.assertEqual(decoder.feed(b"EV2 one"), [])
        self.assertEqual(decoder.feed(b" two\nPONG\nEV "), [b"EV2 one two", b"PONG"])
        self.assertEqual(decoder.feed(b"three\n"), [b"EV three"])

    def test_oversized_frame_is_discarded_through_newline(self):
        decoder = SerialFrameDecoder(4)
        self.assertEqual(decoder.feed(b"12345EV forged"), [])
        self.assertEqual(decoder.feed(b" tail\nOK\n"), [b"OK"])

    def test_partial_frame_can_be_expired(self):
        decoder = SerialFrameDecoder(64)
        decoder.feed(b"partial")
        self.assertTrue(decoder.discard_partial())
        self.assertFalse(decoder.discard_partial())


class DiagnosticTests(unittest.TestCase):
    def test_structured_log_rotation_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "helper.log"
            with (
                mock.patch.dict("os.environ", {"TINYTOUCH_DIAGNOSTIC_LOG": str(path)}),
                mock.patch.object(runtime, "MAX_DIAGNOSTIC_BYTES", 100),
            ):
                for sequence in range(5):
                    diagnostic("test.event", sequence=sequence, payload="x" * 40)
            self.assertTrue(path.is_file())
            self.assertTrue(path.with_suffix(".log.1").is_file())
            self.assertLessEqual(path.stat().st_size, 150)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()

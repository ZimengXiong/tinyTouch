"""Exercise lease cleanup races using temporary files and real file locks."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "macos"))
import tinytouch_runtime as runtime  # noqa: E402


class LeaseCleanupTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = root / "lease.json"
        self.ack = root / "ack.json"
        self.observer = runtime.LeaseObserver(self.path, self.ack)

    def new_lease(self):
        lease = runtime.ForegroundLease(self.path, self.ack)
        self.addCleanup(lease.release)
        return lease

    def test_observer_does_not_delete_a_lease_published_after_its_read(self):
        stale = runtime.LeaseRecord(999999, "a" * 32, 1).as_json()
        for snapshot in (None, stale):
            with self.subTest(snapshot=snapshot):
                lease = self.new_lease()
                read = runtime.read_json_object

                def raced_read(path):
                    lease.acquire(wait_for_ack=False)
                    return snapshot

                with mock.patch.object(runtime, "read_json_object", side_effect=raced_read):
                    self.assertIsNone(self.observer.active())
                self.assertEqual(runtime.LeaseRecord.parse(read(self.path)), lease.record)
                self.assertEqual(self.observer.active(), lease.record)
                self.observer.acknowledge(lease.record)
                lease.release()

    def test_observer_leaves_incomplete_state_while_owner_holds_lock(self):
        lease = self.new_lease().acquire(wait_for_ack=False)
        self.path.write_text("incomplete")
        self.ack.write_text("old acknowledgement")
        self.assertIsNone(self.observer.active())
        self.assertEqual(self.path.read_text(), "incomplete")
        self.assertTrue(self.ack.exists())
        lease.release()
        self.assertIsNone(self.observer.active())
        self.assertFalse(self.path.exists())
        self.assertFalse(self.ack.exists())

    def test_publication_failure_releases_the_lock_without_context_manager(self):
        lease = self.new_lease()
        with mock.patch.object(runtime, "atomic_write_json", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                lease.acquire(wait_for_ack=False)
        self.assertIsNone(lease.record)
        self.assertIsNone(lease._lock)
        replacement = self.new_lease().acquire(wait_for_ack=False)
        self.assertEqual(self.observer.active(), replacement.record)

    def test_acknowledgement_read_failure_releases_the_lock(self):
        lease = self.new_lease()
        with mock.patch.object(
            lease, "_wait_for_ack", side_effect=OSError("acknowledgement unavailable")
        ):
            with self.assertRaisesRegex(OSError, "acknowledgement unavailable"):
                lease.acquire(wait_for_ack=True)
        self.assertIsNone(lease.record)
        self.assertIsNone(lease._lock)
        self.assertFalse(self.path.exists())
        self.new_lease().acquire(wait_for_ack=False)

    def test_unlink_failure_still_releases_the_lock(self):
        lease = self.new_lease().acquire(wait_for_ack=False)
        unlink = Path.unlink

        def denied(path, *args, **kwargs):
            if path == self.path:
                raise PermissionError("lease cleanup denied")
            return unlink(path, *args, **kwargs)

        with mock.patch.object(Path, "unlink", denied):
            with self.assertRaisesRegex(PermissionError, "lease cleanup denied"):
                lease.release()
        self.assertIsNone(lease.record)
        self.assertIsNone(lease._lock)
        self.new_lease().acquire(wait_for_ack=False)

    def test_observer_without_state_does_not_create_a_lock_directory(self):
        missing = self.path.parent / "missing"
        observer = runtime.LeaseObserver(missing / "lease", missing / "ack")
        self.assertIsNone(observer.active())
        self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main()

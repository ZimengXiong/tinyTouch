"""Check fingerprint capacity before approving or replacing enrollment."""

import unittest
from unittest import mock

from test_tinytouch_cli import cli


class FingerInventoryTests(unittest.TestCase):
    def inventory(self, fields, **kwargs):
        with mock.patch.object(cli, "serial_command", return_value=[f"OK FINGER LIST {fields}"]):
            return cli.finger_inventory("TT", {"finger_groups": "1"}, **kwargs)

    def test_smaller_sensor_lists_partial_legacy_blocks(self):
        groups, available = self.inventory(
            "groups=1:4,5:2,10:1 available=3 capacity=20 pending=0"
        )
        self.assertEqual(groups, {1: 4, 5: 2, 10: 1})
        self.assertEqual(available, 3)

    def test_pending_empty_block_reserves_capacity(self):
        groups, available = self.inventory(
            "groups=2:0 available=3 capacity=20 pending=2"
        )
        self.assertEqual(groups, {2: -1})
        self.assertEqual(available, 3)

    def test_unusable_enrollment_block_fails_before_confirmation_or_approval(self):
        for finger in (5, 10):
            with (
                self.subTest(finger=finger),
                mock.patch.object(cli, "serial_command", return_value=[
                    "OK FINGER LIST groups=5:2,10:1 available=4 capacity=20 pending=0"
                ]) as command,
                mock.patch.object(cli, "ask") as confirm,
                mock.patch.object(cli, "unlock") as unlock,
            ):
                with self.assertRaisesRegex(cli.ToolError, "cannot store all four views"):
                    cli.enroll_finger("TT", {"finger_groups": "1"}, finger, replace=True)
                confirm.assert_not_called()
                unlock.assert_not_called()
                command.assert_called_once_with("TT", "FINGER LIST", timeout=6)

    def test_slot_zero_does_not_make_finger_ten_fit_a_small_sensor(self):
        self.assertEqual(cli.finger_slots(10), (37, 38, 39, 0))
        with self.assertRaisesRegex(cli.ToolError, "cannot store"):
            self.inventory("groups=none available=9 capacity=39 pending=0", required_finger=10)
        self.assertEqual(
            self.inventory("groups=none available=10 capacity=40 pending=0", required_finger=10),
            ({}, 10),
        )

    def test_malformed_inventory_cannot_be_used_for_verification(self):
        invalid = (
            "groups=1:1,1:4 available=9 capacity=40 pending=0",
            "groups=1:0 available=9 capacity=40 pending=0",
            "groups=5:4 available=4 capacity=20 pending=0",
            "groups=none available=10 capacity=20 pending=0",
            "groups=none available=10 capacity=0 pending=0",
            "groups=none available=10 capacity=41 pending=0",
            "groups=none available=10 capacity=40 pending=1",
            "groups=none available=10 pending=0",
            "groups=1:4:2 available=9 capacity=40 pending=0",
        )
        for fields in invalid:
            with self.subTest(fields=fields):
                with self.assertRaisesRegex(cli.ToolError, "invalid fingerprint inventory"):
                    self.inventory(fields)


if __name__ == "__main__":
    unittest.main()

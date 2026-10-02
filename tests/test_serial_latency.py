import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

import serial

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "macos"))
from tinytouch_runtime import SerialFrameDecoder, read_available


class SerialLatencyTests(unittest.TestCase):
    def test_idle_read_waits_for_one_byte_and_bursts_stay_bounded(self):
        connection = mock.Mock()
        for waiting, expected in ((0, 1), (3, 3), (200, 200), (5000, 256)):
            connection.in_waiting = waiting
            read_available(connection)
            connection.read.assert_called_with(expected)

    def test_disconnect_is_not_hidden(self):
        connection = mock.Mock()
        type(connection).in_waiting = mock.PropertyMock(side_effect=serial.SerialException("gone"))
        with self.assertRaises(serial.SerialException):
            read_available(connection)
        connection.read.assert_not_called()

    @unittest.skipUnless(hasattr(os, "openpty"), "requires a POSIX pseudo terminal")
    def test_short_complete_frame_does_not_wait_for_serial_timeout(self):
        master, slave = os.openpty()
        try:
            with serial.Serial(os.ttyname(slave), timeout=1) as connection:
                decoder = SerialFrameDecoder(1024)
                os.write(master, b"PONG 6\r\nEV short complete frame\r\n")
                received = []
                started = time.monotonic()
                while len(received) < 2:
                    received.extend(decoder.feed(read_available(connection)))
                self.assertEqual(received, [b"PONG 6\r", b"EV short complete frame\r"])
                # Generous relative to normal sub-ms delivery, but catches the
                # old read(256) waiting for the full one-second serial timeout.
                self.assertLess(time.monotonic() - started, 0.5)
        finally:
            os.close(master)
            os.close(slave)

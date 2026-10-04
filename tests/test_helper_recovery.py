"""Exercise reconnects with a serial transport and clock owned by each test."""

import hashlib
import os
import select
import sys
import threading
import unittest
from collections import deque
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "macos"))
import tinytouch_helper as helper  # noqa: E402


DEVICE_ID = "TT-001122334455"
PORT = "/dev/cu.test"
KEY = bytes(range(32))
NONCE = "ab" * 16


def event_frame(version=1):
    if version == 1:
        material = f"EV|{NONCE}|1|2|99"
        return f"EV {NONCE} 1 2 99 {helper.mac_hex(KEY, material)}\n".encode()
    key_id = hashlib.sha256(KEY).hexdigest()[:16]
    material = f"EV2|{key_id}|{NONCE}|1|2|99"
    return f"EV2 {NONCE} 1 2 99 {key_id}:{helper.mac_hex(KEY, material)}\n".encode()


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now


class Connection:
    def __init__(self, clock, chunks=(), on_write=None):
        self.clock = clock
        self.chunks = deque(chunks)
        self.on_write = on_write
        self.written = []
        self.closed = False
        self.reads = 0

    @property
    def in_waiting(self):
        return len(self.chunks[0]) if self.chunks else 0

    def read(self, size):
        self.reads += 1
        if self.reads > 100:
            raise AssertionError("The worker did not finish within the test window.")
        self.clock.now += 0.2
        if not self.chunks:
            return b""
        chunk = self.chunks.popleft()
        if len(chunk) > size:
            self.chunks.appendleft(chunk[size:])
        return chunk[:size]

    def write(self, payload):
        self.written.append(payload)
        if self.on_write:
            self.on_write(payload)

    def flush(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed = True


@contextmanager
def serving(connection, *, reattached=True):
    password, key = bytearray(b"test password"), bytearray(KEY)
    with ExitStack() as stack:
        for name, value in (
            ("open_serial", connection),
            ("load_passwords", {0: password}),
            ("pairing_keychain_get", key),
            ("load_settings", {"keyboard_layout": "us"}),
            ("load_state", {"seen_nonces": []}),
            ("device_ports", [PORT]),
        ):
            stack.enter_context(mock.patch.object(helper, name, return_value=value))
        stack.enter_context(mock.patch.object(helper, "REATTACHED_DEVICES", [DEVICE_ID] if reattached else []))
        stack.enter_context(mock.patch.object(helper.time, "monotonic", connection.clock.monotonic))
        stack.enter_context(mock.patch.object(helper, "diagnostic"))
        remember = stack.enter_context(mock.patch.object(helper, "remember_nonce"))
        yield password, key, remember


class StartupRecoveryTests(unittest.TestCase):
    def test_events_in_status_batch_are_delivered_for_both_protocols(self):
        for version in (1, 2):
            frame = event_frame(version)
            for batch in (
                [frame + b"OK STATUS protocol=6\n"],
                [b"OK STATUS protocol=6\n" + frame],
                [b"OK STATUS protocol=6\n" + frame[:17], frame[17:]],
            ):
                with self.subTest(version=version, batch=batch):
                    connection = Connection(Clock(), batch)
                    with serving(connection) as (password, key, remember):
                        helper.serve_port(PORT, once=True, device_id=DEVICE_ID)
                    self.assertEqual(len(connection.written), 2)
                    self.assertTrue(connection.written[1].startswith(b"PW"))
                    remember.assert_called_once()
                    self.assertFalse(any(password))
                    self.assertFalse(any(key))
                    self.assertTrue(connection.closed)

    def test_probe_stops_when_foreground_owner_requests_port(self):
        stopped = threading.Event()
        connection = Connection(Clock(), on_write=lambda _: stopped.set())
        with serving(connection) as (password, key, _):
            helper.serve_port(PORT, stop_event=stopped, device_id=DEVICE_ID)
        self.assertEqual(connection.written, [b"STATUS\n"])
        self.assertEqual(connection.reads, 0)
        self.assertTrue(connection.closed)
        self.assertFalse(any(password))
        self.assertFalse(any(key))

    def test_cancelled_worker_does_not_request_usb_reconnect(self):
        stopped = threading.Event()
        connection = Connection(
            Clock(), [b"OK STATUS protocol=6\n"], on_write=lambda _: stopped.set()
        )
        with serving(connection, reattached=False):
            helper.serve_port(PORT, stop_event=stopped, device_id=DEVICE_ID)
        self.assertEqual(connection.written, [b"STATUS\n"])

    def test_status_timeout_closes_transport_and_wipes_secrets(self):
        connection = Connection(Clock(), [b"PONG 6\n"])
        with serving(connection) as (password, key, remember):
            with self.assertRaises(helper.serial.SerialException):
                helper.serve_port(PORT, device_id=DEVICE_ID)
        remember.assert_not_called()
        self.assertTrue(connection.closed)
        self.assertFalse(any(password))
        self.assertFalse(any(key))

    def test_probe_keeps_oversized_frame_quarantine(self):
        connection = Connection(Clock(), [b"OK STATUS protocol=6\n12345"])
        decoder = helper.SerialFrameDecoder(4)
        # The STATUS line must fit before reducing the limit for its trailing data.
        decoder.maximum = helper.MAX_SERIAL_LINE_BYTES
        connection.chunks = deque([b"OK STATUS protocol=6\n" + b"x" * 2049])
        with mock.patch.object(helper.time, "monotonic", connection.clock.monotonic):
            self.assertEqual(
                helper.require_startup_status(connection, DEVICE_ID, PORT, decoder=decoder), []
            )
        # Finish the oversized frame in the same decoder used by the worker.
        for chunk in connection.chunks:
            decoder.feed(chunk)
        self.assertEqual(decoder.feed(event_frame() + event_frame()), [event_frame().rstrip(b"\n")])


class WatchdogRecoveryTests(unittest.TestCase):
    def test_noise_does_not_acknowledge_heartbeat_or_prevent_recovery(self):
        for noise in (b"boot diagnostic\n", b"x", b"\xff\n", b"PONG broken\n"):
            with self.subTest(noise=noise):
                connection = Connection(Clock(), [b"OK STATUS protocol=6\n"])
                original_read = connection.read

                def read(size):
                    if not connection.chunks:
                        connection.chunks.append(noise)
                    return original_read(size)

                connection.read = read
                with serving(connection) as (password, key, remember):
                    with self.assertRaises(helper.serial.SerialException):
                        helper.serve_port(PORT, device_id=DEVICE_ID)
                self.assertIn(b"PING\n", connection.written)
                self.assertLess(connection.clock.now, 8)
                self.assertTrue(connection.closed)
                remember.assert_not_called()
                self.assertFalse(any(password))
                self.assertFalse(any(key))

    def test_firmware_pong_formats_keep_idle_transport_alive(self):
        for pong in (b"PONG\n", b"PONG 6\n"):
            with self.subTest(pong=pong):
                stopped = threading.Event()
                connection = Connection(Clock(), [b"OK STATUS protocol=6\n"])

                def write(payload):
                    if payload == b"PING\n":
                        connection.chunks.extend([b"P", pong[1:]])

                connection.on_write = write
                original_read = connection.read

                def read(size):
                    if connection.clock.now >= 12:
                        stopped.set()
                    return original_read(size)

                connection.read = read
                with serving(connection):
                    helper.serve_port(PORT, stop_event=stopped, device_id=DEVICE_ID)
                self.assertGreaterEqual(connection.written.count(b"PING\n"), 2)
                self.assertTrue(connection.closed)

    def test_disappeared_port_is_checked_even_while_bytes_arrive(self):
        connection = Connection(Clock(), [b"OK STATUS protocol=6\n"])
        original_read = connection.read

        def read(size):
            if not connection.chunks:
                connection.chunks.append(b"diagnostic\n")
            return original_read(size)

        connection.read = read
        with serving(connection), mock.patch.object(helper, "device_ports", return_value=[]):
            with self.assertRaises(helper.serial.SerialException):
                helper.serve_port(PORT, device_id=DEVICE_ID)
        self.assertLess(connection.clock.now, 2)

    def test_serial_writes_do_not_wait_in_unbounded_driver_drain(self):
        connection = Connection(Clock(), [b"OK STATUS protocol=6\n", event_frame()])
        connection.flush = mock.Mock(side_effect=AssertionError("tcdrain can block after unplug"))
        with serving(connection):
            helper.serve_port(PORT, once=True, device_id=DEVICE_ID)
        self.assertEqual(len(connection.written), 2)
        connection.flush.assert_not_called()

    def test_password_write_failure_does_not_consume_nonce(self):
        connection = Connection(Clock(), [b"OK STATUS protocol=6\n", event_frame()])

        def write(payload):
            if payload.startswith(b"PW "):
                raise helper.serial.SerialTimeoutException("write deadline")

        connection.on_write = write
        with serving(connection) as (password, key, remember):
            with self.assertRaises(helper.serial.SerialTimeoutException):
                helper.serve_port(PORT, once=True, device_id=DEVICE_ID)
        remember.assert_not_called()
        self.assertTrue(connection.closed)
        self.assertFalse(any(password))
        self.assertFalse(any(key))


class PseudoTerminalRecoveryTests(unittest.TestCase):
    @unittest.skipUnless(hasattr(os, "openpty"), "requires a POSIX pseudo terminal")
    def test_real_transport_delivers_event_sent_with_startup_status(self):
        master, slave = os.openpty()
        port = os.ttyname(slave)
        done = threading.Event()
        replies = []
        device_errors = []

        def device():
            buffered = b""
            try:
                while not done.is_set():
                    readable, _, _ = select.select([master], [], [], 0.1)
                    if not readable:
                        continue
                    buffered += os.read(master, 1024)
                    while b"\n" in buffered:
                        line, buffered = buffered.split(b"\n", 1)
                        if line == b"STATUS":
                            os.write(master, b"OK STATUS protocol=6\n" + event_frame(2))
                        elif line.startswith(b"PW2 "):
                            replies.append(line.decode("ascii"))
                            done.set()
                        elif line == b"PING":
                            os.write(master, b"PONG 6\n")
            except Exception as exc:
                device_errors.append(exc)

        thread = threading.Thread(target=device)
        password, key = bytearray(b"test password"), bytearray(KEY)
        try:
            with helper.serial.Serial(port, timeout=0.05, write_timeout=0.2) as connection:
                thread.start()
                with ExitStack() as stack:
                    for name, value in (
                        ("open_serial", connection),
                        ("load_passwords", {0: password}),
                        ("pairing_keychain_get", key),
                        ("load_settings", {"keyboard_layout": "us"}),
                        ("load_state", {"seen_nonces": []}),
                        ("device_ports", [port]),
                    ):
                        stack.enter_context(mock.patch.object(helper, name, return_value=value))
                    stack.enter_context(mock.patch.object(helper, "REATTACHED_DEVICES", [DEVICE_ID]))
                    remember = stack.enter_context(mock.patch.object(helper, "remember_nonce"))
                    stack.enter_context(mock.patch.object(helper, "diagnostic"))
                    helper.serve_port(port, once=True, device_id=DEVICE_ID)
                    remember.assert_called_once()
                self.assertTrue(done.wait(1), "The device did not receive the encrypted reply.")
            self.assertEqual(device_errors, [])
            parts = replies[0].split()
            self.assertEqual(parts[:3], ["PW2", hashlib.sha256(KEY).hexdigest()[:16], NONCE])
            self.assertEqual(
                helper.aes_ctr_crypt(helper.session_key(KEY, NONCE), bytes.fromhex(parts[3]), bytes.fromhex(parts[4])),
                b"test password",
            )
            self.assertFalse(any(password))
            self.assertFalse(any(key))
        finally:
            done.set()
            if thread.ident is not None:
                thread.join(1)
            os.close(master)
            os.close(slave)


if __name__ == "__main__":
    unittest.main()

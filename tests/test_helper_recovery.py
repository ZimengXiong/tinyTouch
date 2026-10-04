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


def event_frame(version=1, nonce=NONCE):
    if version == 1:
        material = f"EV|{nonce}|1|2|99"
        return f"EV {nonce} 1 2 99 {helper.mac_hex(KEY, material)}\n".encode()
    key_id = hashlib.sha256(KEY).hexdigest()[:16]
    material = f"EV2|{key_id}|{nonce}|1|2|99"
    return f"EV2 {nonce} 1 2 99 {key_id}:{helper.mac_hex(KEY, material)}\n".encode()


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
        if self.reads > 200:
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
        connection = Connection(Clock(), [b"OK STATUS protocol=6\n" + b"x" * 2049])
        decoder = helper.SerialFrameDecoder(helper.MAX_SERIAL_LINE_BYTES)
        with mock.patch.object(helper.time, "monotonic", connection.clock.monotonic):
            self.assertEqual(
                helper.require_startup_status(connection, DEVICE_ID, PORT, decoder=decoder), []
            )
        # Finish the oversized frame in the same decoder used by the worker.
        for chunk in connection.chunks:
            decoder.feed(chunk)
        self.assertEqual(decoder.feed(event_frame() + event_frame()), [event_frame().rstrip(b"\n")])

    def test_slow_sensor_status_is_allowed_to_finish_without_reopening(self):
        connection = Connection(Clock(), [b""] * 60 + [b"OK STATUS sensor=ready\n", event_frame()])
        with serving(connection):
            helper.serve_port(PORT, once=True, device_id=DEVICE_ID)
        self.assertGreater(connection.clock.now, 12)
        self.assertEqual(connection.written[0], b"STATUS\n")
        self.assertTrue(connection.written[1].startswith(b"PW "))


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


class FrameExpiryRecoveryTests(unittest.TestCase):
    def test_oversized_frame_stays_quarantined_across_idle_expiry(self):
        fresh_nonce = "cd" * 16
        connection = Connection(Clock(), [
            b"OK STATUS protocol=6\n", b"x" * 2049,
            *([b""] * 7), event_frame(), event_frame(nonce=fresh_nonce),
        ])
        with serving(connection) as (_, _, remember):
            helper.serve_port(PORT, once=True, device_id=DEVICE_ID)
        self.assertEqual(connection.written[1].split()[1].decode(), fresh_nonce)
        self.assertEqual(remember.call_args.args[1], fresh_nonce)

    def test_absolute_frame_expiry_rejects_slow_trickle_and_recovers_next_frame(self):
        fresh_nonce = "cd" * 16
        frame = event_frame()
        connection = Connection(Clock(), [
            b"OK STATUS protocol=6\n",
            *[frame[index:index + 10] for index in range(0, len(frame), 10)],
            event_frame(nonce=fresh_nonce),
        ])
        with serving(connection):
            helper.serve_port(PORT, once=True, device_id=DEVICE_ID)
        self.assertEqual(connection.written[1].split()[1].decode(), fresh_nonce)

    def test_unknown_event_version_is_diagnosed_and_following_event_is_delivered(self):
        unknown = event_frame().replace(b"EV ", b"EV3 ")
        for chunks in (
            [b"OK STATUS protocol=6\n", unknown, event_frame()],
            [unknown + b"OK STATUS protocol=6\n" + event_frame()],
        ):
            with self.subTest(chunks=chunks):
                connection = Connection(Clock(), chunks)
                with serving(connection), mock.patch.object(helper, "diagnostic") as log:
                    helper.serve_port(PORT, once=True, device_id=DEVICE_ID)
                self.assertEqual(len(connection.written), 2)
                log.assert_any_call(
                    "protocol.frame_rejected", level="warning", device_id=DEVICE_ID,
                    reason="unsupported_event_version", version="EV3",
                )

    def test_partial_prefix_expiry_does_not_drop_next_intact_event(self):
        connection = Connection(Clock(), [
            b"OK STATUS protocol=6\nEV partial", *([b""] * 7), event_frame(2),
        ])
        with serving(connection):
            helper.serve_port(PORT, once=True, device_id=DEVICE_ID)
        self.assertTrue(connection.written[1].startswith(b"PW2 "))

    def test_short_fragments_still_deliver_before_frame_deadline(self):
        frame = event_frame(2)
        connection = Connection(Clock(), [
            b"OK STATUS protocol=6\n", frame[:40], frame[40:80], frame[80:],
        ])
        with serving(connection):
            helper.serve_port(PORT, once=True, device_id=DEVICE_ID)
        self.assertTrue(connection.written[1].startswith(b"PW2 "))


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


class EndManager(Exception):
    """Stop a deterministic manager scenario after its final scan."""


class ManagedWorker:
    def __init__(self, endpoint, *, finish_on_stop=True):
        self.endpoint = endpoint
        self.error = None
        self.started_at = 0.0
        self.planned_stop = False
        self.alive = True
        self.finish_on_stop = finish_on_stop
        self.thread = mock.Mock()
        self.thread.is_alive.side_effect = lambda: self.alive

    def start(self):
        pass

    def stop(self):
        self.planned_stop = True
        if self.finish_on_stop:
            self.alive = False


class ManagerRecoveryTests(unittest.TestCase):
    @contextmanager
    def manager(self, endpoints):
        clock = Clock()
        workers = []

        def create(endpoint):
            worker = ManagedWorker(endpoint)
            workers.append(worker)
            return worker

        def sleep(delay):
            clock.now += delay

        with (
            mock.patch.object(helper, "Worker", side_effect=create),
            mock.patch.object(helper, "device_endpoints", side_effect=endpoints),
            mock.patch.object(helper.LeaseObserver, "active", return_value=None),
            mock.patch.object(helper, "credentials_exist", return_value=True),
            mock.patch.object(helper.time, "monotonic", clock.monotonic),
            mock.patch.object(helper.time, "sleep", side_effect=sleep),
            mock.patch.object(helper, "diagnostic"),
        ):
            yield workers

    def test_port_or_location_change_replaces_worker_for_same_identity(self):
        old = helper.DeviceEndpoint(DEVICE_ID, PORT, "1-1")
        for new in (
            helper.DeviceEndpoint(DEVICE_ID, "/dev/cu.renumbered", "1-1"),
            helper.DeviceEndpoint(DEVICE_ID, PORT, "2-3"),
        ):
            with self.subTest(new=new), self.manager([[old], [new], EndManager()]) as workers:
                with self.assertRaises(EndManager):
                    helper._manage_workers({})
            self.assertEqual([worker.endpoint for worker in workers], [old, new])
            self.assertTrue(workers[0].planned_stop)
            workers[0].thread.join.assert_called_once()

    def test_path_change_does_not_inherit_failed_path_backoff(self):
        old = helper.DeviceEndpoint(DEVICE_ID, PORT, "1-1")
        new = helper.DeviceEndpoint(DEVICE_ID, "/dev/cu.renumbered", "2-3")
        failed = ManagedWorker(old)
        failed.alive = False
        failed.error = OSError("disconnected")
        with self.manager([[old], [new], EndManager()]) as workers:
            with self.assertRaises(EndManager):
                helper._manage_workers({DEVICE_ID: failed})
        self.assertEqual([worker.endpoint for worker in workers], [new])

    def test_suspend_acknowledges_only_after_worker_exits_and_stays_quiet(self):
        endpoint = helper.DeviceEndpoint(DEVICE_ID, PORT, "1-1")
        worker = ManagedWorker(endpoint, finish_on_stop=False)
        lease = mock.Mock(nonce="a" * 32, pid=123)
        calls = 0

        def active():
            nonlocal calls
            calls += 1
            if calls == 2:
                worker.alive = False
            if calls == 5:
                raise EndManager()
            return lease

        def acknowledge(record):
            self.assertIs(record, lease)
            self.assertFalse(worker.alive)
            worker.thread.join.assert_called_once()

        with (
            mock.patch.object(helper.LeaseObserver, "active", side_effect=active),
            mock.patch.object(helper.LeaseObserver, "acknowledge", side_effect=acknowledge) as ack,
            mock.patch.object(helper, "device_endpoints") as discovery,
            mock.patch.object(helper.time, "sleep"),
            mock.patch.object(helper, "diagnostic") as log,
        ):
            with self.assertRaises(EndManager):
                helper._manage_workers({DEVICE_ID: worker})
        ack.assert_called_once_with(lease)
        discovery.assert_not_called()
        transitions = [call.kwargs["current"] for call in log.call_args_list if call.args[0] == "manager.transition"]
        self.assertEqual(transitions, ["running", "draining", "suspended"])

    def test_foreground_repair_retries_credentials_as_soon_as_lease_ends(self):
        endpoint = helper.DeviceEndpoint(DEVICE_ID, PORT, "1-1")
        lease = mock.Mock(nonce="a" * 32, pid=123)
        with self.manager([[endpoint], [endpoint], EndManager()]) as workers:
            with (
                mock.patch.object(helper.LeaseObserver, "active", side_effect=[None, lease, None, None]),
                mock.patch.object(helper.LeaseObserver, "acknowledge"),
                mock.patch.object(helper, "credentials_exist", side_effect=[False, True]) as credentials,
            ):
                with self.assertRaises(EndManager):
                    helper._manage_workers({})
        self.assertEqual(credentials.call_count, 2)
        self.assertEqual([worker.endpoint for worker in workers], [endpoint])

    def test_missing_credentials_are_rechecked_without_process_restart(self):
        endpoint = helper.DeviceEndpoint(DEVICE_ID, PORT, "1-1")
        scans = [[endpoint]] * 8 + [EndManager()]
        with self.manager(scans) as workers:
            with mock.patch.object(helper, "credentials_exist", side_effect=[False, True]) as credentials:
                with self.assertRaises(EndManager):
                    helper._manage_workers({})
        self.assertEqual(credentials.call_count, 2)
        self.assertEqual([worker.endpoint for worker in workers], [endpoint])

    def test_replacement_waits_for_old_worker_to_release_transport(self):
        old = helper.DeviceEndpoint(DEVICE_ID, PORT, "1-1")
        new = helper.DeviceEndpoint(DEVICE_ID, "/dev/cu.renumbered", "2-3")
        worker = ManagedWorker(old, finish_on_stop=False)
        scans = 0
        with self.manager([]) as replacements:
            def discover():
                nonlocal scans
                scans += 1
                if scans == 2:
                    self.assertTrue(worker.planned_stop)
                    self.assertEqual(replacements, [])
                    worker.alive = False
                if scans == 3:
                    raise EndManager()
                return [new]

            with mock.patch.object(helper, "device_endpoints", side_effect=discover):
                with self.assertRaises(EndManager):
                    helper._manage_workers({DEVICE_ID: worker})
        worker.thread.join.assert_called_once()
        self.assertEqual([item.endpoint for item in replacements], [new])


if __name__ == "__main__":
    unittest.main()

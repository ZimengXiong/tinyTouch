import importlib.util
import hashlib
import tempfile
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "tinytouch_helper", ROOT / "software" / "macos-helper" / "tinytouch_helper.py"
)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


class SerialFramingTests(unittest.TestCase):
    def test_event_glued_behind_a_truncated_one_is_recovered(self):
        nonce = "c51a6405ed821b8fe1b574ee20c6d05f"
        intact = f"EV {nonce} 5 1 1 deadbeef"
        glued = f"EV d5327a6c756644e27{intact}"
        self.assertEqual(helper.resynchronize_event(glued), intact)

    def test_resynchronize_leaves_clean_lines_alone(self):
        for line in ("EV aabb 1 1 1 ccdd", "PONG", "OK STATUS firmware=unified", ""):
            self.assertEqual(helper.resynchronize_event(line), line)

    def test_event_v2_glued_behind_truncated_data_is_recovered(self):
        intact = f"EV2 {'ab' * 16} 5 1 1 deadbeefdeadbeef:{'cd' * 32}"
        self.assertEqual(helper.resynchronize_event(f"EV partial{intact}"), intact)


class WorkerStateMachineTests(unittest.TestCase):
    def test_worker_failure_has_explicit_terminal_phase(self):
        endpoint = helper.DeviceEndpoint("TT-001122334455", "/dev/cu.example", "1-1")
        with mock.patch.object(helper, "serve_port", side_effect=OSError("injected")):
            worker = helper.Worker(endpoint)
            self.assertEqual(worker.phase, helper.WorkerPhase.CREATED)
            worker.start()
            worker.thread.join()
        self.assertEqual(worker.phase, helper.WorkerPhase.FAILED)
        self.assertIsInstance(worker.error, OSError)

    def test_worker_drain_has_explicit_phase(self):
        endpoint = helper.DeviceEndpoint("TT-001122334455", "/dev/cu.example", "1-1")
        worker = helper.Worker(endpoint)
        worker.stop()
        self.assertEqual(worker.phase, helper.WorkerPhase.DRAINING)
        self.assertTrue(worker.stop_event.is_set())

    def test_worker_start_records_stability_window_origin(self):
        endpoint = helper.DeviceEndpoint("TT-001122334455", "/dev/cu.example", "1-1")
        with (
            mock.patch.object(helper, "serve_port"),
            mock.patch.object(helper.time, "monotonic", return_value=123.0),
        ):
            worker = helper.Worker(endpoint)
            worker.start()
            worker.thread.join()
        self.assertEqual(worker.started_at, 123.0)
        self.assertEqual(worker.phase, helper.WorkerPhase.STOPPED)

    def test_worker_carries_stable_identity_across_port_churn(self):
        endpoint = helper.DeviceEndpoint("TT-001122334455", "/dev/cu.renumbered", "1-1")
        with mock.patch.object(helper, "serve_port") as serve_port:
            worker = helper.Worker(endpoint)
            worker.start()
            worker.thread.join()
        serve_port.assert_called_once_with(
            "/dev/cu.renumbered",
            stop_event=worker.stop_event,
            device_id="TT-001122334455",
        )


class HelperProtocolTests(unittest.TestCase):
    @staticmethod
    def decrypt_response(key, nonce, response):
        parts = response.split()
        offset = 1 if parts[0] == "PW" else 2
        iv_hex, ciphertext_hex = parts[offset + 1], parts[offset + 2]
        return helper.aes_ctr_crypt(
            helper.session_key(key, nonce), bytes.fromhex(iv_hex),
            bytes.fromhex(ciphertext_hex),
        )

    def test_authenticated_event_returns_decryptable_password(self):
        key = bytes(range(32))
        password = b"correct horse battery staple!"
        nonce = "01" * 16
        event_mac = helper.mac_hex(key, f"EV|{nonce}|7|1|1")
        response = helper.handle_event(
            f"EV {nonce} 7 1 1 {event_mac}",
            password,
            key,
            {"seen_nonces": []},
            persist_state=False,
        )
        self.assertIsNotNone(response)
        kind, got_nonce, iv_hex, ciphertext_hex, response_mac = response.split()
        self.assertEqual((kind, got_nonce), ("PW", nonce))
        self.assertEqual(
            response_mac,
            helper.mac_hex(key, f"PW|{nonce}|{iv_hex}|{ciphertext_hex}"),
        )
        plaintext = helper.aes_ctr_crypt(
            helper.session_key(key, nonce), bytes.fromhex(iv_hex), bytes.fromhex(ciphertext_hex)
        )
        self.assertEqual(plaintext, password)

    def test_wipeable_secret_buffers_are_supported(self):
        key = bytearray(range(32))
        password = bytearray(b"wipe me")
        nonce = "0d" * 16
        event_mac = helper.mac_hex(key, f"EV|{nonce}|1|1|1")
        response = helper.handle_event(
            f"EV {nonce} 1 1 1 {event_mac}",
            password,
            key,
            {"seen_nonces": []},
            persist_state=False,
        )
        self.assertEqual(self.decrypt_response(key, nonce, response), password)

    def test_commoncrypto_matches_nist_aes_256_ctr_vector(self):
        key = bytes.fromhex(
            "603deb1015ca71be2b73aef0857d7781"
            "1f352c073b6108d72d9810a30914dff4"
        )
        iv = bytes.fromhex("f0f1f2f3f4f5f6f7f8f9fafbfcfdfeff")
        plaintext = bytes.fromhex("6bc1bee22e409f96e93d7e117393172a")
        expected = bytes.fromhex("601ec313775789a5b7a7f504bbf3d228")
        self.assertEqual(helper.aes_ctr_crypt(key, iv, plaintext), expected)

    def test_replayed_nonce_is_rejected(self):
        key = bytes(range(32))
        nonce = "02" * 16
        event_mac = helper.mac_hex(key, f"EV|{nonce}|1|1|1")
        state = {"seen_nonces": [nonce]}
        response = helper.handle_event(
            f"EV {nonce} 1 1 1 {event_mac}",
            b"password",
            key,
            state,
            persist_state=False,
        )
        self.assertIsNone(response)

    def test_nonce_replay_is_case_insensitive(self):
        key = bytes(range(32))
        nonce = "AB" * 16
        event_mac = helper.mac_hex(key, f"EV|{nonce}|1|1|1")
        response = helper.handle_event(
            f"EV {nonce} 1 1 1 {event_mac}",
            b"password",
            key,
            {"seen_nonces": [nonce.lower()]},
            persist_state=False,
        )
        self.assertIsNone(response)

    def test_nonce_can_be_recorded_after_serial_delivery(self):
        key = bytes(range(32))
        nonce = "0c" * 16
        state = {"seen_nonces": []}
        event_mac = helper.mac_hex(key, f"EV|{nonce}|1|1|1")
        response = helper.handle_event(
            f"EV {nonce} 1 1 1 {event_mac}",
            b"password",
            key,
            state,
            persist_state=False,
            record_nonce=False,
        )
        self.assertIsNotNone(response)
        self.assertEqual(state["seen_nonces"], [])
        with mock.patch.object(helper, "save_state") as save_state:
            helper.remember_nonce(state, nonce, "DEVICE")
        self.assertEqual(state["seen_nonces"], [nonce])
        save_state.assert_called_once_with(state, "DEVICE")

    def test_v2_event_selects_this_computers_independent_key(self):
        key = bytes(range(32))
        password = b"a different Mac password"
        nonce = "03" * 16
        key_id = hashlib.sha256(key).hexdigest()[:16]
        event_mac = helper.mac_hex(key, f"EV2|{key_id}|{nonce}|8|1|77")
        response = helper.handle_event(
            f"EV2 {nonce} 8 1 77 deadbeefdeadbeef:{'00' * 32} {key_id}:{event_mac}",
            password,
            key,
            {"seen_nonces": []},
            persist_state=False,
        )
        self.assertIsNotNone(response)
        kind, got_id, got_nonce, iv_hex, ciphertext_hex, response_mac = response.split()
        self.assertEqual((kind, got_id, got_nonce), ("PW2", key_id, nonce))
        self.assertEqual(
            response_mac,
            helper.mac_hex(key, f"PW2|{key_id}|{nonce}|{iv_hex}|{ciphertext_hex}"),
        )
        plaintext = helper.aes_ctr_crypt(
            helper.session_key(key, nonce), bytes.fromhex(iv_hex), bytes.fromhex(ciphertext_hex)
        )
        self.assertEqual(plaintext, password)

    def test_v2_event_for_another_computer_is_ignored(self):
        response = helper.handle_event(
            f"EV2 {'04' * 16} 1 1 1 deadbeefdeadbeef:{'00' * 32}",
            b"password",
            bytes(range(32)),
            {"seen_nonces": []},
            persist_state=False,
        )
        self.assertIsNone(response)

    def test_parser_rejects_duplicate_or_excess_authenticators(self):
        key = bytes(range(32))
        nonce = "05" * 16
        key_id = hashlib.sha256(key).hexdigest()[:16]
        event_mac = helper.mac_hex(key, f"EV2|{key_id}|{nonce}|1|1|1")
        authenticator = f"{key_id}:{event_mac}"
        self.assertIsNone(helper.parse_event(
            f"EV2 {nonce} 1 1 1 {authenticator} {authenticator}", key
        ))
        extras = " ".join(f"{index:016x}:{'00' * 32}" for index in range(9))
        self.assertIsNone(helper.parse_event(f"EV2 {nonce} 1 1 1 {extras}", key))

    def test_parser_rejects_noncanonical_numbers_and_ranges(self):
        key = bytes(range(32))
        nonce = "06" * 16
        for counter, slot, score in (
            ("-1", "1", "1"),
            (str(1 << 64), "1", "1"),
            ("1", "0", "1"),
            ("1", "1", str(1 << 31)),
        ):
            self.assertIsNone(
                helper.parse_event(f"EV {nonce} {counter} {slot} {score} {'00' * 32}", key)
            )

    def test_fingerprint_slot_selects_override_and_falls_back_to_default(self):
        key = bytes(range(32))
        passwords = {0: b"default", 5: b"fifth finger"}
        for slot, expected in ((5, b"fifth finger"), (2, b"default")):
            nonce = f"{slot:02x}" * 16
            event_mac = helper.mac_hex(key, f"EV|{nonce}|1|{slot}|42")
            response = helper.handle_event(
                f"EV {nonce} 1 {slot} 42 {event_mac}", passwords, key,
                {"seen_nonces": []}, persist_state=False,
            )
            self.assertEqual(self.decrypt_response(key, nonce, response), expected)

    def test_layout_translation_happens_before_encryption(self):
        key = bytes(range(32))
        nonce = "0a" * 16
        event_mac = helper.mac_hex(key, f"EV|{nonce}|1|1|42")
        response = helper.handle_event(
            f"EV {nonce} 1 1 42 {event_mac}", b";", key,
            {"seen_nonces": []}, persist_state=False,
            keyboard_map={";": "<"},
        )
        self.assertEqual(self.decrypt_response(key, nonce, response), b"<")

    def test_unsupported_layout_character_refuses_complete_event(self):
        key = bytes(range(32))
        nonce = "0b" * 16
        event_mac = helper.mac_hex(key, f"EV|{nonce}|1|1|42")
        state = {"seen_nonces": []}
        response = helper.handle_event(
            f"EV {nonce} 1 1 42 {event_mac}", "é".encode(), key, state,
            persist_state=False, keyboard_map={"e": "e"},
        )
        self.assertIsNone(response)
        self.assertEqual(state["seen_nonces"], [])

    def test_serial_framing_preserves_split_and_multiple_events(self):
        lines, remainder = helper.split_serial_lines(b"EV2 abc", b" def\nPONG\nEV x")
        self.assertEqual(lines, [b"EV2 abc def", b"PONG"])
        self.assertEqual(remainder, b"EV x")
        lines, remainder = helper.split_serial_lines(remainder, b" y\n")
        self.assertEqual(lines, [b"EV x y"])
        self.assertEqual(remainder, b"")

    def test_serial_framing_drops_oversized_incomplete_line(self):
        lines, remainder = helper.split_serial_lines(
            b"", b"x" * (helper.MAX_SERIAL_LINE_BYTES + 1)
        )
        self.assertEqual(lines, [])
        self.assertEqual(remainder, b"")

    def test_oversized_password_is_refused(self):
        with self.assertRaises(ValueError):
            helper.translate_password(b"x" * 161, None)

    def test_stale_cli_suspension_is_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            suspend = Path(directory) / "suspend"
            acknowledgement = Path(directory) / "ack"
            suspend.write_text("999999\n")
            acknowledgement.write_text("old\n")
            with (
                mock.patch.object(helper, "SUSPEND_PATH", suspend),
                mock.patch.object(helper, "SUSPEND_ACK_PATH", acknowledgement),
                mock.patch.object(helper.os, "kill", side_effect=ProcessLookupError),
            ):
                helper.wait_for_cli_suspension()
            self.assertFalse(suspend.exists())
            self.assertFalse(acknowledgement.exists())


if __name__ == "__main__":
    unittest.main()

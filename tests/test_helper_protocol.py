import importlib.util
import hashlib
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


if __name__ == "__main__":
    unittest.main()

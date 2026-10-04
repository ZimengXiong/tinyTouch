"""Check layout translation and credential diagnostics without native changes."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "password_mapping_helper", ROOT / "macos/tinytouch_helper.py"
)
helper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helper)


class PasswordMappingTests(unittest.TestCase):
    def tearDown(self):
        helper._keyboard_output_map.cache_clear()

    def test_dead_key_character_requires_a_completed_sequence(self):
        hitoolbox = mock.Mock()

        def translate(layout, key, action, modifiers, keyboard_type, options,
                      state, capacity, actual, chars):
            actual._obj.value = 0
            if state._obj.value == 1:
                state._obj.value = 0
                if key in (helper._MAC_KEYCODES[" "], helper._MAC_KEYCODES["e"]):
                    actual._obj.value = 1
                    chars[0] = ord("^" if key == helper._MAC_KEYCODES[" "]
                                   else "Ê" if modifiers else "ê")
                return 0
            if key == helper._MAC_KEYCODES["["] and not modifiers:
                if options & 1:
                    actual._obj.value = 1
                    chars[0] = ord("^")
                else:
                    state._obj.value = 1
            elif key in (helper._MAC_KEYCODES[" "], helper._MAC_KEYCODES["e"]):
                actual._obj.value = 1
                chars[0] = ord(" " if key == helper._MAC_KEYCODES[" "]
                               else "E" if modifiers else "e")
            return 0

        hitoolbox.UCKeyTranslate.side_effect = translate
        with mock.patch.object(
            helper, "_keyboard_layout_libraries", return_value=(hitoolbox, None, None)
        ):
            mapping = helper._keyboard_output_map(b"dead-key-layout")
        self.assertEqual(mapping["^"], "[ ")
        self.assertEqual(mapping["ê"], "[e")
        self.assertEqual(helper.translate_password("^ê".encode(), mapping), b"[ [e")

    def test_composed_password_limit_counts_typed_keys(self):
        with self.assertRaises(ValueError):
            helper.translate_password("ê".encode() * 81, {"ê": "[e"})

    def test_us_layout_rejects_control_characters(self):
        for password in (b"abc\x00def", b"abc\n", b"\t", b"\x7f"):
            with self.subTest(password=password), self.assertRaises(ValueError):
                helper.translate_password(password, None)

    def test_unrepresentable_password_does_not_expose_characters_in_logs(self):
        key = bytes(range(32))
        nonce = "ab" * 16
        signature = helper.mac_hex(key, f"EV|{nonce}|1|1|42")
        state = {"seen_nonces": []}
        with mock.patch.object(helper, "diagnostic") as diagnostic:
            response = helper.handle_event(
                f"EV {nonce} 1 1 42 {signature}", "é".encode(), key, state,
                persist_state=False, keyboard_map={"e": "e"},
            )
        self.assertIsNone(response)
        self.assertEqual(state["seen_nonces"], [])
        self.assertNotIn("é", repr(diagnostic.call_args_list))
        self.assertEqual(
            diagnostic.call_args.kwargs["reason"], "keyboard_layout_unrepresentable"
        )

    def test_valid_json_with_invalid_settings_shape_uses_auto(self):
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(helper, "STATE_DIR", Path(directory)):
                path = helper.settings_path("TT-123456ABCDEF")
                for value in ([], None, 4, {"keyboard_layout": []}):
                    with self.subTest(value=value):
                        path.write_text(json.dumps(value))
                        self.assertEqual(
                            helper.load_settings("TT-123456ABCDEF"),
                            {"keyboard_layout": "auto"},
                        )


if __name__ == "__main__":
    unittest.main()

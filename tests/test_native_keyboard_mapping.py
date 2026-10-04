"""Read built-in macOS layout fixtures without selecting or typing into them."""

import ctypes
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "native_keyboard_helper", ROOT / "macos/tinytouch_helper.py"
)
helper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helper)


class NativeKeyboardMappingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        hitoolbox, foundation, layout_key = helper._keyboard_layout_libraries()
        pointer = ctypes.c_void_p
        hitoolbox.TISCreateInputSourceList.argtypes = [pointer, ctypes.c_bool]
        hitoolbox.TISCreateInputSourceList.restype = pointer
        foundation.CFArrayGetCount.argtypes = [pointer]
        foundation.CFArrayGetCount.restype = ctypes.c_ssize_t
        foundation.CFArrayGetValueAtIndex.argtypes = [pointer, ctypes.c_ssize_t]
        foundation.CFArrayGetValueAtIndex.restype = pointer
        foundation.CFStringGetCString.argtypes = [
            pointer, pointer, ctypes.c_ssize_t, ctypes.c_uint32,
        ]
        foundation.CFStringGetCString.restype = ctypes.c_bool
        source_key = pointer.in_dll(hitoolbox, "kTISPropertyInputSourceID")
        sources = hitoolbox.TISCreateInputSourceList(None, True)
        cls.layouts = {}
        if not sources:
            raise unittest.SkipTest("macOS layout fixtures are unavailable")
        try:
            for index in range(foundation.CFArrayGetCount(sources)):
                source = foundation.CFArrayGetValueAtIndex(sources, index)
                source_id = hitoolbox.TISGetInputSourceProperty(source, source_key)
                label = ctypes.create_string_buffer(512)
                if not foundation.CFStringGetCString(source_id, label, len(label), 0x08000100):
                    continue
                name = label.value.decode()
                if name not in {
                    "com.apple.keylayout.US", "com.apple.keylayout.French",
                    "com.apple.keylayout.Croatian",
                }:
                    continue
                data = hitoolbox.TISGetInputSourceProperty(source, layout_key)
                if data:
                    raw = ctypes.string_at(
                        foundation.CFDataGetBytePtr(data), foundation.CFDataGetLength(data)
                    )
                    cls.layouts[name] = helper._keyboard_output_map(raw)
        finally:
            foundation.CFRelease(sources)

    def layout(self, name):
        key = "com.apple.keylayout." + name
        if key not in self.layouts:
            self.skipTest(f"The built-in {name} layout is unavailable")
        return self.layouts[key]

    def test_us_printable_ascii_uses_the_same_wire_characters(self):
        password = bytes(range(32, 127))
        self.assertEqual(helper.translate_password(password, self.layout("US")), password)

    def test_croatian_semicolon_uses_shift_comma(self):
        self.assertEqual(helper.translate_password(b";", self.layout("Croatian")), b"<")

    def test_french_accents_complete_before_the_next_character(self):
        mapping = self.layout("French")
        self.assertEqual(helper.translate_password("^êa".encode(), mapping), b"[ [eq")


if __name__ == "__main__":
    unittest.main()

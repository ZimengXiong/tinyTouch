#!/usr/bin/env python3
# Needed for the `X | None` annotations below on Python 3.9, which is still the stock
# interpreter on current macOS and is what tinytouch builds its venv from.
from __future__ import annotations

import argparse
import ctypes
from enum import Enum
import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import NamedTuple

import serial
import serial.tools.list_ports
try:
    from tinytouch_keychain import (
        KeychainError, get_password_bytes, has_password, set_background_mode, set_password,
    )
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from tinytouch_keychain import (
        KeychainError, get_password_bytes, has_password, set_background_mode, set_password,
    )
from tinytouch_runtime import (
    BackoffPolicy,
    LeaseObserver,
    SerialFrameDecoder,
    atomic_write_json,
    diagnostic,
)


SERVICE = "tinyTouch"
ACCOUNT = "tinyTouch"
PAIRING_SERVICE = "tinyTouch-pairing"
STATE_DIR = Path.home() / "Library" / "Application Support" / "tinyTouch"
SUSPEND_PATH = STATE_DIR / "helper-suspend"
SUSPEND_ACK_PATH = STATE_DIR / "helper-suspend-ack"
MAX_SEEN_NONCES = 256
HEARTBEAT_INTERVAL_SECONDS = 5.0
HEARTBEAT_TIMEOUT_SECONDS = 2.0
MAX_SERIAL_LINE_BYTES = 2048
PARTIAL_FRAME_TIMEOUT_SECONDS = 1.0
MAX_PASSWORD_BYTES = 160
MAX_EVENT_AUTHENTICATORS = 8
MAX_COUNTER = (1 << 64) - 1
MAX_SCORE = (1 << 31) - 1

# macOS virtual key codes for the physical keys used by TinyUSB's US ASCII map.
_MAC_KEYCODES = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6,
    "x": 7, "c": 8, "v": 9, "b": 11, "q": 12, "w": 13, "e": 14,
    "r": 15, "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21,
    "6": 22, "5": 23, "=": 24, "9": 25, "7": 26, "-": 27, "8": 28,
    "0": 29, "]": 30, "o": 31, "u": 32, "[": 33, "i": 34, "p": 35,
    "l": 37, "j": 38, "'": 39, "k": 40, ";": 41, "\\": 42, ",": 43,
    "/": 44, "n": 45, "m": 46, ".": 47, " ": 49, "`": 50,
}
_US_SHIFTED = dict(zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ!@#$%^&*()_+{}|:\"<>?~",
                       "abcdefghijklmnopqrstuvwxyz1234567890-=[]\\;',./`"))

_COMMON_CRYPTO = None

_CC_ENCRYPT = 0
_CC_MODE_CTR = 4
_CC_ALGORITHM_AES = 0
_CC_NO_PADDING = 0
_CC_MODE_OPTION_CTR_BE = 0x0002


def _common_crypto():
    global _COMMON_CRYPTO
    if _COMMON_CRYPTO is not None:
        return _COMMON_CRYPTO
    library = ctypes.CDLL("/usr/lib/system/libcommonCrypto.dylib")
    library.CCCryptorCreateWithMode.argtypes = [
        ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
        ctypes.c_size_t, ctypes.c_int, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p),
    ]
    library.CCCryptorCreateWithMode.restype = ctypes.c_int32
    library.CCCryptorUpdate.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
        ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t),
    ]
    library.CCCryptorUpdate.restype = ctypes.c_int32
    library.CCCryptorRelease.argtypes = [ctypes.c_void_p]
    library.CCCryptorRelease.restype = ctypes.c_int32
    _COMMON_CRYPTO = library
    return library


def normalize_serial(value: str) -> str:
    return "".join(char for char in value.upper() if char.isalnum() or char in "_.-")


def port_identity(port_name: str) -> str:
    for port in serial.tools.list_ports.comports():
        if port.device == port_name and port.serial_number:
            identity = normalize_serial(port.serial_number)
            if identity:
                return identity
    identity = normalize_serial(Path(port_name).name)
    if not identity:
        raise RuntimeError(f"tinyTouch port {port_name} has no stable device identity")
    return identity


def keychain_set(password: str, device_id: str = ACCOUNT) -> None:
    set_password(SERVICE, device_id, password)


def keychain_get(device_id: str = ACCOUNT) -> bytearray:
    value = get_password_bytes(SERVICE, device_id)
    if value is None:
        raise KeyError(f"No Keychain password for {device_id}")
    return value


def fingerprint_account(device_id: str, slot: int) -> str:
    return f"{device_id}:fingerprint:{slot}"


def load_passwords(device_id: str) -> dict[int, bytearray]:
    passwords = {0: keychain_get(device_id)}
    for slot in range(1, 6):
        account = fingerprint_account(device_id, slot)
        if has_password(SERVICE, account):
            try:
                passwords[slot] = keychain_get(account)
            except KeyError:
                pass
    return passwords


def settings_path(device_id: str) -> Path:
    return STATE_DIR / f"settings-{normalize_serial(device_id)}.json"


def load_settings(device_id: str) -> dict[str, str]:
    try:
        value = json.loads(settings_path(device_id).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {"keyboard_layout": "auto"}
    layout = value.get("keyboard_layout", "auto")
    return {"keyboard_layout": layout if layout in {"auto", "us"} else "auto"}


def current_keyboard_output_map() -> dict[str, str]:
    hitoolbox = ctypes.CDLL(
        "/System/Library/Frameworks/Carbon.framework/Frameworks/"
        "HIToolbox.framework/HIToolbox"
    )
    core_foundation = ctypes.CDLL(
        "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
    )
    hitoolbox.TISCopyCurrentASCIICapableKeyboardLayoutInputSource.restype = ctypes.c_void_p
    hitoolbox.TISGetInputSourceProperty.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    hitoolbox.TISGetInputSourceProperty.restype = ctypes.c_void_p
    core_foundation.CFDataGetBytePtr.argtypes = [ctypes.c_void_p]
    core_foundation.CFDataGetBytePtr.restype = ctypes.c_void_p
    core_foundation.CFRelease.argtypes = [ctypes.c_void_p]
    translate = hitoolbox.UCKeyTranslate
    translate.argtypes = [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_uint16,
                          ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
                          ctypes.POINTER(ctypes.c_uint32), ctypes.c_uint32,
                          ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint16)]
    translate.restype = ctypes.c_int32
    property_key = ctypes.c_void_p.in_dll(hitoolbox, "kTISPropertyUnicodeKeyLayoutData")
    source = hitoolbox.TISCopyCurrentASCIICapableKeyboardLayoutInputSource()
    if not source:
        raise RuntimeError("macOS did not provide a keyboard layout")
    try:
        data = hitoolbox.TISGetInputSourceProperty(source, property_key)
        layout = core_foundation.CFDataGetBytePtr(data) if data else None
        if not layout:
            raise RuntimeError("macOS keyboard layout has no Unicode key map")
        output_map: dict[str, str] = {}
        for wire in (chr(value) for value in range(32, 127)):
            base = _US_SHIFTED.get(wire, wire)
            keycode = _MAC_KEYCODES.get(base.lower())
            if keycode is None:
                continue
            modifiers = 2 if wire in _US_SHIFTED else 0  # Carbon shiftKey >> 8
            dead_key = ctypes.c_uint32(0)
            actual = ctypes.c_uint32(0)
            chars = (ctypes.c_uint16 * 4)()
            status = translate(layout, keycode, 0, modifiers, 0, 1,
                               ctypes.byref(dead_key), len(chars),
                               ctypes.byref(actual), chars)
            if status == 0 and actual.value == 1 and dead_key.value == 0:
                output_map[chr(chars[0])] = wire
        return output_map
    finally:
        core_foundation.CFRelease(source)


def translate_password(password: bytes, output_map: dict[str, str] | None) -> bytes:
    if output_map is None:
        password.decode("ascii")
        result = password
    else:
        text = password.decode("utf-8")
        try:
            result = "".join(output_map[char] for char in text).encode("ascii")
        except KeyError as exc:
            raise ValueError(
                f"character {exc.args[0]!r} is unavailable in this keyboard layout"
            ) from exc
    if len(result) > MAX_PASSWORD_BYTES:
        raise ValueError(f"password exceeds {MAX_PASSWORD_BYTES} typed characters")
    return result


def parse_pairing_key(key_hex: str) -> bytes:
    try:
        key = bytes.fromhex(key_hex.strip())
    except ValueError as exc:
        raise SystemExit("Pairing key must be 64 hex characters.") from exc
    if len(key) != 32:
        raise SystemExit("Pairing key must be exactly 32 bytes / 64 hex characters.")
    return key


def pairing_keychain_set(key_hex: str, device_id: str) -> None:
    key = parse_pairing_key(key_hex)
    set_password(PAIRING_SERVICE, device_id, key.hex())


def pairing_keychain_get(device_id: str) -> bytearray:
    value = get_password_bytes(PAIRING_SERVICE, device_id)
    if value is None:
        raise KeyError(f"No Keychain pairing key for {device_id}")
    try:
        return bytearray(parse_pairing_key(value.decode("ascii")))
    finally:
        value[:] = b"\x00" * len(value)


def mac_hex(pairing_key: bytes, message: str) -> str:
    return hmac.new(pairing_key, message.encode("ascii"), hashlib.sha256).hexdigest()


def session_key(pairing_key: bytes, nonce_hex: str) -> bytes:
    return hmac.new(pairing_key, f"SESSION|{nonce_hex}".encode("ascii"), hashlib.sha256).digest()


def aes_ctr_crypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    if len(key) not in {16, 24, 32} or len(iv) != 16:
        raise ValueError("AES-CTR requires a 16/24/32-byte key and a 16-byte IV")
    try:
        common_crypto = _common_crypto()
    except OSError:
        # Test/development portability. Production macOS uses CommonCrypto and
        # does not need an additional crypto dependency.
        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        except ImportError as exc:
            raise RuntimeError("AES-CTR backend is unavailable") from exc
        cipher = Cipher(algorithms.AES(key), modes.CTR(iv))
        encryptor = cipher.encryptor()
        return encryptor.update(data) + encryptor.finalize()

    cryptor = ctypes.c_void_p()
    key_buffer = ctypes.create_string_buffer(key, len(key))
    iv_buffer = ctypes.create_string_buffer(iv, len(iv))
    status = common_crypto.CCCryptorCreateWithMode(
        _CC_ENCRYPT,
        _CC_MODE_CTR,
        _CC_ALGORITHM_AES,
        _CC_NO_PADDING,
        iv_buffer,
        key_buffer,
        len(key),
        None,
        0,
        0,
        _CC_MODE_OPTION_CTR_BE,
        ctypes.byref(cryptor),
    )
    if status != 0:
        raise RuntimeError(f"CommonCrypto could not create AES-CTR context ({status})")
    try:
        if not data:
            return b""
        if isinstance(data, bytearray):
            input_buffer = (ctypes.c_ubyte * len(data)).from_buffer(data)
        else:
            input_buffer = ctypes.create_string_buffer(data, len(data))
        output_buffer = ctypes.create_string_buffer(len(data))
        moved = ctypes.c_size_t()
        status = common_crypto.CCCryptorUpdate(
            cryptor,
            input_buffer,
            len(data),
            output_buffer,
            len(data),
            ctypes.byref(moved),
        )
        if status != 0 or moved.value != len(data):
            raise RuntimeError(f"CommonCrypto AES-CTR failed ({status})")
        return output_buffer.raw[:moved.value]
    finally:
        common_crypto.CCCryptorRelease(cryptor)


def encrypt_password(pairing_key: bytes, nonce_hex: str, password: bytes) -> tuple[str, str]:
    iv = os.urandom(16)
    ciphertext = aes_ctr_crypt(session_key(pairing_key, nonce_hex), iv, password)
    return iv.hex(), ciphertext.hex()


def state_path(device_id: str) -> Path:
    suffix = normalize_serial(device_id)
    if not suffix:
        raise ValueError("A stable tinyTouch device identity is required")
    return STATE_DIR / f"state-{suffix}.json"


def load_state(device_id: str) -> dict:
    path = state_path(device_id)
    try:
        with path.open("r", encoding="utf-8") as f:
            state = json.load(f)
    except FileNotFoundError:
        return {"seen_nonces": []}
    except (OSError, json.JSONDecodeError):
        return {"seen_nonces": []}
    seen = state.get("seen_nonces", [])
    if not isinstance(seen, list):
        seen = []
    return {"seen_nonces": [str(item).lower() for item in seen[-MAX_SEEN_NONCES:]]}


def save_state(state: dict, device_id: str) -> None:
    atomic_write_json(state_path(device_id), state)


def remember_nonce(state: dict, nonce: str, device_id: str) -> None:
    seen_nonces = state.setdefault("seen_nonces", [])
    seen_nonces.append(nonce.lower())
    state["seen_nonces"] = seen_nonces[-MAX_SEEN_NONCES:]
    save_state(state, device_id)


def valid_hex(value: str, byte_len: int) -> bool:
    if len(value) != byte_len * 2:
        return False
    try:
        bytes.fromhex(value)
    except ValueError:
        return False
    return True


class AuthenticatedEvent(NamedTuple):
    version: int
    nonce: str
    counter: int
    slot: int
    score: int
    authenticator: str
    key_id: str | None


def parse_event(line: str, pairing_key: bytes) -> AuthenticatedEvent | None:
    """Parse and authenticate one HID event frame."""
    if not isinstance(line, str) or len(line.encode("utf-8", "replace")) > MAX_SERIAL_LINE_BYTES:
        return None
    parts = line.strip().split()
    if not parts or parts[0] not in {"EV", "EV2"}:
        return None
    version = 1 if parts[0] == "EV" else 2
    if version == 1 and len(parts) != 6:
        return None
    if version == 2 and not 6 <= len(parts) <= 5 + MAX_EVENT_AUTHENTICATORS:
        return None
    nonce, counter_text, slot_text, score_text = parts[1:5]
    if not valid_hex(nonce, 16):
        diagnostic("protocol.event_rejected", level="warning", reason="invalid_nonce")
        return None
    if not all(re.fullmatch(r"[0-9]+", item) for item in (counter_text, slot_text, score_text)):
        return None
    counter, slot, score = map(int, (counter_text, slot_text, score_text))
    if not 0 <= counter <= MAX_COUNTER or slot not in range(1, 6) or not 0 <= score <= MAX_SCORE:
        return None

    key_id: str | None = None
    if version == 1:
        authenticator = parts[5].lower()
        material = f"EV|{nonce}|{counter_text}|{slot_text}|{score_text}"
    else:
        key_id = hashlib.sha256(pairing_key).hexdigest()[:16]
        authenticators: dict[str, str] = {}
        for value in parts[5:]:
            match = re.fullmatch(r"([0-9A-Fa-f]{16}):([0-9A-Fa-f]{64})", value)
            if match is None or match.group(1).lower() in authenticators:
                return None
            authenticators[match.group(1).lower()] = match.group(2).lower()
        authenticator = authenticators.get(key_id, "")
        material = f"EV2|{key_id}|{nonce}|{counter_text}|{slot_text}|{score_text}"
    if not valid_hex(authenticator, 32):
        return None
    expected = mac_hex(pairing_key, material)
    if not hmac.compare_digest(expected, authenticator):
        diagnostic("protocol.event_rejected", level="warning", reason="invalid_mac")
        return None
    return AuthenticatedEvent(version, nonce, counter, slot, score, authenticator, key_id)


def handle_event(
    line: str,
    password: bytes | dict[int, bytes],
    pairing_key: bytes,
    state: dict | None = None,
    persist_state: bool = True,
    record_nonce: bool = True,
    device_id: str | None = None,
    keyboard_map: dict[str, str] | None = None,
) -> str | None:
    event = parse_event(line, pairing_key)
    if event is None:
        return None
    nonce = event.nonce
    key_id = event.key_id
    fingerprint_slot = event.slot
    if state is not None:
        seen_nonces = state.setdefault("seen_nonces", [])
        if nonce.lower() in seen_nonces:
            diagnostic("protocol.event_rejected", level="warning", reason="replayed_nonce")
            return None
    selected_password = (password.get(fingerprint_slot) or password.get(0)) \
        if isinstance(password, dict) else password
    if not selected_password:
        diagnostic(
            "protocol.event_rejected",
            level="warning",
            reason="password_missing",
            fingerprint_slot=fingerprint_slot,
        )
        return None
    try:
        wire_password = translate_password(selected_password, keyboard_map)
    except (UnicodeError, ValueError) as exc:
        diagnostic(
            "protocol.event_rejected",
            level="warning",
            reason="keyboard_layout_unrepresentable",
            detail=str(exc),
        )
        return None
    iv_hex, ct_hex = encrypt_password(pairing_key, nonce, wire_password)
    if key_id is None:
        reply_material = f"PW|{nonce}|{iv_hex}|{ct_hex}"
        reply = f"PW {nonce} {iv_hex} {ct_hex}"
    else:
        reply_material = f"PW2|{key_id}|{nonce}|{iv_hex}|{ct_hex}"
        reply = f"PW2 {key_id} {nonce} {iv_hex} {ct_hex}"
    reply_mac = mac_hex(pairing_key, reply_material)
    if state is not None and record_nonce:
        seen_nonces.append(nonce.lower())
        state["seen_nonces"] = seen_nonces[-MAX_SEEN_NONCES:]
        if persist_state:
            save_state(state, device_id)
    return f"{reply} {reply_mac}\n"


def resynchronize_event(line: str, device_id: str = "") -> str:
    """Recover the trailing event when a truncated one is glued to its front.

    A USB suspend can cut the device's write off mid-event. Those bytes carry no
    newline, so when the bus resumes they flush together with the next event and
    arrive as one line: ``EV <partial>EV <nonce> <counter> ...``. That parses as too
    many fields and is discarded, losing a second real touch on top of the one already
    lost. Everything before the last event marker is unrecoverable, so drop it and keep
    the intact event behind it.
    """
    markers = [line.rfind("EV "), line.rfind("EV2 ")]
    marker = max(markers)
    if marker <= 0:
        return line
    diagnostic(
        "protocol.truncated_prefix_discarded",
        level="warning",
        device_id=device_id,
        offset=marker,
    )
    return line[marker:]


def open_serial(port: str) -> serial.Serial:
    ser = serial.Serial()
    ser.port = port
    ser.baudrate = 115200
    ser.timeout = 0.2
    ser.write_timeout = 2
    try:
        ser.dtr = True
        ser.rts = False
    except (OSError, serial.SerialException):
        pass
    ser.open()
    try:
        ser.dtr = True
        ser.rts = False
    except (OSError, serial.SerialException):
        pass
    return ser


def split_serial_lines(buffer: bytes, chunk: bytes) -> tuple[list[bytes], bytes]:
    parts = (buffer + chunk).split(b"\n")
    lines, remainder = parts[:-1], parts[-1]
    if len(remainder) > MAX_SERIAL_LINE_BYTES:
        remainder = b""
    return lines, remainder


def serve_port(
    port: str,
    once: bool = False,
    stop_event: threading.Event | None = None,
    device_id: str | None = None,
) -> None:
    device_id = normalize_serial(device_id or "") or port_identity(port)
    password = load_passwords(device_id)
    pairing_key = pairing_keychain_get(device_id)
    state = load_state(device_id)
    settings = load_settings(device_id)
    last_port_check = 0.0
    last_received = time.monotonic()
    heartbeat_sent_at: float | None = None
    decoder = SerialFrameDecoder(MAX_SERIAL_LINE_BYTES)
    try:
        with open_serial(port) as ser:
            diagnostic("worker.connected", device_id=device_id, port=port)
            while True:
                if stop_event is not None and stop_event.is_set():
                    diagnostic("worker.drained", device_id=device_id, port=port)
                    return
                chunk = ser.read(256)
                if not chunk:
                    # pyserial can leave a descriptor open after macOS removes
                    # the USB device during sleep.  In that state readline()
                    # simply times out forever, so the manager never gets a
                    # chance to open the device again after wake.
                    now = time.monotonic()
                    if now - last_received >= PARTIAL_FRAME_TIMEOUT_SECONDS:
                        if decoder.discard_partial():
                            diagnostic(
                                "protocol.partial_frame_expired",
                                level="warning",
                                device_id=device_id,
                            )
                    if now - last_port_check >= 1.0:
                        last_port_check = now
                        if port not in device_ports():
                            raise serial.SerialException(
                                f"serial device disappeared: {port}"
                            )
                    if heartbeat_sent_at is not None:
                        if now - heartbeat_sent_at >= HEARTBEAT_TIMEOUT_SECONDS:
                            raise serial.SerialException(
                                f"serial device stopped responding after sleep: {port}"
                            )
                    elif now - last_received >= HEARTBEAT_INTERVAL_SECONDS:
                        ser.write(b"PING\n")
                        ser.flush()
                        heartbeat_sent_at = now
                    continue
                last_received = time.monotonic()
                heartbeat_sent_at = None
                for raw in decoder.feed(chunk):
                    try:
                        line = raw.decode("ascii").strip()
                    except UnicodeDecodeError:
                        diagnostic(
                            "protocol.frame_rejected",
                            level="warning",
                            device_id=device_id,
                            reason="non_ascii",
                        )
                        continue
                    if line == "PONG":
                        continue
                    line = resynchronize_event(line, device_id)
                    if not (line.startswith("EV ") or line.startswith("EV2 ")):
                        continue
                    keyboard_map = (current_keyboard_output_map()
                                    if settings["keyboard_layout"] == "auto" else None)
                    reply = handle_event(line, password, pairing_key, state,
                                         device_id=device_id, keyboard_map=keyboard_map,
                                         record_nonce=False)
                    if reply:
                        ser.write(reply.encode("ascii"))
                        ser.flush()
                        remember_nonce(state, line.split()[1], device_id)
                        diagnostic(
                            "protocol.password_delivered",
                            device_id=device_id,
                            fingerprint_slot=int(line.split()[3]),
                        )
                        if once:
                            return
                time.sleep(0.01)
    finally:
        for value in password.values():
            value[:] = b"\x00" * len(value)
        pairing_key[:] = b"\x00" * len(pairing_key)


class DeviceEndpoint(NamedTuple):
    device_id: str
    port: str
    location: str


def device_endpoints() -> list[DeviceEndpoint]:
    endpoints: list[DeviceEndpoint] = []
    for item in serial.tools.list_ports.comports():
        if not (
            item.vid == 0x303A
            and item.pid == 0x4001
            and isinstance(item.serial_number, str)
            and re.fullmatch(r"TT-[0-9A-Fa-f]{12}", item.serial_number)
        ):
            continue
        endpoints.append(
            DeviceEndpoint(
                normalize_serial(item.serial_number),
                item.device,
                item.location if isinstance(item.location, str) else "",
            )
        )
    return sorted(endpoints, key=lambda endpoint: (endpoint.device_id, endpoint.port))


def device_ports() -> list[str]:
    return [endpoint.port for endpoint in device_endpoints()]


def credentials_exist(device_id: str) -> bool:
    return all(has_password(service, device_id) for service in (PAIRING_SERVICE, SERVICE))


class Worker:
    def __init__(self, endpoint: DeviceEndpoint):
        self.endpoint = endpoint
        self.stop_event = threading.Event()
        self.error: BaseException | None = None
        self.planned_stop = False
        self.phase = WorkerPhase.CREATED
        self.started_at: float | None = None
        self.thread = threading.Thread(
            target=self._run,
            daemon=True,
            name=f"tinyTouch-{endpoint.device_id}",
        )

    def _run(self) -> None:
        try:
            serve_port(
                self.endpoint.port,
                stop_event=self.stop_event,
                device_id=self.endpoint.device_id,
            )
        except Exception as exc:
            self.error = exc
            self.phase = WorkerPhase.FAILED
        else:
            self.phase = WorkerPhase.STOPPED

    def start(self) -> None:
        self.phase = WorkerPhase.RUNNING
        self.started_at = time.monotonic()
        self.thread.start()

    def stop(self) -> None:
        self.planned_stop = True
        self.phase = WorkerPhase.DRAINING
        self.stop_event.set()


class WorkerPhase(Enum):
    CREATED = "created"
    RUNNING = "running"
    DRAINING = "draining"
    STOPPED = "stopped"
    FAILED = "failed"


class ManagerPhase(Enum):
    STARTING = "starting"
    RUNNING = "running"
    DRAINING = "draining"
    SUSPENDED = "suspended"


def run_manager() -> None:
    workers: dict[str, Worker] = {}
    failures: dict[str, int] = {}
    retry_after: dict[str, float] = {}
    backoff = BackoffPolicy(initial=0.25, maximum=30.0)
    lease_observer = LeaseObserver(SUSPEND_PATH, SUSPEND_ACK_PATH)
    active_lease_nonce: str | None = None
    phase = ManagerPhase.STARTING

    def transition(target: ManagerPhase, reason: str) -> None:
        nonlocal phase
        if phase == target:
            return
        diagnostic(
            "manager.transition",
            previous=phase.value,
            current=target.value,
            reason=reason,
        )
        phase = target

    diagnostic("manager.started", pid=os.getpid(), phase=phase.value)
    transition(ManagerPhase.RUNNING, "startup_complete")
    while True:
        now = time.monotonic()
        lease = lease_observer.active()
        if lease is not None:
            transition(ManagerPhase.DRAINING, "foreground_lease")
            for worker in workers.values():
                worker.stop()
            for device_id, worker in list(workers.items()):
                if not worker.thread.is_alive():
                    worker.thread.join()
                    del workers[device_id]
            if not workers:
                lease_observer.acknowledge(lease)
                transition(ManagerPhase.SUSPENDED, "workers_drained")
                if active_lease_nonce != lease.nonce:
                    diagnostic("manager.suspended", owner_pid=lease.pid)
                    active_lease_nonce = lease.nonce
            time.sleep(0.05)
            continue
        if active_lease_nonce is not None:
            diagnostic("manager.resumed")
            active_lease_nonce = None
        transition(ManagerPhase.RUNNING, "lease_released")

        endpoints = {endpoint.device_id: endpoint for endpoint in device_endpoints()}
        for device_id, worker in list(workers.items()):
            current = endpoints.get(device_id)
            if current is None or current.port != worker.endpoint.port:
                worker.stop()
            if worker.thread.is_alive():
                continue
            worker.thread.join()
            del workers[device_id]
            if worker.planned_stop:
                retry_after[device_id] = now
                continue
            if worker.started_at is not None and now - worker.started_at >= 30:
                failures[device_id] = 0
            failures[device_id] = failures.get(device_id, 0) + 1
            delay = backoff.delay(failures[device_id])
            retry_after[device_id] = now + delay
            diagnostic(
                "worker.failed",
                level="warning",
                device_id=device_id,
                error_type=type(worker.error).__name__ if worker.error else "unexpected_exit",
                retry_seconds=round(delay, 3),
            )

        for device_id, endpoint in endpoints.items():
            if device_id in workers or now < retry_after.get(device_id, 0.0):
                continue
            try:
                configured = credentials_exist(device_id)
            except KeychainError as exc:
                failures[device_id] = failures.get(device_id, 0) + 1
                delay = backoff.delay(failures[device_id])
                retry_after[device_id] = now + delay
                diagnostic(
                    "keychain.unavailable",
                    level="warning",
                    device_id=device_id,
                    status=getattr(exc, "status", None),
                    retry_seconds=round(delay, 3),
                )
                continue
            if not configured:
                diagnostic("worker.credentials_missing", level="warning", device_id=device_id)
                retry_after[device_id] = now + 30
                continue
            worker = Worker(endpoint)
            workers[device_id] = worker
            worker.start()
            retry_after.pop(device_id, None)
            diagnostic("worker.started", device_id=device_id, port=endpoint.port)
        time.sleep(0.1)


def run(port: str | None, once: bool) -> None:
    if port:
        while True:
            try:
                serve_port(port, once)
                return
            except (OSError, serial.SerialException, subprocess.CalledProcessError) as exc:
                diagnostic(
                    "worker.reconnect",
                    level="warning",
                    error_type=type(exc).__name__,
                )
                time.sleep(1)
    if once:
        raise SystemExit("--once requires --port when multiple-device mode is active")
    run_manager()


def wait_for_cli_suspension() -> None:
    """Compatibility entry point; manager now observes leases continuously."""
    observer = LeaseObserver(SUSPEND_PATH, SUSPEND_ACK_PATH)
    while True:
        record = observer.active()
        if record is None:
            return
        observer.acknowledge(record)
        time.sleep(0.2)


def self_test(device_id: str) -> None:
    password = keychain_get(device_id)
    pairing_key = pairing_keychain_get(device_id)
    nonce = "00" * 16
    event_mac = mac_hex(pairing_key, f"EV|{nonce}|1|1|123")
    reply = handle_event(
        f"EV {nonce} 1 1 123 {event_mac}",
        password,
        pairing_key,
        {"seen_nonces": []},
        persist_state=False,
        device_id=device_id,
    )
    assert reply is not None
    parts = reply.split()
    assert parts[0] == "PW"
    assert hmac.compare_digest(parts[4], mac_hex(pairing_key, f"PW|{parts[1]}|{parts[2]}|{parts[3]}"))
    key_id = hashlib.sha256(pairing_key).hexdigest()[:16]
    event_mac = mac_hex(pairing_key, f"EV2|{key_id}|{nonce}|2|1|123")
    reply = handle_event(
        f"EV2 {nonce} 2 1 123 deadbeefdeadbeef:{'00' * 32} {key_id}:{event_mac}",
        password,
        pairing_key,
        {"seen_nonces": []},
        persist_state=False,
        device_id=device_id,
    )
    assert reply is not None
    parts = reply.split()
    assert parts[0] == "PW2" and parts[1] == key_id
    assert hmac.compare_digest(
        parts[5], mac_hex(pairing_key, f"PW2|{parts[1]}|{parts[2]}|{parts[3]}|{parts[4]}")
    )
    print("self-test ok")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port")
    parser.add_argument("--device-id", required=False)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        if not args.device_id:
            raise SystemExit("--device-id is required for --self-test")
        self_test(args.device_id)
        return
    set_background_mode()
    failures = 0
    backoff = BackoffPolicy(initial=0.25, maximum=30.0)
    while True:
        try:
            wait_for_cli_suspension()
            run(args.port, args.once)
            return
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            failures += 1
            delay = backoff.delay(failures)
            diagnostic(
                "manager.restart",
                level="error",
                error_type=type(exc).__name__,
                retry_seconds=round(delay, 3),
            )
            time.sleep(delay)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
import argparse
import ctypes
import hashlib
import hmac
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import serial
import serial.tools.list_ports
try:
    from tinytouch_keychain import KeychainError, get_password, has_password, set_password
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from tinytouch_keychain import KeychainError, get_password, has_password, set_password


SERVICE = "tinyTouch"
ACCOUNT = "tinyTouch"
PAIRING_SERVICE = "tinyTouch-pairing"
PREFERRED_SERIAL = "B8F862FB478C"
STATE_DIR = Path.home() / "Library" / "Application Support" / "tinyTouch"
MAX_SEEN_NONCES = 256
HEARTBEAT_INTERVAL_SECONDS = 5.0
HEARTBEAT_TIMEOUT_SECONDS = 2.0
MAX_SERIAL_LINE_BYTES = 2048
PARTIAL_FRAME_TIMEOUT_SECONDS = 1.0
MAX_PASSWORD_BYTES = 160

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

_COMMON_CRYPTO = ctypes.CDLL("/usr/lib/system/libcommonCrypto.dylib")
_COMMON_CRYPTO.CCCryptorCreateWithMode.argtypes = [
    ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
    ctypes.c_size_t, ctypes.c_int, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p),
]
_COMMON_CRYPTO.CCCryptorCreateWithMode.restype = ctypes.c_int32
_COMMON_CRYPTO.CCCryptorUpdate.argtypes = [
    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
    ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t),
]
_COMMON_CRYPTO.CCCryptorUpdate.restype = ctypes.c_int32
_COMMON_CRYPTO.CCCryptorRelease.argtypes = [ctypes.c_void_p]
_COMMON_CRYPTO.CCCryptorRelease.restype = ctypes.c_int32

_CC_ENCRYPT = 0
_CC_MODE_CTR = 4
_CC_ALGORITHM_AES = 0
_CC_NO_PADDING = 0
_CC_MODE_OPTION_CTR_BE = 0x0002


def normalize_serial(value: str) -> str:
    return "".join(char for char in value.upper() if char.isalnum() or char in "_.-")


def port_identity(port_name: str) -> str:
    for port in serial.tools.list_ports.comports():
        if port.device == port_name and port.serial_number:
            identity = normalize_serial(port.serial_number)
            if identity:
                return identity
    return normalize_serial(Path(port_name).name) or PREFERRED_SERIAL


def keychain_set(password: str, device_id: str = ACCOUNT) -> None:
    set_password(SERVICE, device_id, password)


def keychain_get(device_id: str = ACCOUNT) -> bytes:
    value = get_password(SERVICE, device_id)
    if value is None:
        raise KeyError(f"No Keychain password for {device_id}")
    return value.encode("utf-8")


def fingerprint_account(device_id: str, slot: int) -> str:
    return f"{device_id}:fingerprint:{slot}"


def load_passwords(device_id: str) -> dict[int, bytes]:
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


def pairing_keychain_set(key_hex: str, device_id: str = PREFERRED_SERIAL) -> None:
    key = parse_pairing_key(key_hex)
    set_password(PAIRING_SERVICE, device_id, key.hex())


def pairing_keychain_get(device_id: str = PREFERRED_SERIAL) -> bytes:
    value = get_password(PAIRING_SERVICE, device_id)
    if value is None:
        raise KeyError(f"No Keychain pairing key for {device_id}")
    return parse_pairing_key(value)


def mac_hex(pairing_key: bytes, message: str) -> str:
    return hmac.new(pairing_key, message.encode("ascii"), hashlib.sha256).hexdigest()


def session_key(pairing_key: bytes, nonce_hex: str) -> bytes:
    return hmac.new(pairing_key, f"SESSION|{nonce_hex}".encode("ascii"), hashlib.sha256).digest()


def aes_ctr_crypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    if len(key) not in {16, 24, 32} or len(iv) != 16:
        raise ValueError("AES-CTR requires a 16/24/32-byte key and a 16-byte IV")
    cryptor = ctypes.c_void_p()
    key_buffer = ctypes.create_string_buffer(key, len(key))
    iv_buffer = ctypes.create_string_buffer(iv, len(iv))
    status = _COMMON_CRYPTO.CCCryptorCreateWithMode(
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
        input_buffer = ctypes.create_string_buffer(data, len(data))
        output_buffer = ctypes.create_string_buffer(len(data))
        moved = ctypes.c_size_t()
        status = _COMMON_CRYPTO.CCCryptorUpdate(
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
        _COMMON_CRYPTO.CCCryptorRelease(cryptor)


def encrypt_password(pairing_key: bytes, nonce_hex: str, password: bytes) -> tuple[str, str]:
    iv = os.urandom(16)
    ciphertext = aes_ctr_crypt(session_key(pairing_key, nonce_hex), iv, password)
    return iv.hex(), ciphertext.hex()


def state_path(device_id: str | None = None) -> Path:
    suffix = normalize_serial(device_id or "legacy")
    return STATE_DIR / f"state-{suffix}.json"


def load_state(device_id: str | None = None) -> dict:
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
    return {"seen_nonces": [str(item) for item in seen[-MAX_SEEN_NONCES:]]}


def save_state(state: dict, device_id: str | None = None) -> None:
    path = state_path(device_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(state, f, separators=(",", ":"))
    tmp.replace(path)


def valid_hex(value: str, byte_len: int) -> bool:
    if len(value) != byte_len * 2:
        return False
    try:
        bytes.fromhex(value)
    except ValueError:
        return False
    return True


def handle_event(
    line: str,
    password: bytes | dict[int, bytes],
    pairing_key: bytes,
    state: dict | None = None,
    persist_state: bool = True,
    device_id: str | None = None,
    keyboard_map: dict[str, str] | None = None,
) -> str | None:
    parts = line.strip().split()
    if not parts or parts[0] not in {"EV", "EV2"}:
        return None
    if parts[0] == "EV":
        if len(parts) != 6:
            return None
        _, nonce, counter, slot, score, got_mac = parts
        key_id = None
        mac_material = f"EV|{nonce}|{counter}|{slot}|{score}"
    else:
        if len(parts) < 6:
            return None
        _, nonce, counter, slot, score, *authenticators = parts
        key_id = hashlib.sha256(pairing_key).hexdigest()[:16]
        prefix = f"{key_id}:"
        match = next((item[len(prefix):] for item in authenticators
                      if item.lower().startswith(prefix)), None)
        if match is None:
            return None
        got_mac = match
        mac_material = f"EV2|{key_id}|{nonce}|{counter}|{slot}|{score}"
    try:
        fingerprint_slot = int(slot)
        fingerprint_score = int(score)
    except ValueError:
        return None
    if fingerprint_slot not in range(1, 6) or fingerprint_score < 0:
        return None
    if not valid_hex(nonce, 16):
        print("bad event nonce", file=sys.stderr)
        return None
    expected = mac_hex(pairing_key, mac_material)
    if not hmac.compare_digest(expected, got_mac.lower()):
        print("bad event mac", file=sys.stderr)
        return None
    if state is not None:
        seen_nonces = state.setdefault("seen_nonces", [])
        if nonce in seen_nonces:
            print("replayed event nonce", file=sys.stderr)
            return None
    selected_password = (password.get(fingerprint_slot) or password.get(0)) \
        if isinstance(password, dict) else password
    if not selected_password:
        print(f"no password configured for fingerprint {fingerprint_slot}", file=sys.stderr)
        return None
    try:
        wire_password = translate_password(selected_password, keyboard_map)
    except (UnicodeError, ValueError) as exc:
        print(f"password cannot be typed safely: {exc}", file=sys.stderr)
        return None
    iv_hex, ct_hex = encrypt_password(pairing_key, nonce, wire_password)
    if key_id is None:
        reply_material = f"PW|{nonce}|{iv_hex}|{ct_hex}"
        reply = f"PW {nonce} {iv_hex} {ct_hex}"
    else:
        reply_material = f"PW2|{key_id}|{nonce}|{iv_hex}|{ct_hex}"
        reply = f"PW2 {key_id} {nonce} {iv_hex} {ct_hex}"
    reply_mac = mac_hex(pairing_key, reply_material)
    if state is not None:
        seen_nonces.append(nonce)
        state["seen_nonces"] = seen_nonces[-MAX_SEEN_NONCES:]
        if persist_state:
            save_state(state, device_id)
    return f"{reply} {reply_mac}\n"


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


def serve_port(port: str, once: bool = False) -> None:
    device_id = port_identity(port)
    password = load_passwords(device_id)
    pairing_key = pairing_keychain_get(device_id)
    state = load_state(device_id)
    settings = load_settings(device_id)
    last_port_check = 0.0
    last_received = time.monotonic()
    heartbeat_sent_at: float | None = None
    serial_buffer = b""
    try:
        with open_serial(port) as ser:
            print(f"helper listening on {port} ({device_id})", flush=True)
            while True:
                chunk = ser.read(256)
                if not chunk:
                    # pyserial can leave a descriptor open after macOS removes
                    # the USB device during sleep.  In that state readline()
                    # simply times out forever, so the manager never gets a
                    # chance to open the device again after wake.
                    now = time.monotonic()
                    if (serial_buffer and now - last_received >= PARTIAL_FRAME_TIMEOUT_SECONDS):
                        print(f"discarded incomplete serial event from {device_id}",
                              file=sys.stderr, flush=True)
                        serial_buffer = b""
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
                lines, serial_buffer = split_serial_lines(serial_buffer, chunk)
                for raw in lines:
                    line = raw.decode("utf-8", "replace").strip()
                    if line == "PONG":
                        continue
                    if line:
                        print(f"{device_id}: {line}", flush=True)
                    if not (line.startswith("EV ") or line.startswith("EV2 ")):
                        continue
                    keyboard_map = (current_keyboard_output_map()
                                    if settings["keyboard_layout"] == "auto" else None)
                    reply = handle_event(line, password, pairing_key, state,
                                         device_id=device_id, keyboard_map=keyboard_map)
                    if reply:
                        ser.write(reply.encode("ascii"))
                        ser.flush()
                        print(f"sent encrypted password to {device_id}", flush=True)
                        if once:
                            return
                time.sleep(0.01)
    finally:
        password = {slot: b"\x00" * len(value) for slot, value in password.items()}
        pairing_key = b"\x00" * len(pairing_key)


def device_ports() -> list[str]:
    return sorted(port.device for port in serial.tools.list_ports.comports()
                  if port.device.startswith("/dev/cu.usbmodem"))


def credentials_exist(device_id: str) -> bool:
    return all(has_password(service, device_id) for service in (PAIRING_SERVICE, SERVICE))


def run_manager() -> None:
    workers: dict[str, threading.Thread] = {}
    failed_attempts: dict[str, float] = {}
    while True:
        now = time.monotonic()
        for port, worker in list(workers.items()):
            if not worker.is_alive():
                worker.join()
                del workers[port]
                failed_attempts[port] = now
        for port in device_ports():
            if port in workers:
                continue
            if now - failed_attempts.get(port, 0.0) < 15.0:
                continue
            device_id = port_identity(port)
            if not credentials_exist(device_id):
                continue
            worker = threading.Thread(target=managed_worker, args=(port,), daemon=True,
                                      name=f"tinyTouch-{device_id}")
            workers[port] = worker
            worker.start()
        time.sleep(1)


def managed_worker(port: str) -> None:
    try:
        serve_port(port)
    except (OSError, serial.SerialException, subprocess.CalledProcessError, KeychainError) as exc:
        print(f"worker for {port} stopped: {exc}", file=sys.stderr, flush=True)


def run(port: str | None, once: bool) -> None:
    if port:
        while True:
            try:
                serve_port(port, once)
                return
            except (OSError, serial.SerialException, subprocess.CalledProcessError) as exc:
                print(f"serial reconnect after error: {exc}", file=sys.stderr, flush=True)
                time.sleep(1)
    if once:
        raise SystemExit("--once requires --port when multiple-device mode is active")
    run_manager()


def self_test(device_id: str = PREFERRED_SERIAL) -> None:
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
    parser.add_argument("--device-id", default=PREFERRED_SERIAL)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test(args.device_id)
        return
    while True:
        try:
            run(args.port, args.once)
            return
        except KeyboardInterrupt:
            raise
        except BaseException as exc:
            print(f"top-level restart after error: {exc!r}", file=sys.stderr, flush=True)
            time.sleep(1)


if __name__ == "__main__":
    main()

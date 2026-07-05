#!/usr/bin/env python3
import argparse
import glob
import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import serial
import serial.tools.list_ports
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


SERVICE = "tinyTouch"
ACCOUNT = "tinyTouch"
PAIRING_SERVICE = "tinyTouch-pairing"
PREFERRED_SERIAL = "B8F862FB478C"
STATE_PATH = Path.home() / "Library" / "Application Support" / "tinyTouch" / "state.json"
MAX_SEEN_NONCES = 256


def keychain_set(password: str) -> None:
    subprocess.run(
        [
            "security",
            "add-generic-password",
            "-U",
            "-a",
            ACCOUNT,
            "-s",
            SERVICE,
            "-w",
            password,
        ],
        check=True,
    )


def keychain_get() -> bytes:
    result = subprocess.run(
        [
            "security",
            "find-generic-password",
            "-a",
            ACCOUNT,
            "-s",
            SERVICE,
            "-w",
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout.rstrip("\n").encode("utf-8")


def parse_pairing_key(key_hex: str) -> bytes:
    try:
        key = bytes.fromhex(key_hex.strip())
    except ValueError as exc:
        raise SystemExit("Pairing key must be 64 hex characters.") from exc
    if len(key) != 32:
        raise SystemExit("Pairing key must be exactly 32 bytes / 64 hex characters.")
    return key


def pairing_keychain_set(key_hex: str) -> None:
    key = parse_pairing_key(key_hex)
    subprocess.run(
        [
            "security",
            "add-generic-password",
            "-U",
            "-a",
            PREFERRED_SERIAL,
            "-s",
            PAIRING_SERVICE,
            "-w",
            key.hex(),
        ],
        check=True,
    )


def pairing_keychain_get() -> bytes:
    result = subprocess.run(
        [
            "security",
            "find-generic-password",
            "-a",
            PREFERRED_SERIAL,
            "-s",
            PAIRING_SERVICE,
            "-w",
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return parse_pairing_key(result.stdout.rstrip("\n"))


def mac_hex(pairing_key: bytes, message: str) -> str:
    return hmac.new(pairing_key, message.encode("ascii"), hashlib.sha256).hexdigest()


def session_key(pairing_key: bytes, nonce_hex: str) -> bytes:
    return hmac.new(pairing_key, f"SESSION|{nonce_hex}".encode("ascii"), hashlib.sha256).digest()


def encrypt_password(pairing_key: bytes, nonce_hex: str, password: bytes) -> tuple[str, str]:
    iv = os.urandom(16)
    cipher = Cipher(algorithms.AES(session_key(pairing_key, nonce_hex)), modes.CTR(iv))
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(password) + encryptor.finalize()
    return iv.hex(), ciphertext.hex()


def load_state() -> dict:
    try:
        with STATE_PATH.open("r", encoding="utf-8") as f:
            state = json.load(f)
    except FileNotFoundError:
        return {"seen_nonces": []}
    except (OSError, json.JSONDecodeError):
        return {"seen_nonces": []}
    seen = state.get("seen_nonces", [])
    if not isinstance(seen, list):
        seen = []
    return {"seen_nonces": [str(item) for item in seen[-MAX_SEEN_NONCES:]]}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(state, f, separators=(",", ":"))
    tmp.replace(STATE_PATH)


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
    password: bytes,
    pairing_key: bytes,
    state: dict | None = None,
    persist_state: bool = True,
) -> str | None:
    parts = line.strip().split()
    if len(parts) != 6 or parts[0] != "EV":
        return None
    _, nonce, counter, slot, score, got_mac = parts
    if not valid_hex(nonce, 16):
        print("bad event nonce", file=sys.stderr)
        return None
    expected = mac_hex(pairing_key, f"EV|{nonce}|{counter}|{slot}|{score}")
    if not hmac.compare_digest(expected, got_mac.lower()):
        print("bad event mac", file=sys.stderr)
        return None
    if state is not None:
        seen_nonces = state.setdefault("seen_nonces", [])
        if nonce in seen_nonces:
            print("replayed event nonce", file=sys.stderr)
            return None
    iv_hex, ct_hex = encrypt_password(pairing_key, nonce, password)
    reply_mac = mac_hex(pairing_key, f"PW|{nonce}|{iv_hex}|{ct_hex}")
    if state is not None:
        seen_nonces.append(nonce)
        state["seen_nonces"] = seen_nonces[-MAX_SEEN_NONCES:]
        if persist_state:
            save_state(state)
    return f"PW {nonce} {iv_hex} {ct_hex} {reply_mac}\n"


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


def run(port: str, once: bool) -> None:
    password = keychain_get()
    pairing_key = pairing_keychain_get()
    state = load_state()
    try:
        while True:
            active_port = port or find_esp_port()
            try:
                with open_serial(active_port) as ser:
                    print(f"helper listening on {active_port}", flush=True)
                    while True:
                        raw = ser.readline()
                        if not raw:
                            continue
                        line = raw.decode("utf-8", "replace").strip()
                        if line:
                            print(f"esp: {line}", flush=True)
                        reply = handle_event(line, password, pairing_key, state)
                        if reply:
                            ser.write(reply.encode("ascii"))
                            ser.flush()
                            print("sent encrypted password", flush=True)
                            if once:
                                return
                        time.sleep(0.01)
            except (OSError, serial.SerialException) as exc:
                print(f"serial reconnect after error: {exc}", file=sys.stderr, flush=True)
                time.sleep(1)
    finally:
        password = b"\x00" * len(password)
        pairing_key = b"\x00" * len(pairing_key)


def find_esp_port() -> str:
    for port in sorted(serial.tools.list_ports.comports(), key=lambda item: item.device):
        if not port.device.startswith("/dev/cu.usbmodem"):
            continue
        serial_number = (port.serial_number or "").replace(":", "").upper()
        if serial_number == PREFERRED_SERIAL:
            return port.device
    ports = sorted(glob.glob("/dev/cu.usbmodem*"))
    for port in ports:
        if PREFERRED_SERIAL in port:
            return port
    raise SystemExit(f"No ESP32-S3 serial port found for serial {PREFERRED_SERIAL}.")


def self_test() -> None:
    password = keychain_get()
    pairing_key = pairing_keychain_get()
    nonce = "00" * 16
    event_mac = mac_hex(pairing_key, f"EV|{nonce}|1|1|123")
    reply = handle_event(
        f"EV {nonce} 1 1 123 {event_mac}",
        password,
        pairing_key,
        {"seen_nonces": []},
        persist_state=False,
    )
    assert reply is not None
    parts = reply.split()
    assert parts[0] == "PW"
    assert hmac.compare_digest(parts[4], mac_hex(pairing_key, f"PW|{parts[1]}|{parts[2]}|{parts[3]}"))
    print("self-test ok")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port")
    parser.add_argument("--set-password")
    parser.add_argument("--set-pairing-key")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.set_password is not None:
        keychain_set(args.set_password)
        print("password stored in Keychain")
    if args.set_pairing_key is not None:
        pairing_keychain_set(args.set_pairing_key)
        print("pairing key stored in Keychain")
    if args.self_test:
        self_test()
        return
    if args.set_password is None and args.set_pairing_key is None:
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

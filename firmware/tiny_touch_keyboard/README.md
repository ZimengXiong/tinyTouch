# tinyTouch Keyboard Firmware

This is the original convenience-oriented tinyTouch mode. The ESP32-S3 behaves
like a USB keyboard and types the Mac password after a fingerprint match.

This mode is useful, but intentionally less secure than the smartcard firmware.
Use it when compatibility and convenience matter more than isolation.

## How It Works

The Mac helper stores the real password and pairing key in the macOS Keychain.
The ESP stores only the pairing key.

For each successful fingerprint match:

1. The ESP creates a random nonce.
2. The ESP sends an authenticated `EV ...` request to the Mac helper.
3. The helper rejects bad MACs and replayed nonces.
4. The helper encrypts the Keychain password with AES-CTR using a session key
   derived from the pairing key and nonce.
5. The ESP verifies the reply, decrypts the password in RAM, types it over USB
   HID, then wipes the password buffer.

## Advantages

- Works in almost any focused password field.
- Does not require macOS smart-card pairing.
- Works with apps that do not support PIV/smart cards.
- Easier to understand and modify than the CCID/PIV firmware.

## Disadvantages

- The real password is typed into whichever field currently has focus.
- A malicious or mistaken focused field can receive the password.
- It is not a smart card and does not provide challenge/response login.
- Anyone who can spoof the fingerprint sensor UART can trigger the flow.
- Flash dumping the ESP can expose pairing material unless ESP flash encryption
  and secure boot are enabled.
- A compromised Mac helper can expose the password.

## Setup

Install dependencies:

```sh
cd ~/Projects/tinyTouch
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

Create a pairing key:

```sh
PAIRING_KEY="$(openssl rand -hex 32)"
echo "$PAIRING_KEY"
```

Store the pairing key in the macOS Keychain:

```sh
.venv/bin/python tinytouch_helper.py --set-pairing-key "$PAIRING_KEY"
```

Create the ESP secret file:

```sh
cp firmware/tiny_touch_keyboard/secrets.example.h firmware/tiny_touch_keyboard/secrets.h
```

Edit `firmware/tiny_touch_keyboard/secrets.h` so the 32 bytes match
`PAIRING_KEY`.

Store the password in the macOS Keychain:

```sh
.venv/bin/python tinytouch_helper.py --set-password 'your-password-here'
```

Flash with Arduino IDE.

Board settings used during development:

```text
USB CDC on Boot: Enabled
USB Mode: USB-OTG
```

Run the helper once:

```sh
.venv/bin/python tinytouch_helper.py
```

Touching an enrolled finger should produce a match and type the password.

## Background Helper

The included launchd plist may need local path edits:

```text
launchd/com.tinytouch.helper.plist
```

Install:

```sh
mkdir -p ~/Library/LaunchAgents
cp launchd/com.tinytouch.helper.plist ~/Library/LaunchAgents/
launchctl unload ~/Library/LaunchAgents/com.tinytouch.helper.plist 2>/dev/null || true
launchctl load ~/Library/LaunchAgents/com.tinytouch.helper.plist
```

Logs:

```text
/tmp/tinytouch-helper.log
/tmp/tinytouch-helper.err
```

Stop the helper:

```sh
launchctl unload ~/Library/LaunchAgents/com.tinytouch.helper.plist
```

Do not commit `firmware/tiny_touch_keyboard/secrets.h`.

## When To Prefer Smartcard Mode

Use `firmware/tiny_touch_smartcard` instead if you care more about security than
universal password-field compatibility. Smartcard mode avoids typing the real
password and lets macOS authenticate against a PIV key, but it only works in
smart-card-aware auth flows.

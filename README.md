# **tinyTouch** authenticates you insecurely 🙂
authenticate, sudo, login with your fingerprint in clamshell and mac minis without having to spend $149

https://github.com/user-attachments/assets/efc933bb-c136-4f07-9db1-4b286c018932

## bom

| Item                          | Used here                 | Notes                                                                                                                              |
| ----------------------------- | ------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| esp32s3              | seeed studio esp32s3 | any microcontroller works; must support native hid and a hardware uart. (and preferably secure boot and flash encryption |
| fingerprint module       | zw101                     | uses the common `0xEF01` packet protocol, `GenImg`, `Img2Tz`, `Search`, `Match`, and led command `0x3C`.                      |
| mac                           |        | stores the password and pairing key in Keychain.                                                                                   |
| misc. wiring, etc |                           |                                                                                                                                    |


## wiring

standard uart (tx->rx) and int pin (detect finger press) to any pin


## security

esp never stores the password. it stores only a 32-byte pairing key

for each successful fingerprint match:

1. esp creates a random nonce
2. esp sends an authenticated `EV ...` request to the mac helper
3. helper rejects bad macs and replayed nonces
4. helper encrypts the Keychain password with aes-ctr using a session key derived from the pairing key and nonce
5. esp verifies the reply, decrypts the password in ram, types it, then wipes the password buffer

anyone with the programmed ESP and the paired Mac can request the password when the fingerprint matches (or uart is spoofed). secure separately but not together

## setup

### 1. install dependencies

```sh
cd ~/Projects/tinyTouch
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

### 2. create a pairing key

```sh
PAIRING_KEY="$(openssl rand -hex 32)"
echo "$PAIRING_KEY"
```

### 3. store that key in the macOS Keychain:

```sh
.venv/bin/python tinytouch_helper.py --set-pairing-key "$PAIRING_KEY"
```

### 4. create the ESP secret file:

```sh
cp firmware/tiny_touch_keyboard/secrets.example.h firmware/tiny_touch_keyboard/secrets.h
```

### 5. edit `firmware/tiny_touch_keyboard/secrets.h` so the 32 bytes match `PAIRING_KEY`.

### 6. store the password in Keychain

```sh
.venv/bin/python tinytouch_helper.py --set-password 'your-password-here'
```

### 7. flash the ESP firmware

open `firmware/tiny_touch_keyboard/tiny_touch_keyboard.ino` in Arduino IDE.

use these board settings:
`USB CDC on Boot: Enabled`
`USB Mode: USB-OTG`

### 8. run the helper once

```sh
.venv/bin/python tinytouch_helper.py
```

should see `helper listening on ...`. touching an enrolled finger should produce `MATCH`, then `TYPED`.

### 9. install the macOS background worker

the included plist uses this repo path, you should change it:

```text
/Users/xzm/Projects/tinyTouch
```

### 10. install it

```sh
mkdir -p ~/Library/LaunchAgents
cp launchd/com.tinytouch.helper.plist ~/Library/LaunchAgents/
launchctl unload ~/Library/LaunchAgents/com.tinytouch.helper.plist 2>/dev/null || true
launchctl load ~/Library/LaunchAgents/com.tinytouch.helper.plist
```

## other
logs are written to:

```text
/tmp/tinytouch-helper.log
/tmp/tinytouch-helper.err
```

stop the launchd worker

```sh
launchctl unload ~/Library/LaunchAgents/com.tinytouch.helper.plist
```

do not commit `firmware/tiny_touch_keyboard/secrets.h`

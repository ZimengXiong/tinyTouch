# tinyTouch Smartcard Firmware

This firmware turns the ESP32-S3 into a fingerprint-gated USB smart card for
macOS. It is the safer tinyTouch mode: the device authenticates by performing PIV
private-key operations instead of typing the Mac password.

## Current Behavior

USB interfaces:

- **CCID/PIV smart card:** used by macOS CryptoTokenKit, `sc_auth`, login, and
  `pam_smartcard`.
- **HID keyboard:** used only to submit the dummy PIN `000000` + Enter.

Fingerprint behavior:

- A fingerprint match opens a biometric authorization window.
- PIV private-key use requires both the dummy PIN shim and a recent fingerprint
  match.
- HID fallback password typing has been removed.

LED behavior:

- Idle: blue.
- Successful fingerprint match: green flash, then blue.
- Failed match: red flash, then blue.

## What Works

- macOS enumerates the device as `tinyTouch tinyTouch PIV`.
- PIV authentication identity appears in `sc_auth identities`.
- macOS can pair the PIV auth certificate.
- `sudo` can invoke Apple's `pam_smartcard` path.
- The smartcard PIN prompt can be satisfied by the HID dummy PIN submit.
- PIV slot `9A` signs for authentication.
- PIV slot `9D` is present for key management/keychain wrapping behavior.

Useful commands:

```sh
system_profiler SPSmartCardsDataType
sc_auth identities
sc_auth list -u "$USER" -v
```

Test `sudo` smartcard auth:

```sh
sudo -k
sudo -v
```

When macOS asks for the PIN, touch the fingerprint sensor. The firmware should
authorize the PIV operation and type `000000` + Enter.

## What Does Not Work

It does not unlock:

- The macOS Passwords app.
- Generic password fields in arbitrary apps.
- Websites.
- App prompts that use LocalAuthentication, Keychain UI, or Authorization
  Services without smart-card support.

PAM helps with `sudo` and other PAM-aware command-line paths. 

## Security Model

The smartcard firmware is meant to be better than typing the real password, but
it is still a prototype.

Protected against:

- Passive observation of the Mac password typed by the ESP, because the ESP does
  not type the real password.
- Generic password-field capture in normal operation, because the HID interface
  only types the dummy PIN.
- Reusing the token without a fingerprint match, assuming the sensor UART is not
  spoofed.

Not protected against yet:

- Flash dumping the ESP and extracting PIV private keys from firmware.
- Spoofing the fingerprint sensor over UART.
- A compromised Mac using the token while the user is present.
- A malicious focused field receiving `000000` if the user touches the sensor
  while the HID interface is active.

## PIV Slots

The firmware exposes:

- `9A`: PIV Authentication, RSA 2048 signing.
- `9D`: Key Management, RSA 2048 decrypt/unwrap.

macOS expects `9A` for login/authentication and may use `9D` for keychain
wrapping/unwrapping behavior.

## Build

Install ESP-IDF, then:

```sh
cd firmware/tiny_touch_smartcard
idf.py set-target esp32s3
idf.py build
```

Secrets live in:

```text
main/secrets.h
```

Start from:

```sh
cp main/secrets.example.h main/secrets.h
```

`main/secrets.h` must contain the PIV certificates and private keys for `9A`
and `9D`.

Generate test keys:

```sh
openssl req -newkey rsa:2048 -nodes -keyout piv_key_9a.pem -x509 -days 3650 -out piv_cert_9a.pem -subj "/CN=tinytouch piv auth/"
openssl req -newkey rsa:2048 -nodes -keyout piv_key_9d.pem -x509 -days 3650 -out piv_cert_9d.pem -subj "/CN=tinytouch piv key management/"
```

Then paste:

- `piv_cert_9a.pem` into `PIV_CERT_9A_PEM`
- `piv_key_9a.pem` into `PIV_PRIVATE_KEY_9A_PEM`
- `piv_cert_9d.pem` into `PIV_CERT_9D_PEM`
- `piv_key_9d.pem` into `PIV_PRIVATE_KEY_9D_PEM`

Do not commit real secrets.

## Flashing

Normal ESP-IDF flashing may work:

```sh
idf.py -p /dev/cu.usbmodem101 flash
```

On this board, chunked app flashing has been more reliable:

```sh
cd firmware/tiny_touch_smartcard
setopt NULL_GLOB
rm -f build/app_*
split -b 65536 build/tiny_touch_smartcard.bin build/app_
source /Users/xzm/esp/esp-idf/export.sh >/dev/null

for item in aa:0x10000 ab:0x20000 ac:0x30000 ad:0x40000 ae:0x50000 af:0x60000; do
  name=${item%%:*}
  off=${item##*:}
  python /Users/xzm/esp/esp-idf/components/esptool_py/esptool/esptool.py \
    --chip esp32s3 \
    -p /dev/cu.usbmodem101 \
    -b 38400 \
    --before no_reset \
    --after no_reset \
    write_flash \
    --flash_mode dio \
    --flash_size 2MB \
    --flash_freq 80m \
    "$off" "build/app_$name"
done
```

If a chunk fails, retry only the failed chunk and the chunks after it.

Bootloader mode:

1. Hold BOOT.
2. Tap RESET.
3. Release BOOT.
4. `/dev/cu.usbmodem101` should appear.

After flashing, tap RESET with BOOT released if macOS does not immediately see
the smartcard.

## macOS Pairing

After the token appears:

```sh
sc_auth identities
sudo sc_auth pair -u "$USER" -h <auth-cert-hash>
```

Useful smartcard inventory:

```sh
system_profiler SPSmartCardsDataType
```

If keychain prompts continue after fixing `9D`, unpair and re-pair so macOS can
store the wrapping state again:

```sh
sudo sc_auth unpair -u "$USER" -h <auth-cert-hash>
sudo sc_auth pair -u "$USER" -h <auth-cert-hash>
```

## `sudo` With PAM

macOS includes `pam_smartcard.so`.

The relevant line in `/etc/pam.d/sudo` is:

```text
auth       sufficient     pam_smartcard.so
```

When configured and paired, `sudo -v` can ask for the PIV PIN:

```text
Enter PIN for 'Certificate For PIV Authentication (...)':
```

Touch the sensor. The ESP types the dummy PIN and performs the fingerprint-gated
PIV operation.

Be careful editing PAM files. A bad PAM configuration can break `sudo`.

## Implementation Notes

- `usb_ccid.c` implements the TinyUSB CCID bridge and forwards APDUs to `piv.c`.
- `piv.c` implements the PIV APDU surface used by macOS.
- `fingerprint.c` implements the ZW101-style UART protocol and LED commands.
- `touch_pin_hid.c` handles HID dummy PIN submission after fingerprint match.
- The fingerprint UART is mutex-protected so background HID polling and PIV auth
  do not corrupt sensor packets.
- The HID PIN typing sends six separate `0` press/release events with short
  delays

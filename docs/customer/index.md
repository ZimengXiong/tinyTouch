# tinyTouch guide

This guide covers DIY assembly, factory flashing, setup, use, updates, and recovery.

## Build a device

Use an ESP32-S3 Super Mini or Seeed Studio XIAO ESP32-S3 with a ZW101-style UART fingerprint sensor. The sensor uses 3.3 V power and logic.

| Sensor pin | Signal | ESP32-S3 | XIAO |
|---:|---|---|---|
| 1 | VTouch | 3V3 | 3V3 |
| 2 | TouchOut | GPIO2 | D1 |
| 3 | VCC | 3V3 | 3V3 |
| 4 | TX | GPIO44, RX | D7 |
| 5 | RX | GPIO43, TX | D6 |
| 6 | GND | GND | GND |

Cross the UART pair:

```text
Sensor TX, pin 4 -> ESP GPIO44, RX
Sensor RX, pin 5 -> ESP GPIO43, TX
```

Check continuity and confirm that 3V3 and GND are not shorted before connecting USB. Download the <a href="/downloads/tinytouch-case-top.stl">case top</a> and <a href="/downloads/tinytouch-case-bottom.stl">case bottom</a> files for an enclosure.

## Flash factory firmware

Purchased devices already have firmware. Use the [Flash center](/flash) for a new DIY board. Select **Factory firmware**, connect the board, and follow the tool instructions. If the board does not enter download mode, hold **BOOT**, tap **RESET**, release **BOOT**, and try again.

## Set up the device

Install the CLI on an Apple silicon or Intel Mac:

```sh
curl -fsSL https://alpacaengineer.ing/tinytouch/batch-0/install.sh | sh
tinytouch setup
```

When prompted, choose [PIV or HID](#choose-a-mode). To choose directly:

```sh
tinytouch setup --mode piv
tinytouch setup --mode hid
```

Setup creates the device identity, enrolls four views of one finger, and configures the selected mode.

### Enroll one finger

Use the same finger for all four prompts. Place the center, left edge, right edge, and fingertip on the sensor. Lift between scans. Do not slide.

Check the result:

```sh
tinytouch status
```

The result should show a ready sensor, the selected mode, provisioned PIV keys, and four enrolled views. HID mode also shows a trusted computer and a running helper.

If setup says that a fingerprint is already enrolled, follow the [recovery procedure](/reference/recovery).

## Choose a mode

| Mode | Use | macOS authentication |
|---|---|---|
| PIV | macOS login and `sudo` | Fingerprint authorizes a private-key operation. |
| HID | Password fields that do not accept PIV | The helper sends the Keychain password after fingerprint authorization. |

Change modes without reflashing:

```sh
tinytouch mode piv
tinytouch mode hid
```

### PIV

PIV presents a USB smart card. macOS requests a private-key operation. tinyTouch types the fixed PIN `111111` into the macOS PIN prompt.

### HID

HID presents a USB keyboard. The helper reads the password from the macOS Keychain, sends one authenticated and encrypted response after a fingerprint match, and the device decrypts it in RAM and types it. Use `tinytouch keyboard-layout us` when the target field uses a fixed US layout.

## Use tinyTouch

For PIV, start macOS login or a command such as `sudo -v`, wait for the PIN prompt, and touch an enrolled finger. If macOS shows a password field instead, run `tinytouch pair`.

For HID, focus the intended password field, touch an enrolled finger, and keep focus unchanged until the password is entered. The device cannot verify which field has focus.

## Manage the device

```sh
tinytouch status --verbose
tinytouch enroll
tinytouch delete --slot 3
tinytouch delete --all
tinytouch add-computer
tinytouch computers list
tinytouch password set
```

Reset device state without changing firmware:

```sh
tinytouch factory-reset
```

## Update firmware

```sh
tinytouch update
```

Touch an enrolled finger when prompted. Keep the device connected until the command finishes. The CLI checks the signed image, writes the inactive OTA slot, checks the new image, and confirms it. An older device may need a one-time partition migration. The CLI performs it without **BOOT**.

An already-current device reports:

```text
tinyTouch is up to date.
```

## Recover the device

Recovery erases fingerprints, PIV keys, HID pairings, settings, and firmware state. Try `tinytouch status --verbose` and `tinytouch factory-reset` first. Use the [Flash center](/flash#recovery), select **Recovery firmware**, and follow the instructions.

See [CLI commands](/reference/cli) for command details.

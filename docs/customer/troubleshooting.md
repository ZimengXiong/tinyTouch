---
title: Troubleshooting
description: Diagnose USB, fingerprint sensor, PIV, HID helper, update, and flashing problems.
---

# Troubleshooting

## Device not found

1. Use a known data-capable USB cable.
2. Connect directly to the Mac.
3. Unplug and reconnect tinyTouch in normal mode, without holding **BOOT**.
4. Close Arduino, ESP-IDF monitor, `screen`, and other serial tools.
5. Look for `/dev/cu.usbmodem*`:

```sh
ls /dev/cu.usbmodem*
```

To select a device:

```sh
tinytouch status --port /dev/cu.usbmodem101
```

## `sensor` reports `offline`

Check both 3.3 V connections, ground, and UART direction. Connect sensor TX to RX and sensor RX to TX.

Reconnect the device, then run `tinytouch status`.

## Serial port busy

Close serial monitors and other tinyTouch commands. If the port remains busy, inspect the HID helper:

```sh
launchctl print "gui/$UID/com.tinytouch.helper"
tail -n 100 "$HOME/Library/Logs/tinyTouch/helper.err"
```

Rerun HID setup to repair the LaunchAgent:

```sh
tinytouch setup --mode hid --skip-enroll
```

## PIV not found

Reconnect the device, wait several seconds, then inspect the smart-card state:

```sh
system_profiler SPSmartCardsDataType
sc_auth identities
```

For an unpaired identity, run `tinytouch pair`. If no identity appears, run `tinytouch keys`, reconnect, and retry. For a PIN prompt, enter `111111`.

## HID doesn't type

Check that the helper is loaded and inspect both logs:

```sh
launchctl print "gui/$UID/com.tinytouch.helper"
tail -n 100 "$HOME/Library/Logs/tinyTouch/helper.log"
tail -n 100 "$HOME/Library/Logs/tinyTouch/helper.err"
tinytouch logs
```

To recreate credentials and the helper, run `tinytouch setup --mode hid --skip-enroll`. HID supports passwords up to 160 UTF-8 bytes and characters available in the active ASCII-capable keyboard layout.

## Flashing fails

In Chrome or Edge, close other serial tools and retry. For a connection timeout, disconnect USB, hold **BOOT** while plugging it in, then release **BOOT**.

## Report a problem

Include the board, mode, and this output:

```sh
tinytouch --version
sw_vers
tinytouch --verbose status
tinytouch logs
```

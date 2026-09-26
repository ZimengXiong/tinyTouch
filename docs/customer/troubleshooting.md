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

## Fingerprints enroll but do not trigger typing

Check the INT wire as well as UART: sensor INT must reach **GPIO2** (XIAO **D1**).
The background touch detector uses this wire. Both sensor power pins need 3.3 V.
Run `tinytouch update` for boot initialization, framing, and USB wake fixes.

A green ring alone does not prove HID pairing is complete. `tinytouch status`
must show `mode=hid` and at least one host. Finish pairing on this Mac with
`tinytouch setup --mode hid --skip-enroll` after reconnecting.

## No enrolled finger matches

`AUTH no_match` means no stored template matched, even if `sensor=ready`.
Try an enrolled finger. If none work or a new sensor contains unknown templates,
use [Recovery firmware](/customer/recovery); ordinary factory reset still requires
a matching finger. Recovery clears the sensor as well as device keys and settings.

## Wrong password or keyboard characters

Use `tinytouch password set` to replace the saved password. No factory reset is
needed. The current helper maps characters through macOS's active ASCII-capable
keyboard layout; use the same input source at setup and at the password field.
Unsupported characters are rejected and logged instead of being silently replaced.

## PIV login still asks for the login Keychain password

Update both firmware and CLI first. Current certificates distinguish the login
and key-management identities, and setup rejects an incomplete Keychain pairing.
A macOS login password and a smart-card PIN are different: do not enter `111111`
in a Keychain password prompt. If the problem persists, retain your existing
keys and collect `tinytouch --version`, `tinytouch status`, `sw_vers`, and
`sc_auth identities` for diagnosis. A repeated prompt still needs investigation
on the affected Mac; repeatedly resetting the device is not a reliable repair.

## Turn off the idle blue ring

Run `tinytouch config idle_led 0` and approve with your fingerprint. The setting
survives reconnects; green/red result feedback remains enabled. Use
`tinytouch config idle_led 1` to restore the idle light.

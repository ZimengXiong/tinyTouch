---
title: Recover tinyTouch
description: Choose between normal diagnostics, factory reset, factory flashing, and full recovery.
---

# Recover tinyTouch

Recovery erases fingerprints, device keys, registered computers, and settings.
Factory flashing alone preserves saved device state.

## Reset from the CLI

If the device responds, run:

```sh
tinytouch factory-reset
```

Approve with your fingerprint. This also removes this device's local HID credentials.
macOS smart-card pairings are preserved because the CLI cannot match a pairing
to the selected device. See [pairing cleanup](../reference/recovery.md) to remove
a stale pairing after reset.
The CLI verifies the cleared device state before removing the Mac setup.

If every enrolled finger fails with `AUTH no_match`, use browser recovery below.
Factory reset, enrollment, deletion, and normal updates require a matching
fingerprint while templates remain on the sensor.

## Recover with the browser

1. Disconnect tinyTouch.
2. Hold **BOOT** while plugging in USB, then release **BOOT**.
3. Open the [Flash center](/flash?firmware=recovery) in Chrome or Edge.
4. Select **Recovery firmware** and choose **Erase and recover**.
5. Select the ESP32-S3 download-mode port.
6. After flashing, leave the device connected for 20 seconds.
7. Unplug and reconnect it once.
8. Check the cleared state:

```sh
tinytouch status
tinytouch fingers
```

After successful recovery, `fingerprints` and `hosts` are `0`, `piv` is
`unconfigured`, and no fingerprint blocks remain.

9. Run setup:

```sh
tinytouch setup
```

If templates remain, check the sensor wiring and repeat recovery. Reflashing the
ESP32 alone does not erase templates stored in the fingerprint sensor.
Browser recovery cannot remove Mac Keychain items or smart card pairings.
See [Recovery reference](/reference/recovery) for local cleanup details.

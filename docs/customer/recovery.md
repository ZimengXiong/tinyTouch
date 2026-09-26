---
title: Recover tinyTouch
description: Choose between normal diagnostics, factory reset, factory flashing, and full recovery.
---

# Recover tinyTouch

Recovery erases fingerprints, keys, pairings, and settings.

## Reset from the CLI

If the device responds, run:

```sh
tinytouch factory-reset
```

Approve with your fingerprint. This also removes local credentials and PIV pairing.

## Recover with the browser

1. Disconnect tinyTouch.
2. Hold **BOOT** while plugging in USB, then release **BOOT**.
3. Open the [Flash center](/flash?firmware=recovery) in Chrome or Edge.
4. Select **Recovery firmware** and choose **Erase and recover**.
5. Select the ESP32-S3 download-mode port.
6. After flashing, leave the device connected for 20 seconds.
7. Unplug and reconnect it once.
8. Run setup:

```sh
tinytouch setup
```

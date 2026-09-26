---
title: Recovery reference
description: Compare factory reset, factory reflashing, recovery firmware, and their effect on state.
---

# Recovery reference

| Path | Needs normal firmware | Needs fingerprint | Rewrites firmware | Erases state |
|---|---:|---:|---:|---:|
| `tinytouch update` | Yes | Yes | OTA application only | No |
| Factory browser flash | No | No | Bootloader, partitions, app, OTA state | Normally no |
| `tinytouch factory-reset` | Yes | Yes | No | Yes |
| Recovery browser flash | No | No | Recovery image and one-time marker | Yes |

## Normal factory reset

```sh
tinytouch factory-reset
```

Use this when serial communication and fingerprint approval work. It also removes local HID credentials and PIV pairing.

## Factory browser flash

Factory flashing installs release firmware without erasing valid NVS state.

## Recovery browser flash

Recovery writes a one-time marker at `0x212000`. On boot, it:

1. deletes and verifies sensor templates;
2. erases ESP32 NVS;
3. erases the request marker.

If sensor cleanup fails, recovery preserves device state.

Recovery removes:

- fingerprint templates from the sensor;
- PIV private keys and certificates;
- HID host pairing keys;
- mode and timing settings;
- OTA and configuration state stored in NVS.

Recovery can't remove macOS Keychain items or smart-card pairing. Run setup again and manage stale pairing with `sc_auth`.

To run recovery, follow [Recover tinyTouch](/customer/recovery).

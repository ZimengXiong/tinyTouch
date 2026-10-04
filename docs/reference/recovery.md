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

Use this when serial communication and fingerprint approval work. It also removes
this device's local HID credentials. It preserves macOS smart-card pairings because
the CLI cannot match a pairing to the selected device. Inspect and remove a stale
pairing with the `sc_auth` commands below.

## Factory browser flash

Factory flashing installs release firmware without erasing valid NVS state.

## Recovery browser flash

Recovery writes a one-time marker at `0x212000`. On boot, it:

1. deletes and verifies sensor templates;
2. erases ESP32 NVS;
3. erases the request marker.

If sensor cleanup fails, recovery preserves ESP32 NVS and the request marker.
Some sensor templates may already have been deleted. Recovery retries after
reconnecting the device; it does not report completion until the sensor count is zero.

Recovery removes:

- fingerprint templates from the sensor;
- PIV private keys and certificates;
- HID host pairing keys;
- mode and timing settings;
- OTA and configuration state stored in NVS.

Recovery can't remove macOS Keychain items or smart card pairing. Run setup again and manage stale pairing with `sc_auth`.

Use `sc_auth list -u "$USER"` to inspect saved pairings. To remove a stale identity,
use `sudo sc_auth unpair -u "$USER" -h HASH`, replacing `HASH` with that identity's
hash. Check the selected identity before removing it, especially if you use other
smart cards.

To run recovery, follow [Recover tinyTouch](/customer/recovery).

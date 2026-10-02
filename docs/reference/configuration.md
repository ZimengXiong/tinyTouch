---
title: Device configuration
description: tinyTouch persistent state, defaults, limits, status fields, and local macOS files.
---

# Device configuration

tinyTouch stores schema-6 configuration in ESP32 NVS. Invalid data resets to defaults.

## Firmware defaults and limits

| Setting | Default | Valid range |
|---|---:|---:|
| Sensor LED | On | Off or on |
| Mode | PIV | PIV or HID |
| Touch-activated PIV | Off | Off or on |
| Submit Enter after HID password | On | Off or on |
| HID typing delay | 7 ms | 1–100 ms |
| Touch cooldown | 800 ms | 100–5000 ms |
| HID computers | 0 | Up to 8 |
| Fingerprint slots | 0 | Slots 1–5 |

After enrollment, protected changes require a matching fingerprint.

## Sensor LED

Run `tinytouch led off` to disable the ring, or `tinytouch led on` to restore it.
This applies to idle and authentication lighting without disabling fingerprint
sensing. The setting survives reconnects; factory reset restores it to on.
Firmware 0.1.29 adds this setting separately from the existing configuration,
so upgrading preserves fingerprints, paired computers, and timing settings.

## Password entry in PIV mode

Firmware 0.1.30 adds `tinytouch piv-touch on`. After authorizing the change,
unplug and reconnect tinyTouch. PIV mode then hides its smart-card interface
while idle, so macOS can offer password entry at the login or lock screen.

Touching the sensor exposes the card for up to 20 seconds. A matching
fingerprint is still required to use the private key. The card hides after the
login and Login Keychain operations finish transferring their responses to
macOS, or after a failed match. Login takes slightly longer because macOS must
discover the card before the device types its dummy PIN. The timeout returns to
password entry if login does not complete. Configuration commands and firmware
transfers finish before an automatic USB reconnect.

The setting is off by default, survives reconnects, and does not change HID
authentication. Run `tinytouch piv-touch off`, then unplug and reconnect, to
restore continuous PIV visibility. Factory reset restores the default. Upgrading
preserves the existing configuration and pairings.

`tinytouch pair` can temporarily expose the card for setup after fingerprint
authorization. This discovery window lasts 60 seconds; it does not grant
private-key access by itself. A Mac policy requiring smart cards still applies.

## Fingerprint profile

Setup stores four views of one finger in slots 1–4. Slot 5 is available for manual enrollment.

```sh
tinytouch enroll 5
tinytouch delete 5
```

The sensor stores templates. ESP32 NVS doesn't store fingerprint data.

## HID hosts

Firmware supports eight HID hosts. Login Keychain stores each Mac's password and pairing key.

```sh
tinytouch computers
tinytouch computers remove HOST_ID
```

Removing the last host selects PIV mode.

## PIV state

`piv=ready` means the device has a private key and certificate. Run `sc_auth identities` to check macOS pairing.
With touch activation enabled, an idle card is hidden from discovery; use
`tinytouch pair` to discover it for pairing. `piv_touch` reports the saved setting,
`piv_touch_active` reports the setting applied at boot, and `piv_visible` reports
current USB visibility.

## macOS paths

| Path | Purpose |
|---|---|
| `~/Library/LaunchAgents/com.tinytouch.helper.plist` | HID background service |
| `~/Library/Application Support/tinyTouch/` | CLI bundles, helper coordination, replay state, and per-device settings |
| `~/Library/Logs/tinyTouch/helper.log` | HID helper standard output |
| `~/Library/Logs/tinyTouch/helper.err` | HID helper diagnostics |

Login Keychain stores secrets. Run setup on each Mac.

## Reset behavior

`tinytouch factory-reset` clears device state, local HID credentials, and PIV pairing. Browser recovery clears device state without normal authorization.

See [Recovery](/reference/recovery) for a comparison of each reset path.

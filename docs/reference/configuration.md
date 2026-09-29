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
| Submit Enter after HID password | On | Off or on |
| HID typing delay | 7 ms | 1–100 ms |
| Touch cooldown | 800 ms | 100–5000 ms |
| HID computers | 0 | Up to 8 |
| Fingers | 0 | Up to 10 fixed blocks, four templates per finger |

After enrollment, protected changes require a matching fingerprint.

## Sensor LED

Run `tinytouch led off` to disable the ring, or `tinytouch led on` to restore it.
This applies to idle and authentication lighting without disabling fingerprint
sensing. The setting survives reconnects; factory reset restores it to on.
Use `tinytouch led only-auth` to disable idle blue while keeping red/green
authentication feedback (CLI and firmware 0.1.30+).
Firmware 0.1.29 adds this setting separately from the existing configuration,
so upgrading preserves fingerprints, paired computers, and timing settings.

## Fingers and existing enrollment

Each finger owns a fixed block of four templates. You enroll the whole finger
with one command; the CLI guides you through its left, right, top, and center
views. Use the same finger throughout the sequence.

```sh
tinytouch fingers
tinytouch enroll 1
tinytouch enroll 2
tinytouch delete 2
```

| Finger | Logical templates |
|---|---|
| 1 | 1–4 |
| 2 | 5–8 |
| 3 | 9–12 |
| 4 | 13–16 |
| 5 | 17–20 |
| 6 | 21–24 |
| 7 | 25–28 |
| 8 | 29–32 |
| 9 | 33–36 |
| 10 | 37–40 |

Any occupied template reserves its entire block. For example, existing templates
1–5 occupy fingers 1 and 2, leaving eight empty finger blocks. Sparse enrollment
is checked from the sensor's actual index, not inferred from its total count.
`tinytouch fingers` reports occupied and partially occupied blocks and remaining
capacity. Partial blocks are usable with their existing prints; they are never
silently filled or overwritten.

Upgrading preserves every existing template. Older independently enrolled prints
within a block can belong to different physical fingers; the CLI reports the
block as occupied without claiming those prints belong to one person or finger.
`tinytouch enroll 1` asks before replacing **all** templates in block 1 and leaves
other blocks untouched. `--replace` explicitly authorizes replacement without
that prompt. `tinytouch delete 1` removes the entire first block.

First-time setup enrolls finger 1. Rerunning setup preserves existing enrollment,
including incomplete legacy blocks. An older CLI's individual-template write
commands are rejected with an update instruction, preventing accidental changes
to a whole-finger enrollment.

Enrollment writes a persistent journal before changing its block. A failed or
cancelled enrollment removes its partial templates. If disconnected or powered
off during cleanup, the device retries cleanup on its next boot or enrollment.
Pending templates cannot authorize or unlock. Replacing a finger removes its old
prints; if replacement fails, enroll that finger again. Other blocks remain intact.

The [ZW111 specification](https://5.imimg.com/data5/SELLER/Doc/2025/5/514350720/IS/QD/SO/1833510/apt-hi-link-hlk-zw111-finger-print-module-with-cable.pdf)
lists a capacity of 40 templates. Firmware checks the reported capacity and
occupied index before writing; an unreadable or inconsistent inventory stops the
operation. Sensor addresses are zero-based: logical template 40 uses physical
address 0, preserving the existing physical addresses 1–5 while allowing all ten
blocks. HID event IDs remain 1–40.

The sensor stores templates. ESP32 NVS stores the LED preference and enrollment
journal, separately from the existing device configuration and credentials.

## HID hosts

Firmware supports eight HID hosts. Login Keychain stores each Mac's password and pairing key.

```sh
tinytouch computers
tinytouch computers remove HOST_ID
```

Removing the last host selects PIV mode.

## PIV state

`piv=ready` means the device has a private key and certificate. Run `sc_auth identities` to check macOS pairing.

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

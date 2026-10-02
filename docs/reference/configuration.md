---
title: Device configuration
description: tinyTouch persistent state, defaults, limits, status fields, and local macOS files.
---

# Device configuration

tinyTouch stores schema-6 configuration in ESP32 NVS. Invalid data resets to defaults.

## User preferences

Run `tinytouch config list` for the complete catalog, or open **Device settings**
in the interactive menu. All preferences below are available through both entry
points. `tinytouch config` displays saved values beside defaults.

| Name | Default | Values | Effect |
|---|---|---|---|
| `mode` | `piv` | `piv`, `hid` | Smart card authentication or password entry; requires reconnect |
| `led` | `on` | `on`, `off`, `only-auth` | All lighting, no lighting, or authentication feedback only |
| `typing_delay_ms` | `7` | 1–100 ms | Delay after each HID key press and release |
| `submit_enter` | `on` | `on`/`off`, `1`/`0` | Press Enter after typing the password or automatic PIV PIN |
| `touch_cooldown_ms` | `800` | 100–5000 ms | Minimum interval between touch actions |
| `led_idle_color` | `blue` | Palette below | Idle and enrollment color |
| `led_success_color` | `green` | Palette below | Fingerprint match color |
| `led_failure_color` | `red` | Palette below | Failed fingerprint match color |
| `led_idle_end_color` | `blue` | Palette below | End color for the breathing effect |
| `led_idle_effect` | `steady` | `steady`, `breathe`, `flash`, `fade-in`, `fade-out` | Idle animation |
| `led_idle_cycles` | `0` | 0–255 | 0 repeats continuously; other values limit animation repeats |
| `led_feedback_ms` | `350` | 50–2000 ms | Success and failure feedback duration; longer values delay touch processing |
| `piv_auto_type` | `on` | `on`/`off`, `1`/`0` | Automatically type the PIV PIN after a fingerprint match |

Colors and effects use the ordinary RGB `PS_ControlBLN` command documented in
the [Hi-Link sensor manual, section 3.5.7](https://r0.hlktech.com/download/HLK-ZW111/1/%E6%8C%87%E7%BA%B9%E6%A8%A1%E7%BB%84%E4%BA%A7%E5%93%81%E7%94%A8%E6%88%B7%E6%89%8B%E5%86%8C_V1.5.1.pdf).
The palette is `off`, `blue`, `green`, `cyan`, `red`, `purple`, `yellow`, `white`.
`magenta` is accepted as an alias for `purple` by `config`.

Custom LED preferences and `piv_auto_type` require firmware 0.1.31+ with
`custom_config=1`. With automatic PIV entry off, a matching fingerprint still
grants smart card presence. Enter `111111` manually if macOS requests the PIN.

Preferences apply immediately except device mode. Factory reset restores all
defaults. The original credential blob, host keys and fingerprint journal retain
their existing storage formats. New preferences use a separate, versioned NVS
blob; missing or invalid extra preferences use defaults without rewriting credentials.

## Firmware defaults and limits

| Setting | Default | Valid range |
|---|---:|---:|
| Sensor lighting | On | Off, on, or authentication feedback only |
| Mode | PIV | PIV or HID |
| Submit Enter after HID password | On | Off or on |
| HID typing delay | 7 ms | 1–100 ms |
| Touch cooldown | 800 ms | 100–5000 ms |
| HID computers | 0 | Up to 8 |
| Fingerprint blocks | 0 | Up to 10 fixed blocks, four templates per finger |

After enrollment, protected changes require a matching fingerprint.

## Sensor lighting

Run `tinytouch led off` to disable the ring, or `tinytouch led on` to restore it.
This applies to idle and authentication lighting without disabling fingerprint
sensing. The setting survives reconnects; factory reset restores it to on.
Use `tinytouch led only-auth` to disable idle lighting while keeping configured
authentication feedback (CLI and firmware 0.1.30+).
The LED setting, introduced in 0.1.29, is stored separately from other configuration,
so upgrading preserves fingerprints, paired computers, and timing settings.

```sh
tinytouch led color idle purple
tinytouch led color success cyan
tinytouch led effect breathe
tinytouch config led_idle_end_color blue
tinytouch config led_feedback_ms 200
tinytouch led preset neon
tinytouch led preview cyan --effect flash
```

Only the breathing effect uses separate start and end colors. Other effects use
the idle color for both. Repeat count is ignored for steady lighting. Presets
preserve lighting mode and feedback duration. Preview restores the saved idle state
without changing NVS.

The ordinary RGB packet does not include brightness or period controls. Extended
speed, brightness and marquee formats require sensor-specific support and are
not exposed. UART pins, baud rate, sensor address, fingerprint block size, PIV
authentication windows and protocol identifiers remain implementation parameters.

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
1–5 occupy fingers 1 and 2, leaving eight empty fingerprint blocks. Sparse enrollment
is checked from the sensor's actual index, not inferred from its total count.
`tinytouch fingers` and the interactive menu use the same **Enrolled**,
**Partially enrolled**, and **Cleanup pending** descriptions. Both show the
remaining capacity. Partial blocks are usable with their existing prints; they are never
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

## Registered HID computers

Firmware supports eight registered HID computers. The login Keychain stores
each Mac's password and pairing key.

```sh
tinytouch computers
tinytouch computers remove HOST_ID
```

Removing the last registered computer selects PIV mode.

## PIV state

`piv=ready` means the device has a private key and certificate. Run `sc_auth identities` to check macOS pairing.

## macOS paths

| Path | Purpose |
|---|---|
| `~/Library/LaunchAgents/com.tinytouch.helper.plist` | HID background service |
| `~/Library/Application Support/tinyTouch/` | CLI bundles, helper coordination, replay state, and per-device settings |
| `~/Library/Logs/tinyTouch/helper.log` | HID helper standard output |
| `~/Library/Logs/tinyTouch/helper.err` | HID helper diagnostics |

The login Keychain stores secrets. Run setup on each Mac.

## Reset behavior

`tinytouch factory-reset` clears device state, local HID credentials, and PIV pairing. Browser recovery clears device state without normal authorization.

See [Recovery](/reference/recovery) for a comparison of each reset path.

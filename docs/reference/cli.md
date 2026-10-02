---
title: CLI commands
description: Complete protocol-6 tinyTouch command reference for the current macOS CLI.
---

# CLI commands

For installed command help, run `tinytouch COMMAND --help`.

## Global options

```text
tinytouch [--verbose] COMMAND
tinytouch --version
tinytouch --help
```

| Option | Description |
|---|---|
| `-h`, `--help` | Show help |
| `--verbose` | Print subprocess, serial, and device-response diagnostics |
| `--version` | Print the installed CLI version and exit |

Global options must precede the command:

```sh
tinytouch --verbose status
```

Most commands accept `--port PATH`. Without it, the CLI finds or prompts for a device.

## `setup`

```text
tinytouch setup [--mode {hid,piv}] [--port PATH] [--skip-enroll] [--no-pair]
```

Checks the device, enrolls a fingerprint, and configures PIV or HID.

`--skip-enroll` keeps current templates. `--no-pair` applies only to PIV and leaves the identity unpaired on macOS.

## `mode`

```text
tinytouch mode {hid,piv} [--port PATH]
```

Changes mode after fingerprint approval. Reconnect the device when prompted.

## `led`

```text
tinytouch led {on,off,only-auth} [--port PATH]
```

`on` enables the sensor ring. `off` disables it, including authentication feedback.
`only-auth` disables idle blue and keeps red/green authentication feedback
(CLI and firmware 0.1.30+).

Full `off` mode also disables the sensor's automatic success/failure animations.
The first update that adds manual sensor lighting requires a physical unplug and
reconnect after the manual-mode command is saved. A software reset is insufficient.
The CLI reports the required reconnect instead of claiming the light is already off.
Older firmware can clear the LED after authentication but can still flash green;
update firmware before using full `off` mode.

`tinytouch status` reports `led_control=manual` when the migration is complete and
`led_sync=synced` when the latest LED command was acknowledged. `reconnect` means
sensor power must be cycled. Fingerprint matching remains enabled in all LED modes.

## `piv-touch`

```text
tinytouch piv-touch {on,off} [--port PATH]
```

Enables or disables touch activation for PIV mode. Requires firmware 0.1.30 or
later and fingerprint approval on an enrolled device. Unplug and reconnect to
apply the saved setting.

When enabled, the smart-card interface stays hidden until touch, keeping
password entry available while idle. A fingerprint match still authorizes PIV
authentication. Discovery adds a short login delay. The card hides after login
and Login Keychain responses reach macOS, after a failed match, or after a
20-second timeout. The default is `off`.

See [Device configuration](/reference/configuration#password-entry-in-piv-mode)
for setup behavior and visibility windows.

## `status`

```text
tinytouch status [--port PATH]
```

Prints JSON containing:

| Field | Meaning |
|---|---|
| `protocol` | Host/device protocol; the current CLI requires `6` |
| `firmware` | Firmware semantic version |
| `build` | First 12 characters of the source commit, or `development` |
| `mode` | `piv` or `hid` |
| `piv` | `ready` or `unconfigured` |
| `led` | Saved sensor LED setting: `on`/`off` (0.1.29+) or `only-auth` (0.1.30+) |
| `led_only_auth` | `1` when authentication-only lighting is supported (firmware 0.1.30+) |
| `piv_touch` | Saved touch activation preference: `on` or `off` (firmware 0.1.30+) |
| `piv_touch_active` | Touch activation setting applied at boot: `on` or `off` |
| `piv_delay_ms` | Saved delay before PIN entry after PIV selection and USB/HID readiness, in milliseconds |
| `piv_visible` | Whether USB currently exposes the smart-card interface: `yes` or `no` |
| `sensor` | `ready` or `offline` after a live UART probe |
| `fingerprints` | Raw template count, retained for compatibility; use `tinytouch fingers` for finger blocks |
| `finger_groups` | `1` when whole-finger commands are supported (firmware 0.1.29+) |
| `hosts` | Number of registered HID computers |
| `ota` | `idle`, `writing`, or `staged` |

## `test`

```text
tinytouch test [--port PATH]
```

Checks serial communication and device status.

## `logs`

```text
tinytouch logs [--port PATH]
```

Prints up to 32 recent touch and HID events.

## `enroll`

```text
tinytouch enroll FINGER [--replace] [--port PATH]
```

Enrolls finger `1` through `10` through the complete four-view sequence. Use the
same finger for every view and lift it when prompted. Each view is scanned twice.

```sh
tinytouch enroll 1
tinytouch enroll 2
tinytouch enroll 3 --port /dev/cu.usbmodem101
```

## `fingers`

```text
tinytouch fingers [--port PATH]
```

Lists occupied and partially occupied finger blocks and space for additional
fingers. With existing templates 1–5, fingers 1 and 2 are occupied and eight blocks
remain available. No changes or fingerprint authorization are needed to list them.

## `delete`

```text
tinytouch delete FINGER [--port PATH]
```

Deletes the **entire** block for finger `1` through `10` after fingerprint approval.
For example, `tinytouch delete 2` deletes templates 5–8, including any legacy print
in that block. It does not delete other fingers. To delete all state, use
`factory-reset`.

See [Device configuration](/reference/configuration#fingers-and-existing-enrollment)
for the complete mapping and interrupted-enrollment behavior.

## `computers`

```text
tinytouch computers [list] [--port PATH]
tinytouch computers remove HOST_ID [--port PATH]
```

Lists or removes up to eight HID hosts. Removing the final host selects PIV mode. To add a host, run HID setup on that Mac.

## `keys`

```text
tinytouch keys [--port PATH]
```

Creates a PIV identity after fingerprint approval.

## `pair`

```text
tinytouch pair [--port PATH]
```

Pairs the PIV identity with the current macOS user. Requires administrator and fingerprint approval.
When touch activation is enabled, this command temporarily exposes the PIV
identity for discovery and renews the setup window before pairing.

## `update`

```text
tinytouch update [--port PATH]
```

Updates the CLI, HID helper, and firmware. Reconnect the device after staging.

## `factory-reset`

```text
tinytouch factory-reset [--port PATH]
```

Removes fingerprints, keys, hosts, settings, local HID credentials, and PIV pairing. Requires confirmation and a fingerprint.

## `rom` / `bootloader`

```text
tinytouch rom [--port PATH]
tinytouch bootloader [--port PATH]
```

Prints ROM-mode instructions. Flash with the [Flash center](/flash) or ESP-IDF.

## `config`

```text
tinytouch config NAME [VALUE] [--port PATH]
```

Without a value, prints status. With a value, writes a protected setting:

| Name | Range | Default | Effect |
|---|---:|---:|---|
| `typing_delay_ms` | 1–100 | 7 | Delay after HID key press and release |
| `piv_delay_ms` | 0–5000 | 25 | Delay before touch-login PIN entry after PIV selection and USB/HID readiness |
| `submit_enter` | 0 or 1 | 1 | Type Enter after the HID password |
| `touch_cooldown_ms` | 100–5000 | 800 | Minimum interval between touch actions |

`STATUS` reports `piv_delay_ms`, so the CLI verifies that setting after saving it.
It applies to the next touch login without reconnecting. The other settings in
this table are not returned by `STATUS`; their writes can report a verification error.

## Developer commands

| Command | Action |
|---|---|
| `tinytouch hid-smoke` | Test HID setup against a simulated device on macOS |
| `tinytouch enroll-demo` | Preview enrollment in an interactive terminal |

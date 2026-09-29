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
tinytouch led {on,off} [--port PATH]
```

Turns the sensor ring on or off, including authentication feedback. The setting
is saved on the device and survives reconnects. Fingerprint sensing stays active.
An enrolled device asks for a matching fingerprint before changing the setting.

To upgrade from an older release, run `tinytouch update`, unplug and reconnect
once when prompted, then run `tinytouch led off`. Use `tinytouch led on` to restore
the default lighting.

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
| `led` | Saved sensor LED setting: `on` or `off` (firmware 0.1.29+) |
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

Finger 1 owns templates 1–4, finger 2 owns 5–8, and so on. Any existing print in
a block makes it occupied. Occupied blocks require confirmation before replacement;
`--replace` skips that confirmation. Existing templates in other blocks are preserved.
After a failed replacement, the replaced finger may need to be enrolled again.
An enrolled device requires a matching fingerprint before enrollment starts.

First-time setup enrolls finger 1. Rerunning setup keeps existing prints. Update
both CLI and firmware to 0.1.29 or later before using whole-finger commands.

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
| `submit_enter` | 0 or 1 | 1 | Type Enter after the HID password |
| `touch_cooldown_ms` | 100–5000 | 800 | Minimum interval between touch actions |

`STATUS` doesn't return these values. A successful write can still report a verification error.

## Developer commands

| Command | Action |
|---|---|
| `tinytouch hid-smoke` | Test HID setup against a simulated device on macOS |
| `tinytouch enroll-demo` | Preview enrollment in an interactive terminal |

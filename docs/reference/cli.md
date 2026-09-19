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
| `sensor` | `ready` or `offline` after a live UART probe |
| `fingerprints` | Number of occupied sensor slots |
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
tinytouch enroll SLOT [--port PATH]
```

Enrolls slot `1` through `5`. Existing templates require fingerprint approval.

Examples:

```sh
tinytouch enroll 5
tinytouch enroll 3 --port /dev/cu.usbmodem101
```

Setup uses slots 1–4.

## `delete`

```text
tinytouch delete SLOT [--port PATH]
```

Deletes slot `1` through `5` after fingerprint approval. To delete all state, use `factory-reset`.

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

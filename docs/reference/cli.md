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

Enrolls one template in slot `1` through `5`, using two taps of the same finger.
The slot number is a storage location, not a finger view. An occupied slot is
replaced; other slots are kept. Existing templates require fingerprint approval.

Examples:

```sh
tinytouch enroll 5
tinytouch enroll 3 --port /dev/cu.usbmodem101
```

First setup uses slots 1–4 for four views of one finger. To add a different
finger without replacing those views, use `tinytouch enroll 5`. Setup preserves
existing templates, including profiles with a fifth finger.

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
| `idle_led` | 0 or 1 | 1 | Idle blue ring; result feedback remains enabled |
| `typing_delay_ms` | 1–100 | 7 | Delay after HID key press and release |
| `submit_enter` | 0 or 1 | 1 | Type Enter after the HID password |
| `touch_cooldown_ms` | 100–5000 | 800 | Minimum interval between touch actions |

`STATUS` returns `type_delay`, `submit_enter`, and `cooldown` for live verification.

## Developer commands

| Command | Action |
|---|---|
| `tinytouch hid-smoke` | Test HID setup against a simulated device on macOS |
| `tinytouch enroll-demo` | Preview enrollment in an interactive terminal |

## `password`

```sh
tinytouch password set
tinytouch password set --slot 5
tinytouch password list
tinytouch password remove --slot 5
```

Changes this Mac's Keychain credentials without resetting fingerprints or pairing.
The default password applies to every slot without an override. `list` shows only
configured slots; it never prints passwords. Removing an override restores the
default for that slot. Select a device with `--port PATH` when needed.

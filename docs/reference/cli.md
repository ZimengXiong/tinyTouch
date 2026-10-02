---
title: CLI commands
description: Complete protocol-6 tinyTouch command reference for the current macOS CLI.
---

# CLI commands

Run `tinytouch` in Terminal to open the interactive menu. For installed command
help, run `tinytouch COMMAND --help`.

## Interactive menu

```sh
tinytouch
tinytouch menu
tinytouch --port /dev/cu.usbmodem101
```

The menu provides setup, fingerprint management, device settings, registered
computers, status, connection testing, updates, and diagnostics or recovery.
Select an option by number or name. Use `0` to return from a submenu or close the
main menu. No device connection is required to open the menu.

Fingerprint actions display the live inventory before selection. Replacement
uses the existing enrollment confirmation. Deletion and computer removal require
confirmation. Device errors return to the current menu. Ctrl-C cancels the current
operation or closes the current menu; Ctrl-D closes the session.

Menu actions use the same handlers as explicit commands. Explicit commands retain
their arguments, output formats, and exit codes. Without an interactive terminal,
running `tinytouch` prints help and exits without prompting or accessing a device.

## Prompts and results

Setup and the interactive menu use the same HID and PIV mode descriptions.
Options with descriptions use parentheses. Input prompts end with a colon;
confirmation prompts use `[y/N]`, with **No** as the default.

After enrollment, protected changes require fingerprint approval. For example,
a settings change prompts:

```text
Touch the sensor with an enrolled finger to change device settings.
```

Keep your finger off the sensor until the touch prompt. Then touch once and hold
it still. Lift your finger when prompted. An error states the problem and, when
available, the next action. Settings messages distinguish a saved change from a
preview that leaves saved settings unchanged.

## Global options

```text
tinytouch [--verbose] [--port PATH] [COMMAND]
tinytouch --version
tinytouch --help
```

| Option | Description |
|---|---|
| `-h`, `--help` | Show the help text and exit the CLI |
| `--verbose` | Show command and device diagnostics |
| `--port PATH` | Select a serial device for commands or the interactive menu |
| `--version` | Show the CLI version and exit the CLI |

Global options must precede the command:

```sh
tinytouch --verbose status
```

Most commands accept `--port PATH`. Without it, the CLI finds or prompts for a device.
Command-specific `--port` overrides the global selection.

Use `tinytouch help COMMAND` as an alternative to `tinytouch COMMAND --help`.
Invalid arguments show the relevant help command. Runtime errors are written to
standard error. Explicit commands use exit codes `1` for operation errors,
`2` for argument errors, and `130` for Ctrl-C.

## `ports`

```text
tinytouch ports [--json]
```

Lists available USB serial paths without opening a device connection. Select a path with
`tinytouch --port PATH`. If no paths appear, connect tinyTouch with a USB cable that supports data and retry.

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
tinytouch led [show] [--port PATH]
tinytouch led {on,off,only-auth} [--port PATH]
tinytouch led color {idle,success,failure,end} COLOR [--port PATH]
tinytouch led effect EFFECT [--port PATH]
tinytouch led preset {default,ocean,neon,sunset} [--port PATH]
tinytouch led preview COLOR [--effect EFFECT] [--duration-ms MS] [--port PATH]
```

Without an action, shows current lighting settings and defaults.
`on` enables the sensor ring. `off` disables it, including authentication feedback.
`only-auth` disables idle lighting and keeps configured authentication feedback
(CLI and firmware 0.1.30+).

Custom colors, effects, presets and previews require firmware from this branch
(0.1.31+, `custom_config=1`). Older firmware still supports its existing lighting modes.

Colors: `off`, `blue`, `green`, `cyan`, `red`, `purple`, `yellow`, `white`.
Effects: `steady`, `breathe`, `flash`, `fade-in`, `fade-out`.
Success and failure feedback use a steady color. Idle effects resume afterward.

```sh
tinytouch led color idle purple
tinytouch led color success cyan
tinytouch led effect breathe
tinytouch config led_idle_end_color blue
tinytouch config led_idle_cycles 0
tinytouch led preset ocean
tinytouch led preview purple --effect breathe --duration-ms 2000
```

Presets set idle, breathing end, success and failure colors, idle effect, and
continuous animation. They preserve lighting mode and feedback duration. Preset writes
run in one authorized session; if a write fails, the CLI lists fields acknowledged
before the failure. Read `tinytouch led` to inspect the saved state.

Preview requires fingerprint approval, displays a temporary effect for 100–5000 ms
(default: 1500), and restores the saved idle lighting. It does not save preferences.
An explicit preview can illuminate the ring even when saved lighting mode is `off`.

The standard sensor command uses fixed RGB combinations. Arbitrary hex colors,
brightness, animation speed and marquee effects are not exposed because they
require different sensor variants or command formats.

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
| `led` | Saved sensor lighting setting: `on`/`off` (0.1.29+) or `only-auth` (0.1.30+) |
| `led_only_auth` | `1` when authentication-only lighting is supported (firmware 0.1.30+) |
| `sensor` | `ready` or `offline` after a live UART probe |
| `fingerprints` | Raw template count, retained for compatibility; use `tinytouch fingers` for fingerprint blocks |
| `finger_groups` | `1` when whole-finger commands are supported (firmware 0.1.29+) |
| `config_values` | `1` when saved settings are returned for readback verification (0.1.31+) |
| `custom_config` | `1` when custom LED preferences, previews and automatic PIV entry control are supported (0.1.31+) |
| `hosts` | Number of registered HID computers |
| `ota` | `idle`, `writing`, or `staged` |

Firmware 0.1.31+ also reports all preference fields in the [configuration table](/reference/configuration#user-preferences).
LED colors and effects are numeric in raw status; `tinytouch config` converts them
to names.

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

Lists fingerprint blocks and space for additional fingers. The command and
interactive menu use the same status descriptions: **Enrolled**, **Partially
enrolled**, and **Cleanup pending**. Partial enrollment keeps the entire block
reserved. With existing templates 1–5, fingers 1 and 2 are occupied and eight blocks
remain available. No changes or fingerprint approval are needed to list them.

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

Lists or removes up to eight registered HID computers. Removing the last computer
selects PIV mode. To add a computer, run HID setup on that Mac.

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

Clears fingerprints, keys, registered computers, device settings, local HID
credentials, and PIV pairing. Confirm the reset and approve it with an enrolled
fingerprint. In **Diagnostics and recovery**, select:

```text
  7. Factory reset (Clear device and local configuration) [factory-reset]
```

## `rom` / `bootloader`

```text
tinytouch rom [--port PATH]
tinytouch bootloader [--port PATH]
```

Prints ROM-mode instructions. Flash with the [Flash center](/flash) or ESP-IDF.

## `config`

```text
tinytouch config [show] [--json] [--port PATH]
tinytouch config list [--json]
tinytouch config NAME [VALUE] [--json] [--port PATH]
tinytouch settings ...
```

`settings` is an alias for `config`. Without a name, shows current settings and
defaults. `list` shows every setting, value range, default and effect without a
device connection. With a name, reads that saved setting; with a value, writes
it after fingerprint approval. `--json` applies to reads and `list`.

```sh
tinytouch config
tinytouch config list
tinytouch config --json
tinytouch config typing_delay_ms
tinytouch config typing_delay_ms 7
tinytouch config submit_enter off
tinytouch config touch_cooldown_ms 800
tinytouch config led_feedback_ms 200
tinytouch config piv_auto_type off
```

Use `on`/`off` or `1`/`0` for Boolean settings. Setting names also accept hyphens
in place of underscores. `TYPE_DELAY`, `COOLDOWN` and `led_mode` remain accepted
aliases. Unknown names and invalid values are rejected before device access.

See [User preferences](/reference/configuration#user-preferences) for the complete
table, or run `tinytouch config --help`.

Current firmware reports values and the CLI verifies writes against live status.
Older firmware can still accept typing delay, Enter and cooldown writes; these
use the device's `SET` acknowledgement. Unavailable reads display `not reported`
or an update instruction. The CLI does not substitute defaults for missing values.

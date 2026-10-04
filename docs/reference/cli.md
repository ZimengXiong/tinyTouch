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

The main menu contains **Setup**, **Enroll**, **Update**, **Status**, and
**Advanced**. Use the up and down arrows to navigate, Enter to select, and Esc to
go back. Advanced contains settings, fingerprint and computer management, full
status, diagnostics, and service removal. The CLI exits after an action.
No device connection is required to open the menu.

Fingerprint actions display the live inventory before selection. Replacement
uses the existing enrollment confirmation. Deletion and computer removal require
confirmation. Device errors exit the CLI. Ctrl-C cancels the session; Ctrl-D closes
the menu. Terminals without arrow support accept options by number or name.

Menu actions use the same handlers as explicit commands. Explicit commands retain
their arguments, output formats, and exit codes. Without an interactive terminal,
running `tinytouch` prints help and exits without prompting or accessing a device.

## Prompts and results

Setup and the interactive menu use the same HID and PIV mode descriptions.
Input prompts end with a colon;
confirmation prompts use `[y/N]`, with **No** as the default.

After enrollment, protected changes require fingerprint approval. For example,
a settings change prompts:

```text
Touch the device with a registered finger to unlock configuration.
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

## `password`

```text
tinytouch password [--finger {1..10}] [--port PATH]
```

Changes the password typed by HID on this Mac. Enter the new password twice in
the masked terminal prompt. The command preserves fingerprints and HID pairing.
Run HID setup first if this Mac has no saved pairing.

Without `--finger`, changes the default password used by fingers without an
override. With `--finger`, assigns one password to all four enrolled views of
that finger. The finger number is the same number used by `enroll` and `fingers`.
Passwords are stored in macOS Keychain. The command restarts the existing helper
to reload the saved value.

The password must fit the selected layout, use at most 160 UTF-8 bytes, and
require at most 160 typed keys. A dead-key sequence counts as two typed keys.
Control characters are unsupported. A failed Keychain update keeps the previous
password; unlock the login Keychain or run `tinytouch repair` before retrying.

## `keyboard-layout`

```text
tinytouch keyboard-layout [auto|us] [--port PATH]
```

Without a value, shows the saved HID layout setting for this device on this Mac.
`auto` is the default and translates passwords using the active macOS
ASCII-capable layout, including French AZERTY and Croatian QWERTZ. Select your
intended layout in macOS before touching the device. Characters requiring Option
or more than one dead key are unsupported.

Use `us` to send US key positions directly. This can preserve an existing manual
character mapping. To replace a manually mapped password with your actual
password, select the intended macOS layout, run `tinytouch keyboard-layout auto`,
then run `tinytouch password`.

Changes apply to this device on this Mac. The command restarts the existing
helper to reload its settings.

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

Custom colors, effects, presets and previews are included in firmware 0.1.34
(`custom_config=1`). Older firmware still supports its existing lighting modes.

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

Enables or disables touch activation for PIV mode. Requires firmware 0.1.34 or
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
tinytouch status [--summary | --details] [--port PATH]
```

The default output is JSON. `--summary` shows a brief overview; `--details` shows
all fields with readable labels. The menu uses the summary, with full status under
Advanced.

JSON contains:

| Field | Meaning |
|---|---|
| `protocol` | Host/device protocol; the current CLI requires `6` |
| `firmware` | Firmware semantic version |
| `build` | First 12 characters of the source commit, or `development` |
| `mode` | `piv` or `hid` |
| `piv` | `ready` or `unconfigured` |
| `led` | Saved sensor lighting setting: `on`/`off` (0.1.29+) or `only-auth` (0.1.30+) |
| `led_only_auth` | `1` when authentication-only lighting is supported (firmware 0.1.30+) |
| `piv_touch` | Saved touch activation preference: `on` or `off` (firmware 0.1.30+) |
| `piv_touch_active` | Touch activation setting applied at boot: `on` or `off` |
| `piv_delay_ms` | Saved delay before PIN entry after PIV selection and USB/HID readiness, in milliseconds |
| `piv_visible` | Whether USB currently exposes the smart-card interface: `yes` or `no` |
| `sensor` | `ready` or `offline` after a live UART probe |
| `fingerprints` | Raw template count, retained for compatibility; use `tinytouch fingers` for fingerprint blocks |
| `finger_groups` | `1` when whole-finger commands are supported (firmware 0.1.29+) |
| `config_values` | `1` when saved settings are returned for readback verification (0.1.34-dev.1+) |
| `custom_config` | `1` when custom LED preferences, previews and automatic PIV entry control are supported (0.1.34-dev.1+) |
| `hosts` | Number of registered HID computers |
| `ota` | `idle`, `writing`, or `staged` |

Firmware 0.1.34 also reports all preference fields in the [configuration table](/reference/configuration#user-preferences).
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
On an enrolled device, approve with an existing finger before scanning the new
finger. Occupied blocks require replacement confirmation; `--replace` skips that
confirmation. Fingerprint approval still applies.

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
On a smaller sensor, deletion also removes legacy prints in a block that has
too few slots for a new four-view enrollment.

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
When touch activation is enabled, this command temporarily exposes the PIV
identity for discovery and renews the setup window before pairing.
If macOS cannot enable automatic Keychain unlock, the CLI preserves the verified
pairing and reports that the next PIV login needs your Mac password for Keychain.

## `uninstall`

```sh
tinytouch uninstall
```

Stops and removes the background service. The CLI, saved credentials, and device
settings stay installed. This action is also available under **Advanced**.

## `repair`

```text
tinytouch repair [--port PATH]
```

Repairs saved HID credential access and reinstalls the helper from the signed
standalone CLI. Without `--port`, checks saved devices even when tinyTouch is
disconnected. With `--port`, checks the selected device. Enter your login Keychain
password and approve macOS Keychain dialogs when prompted. Saved credentials
remain in place. If credentials are missing, run HID setup.

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
credentials. macOS smart-card pairings are preserved. Confirm the reset and approve it with an enrolled
fingerprint. In **Advanced → Diagnostics**, select **Factory reset**.
The CLI checks the cleared device state before removing the Mac setup. If no
enrolled finger matches, follow [Recover tinyTouch](/customer/recovery).

## `rom` / `bootloader`

```text
tinytouch rom [--port PATH]
tinytouch bootloader [--port PATH]
```

Prints ROM-mode instructions. Flash with the [Flash center](/flash) or ESP-IDF.

### Development release updates

Development releases are opt-in and do not replace the stable update channel.
Install the CLI from the selected prerelease's `install.sh` using its
`TINYTOUCH_RELEASE_ROOT`, then stage its matching firmware explicitly:

```sh
tinytouch update --release-version 0.1.34-dev.1
```

Unplug and reconnect after staging. An ordinary `tinytouch update` selects the
latest stable release.

## `config`

```text
tinytouch config [show] [--json] [--port PATH]
tinytouch config list [--json]
tinytouch config NAME [VALUE] [--json] [--port PATH]
tinytouch settings ...
```

`settings` is an alias for `config`. Without a name, shows current settings.
`list` shows every setting, value range, default and effect without a
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
| Name | Range | Default | Effect |
|---|---:|---:|---|
| `typing_delay_ms` | 1–100 | 1 | Delay after HID key press and release |
| `piv_delay_ms` | 0–5000 | 50 | Delay before touch-login PIN entry after PIV selection and USB/HID readiness |
| `submit_enter` | 0 or 1 | 1 | Type Enter after the HID password |
| `touch_cooldown_ms` | 100–5000 | 800 | Minimum interval between touch actions |

`STATUS` reports `piv_delay_ms`, so the CLI verifies that setting after saving it.
It applies to the next touch login without reconnecting. The other settings are also returned by `STATUS` and verified after saving.

## Developer commands

| Command | Action |
|---|---|
| `tinytouch hid-smoke` | Test serial communication and encrypted helper replies against a simulated device on macOS; saved passwords and the installed service are preserved |
| `tinytouch enroll-demo` | Preview enrollment in an interactive terminal |

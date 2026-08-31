# CLI commands

Use `tinytouch` to set up the device, manage macOS integration, and install signed updates. Run `tinytouch <command> --help` for command-specific options.

## Common options

| Option | Use |
|---|---|
| `--port /dev/cu.usbmodem...` | Select one device when more than one serial port is present. |
| `--verbose` | Show serial messages, shell commands, and diagnostic details. |
| `--help` | Show command syntax without changing state. |

You can place `--verbose` before or after the command.

## Setup and modes

### `setup`

Set up your device with ease. This command provisions keys, enrolls your fingerprint profile, configures the selected mode, and installs the required macOS integration.

```sh
tinytouch setup
tinytouch setup --mode piv
tinytouch setup --mode hid --skip-enroll
tinytouch setup --mode piv --no-pair
```

Use `--skip-enroll` to preserve your existing sensor templates. The `--no-pair` option configures PIV without pairing the current macOS account.

### `mode`

Switch your unified firmware between PIV and HID modes.

```sh
tinytouch mode piv
tinytouch mode hid
```

This command adds any missing host configuration for the target mode. It handles the transition without reflashing your firmware.

### `add-computer`

Bring your current Mac into the device's active mode.

```sh
tinytouch add-computer
```

In PIV mode, the CLI pairs your smart-card identity with the current account. In HID mode, it adds a computer-specific pairing key and stores the matching key and password in this Mac user's Keychain.

## Status and tests

### `status`

Check your device health. This shows the CLI version, firmware, sensor status, fingerprint count, keys, active mode, and host integration.

```sh
tinytouch status
tinytouch status --verbose
```

Start your diagnosis with this safe command. It only reads information and never changes your device state.

### `test`

Ping the firmware and verify the active mode's host path.

```sh
tinytouch test
```

In HID mode, this test types your Keychain password. Please open a safe text field and close any unrelated prompts before running it.

### `logs`

View recent HID helper output to understand background activity.

```sh
tinytouch logs
tinytouch logs --lines 100
```

## Fingerprints

### `enroll`

Register your fingerprint. This command replaces slots 1 through 4 with four orientations of one finger for optimal reliability.

```sh
tinytouch enroll
```

For advanced diagnosis, you can replace a specific slot:

```sh
tinytouch enroll --slot 3
```

### `delete`

Remove one slot or clear all sensor templates.

```sh
tinytouch delete --slot 3
tinytouch delete --all
tinytouch delete --all --yes
```

## PIV identity

### `pair`

Connect your macOS account with the device's PIV authentication certificate.

```sh
tinytouch pair
tinytouch pair --identity <40-character-hash>
```

Add `--identity` when `sc_auth identities` lists multiple candidates.

### `keys`

Create and install a fresh PIV authentication and key-management identity.

```sh
tinytouch keys
tinytouch keys --yes
```

Rotating your keys invalidates previous pairings that refer to the old certificate.

## HID computers

### `computers`

Manage your trusted HID computers.

```sh
tinytouch computers list
tinytouch computers remove 0123456789abcdef
```

The firmware stores up to eight pairing keys. When you remove the current Mac, the command also cleans up the local pairing key.

### `password`

Replace or inspect the HID password stored in this Mac user's Keychain:

```sh
tinytouch password set
tinytouch password list
tinytouch password set --fingerprint 5
tinytouch password remove --fingerprint 5
```

Use a fingerprint override only for a separately enrolled slot. Guided enrollment puts four views of the same finger in slots 1–4.

### `keyboard-layout`

Map HID passwords to the current macOS input source, or force US:

```sh
tinytouch keyboard-layout auto
tinytouch keyboard-layout us
```

Automatic mapping refuses the complete request when a character requires an unsupported dead-key or Option sequence.

### `config`

Show or change device-side HID behavior:

```sh
tinytouch config show
tinytouch config set typing-delay 12
tinytouch config set enter off
tinytouch config set cooldown 1000
```

Typing delay is 1–100 ms. Cooldown is 100–5000 ms. `enter off` applies to HID passwords, not the PIV PIN.

## Firmware and reset

### `update`

Keep your system current. Update the CLI and device from your configured release channel.

```sh
tinytouch update
```

This command requires an enrolled fingerprint for your security. It skips the firmware write when the version and build ID already match.

### `factory-reset`

Start fresh. This safely erases keys, pairings, settings, and sensor templates while preserving your current firmware.

```sh
tinytouch factory-reset
tinytouch factory-reset --yes
```

### `bootloader`

Restart the device in ESP32-S3 download mode after a fingerprint authorization.

```sh
tinytouch bootloader
```

This command supports firmware development. Customer updates call the required transition automatically.

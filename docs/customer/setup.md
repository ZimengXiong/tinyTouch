# Setup

On an Apple silicon or Intel Mac:

```sh
curl -fsSL https://alpacaengineer.ing/tinytouch/batch-0/install.sh | sh
tinytouch setup
```

Follow the CLI instructions.

## Modes

### PIV

PIV presents a USB smart card for macOS login and `sudo`.

### HID

HID presents a USB keyboard and types a Keychain password after a fingerprint match.

Check the device:

```sh
tinytouch status
```

Change modes without reflashing:

```sh
tinytouch mode piv
tinytouch mode hid
```

If setup reports an existing fingerprint, use [Recovery](./recovery).

# Setup

On an Apple silicon or Intel Mac:

```sh
curl -fsSL https://alpacaengineer.ing/tinytouch/batch-0/install.sh | sh
tinytouch setup
```

Choose PIV or HID when prompted.

Use one finger for all four scans. Scan the center, left edge, right edge, and fingertip. Lift between scans.

Check the device:

```sh
tinytouch status
```

PIV presents a USB smart card. HID presents a USB keyboard and types a Keychain password after a fingerprint match.

Change modes without reflashing:

```sh
tinytouch mode piv
tinytouch mode hid
```

If setup reports an existing fingerprint, use [Recovery](./recovery).

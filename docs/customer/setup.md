# Setup

On an Apple silicon or Intel Mac:

## 1. Open the Terminal app

## 2. Install

```sh
curl -fsSL https://github.com/ZimengXiong/tinyTouch/releases/latest/download/install.sh | sh
```

## 3. Set up

```sh
tinytouch setup
```

Follow the CLI instructions. Run `tinytouch setup` again any time to edit your configuration.

## Modes

### PIV

PIV presents a USB smart card for macOS login and `sudo`.

### HID

HID presents a USB keyboard and types a Keychain password after a fingerprint match.

If setup reports an existing fingerprint, use [Recovery](./recovery) to erase and reinstall the factory image.

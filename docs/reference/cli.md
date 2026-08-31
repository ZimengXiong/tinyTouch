# CLI commands

Run `tinytouch <command> --help` for command help.

## Options

```text
--port /dev/cu.usbmodem...   Select a device.
--verbose                   Show diagnostic output.
```

## Setup

```sh
tinytouch setup
tinytouch mode piv
tinytouch mode hid
tinytouch add-computer
```

## Status

```sh
tinytouch status
tinytouch status --verbose
tinytouch test
tinytouch logs
tinytouch logs --lines 100
```

## Fingerprints

```sh
tinytouch enroll
tinytouch enroll --slot 3
tinytouch delete --slot 3
tinytouch delete --all
```

## PIV

```sh
tinytouch pair
tinytouch pair --identity <40-character-hash>
tinytouch keys
```

## HID

```sh
tinytouch computers list
tinytouch computers remove <computer-id>
tinytouch password set
tinytouch password list
tinytouch password remove --fingerprint 5
tinytouch keyboard-layout auto
tinytouch keyboard-layout us
tinytouch config show
tinytouch config set typing-delay 12
tinytouch config set enter off
tinytouch config set cooldown 1000
```

## Firmware

```sh
tinytouch update
tinytouch factory-reset
```

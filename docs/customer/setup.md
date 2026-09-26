---
title: Set up tinyTouch
description: Install the CLI and enroll your fingerprint on macOS.
prev:
  text: Install firmware
  link: /flash
next: false
---

# Set up tinyTouch

Connect tinyTouch to your Mac.

## 1. Install the CLI

Open **Terminal** and run:

```sh
curl -fsSL https://github.com/ZimengXiong/tinyTouch/releases/latest/download/install.sh | sh
```

## 2. Enroll your fingerprint

```sh
tinytouch setup
```

Follow the prompts. Choose PIV for smart-card login or HID for password entry. Lift your finger between scans.

For PIV, enter `111111` if macOS asks for a smart-card PIN. For HID, enter your Mac login password when prompted.

## 3. Try it

Lock your Mac, then touch the sensor to sign in. In HID mode, select the password field first.

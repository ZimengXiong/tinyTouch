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
tinytouch
```

Select **Set up this Mac** and follow the prompts. Choose PIV for smart card login
or HID for password entry. Lift your finger between scans. You can also start
setup directly with `tinytouch setup`.

For PIV, enter `111111` if macOS prompts for the smart card PIN. For HID, enter
your Mac password when prompted. Your typing is hidden; no characters appear
while you type.

## 3. Try it

Lock your Mac, then touch the sensor to sign in. In HID mode, select the password field first.

## 4. Change device settings

Run `tinytouch` and select **Device settings**. Select **Sensor colors and effects**
to change the sensor ring colors, animation, or preset. You can also use commands:

```sh
tinytouch config
tinytouch led preset ocean
```

After enrollment, touch an enrolled finger when the CLI requests approval.
See [Device configuration](/reference/configuration) for settings and limits.

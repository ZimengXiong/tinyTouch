---
title: Update tinyTouch
description: Update the CLI, helper, and firmware.
---

# Update tinyTouch

Updates preserve fingerprints, identities, HID computers, and settings.

Connect tinyTouch and run:

```sh
tinytouch update
```

Approve with your fingerprint. When prompted, reconnect the device and check the version:

```sh
tinytouch status
```

The `ota` field returns to `idle` after a successful boot.

## LED control and whole-finger enrollment

Version 0.1.29 adds LED control and up to ten fingers. After `tinytouch update`
and the requested unplug/reconnect:

```sh
tinytouch led off
tinytouch fingers
tinytouch enroll 2
```

Use an empty finger number to add a finger. An occupied number asks before replacing
its entire block. Existing prints are preserved by the update, including legacy
partial blocks. `tinytouch led on` restores the default lighting.

## If an update is interrupted

Reconnect the device and run `tinytouch update` again.

If the serial interface is unavailable, reinstall **Factory firmware** from the [Flash center](/flash). Use **Recovery firmware** only to erase state.

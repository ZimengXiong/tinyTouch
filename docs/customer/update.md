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

## Upgrade an older CLI

CLIs such as `0.1.25-prod` and `0.1.26-prod` expect the old `-prod` release format.
If `tinytouch update` reports an invalid production version, install the current CLI:

```sh
curl -fsSL https://github.com/ZimengXiong/tinyTouch/releases/latest/download/install.sh | sh
```

## If an update is interrupted

Reconnect the device and run `tinytouch update` again.

If the serial interface is unavailable, reinstall **Factory firmware** from the [Flash center](/flash). Use **Recovery firmware** only to erase state.

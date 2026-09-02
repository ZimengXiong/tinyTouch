# Recovery

Recovery erases fingerprints, keys, pairings, settings, and firmware state. Try this first:

```sh
tinytouch status --verbose
tinytouch factory-reset
```

## Procedure

You need Chrome or Edge, a USB data cable, and access to **BOOT** and **RESET**.

1. Disconnect tinyTouch.
2. Hold **BOOT** while reconnecting it. Or hold **BOOT**, tap **RESET**, and release **BOOT**.
3. Open the [Flash center](/flash) and choose **Recovery**. Recovery erases flash and writes the signed factory image.
4. Select the ESP32-S3 download-mode port.
5. Wait for the erase and flash operation to finish.
6. Unplug and reconnect the device.
7. Wait 20 seconds, then run `tinytouch setup`.

Use `tinytouch update` for routine firmware updates. Recovery deletes device state.

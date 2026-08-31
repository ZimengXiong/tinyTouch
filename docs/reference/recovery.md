# Recovery

Recovery erases every fingerprint template, PIV key, HID pairing, and setting. Use it only when a normal fingerprint-authorized reset cannot work. For example, you might need recovery if a new sensor arrives with an unknown enrolled template.

Please try running `tinytouch status --verbose` and `tinytouch factory-reset` before proceeding with recovery.

## Flash center

Use the [Flash center](/flash#recovery) to run the recovery tool.

## Requirements

To run recovery, you need:

- Chrome or Edge;
- a USB data cable; and
- physical access to **BOOT** and **RESET** on the ESP32-S3 board.

## Procedure

1. Disconnect tinyTouch from USB.
2. Hold **BOOT** while reconnecting it. Alternatively, hold **BOOT**, tap **RESET**, and release **BOOT**.
3. Select **Erase and recover**.
4. Choose the ESP32-S3 download-mode port.
5. Wait for the browser to report a successful completion.
6. Unplug and reconnect the device.
7. Wait 20 seconds for the sensor erase and NVS reset.
8. Run `tinytouch setup` to configure your fresh device.

The recovery build verifies that the sensor template database is empty before erasing NVS. If the sensor does not respond, the firmware preserves your settings and retries on the next boot.

## Recovery is not an update method

Always use `tinytouch update` for routine firmware releases. This command preserves your device state, verifies the signed candidate, and retains a rollback slot. Recovery erases your state entirely and writes a full image through ROM download mode.

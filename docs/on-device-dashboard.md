# On-device dashboard

In **HID mode**, TinyTouch presents a USB Ethernet adapter (CDC-ECM) alongside the keyboard and serial console. That link hosts a small local web UI for managing fingerprints, slot labels, and typing settings on the dongle.

## How to use

1. Put the device in HID mode (`tinytouch setup --mode hid` or `tinytouch mode hid`), then unplug and reconnect if the CLI asks you to.
2. Allow the USB Ethernet accessory if macOS prompts.
3. Open the dashboard:
   - run `tinytouch dashboard`, or
   - browse to [http://192.168.7.1/](http://192.168.7.1/)
4. Tap **Unlock** and touch the fingerprint sensor (or complete first-time setup when no fingerprints are enrolled).
5. Enroll or delete slots, edit labels, and adjust Submit Enter / typing delay / idle ring light as needed.

`tinytouch dashboard` also tries to keep the USB Ethernet service off the default route on macOS so browsing still uses Wi‑Fi or Ethernet.

## What stays on the Mac

Passwords for HID typing stay in this Mac's Keychain. The dashboard does **not** store or edit Mac passwords. Each computer still needs `tinytouch setup --mode hid` or `tinytouch add-computer` so the helper can type when you touch the sensor.

## Architecture notes

- HID mode USB configuration: keyboard + CDC (serial) + CDC-ECM (USB Ethernet).
- Device address: `192.168.7.1` with a single DHCP lease for the host.
- DHCP does not offer a default gateway; the link is for the dashboard only.
- The UI is an embedded gzip HTML page served by ESP-IDF `httpd`.
- Mutating APIs require unlock (fingerprint / first-setup window) and a short-lived session cookie.
- Pausing HID typing during enroll/unlock prevents the background helper path from racing the sensor.

## Limitations

- **macOS + HID ECM**: designed and tested for macOS USB Ethernet accessory support. Windows USB Ethernet is not included in this build.
- **Endpoint budget**: ESP32-S3 has five IN endpoints including EP0. HID mode omits the CDC notification endpoint so ECM can open; TinyUSB treats CDC notif as optional.
- **PIV mode**: keeps CCID and does **not** present the dashboard network interface.
- **Mode switch**: changing between HID and PIV changes the USB topology; reconnect (or let the firmware re-enumerate) after `SET MODE`.
- **Passwords**: remain in Mac Keychain via the TinyTouch helper; never on the dongle UI.

## Smoke check after flash

With the device in HID mode and USB Ethernet up:

```bash
./scripts/verify-dashboard.sh
```

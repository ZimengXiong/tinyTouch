# tinyTouch unified firmware

This ESP-IDF project provides the PIV, HID keyboard, CDC configuration, fingerprint, and authenticated update runtime for both supported ESP32-S3 boards.

Build with ESP-IDF 5.3.x:

```sh
./firmware/build-and-flash --build-only
```

Customers update installed devices with `tinytouch update`. Factory flashing is for blank DIY boards and recovery. See `docs/` for wiring, protocol, release, and security details.

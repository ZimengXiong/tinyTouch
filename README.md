# tinyTouch

tinyTouch adds fingerprint-gated PIV and HID authentication to macOS with an
ESP32-S3 and a UART fingerprint sensor.

- `firmware/` contains the unified ESP-IDF firmware.
- `macos/` contains the CLI and HID background helper.
- `docs/` contains customer guides and the browser flasher.
- `hardware/` contains the printable enclosure.
- `release/` contains release assembly and integrity checks.
- `tests/` contains host, protocol, and release tests.

Customer installation and setup are documented at
[docs.tinytouch.dev](https://docs.tinytouch.dev/customer/).

Build and test with the native tools:

```sh
python3 -m unittest discover -s tests
idf.py -C firmware/tiny_touch_unified build
cd docs && npm ci && npm run build
```

The firmware supports two modes:

- PIV presents a fingerprint-gated USB smart card for macOS login and `sudo`.
- HID types a Keychain password after a fingerprint match.

The fingerprint sensor communicates with the ESP32-S3 over unauthenticated
UART. Treat the device as vulnerable to attacks that involve physical access.

See the [build guide](docs/customer/build.md),
[setup guide](docs/customer/setup.md), and
[CLI reference](docs/reference/cli.md).

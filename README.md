# tinyTouch

tinyTouch adds fingerprint-gated PIV and HID authentication to a Mac using an ESP32-S3 and a UART fingerprint sensor.

## Set up a purchased device

```sh
curl -fsSL https://alpacaengineer.ing/tinytouch/batch-0/install.sh | sh
tinytouch setup
```

Routine firmware updates use `tinytouch update`. They require an enrolled fingerprint and do not require the **BOOT** button.

## Build your own

The unified firmware supports:

- ESP32-S3 Super Mini
- Seeed Studio XIAO ESP32-S3

Connect the sensor UART to GPIO43 and GPIO44, and connect `TouchOut` to GPIO2. Use the [factory flasher](https://alpacaengineer.ing/tinytouch/batch-0/flash/) for the first flash, then run `tinytouch setup`.

## Documentation

The local documentation source is in [`docs/`](docs/). It covers purchased-device setup, DIY hardware, wiring, assembly, firmware, recovery, CLI commands, updates, security, and troubleshooting.

Build it locally with:

```sh
cd docs
npm install
npm run docs:dev
```

## Development

Firmware builds require ESP-IDF 5.3.x:

```sh
./firmware/build-and-flash --build-only
.venv/bin/python -m unittest discover -s tests
```

Both supported boards use the same firmware image and pin assignments. Test USB, sleep, update, and fingerprint changes on physical hardware before release.

Support: [tinytouch@alpacaengineer.ing](mailto:tinytouch@alpacaengineer.ing)

## License

The repository is available under the [MIT License](LICENSE), including commercial use. Keep the license notice with redistributed files, and do not imply that an independent product is made or supported by Alpaca Engineer.

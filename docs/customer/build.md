---
title: Build tinyTouch
description: Get the parts, print the case, and connect the sensor.
prev: false
next:
  text: Install firmware
  link: /flash
---

# Build tinyTouch

<div class="video-guide">Video guide <StoreIcon store="YouTube" href="https://www.youtube.com/watch?v=YsP1hRg28Gw" /></div>

## 1. Get the parts

- Board: ESP32-S3 Super Mini <StoreIcon store="Amazon" href="https://www.amazon.com/Supermini-Development-Bluetooth-Frequency-Compatible/dp/B0GS283V6F" /> <StoreIcon store="AliExpress" href="https://www.aliexpress.us/item/3256809624317467.html" /> or Seeed Studio XIAO ESP32-S3 <StoreIcon store="Amazon" href="https://www.amazon.com/Dual-core-Supported-Efficiency-Interface-Robotics/dp/B0DJ6NQFKX?th=1" />
- Fingerprint sensor <StoreIcon store="Amazon" href="https://www.amazon.com/Buying-ZW111-Fingerprint-Recognition-Semiconductor/dp/B0DLNFLM97" /> <StoreIcon store="AliExpress" href="https://www.aliexpress.us/item/3256804771575951.html" /> (AliExpress includes cable)
- 1.0 mm, 6-pin sensor cable <StoreIcon store="Amazon" href="https://www.amazon.com/Pairs-Micro-Connector-Female-Cables/dp/B0C2ZQKHRG" />
- USB data cable
- Wire and soldering tools

## 2. Print the case

<CaseExplorer />

## 3. Wire it up

<SensorPinout />

## Build firmware from source

The current project is `firmware/tiny_touch_unified`, built with **ESP-IDF 5.3.x**.
There is no current Arduino `.ino` sketch. Select and activate the 5.3 toolchain:

```sh
git clone --branch v5.3.3 --recursive https://github.com/espressif/esp-idf.git ~/esp/esp-idf-v5.3.3
cd ~/esp/esp-idf-v5.3.3
./install.sh esp32s3
. ./export.sh
cd /path/to/tinyTouch
espsecure.py generate_signing_key --version 2 firmware/tiny_touch_unified/secure_boot_signing_key.pem
idf.py -C firmware/tiny_touch_unified build
```

Keep the signing key private and reuse it for your device's later updates.
A locally signed image cannot replace a production-signed image through OTA;
use ROM flashing for your own development device. The [Flash center](/flash)
is the supported way to install official firmware.

After a failed build with another IDF version, remove generated `build/` and
`managed_components/` directories, restore the repository's `sdkconfig` and
`dependencies.lock` (save any local changes first), then build with 5.3.x.
Do not remove the project's signing and partition settings.

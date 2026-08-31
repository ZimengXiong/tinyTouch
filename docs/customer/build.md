# Build

Use an ESP32-S3 Super Mini or Seeed Studio XIAO ESP32-S3 with a ZW101-style UART fingerprint sensor. Use 3.3 V power and logic.

## Wire the sensor

| Sensor pin | Signal | ESP32-S3 | XIAO |
|---:|---|---|---|
| 1 | VTouch | 3V3 | 3V3 |
| 2 | TouchOut | GPIO2 | D1 |
| 3 | VCC | 3V3 | 3V3 |
| 4 | TX | GPIO44 (RX) | D7 |
| 5 | RX | GPIO43 (TX) | D6 |
| 6 | GND | GND | GND |

The UART pair is crossed: sensor TX goes to board RX. Sensor RX goes to board TX.

Check continuity. Confirm that 3V3 and GND are not shorted before connecting USB.

## Case files

- <a href="/downloads/tinytouch-case-top.stl">Case top</a>
- <a href="/downloads/tinytouch-case-bottom.stl">Case bottom</a>

Next: [Flash factory firmware](/flash).

#pragma once

#include <stdbool.h>
#include <stdint.h>

enum {
  FP_LED_BLUE = 0x01,
  FP_LED_GREEN = 0x02,
  FP_LED_RED = 0x04,
};

// The fingerprint driver holds its UART mutex while calling these functions.
void fingerprint_led_init(uint32_t now_ms);
void fingerprint_led_show(uint8_t color, bool present, uint32_t now_ms);
void fingerprint_led_service(bool present, uint32_t now_ms);

// Implemented by the fingerprint driver; true requires a successful sensor ACK.
bool fingerprint_led_command(const uint8_t params[4]);

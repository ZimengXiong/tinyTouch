#pragma once

#include <stdbool.h>
#include <stdint.h>
#include "finger_profiles.h"
#include "device_config.h"

typedef struct {
  uint16_t slot;
  uint16_t score;
} fingerprint_match_t;

void fingerprint_init(void);
bool fingerprint_is_ready(void);
bool fingerprint_recover(void);
bool fingerprint_present_hint(void);
void fingerprint_led_idle(void);
bool fingerprint_set_led_mode(device_led_mode_t mode);
bool fingerprint_set_option(device_option_t option, uint16_t value);
bool fingerprint_preview_led(uint8_t color, uint8_t effect, uint16_t duration_ms);
fingerprint_match_t fingerprint_authorize_poll_match(void);
bool fingerprint_authorize_prompted(void (*prompt)(void));
bool fingerprint_prompted_authorization_active(void);
int fingerprint_count(void);
bool fingerprint_delete_all(void);

typedef struct {
  unsigned capacity;
  uint64_t occupied;
  finger_profiles_t profiles;
} fingerprint_inventory_t;

bool fingerprint_inventory(fingerprint_inventory_t *inventory);
bool fingerprint_enroll_finger(unsigned finger, bool replace, void (*prompt)(const char *),
                               bool (*connected)(void));
bool fingerprint_delete_finger(unsigned finger);

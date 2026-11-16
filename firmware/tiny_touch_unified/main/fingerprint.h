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
void fingerprint_wait_for_touch(void);
void fingerprint_led_idle(void);
void fingerprint_led_service(void);
const char *fingerprint_led_control_status(void);
bool fingerprint_led_update_pending(void);
bool fingerprint_set_led_mode(device_led_mode_t mode);
bool fingerprint_set_option(device_option_t option, uint16_t value);
bool fingerprint_preview_led(uint8_t color, uint8_t effect, uint16_t duration_ms);
fingerprint_match_t fingerprint_authorize_poll_match(void);
// Returns false when the UART is busy or the sensor has no image yet.
// A completed attempt returns true, including an image that did not match.
bool fingerprint_try_poll_match(fingerprint_match_t *match);
// Prompts "TOUCH". After a failed search it prompts "LIFT" and waits for the
// finger to lift, then prompts "TOUCH" before searching again.
bool fingerprint_authorize_prompted(void (*prompt)(const char *event));
// Runs before each match search, after a usable image has been captured.
void fingerprint_set_search_hook(void (*hook)(void));

typedef enum {
  FINGERPRINT_ALERT_NONE = 0,
  FINGERPRINT_ALERT_PIN_REQUIRED,  // Flashes red and white.
  FINGERPRINT_ALERT_PIN_ACCEPTED,  // Flashes white and blue.
} fingerprint_alert_t;
// Polled by the LED worker. An alert flashes in place of the idle colour, and
// match results still show over it.
void fingerprint_set_alert_source(fingerprint_alert_t (*source)(void));
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

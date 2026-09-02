#pragma once

#include <stdbool.h>
#include <stdint.h>

typedef struct {
  uint16_t slot;
  uint16_t score;
} fingerprint_match_t;

void fingerprint_init(void);
bool fingerprint_is_ready(void);
bool fingerprint_present_hint(void);
void fingerprint_led_idle(void);
fingerprint_match_t fingerprint_authorize_poll_match(void);
bool fingerprint_authorize_prompted(void (*prompt)(void));
int fingerprint_count(void);
bool fingerprint_enroll(uint16_t slot, void (*prompt)(const char *message));
bool fingerprint_delete(uint16_t slot);
bool fingerprint_delete_all(void);

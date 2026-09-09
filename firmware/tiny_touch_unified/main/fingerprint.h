#pragma once

#include <stdbool.h>
#include <stdint.h>

typedef struct {
  uint16_t slot;
  uint16_t score;
} fingerprint_match_t;

typedef enum {
  FINGERPRINT_POLL_UNKNOWN = 0,
  FINGERPRINT_POLL_ABSENT,
  FINGERPRINT_POLL_PRESENT,
} fingerprint_presence_t;

typedef struct {
  fingerprint_presence_t presence;
  fingerprint_match_t match;
} fingerprint_poll_t;

void fingerprint_init(void);
bool fingerprint_is_ready(void);
bool fingerprint_recover(void);
// UNKNOWN includes transport errors and a sensor owned by another operation.
// Release checks capture an image but never match it or change the LED.
fingerprint_poll_t fingerprint_poll(bool match_image);
void fingerprint_led_idle(void);
fingerprint_match_t fingerprint_authorize_poll_match(void);
bool fingerprint_authorize_prompted(void (*prompt)(void));
bool fingerprint_prompted_authorization_active(void);
// Changes whenever foreground authorization or enrollment starts or finishes.
uint32_t fingerprint_foreground_generation(void);
int fingerprint_count(void);
bool fingerprint_enroll(uint16_t slot, void (*prompt)(const char *message));
bool fingerprint_delete(uint16_t slot);
bool fingerprint_delete_all(void);

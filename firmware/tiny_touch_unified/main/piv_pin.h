#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

// Optional PIV PIN policy. With no PIN set, the applet keeps accepting the
// public dummy PIN and a fingerprint match alone authorizes PIV signing.
// Setting a PIN adds a fallback: after repeated failed matches, signing needs
// the PIN and then a fingerprint. Using every retry starts a lockout that
// grows with each repeat.

#define PIV_PIN_MIN_LENGTH 6
#define PIV_PIN_MAX_LENGTH 8
#define PIV_PIN_RETRY_LIMIT 5
#define PIV_PIN_MATCH_FAILURE_LIMIT 5
// Using every retry locks the PIN for the next wait in this list. The last
// one repeats. A PIN followed by a matching finger resets the list.
#define PIV_PIN_LOCKOUT_SECONDS {10, 30, 60, 600, 3600}

// The public PIN that tinyTouch types after a fingerprint match, in PIV
// VERIFY format. It is never accepted as a custom PIN.
extern const uint8_t PIV_PIN_DUMMY[PIV_PIN_MAX_LENGTH];

typedef enum {
  PIV_PIN_STATE_OFF = 0,
  PIV_PIN_STATE_ON,
  PIV_PIN_STATE_REQUIRED,
  // Every retry is used. Nothing is checked until the lockout ends.
  PIV_PIN_STATE_LOCKED,
  // The stored record cannot be read. Only a factory reset clears it.
  PIV_PIN_STATE_BLOCKED,
} piv_pin_state_t;

typedef enum {
  PIV_PIN_OK = 0,
  PIV_PIN_WRONG,
  PIV_PIN_LOCKED,
  PIV_PIN_BLOCKED,
  PIV_PIN_INVALID,
  PIV_PIN_STORAGE_ERROR,
} piv_pin_result_t;

void piv_pin_init(void);
piv_pin_state_t piv_pin_state(void);
const char *piv_pin_state_name(void);
uint8_t piv_pin_retries_left(void);
uint32_t piv_pin_lockout_seconds_left(void);

// True when signing must wait for a manually entered PIN.
bool piv_pin_required(void);

// How long a correct smart-card PIN waits for an enrolled finger.
#define PIV_PIN_FINGER_WINDOW_MS 30000

// Checks a PIN in PIV VERIFY format: ASCII digits padded with 0xff to 8 bytes.
// Every attempt consumes a retry before comparison, so removing power during
// the check cannot return it. A correct PIN refunds it and waits for a finger:
// each search in the window spends a retry, and the fallback clears only when
// piv_pin_note_match follows in time.
piv_pin_result_t piv_pin_verify_piv(const uint8_t pin[PIV_PIN_MAX_LENGTH]);
// True while a correct smart-card PIN waits for an enrolled finger.
bool piv_pin_awaiting_finger(void);

// Console variants take the PIN as a NUL-terminated ASCII digit string.
piv_pin_result_t piv_pin_set(const char *current, const char *next);
piv_pin_result_t piv_pin_clear(const char *current);
// Clears a pending fallback without a smart-card prompt, as HID mode needs.
piv_pin_result_t piv_pin_unlock(const char *current);

// Saves a fingerprint search as failed before the sensor runs it, so the
// result cannot be observed and power removed before it counts.
void piv_pin_note_attempt(void);
// Cancels the pending failure after an enrolled finger matched. Inside the
// window after a correct smart-card PIN, it instead restores the retries and
// clears the fallback, returning true.
bool piv_pin_note_match(void);

// Removes the PIN and its counters before a new identity is stored.
bool piv_pin_reset(void);

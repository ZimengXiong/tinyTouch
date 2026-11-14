#pragma once

// Pure PIV user-presence budget accounting. The time window itself stays in
// piv.c; this header only decides which slot operations one grant permits.
// It has no ESP-IDF dependencies so host tests can exercise it directly.

#include <stdbool.h>
#include <stdint.h>

#define PIV_PRESENCE_SLOT_AUTHENTICATION 0x01  // 9a
#define PIV_PRESENCE_SLOT_KEY_MANAGEMENT 0x02  // 9d

// After the 9a login signature, macOS unlocks the Login Keychain with 9d and
// can issue a second 9d operation immediately afterwards. Issue #33 logs show
// "piv_crypto_ok 157" followed 30 ms later by "piv_crypto_rejected 157", and
// macOS then asks for the Keychain password. One fingerprint match therefore
// permits one 9a operation and up to this many 9d operations.
#define PIV_LOGIN_KEY_MANAGEMENT_LIMIT 2

typedef struct {
  uint8_t slots_used;
  uint8_t slots_succeeded;
  uint8_t operations_left;
  uint8_t key_management_left;
  bool repeated_slots;
} piv_presence_budget_t;

static inline void piv_presence_clear(piv_presence_budget_t *budget) {
  budget->slots_used = 0;
  budget->slots_succeeded = 0;
  budget->operations_left = 0;
  budget->key_management_left = 0;
  budget->repeated_slots = false;
}

// A login touch: one 9a operation and a small bounded number of 9d operations.
static inline void piv_presence_grant_login(piv_presence_budget_t *budget) {
  piv_presence_clear(budget);
  budget->operations_left = 1 + PIV_LOGIN_KEY_MANAGEMENT_LIMIT;
  budget->key_management_left = PIV_LOGIN_KEY_MANAGEMENT_LIMIT;
}

// Setup and pairing: a bounded sequence of operations in either slot.
static inline void piv_presence_grant_configuration(piv_presence_budget_t *budget,
                                                    uint8_t operation_limit) {
  piv_presence_clear(budget);
  budget->operations_left = operation_limit;
  budget->key_management_left = operation_limit;
  budget->repeated_slots = true;
}

// Returns true and spends budget when this slot operation is permitted.
static inline bool piv_presence_consume(piv_presence_budget_t *budget, uint8_t slot_bit) {
  if (budget->operations_left == 0) return false;
  if (!budget->repeated_slots) {
    if (slot_bit == PIV_PRESENCE_SLOT_KEY_MANAGEMENT) {
      if (budget->key_management_left == 0) return false;
    } else if (budget->slots_used & slot_bit) {
      return false;
    }
  }
  budget->slots_used |= slot_bit;
  budget->operations_left--;
  if (!budget->repeated_slots && slot_bit == PIV_PRESENCE_SLOT_KEY_MANAGEMENT) {
    budget->key_management_left--;
  }
  return true;
}

static inline bool piv_presence_spent(const piv_presence_budget_t *budget) {
  return budget->operations_left == 0;
}

// Records a delivered operation. Returns true when a login grant has been
// fully used, so a touch-activated card can be hidden without cutting off a
// later Keychain 9d operation. Otherwise the touch window expiry hides it.
static inline bool piv_presence_record_success(piv_presence_budget_t *budget,
                                               uint8_t slot_bit) {
  budget->slots_succeeded |= slot_bit;
  return !budget->repeated_slots &&
         budget->slots_succeeded ==
             (PIV_PRESENCE_SLOT_AUTHENTICATION | PIV_PRESENCE_SLOT_KEY_MANAGEMENT) &&
         budget->key_management_left == 0;
}

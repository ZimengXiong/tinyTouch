#include <assert.h>
#include <stdio.h>

#include "../../firmware/tiny_touch_unified/main/piv_presence.h"

#define AUTH PIV_PRESENCE_SLOT_AUTHENTICATION
#define KEYS PIV_PRESENCE_SLOT_KEY_MANAGEMENT

// Spend one operation and report whether a touch-activated card would hide.
static bool run(piv_presence_budget_t *budget, uint8_t slot, bool *hide) {
  if (!piv_presence_consume(budget, slot)) return false;
  *hide = piv_presence_record_success(budget, slot);
  return true;
}

static void login_allows_keychain_unlock_after_authentication(void) {
  // Issue #33: 9a ok, 9d ok, then a second 9d that macOS needs for Keychain.
  piv_presence_budget_t budget;
  bool hide = false;
  piv_presence_grant_login(&budget);
  assert(run(&budget, AUTH, &hide) && !hide);
  assert(run(&budget, KEYS, &hide) && !hide);
  assert(!piv_presence_spent(&budget));
  assert(run(&budget, KEYS, &hide) && hide);
  assert(piv_presence_spent(&budget));
  // The grant is bounded: no further operation in either slot.
  assert(!piv_presence_consume(&budget, KEYS));
  assert(!piv_presence_consume(&budget, AUTH));
}

static void login_permits_only_one_authentication(void) {
  piv_presence_budget_t budget;
  bool hide = false;
  piv_presence_grant_login(&budget);
  assert(run(&budget, AUTH, &hide));
  assert(!piv_presence_consume(&budget, AUTH));
  // A rejected 9a must not consume the remaining Keychain budget.
  assert(run(&budget, KEYS, &hide));
  assert(run(&budget, KEYS, &hide) && hide);
}

static void login_key_management_is_bounded_without_authentication(void) {
  piv_presence_budget_t budget;
  bool hide = false;
  piv_presence_grant_login(&budget);
  assert(run(&budget, KEYS, &hide) && !hide);
  assert(run(&budget, KEYS, &hide) && !hide);
  assert(!piv_presence_consume(&budget, KEYS));
  // Authentication remains available, and completes the login budget.
  assert(run(&budget, AUTH, &hide) && hide);
}

static void configuration_permits_bounded_repeats(void) {
  piv_presence_budget_t budget;
  bool hide = false;
  piv_presence_grant_configuration(&budget, 8);
  for (int i = 0; i < 4; i++) {
    assert(run(&budget, AUTH, &hide) && !hide);
    assert(run(&budget, KEYS, &hide) && !hide);
  }
  assert(piv_presence_spent(&budget));
  assert(!piv_presence_consume(&budget, AUTH));
  assert(!piv_presence_consume(&budget, KEYS));
}

static void cleared_budget_rejects_everything(void) {
  piv_presence_budget_t budget;
  piv_presence_grant_login(&budget);
  piv_presence_clear(&budget);
  assert(!piv_presence_consume(&budget, AUTH));
  assert(!piv_presence_consume(&budget, KEYS));
}

int main(void) {
  login_allows_keychain_unlock_after_authentication();
  login_permits_only_one_authentication();
  login_key_management_is_bounded_without_authentication();
  configuration_permits_bounded_repeats();
  cleared_budget_rejects_everything();
  puts("piv presence tests passed");
  return 0;
}

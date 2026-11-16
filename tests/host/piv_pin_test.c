#include "piv_pin.c"

static const uint8_t *padded(const char *text) {
  static uint8_t value[PIV_PIN_MAX_LENGTH];
  memset(value, 0xff, sizeof(value));
  memcpy(value, text, strlen(text));
  return value;
}

static void reboot(void) { piv_pin_init(); }

// The search hook runs before every search; a match then cancels it.
static void fail_matches(int count) {
  for (int i = 0; i < count; i++) piv_pin_note_attempt();
}

static bool successful_match(void) {
  piv_pin_note_attempt();
  return piv_pin_note_match();
}

int main(void) {
  // Without a PIN the policy is inert and never writes flash.
  reboot();
  assert(piv_pin_state() == PIV_PIN_STATE_OFF && !piv_pin_required());
  assert(piv_pin_retries_left() == PIV_PIN_RETRY_LIMIT);
  fail_matches(10);
  assert(writes == 0 && !piv_pin_required());
  assert(piv_pin_verify_piv(padded("246810")) == PIV_PIN_INVALID);

  // Only 6-8 digits are accepted, and never the public dummy PIN.
  assert(piv_pin_set(NULL, "111111") == PIV_PIN_INVALID);
  assert(piv_pin_set(NULL, "12345") == PIV_PIN_INVALID);
  assert(piv_pin_set(NULL, "123456789") == PIV_PIN_INVALID);
  assert(piv_pin_set(NULL, "12a456") == PIV_PIN_INVALID);
  assert(piv_pin_set("", "246810") == PIV_PIN_INVALID);
  assert(!stored_present);

  assert(piv_pin_set(NULL, "246810") == PIV_PIN_OK);
  reboot();
  assert(piv_pin_state() == PIV_PIN_STATE_ON);
  // SET cannot replace an existing PIN, and the attempt costs no retry.
  assert(piv_pin_set(NULL, "135790") == PIV_PIN_INVALID);
  assert(piv_pin_retries_left() == PIV_PIN_RETRY_LIMIT);

  // The auto-typed dummy and malformed input are rejected without a retry.
  assert(piv_pin_verify_piv(PIV_PIN_DUMMY) == PIV_PIN_WRONG);
  uint8_t malformed[PIV_PIN_MAX_LENGTH] = {'2', '4', 0xff, '6', '8', '1', '0', 0xff};
  assert(piv_pin_verify_piv(malformed) == PIV_PIN_WRONG);
  assert(piv_pin_retries_left() == PIV_PIN_RETRY_LIMIT);

  // Consecutive failed matches trigger the fallback; a match resets the run.
  fail_matches(PIV_PIN_MATCH_FAILURE_LIMIT - 1);
  successful_match();
  fail_matches(PIV_PIN_MATCH_FAILURE_LIMIT - 1);
  assert(piv_pin_state() == PIV_PIN_STATE_ON);
  // The fifth search counts as failed while it runs. Losing power before the
  // result lands keeps the failure; a match cancels it.
  piv_pin_note_attempt();
  reboot();
  assert(piv_pin_state() == PIV_PIN_STATE_REQUIRED && piv_pin_required());
  // Once required, a later match does not clear it, even after reconnecting.
  successful_match();
  reboot();
  successful_match();
  fail_matches(3);
  assert(piv_pin_state() == PIV_PIN_STATE_REQUIRED);
  int writes_before = writes;
  successful_match();
  assert(writes == writes_before);

  // A wrong PIN costs one retry.
  assert(piv_pin_verify_piv(padded("000000")) == PIV_PIN_WRONG);
  assert(piv_pin_retries_left() == PIV_PIN_RETRY_LIMIT - 1);
  reboot();
  assert(piv_pin_retries_left() == PIV_PIN_RETRY_LIMIT - 1);
  assert(!piv_pin_awaiting_finger());

  // A correct PIN alone clears nothing and costs nothing. It waits for a
  // finger, and repeating it restarts the wait.
  assert(piv_pin_verify_piv(padded("246810")) == PIV_PIN_OK);
  assert(piv_pin_awaiting_finger());
  assert(piv_pin_state() == PIV_PIN_STATE_REQUIRED);
  assert(piv_pin_retries_left() == PIV_PIN_RETRY_LIMIT - 1);
  tick_count += PIV_PIN_FINGER_WINDOW_MS - 1;
  assert(piv_pin_verify_piv(padded("246810")) == PIV_PIN_OK);
  tick_count += PIV_PIN_FINGER_WINDOW_MS - 1;
  assert(piv_pin_awaiting_finger());
  assert(piv_pin_retries_left() == PIV_PIN_RETRY_LIMIT - 1);
  // Losing power drops the wait.
  reboot();
  assert(!piv_pin_awaiting_finger() && piv_pin_state() == PIV_PIN_STATE_REQUIRED);
  assert(!piv_pin_note_match());
  assert(piv_pin_retries_left() == PIV_PIN_RETRY_LIMIT - 1);
  // The window expires without a finger, and a later match grants nothing.
  assert(piv_pin_verify_piv(padded("246810")) == PIV_PIN_OK);
  tick_count += PIV_PIN_FINGER_WINDOW_MS;
  assert(!piv_pin_awaiting_finger());
  successful_match();
  assert(piv_pin_state() == PIV_PIN_STATE_REQUIRED);
  assert(piv_pin_retries_left() == PIV_PIN_RETRY_LIMIT - 1);
  // Inside the window, a wrong PIN and each failed finger spend a retry
  // without ending the wait. A match completes it, even on the last retry.
  assert(piv_pin_verify_piv(padded("246810")) == PIV_PIN_OK);
  assert(piv_pin_verify_piv(padded("000000")) == PIV_PIN_WRONG);
  fail_matches(2);
  assert(piv_pin_retries_left() == 1);
  reboot();
  assert(piv_pin_retries_left() == 1);
  assert(piv_pin_verify_piv(padded("246810")) == PIV_PIN_OK);
  assert(piv_pin_awaiting_finger());
  piv_pin_note_attempt();
  assert(piv_pin_state() == PIV_PIN_STATE_LOCKED);
  assert(piv_pin_note_match());
  assert(!piv_pin_awaiting_finger());
  assert(piv_pin_state() == PIV_PIN_STATE_ON);
  assert(piv_pin_retries_left() == PIV_PIN_RETRY_LIMIT);
  reboot();
  assert(piv_pin_state() == PIV_PIN_STATE_ON);
  assert(piv_pin_retries_left() == PIV_PIN_RETRY_LIMIT);

  // Each login saves the pending attempt and then cancels it.
  writes_before = writes;
  for (int i = 0; i < 10; i++) successful_match();
  assert(writes == writes_before + 20);
  assert(piv_pin_state() == PIV_PIN_STATE_ON);
  // The pending fifth attempt is cancelled by its own match.
  fail_matches(PIV_PIN_MATCH_FAILURE_LIMIT - 1);
  successful_match();
  assert(piv_pin_state() == PIV_PIN_STATE_ON);
  // Unsaved failures still count until power is removed.
  failing_writes = 100;
  fail_matches(PIV_PIN_MATCH_FAILURE_LIMIT);
  assert(piv_pin_state() == PIV_PIN_STATE_REQUIRED);
  failing_writes = 0;
  reboot();
  assert(piv_pin_state() == PIV_PIN_STATE_ON);

  // The retry is stored before comparison. If that write fails, nothing is
  // compared, so a correct PIN cannot be confirmed without spending a retry.
  failing_writes = 1;
  assert(piv_pin_verify_piv(padded("246810")) == PIV_PIN_STORAGE_ERROR);
  assert(piv_pin_retries_left() == PIV_PIN_RETRY_LIMIT);
  // Losing the refund keeps the retry spent; the finger restores it.
  fail_at_write = writes + 2;
  assert(piv_pin_verify_piv(padded("246810")) == PIV_PIN_OK);
  assert(piv_pin_retries_left() == PIV_PIN_RETRY_LIMIT - 1);
  assert(successful_match());
  assert(piv_pin_retries_left() == PIV_PIN_RETRY_LIMIT);
  // Losing the restoring write after the finger keeps the search's spent
  // retry rather than granting a free attempt, and the wait ends.
  assert(piv_pin_verify_piv(padded("246810")) == PIV_PIN_OK);
  piv_pin_note_attempt();
  fail_at_write = writes + 1;
  assert(!piv_pin_note_match());
  assert(!piv_pin_awaiting_finger());
  reboot();
  assert(piv_pin_retries_left() == PIV_PIN_RETRY_LIMIT - 1);
  assert(piv_pin_verify_piv(padded("246810")) == PIV_PIN_OK);
  assert(successful_match());
  assert(piv_pin_retries_left() == PIV_PIN_RETRY_LIMIT);

  // Changing needs the current PIN; the old PIN stops working.
  assert(piv_pin_set("000000", "135790") == PIV_PIN_WRONG);
  assert(piv_pin_set("246810", "135790") == PIV_PIN_OK);
  assert(piv_pin_retries_left() == PIV_PIN_RETRY_LIMIT);
  assert(piv_pin_verify_piv(padded("246810")) == PIV_PIN_WRONG);
  assert(piv_pin_verify_piv(padded("135790")) == PIV_PIN_OK);
  assert(successful_match());
  assert(piv_pin_set("13579", "246810") == PIV_PIN_WRONG);

  // A correct PIN on the last retry keeps it.
  for (int i = 1; i < PIV_PIN_RETRY_LIMIT; i++)
    assert(piv_pin_verify_piv(padded("999999")) == PIV_PIN_WRONG);
  assert(piv_pin_verify_piv(padded("135790")) == PIV_PIN_OK);
  assert(piv_pin_retries_left() == 1 && piv_pin_awaiting_finger());
  assert(successful_match());
  assert(piv_pin_state() == PIV_PIN_STATE_ON);
  assert(piv_pin_retries_left() == PIV_PIN_RETRY_LIMIT);

  // Using every retry locks the PIN, CHANGE and CLEAR included, for each wait
  // in turn; the last repeats. Nothing is checked or saved while locked.
  static const uint32_t waits[] = PIV_PIN_LOCKOUT_SECONDS;
  const size_t levels = sizeof(waits) / sizeof(waits[0]);
  for (int i = 1; i < PIV_PIN_RETRY_LIMIT; i++)
    assert(piv_pin_verify_piv(padded("999999")) == PIV_PIN_WRONG);
  assert(piv_pin_verify_piv(padded("999999")) == PIV_PIN_LOCKED);
  for (size_t level = 0; level <= levels; level++) {
    uint32_t wait = waits[level < levels ? level : levels - 1];
    assert(piv_pin_state() == PIV_PIN_STATE_LOCKED && piv_pin_required());
    assert(piv_pin_lockout_seconds_left() == wait && piv_pin_retries_left() == 0);
    writes_before = writes;
    assert(piv_pin_verify_piv(padded("135790")) == PIV_PIN_LOCKED);
    assert(piv_pin_set("135790", "246810") == PIV_PIN_LOCKED);
    assert(piv_pin_clear("135790") == PIV_PIN_LOCKED);
    successful_match();
    assert(writes == writes_before);
    // Losing power restarts the wait in full.
    tick_count += wait * 1000 - 1;
    reboot();
    assert(piv_pin_lockout_seconds_left() == wait);
    tick_count += wait * 1000;
    // One attempt is then available, and a wrong one locks for longer.
    assert(piv_pin_state() == PIV_PIN_STATE_REQUIRED && piv_pin_lockout_seconds_left() == 0);
    assert(piv_pin_retries_left() == 1);
    assert(piv_pin_verify_piv(padded("999999")) == PIV_PIN_LOCKED);
  }
  // The PIN then a finger resets the schedule.
  tick_count += waits[levels - 1] * 1000;
  assert(piv_pin_verify_piv(padded("135790")) == PIV_PIN_OK);
  assert(successful_match());
  assert(piv_pin_state() == PIV_PIN_STATE_ON);
  assert(piv_pin_retries_left() == PIV_PIN_RETRY_LIMIT);
  for (int i = 1; i < PIV_PIN_RETRY_LIMIT; i++)
    assert(piv_pin_verify_piv(padded("999999")) == PIV_PIN_WRONG);
  assert(piv_pin_verify_piv(padded("999999")) == PIV_PIN_LOCKED);
  assert(piv_pin_lockout_seconds_left() == waits[0]);
  // A lockout reached without the fallback, such as from wrong console PINs,
  // still needs the PIN then a finger.
  tick_count += waits[0] * 1000;
  assert(piv_pin_verify_piv(padded("135790")) == PIV_PIN_OK);
  tick_count += PIV_PIN_FINGER_WINDOW_MS;
  assert(piv_pin_state() == PIV_PIN_STATE_REQUIRED && piv_pin_retries_left() == 1);
  // A search already running when the PIN is accepted completes nothing.
  piv_pin_note_attempt();
  assert(piv_pin_verify_piv(padded("135790")) == PIV_PIN_OK);
  assert(!piv_pin_note_match());
  assert(piv_pin_awaiting_finger() && piv_pin_state() == PIV_PIN_STATE_REQUIRED);
  assert(successful_match());
  assert(piv_pin_state() == PIV_PIN_STATE_ON);
  // Fingers that start a lockout end the window, even when the lockout is
  // shorter than it: after the lockout, the PIN is needed again.
  fail_matches(PIV_PIN_MATCH_FAILURE_LIMIT);
  assert(piv_pin_verify_piv(padded("135790")) == PIV_PIN_OK);
  fail_matches(PIV_PIN_RETRY_LIMIT);
  tick_count += waits[0] * 1000;
  assert(piv_pin_awaiting_finger() && piv_pin_state() == PIV_PIN_STATE_REQUIRED);
  assert(!successful_match());
  assert(!piv_pin_awaiting_finger() && piv_pin_state() == PIV_PIN_STATE_REQUIRED);

  // Replacing the identity removes the PIN.
  assert(piv_pin_reset());
  reboot();
  assert(piv_pin_state() == PIV_PIN_STATE_OFF && !stored_present);
  assert(piv_pin_reset());

  // UNLOCK clears the fallback with the PIN alone over the console, where
  // fingerprint authorization has already run; a wrong PIN spends a retry.
  assert(piv_pin_set(NULL, "246810") == PIV_PIN_OK);
  fail_matches(PIV_PIN_MATCH_FAILURE_LIMIT);
  assert(piv_pin_unlock("000000") == PIV_PIN_WRONG);
  assert(piv_pin_state() == PIV_PIN_STATE_REQUIRED);
  assert(piv_pin_retries_left() == PIV_PIN_RETRY_LIMIT - 1);
  assert(piv_pin_unlock("246810") == PIV_PIN_OK);
  assert(piv_pin_state() == PIV_PIN_STATE_ON);
  assert(piv_pin_retries_left() == PIV_PIN_RETRY_LIMIT);

  // Someone who knows the PIN gets one finger per retry. With none left the
  // lockout starts, the wait ends, and a later match changes nothing.
  fail_matches(PIV_PIN_MATCH_FAILURE_LIMIT);
  assert(piv_pin_verify_piv(padded("246810")) == PIV_PIN_OK);
  fail_matches(PIV_PIN_RETRY_LIMIT);
  assert(piv_pin_state() == PIV_PIN_STATE_LOCKED && piv_pin_awaiting_finger());
  successful_match();
  assert(!piv_pin_awaiting_finger());
  assert(piv_pin_state() == PIV_PIN_STATE_LOCKED);
  assert(piv_pin_reset());

  // CLEAR needs the current PIN and turns the policy off.
  assert(piv_pin_set(NULL, "24681012") == PIV_PIN_OK);
  assert(piv_pin_clear("2468101") == PIV_PIN_WRONG);
  assert(piv_pin_clear("24681012") == PIV_PIN_OK);
  reboot();
  assert(piv_pin_state() == PIV_PIN_STATE_OFF);
  assert(piv_pin_clear("24681012") == PIV_PIN_INVALID);

  // Out-of-range stored counters fail closed: the PIN is still demanded.
  assert(piv_pin_set(NULL, "246810") == PIV_PIN_OK);
  ((stored_pin_t *)stored_blob)->retries_left = 9;
  reboot();
  assert(piv_pin_state() == PIV_PIN_STATE_REQUIRED);
  assert(piv_pin_retries_left() == PIV_PIN_RETRY_LIMIT);
  assert(piv_pin_verify_piv(padded("246810")) == PIV_PIN_OK);
  assert(successful_match());
  assert(piv_pin_state() == PIV_PIN_STATE_ON);

  // A record that exists but cannot be parsed blocks rather than disabling
  // the PIN, whether truncated or from an unknown format version.
  stored_length = 4;
  reboot();
  assert(piv_pin_state() == PIV_PIN_STATE_BLOCKED && piv_pin_required());
  stored_length = sizeof(stored_pin_t);
  ((stored_pin_t *)stored_blob)->version = PIN_POLICY_VERSION + 1;
  reboot();
  assert(piv_pin_state() == PIV_PIN_STATE_BLOCKED);
  assert(piv_pin_verify_piv(padded("246810")) == PIV_PIN_BLOCKED);
  return 0;
}

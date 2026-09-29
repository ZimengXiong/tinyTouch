#define main led_regression_main
#include "led_test.c"
#undef main

static unsigned active_view, cancel_view;
static bool connected(void) { return !cancel_view || active_view < cancel_view; }
static void prompt(const char *event) {
  if (strncmp(event, "VIEW ", 5) == 0) active_view = event[5] - '0';
  if (strcmp(event, "LIFT") == 0) reject_capture = true;
  if (strcmp(event, "TOUCH") == 0 || strcmp(event, "TOUCH_AGAIN") == 0) reject_capture = false;
}

int main(void) {
  defaults(&disk_config); have_config = true;
  device_config_init(); fingerprint_init();
  uint64_t legacy = 0;
  for (unsigned slot = 1; slot <= 5; slot++) legacy |= UINT64_C(1) << slot;
  sensor_templates = legacy;
  fingerprint_inventory_t inventory;
  assert(fingerprint_inventory(&inventory));
  assert(inventory.capacity == 40 && inventory.occupied == legacy);
  assert(finger_profiles_count(legacy & finger_profiles_block(1)) == 4);
  assert(finger_profiles_count(legacy & finger_profiles_block(2)) == 1);
  assert(!fingerprint_enroll_finger(1, false, prompt, connected));
  assert(!fingerprint_enroll_finger(2, false, prompt, connected));
  assert(sensor_templates == legacy);
  assert(fingerprint_enroll_finger(1, true, prompt, connected));
  assert(sensor_templates == legacy && !profiles.pending);
  // Counts cannot substitute for an authoritative index, even before replacement.
  wrong_count = true;
  assert(!fingerprint_inventory(&inventory));
  assert(!fingerprint_enroll_finger(1, true, prompt, connected));
  assert(sensor_templates == legacy); wrong_count = false;
  // A failed replacement clears its own partial block, keeping the other block.
  fail_store = 2;
  assert(!fingerprint_enroll_finger(1, true, prompt, connected));
  assert(sensor_templates == (UINT64_C(1) << 5));
  fail_store = -1;
  assert(fingerprint_enroll_finger(1, false, prompt, connected));
  assert(sensor_templates == legacy);
  // A cancelled third view removes only the new block, never the legacy prints.
  active_view = 0; cancel_view = 3;
  assert(!fingerprint_enroll_finger(3, false, prompt, connected));
  assert(sensor_templates == legacy && !profiles.pending);
  cancel_view = 0;
  // A failed store rolls back, preserving both complete and partial legacy blocks.
  fail_store = 10;
  assert(!fingerprint_enroll_finger(3, false, prompt, connected));
  assert(sensor_templates == legacy && !profiles.pending);
  fail_store = -1;
  // Simulate power loss after the first view: pending templates cannot authorize.
  finger_profiles_t interrupted = profiles;
  interrupted.pending = finger_profiles_block(3);
  assert(finger_profiles_save(&interrupted));
  profiles = interrupted;
  sensor_templates |= UINT64_C(1) << 9;
  search_slot = 9; assert(!fingerprint_authorize_poll_match().slot);
  search_slot = 1; assert(fingerprint_authorize_poll_match().slot == 1);
  // Failed boot cleanup retains the journal and retries on a later boot.
  fail_delete = 9; fingerprint_init();
  assert(profiles.pending && (sensor_templates & (UINT64_C(1) << 9)));
  fail_delete = -1; fingerprint_init();
  assert(!profiles.pending && sensor_templates == legacy);
  // Every finger occupies its own fixed block, including template 40 -> physical 0.
  assert(fingerprint_enroll_finger(2, true, prompt, connected));
  for (unsigned finger = 3; finger <= 10; finger++) {
    assert(fingerprint_enroll_finger(finger, false, prompt, connected));
    assert((sensor_templates & finger_profiles_block(finger)) == finger_profiles_block(finger));
  }
  assert(finger_profiles_count(sensor_templates) == 40);
  search_slot = 0; assert(fingerprint_authorize_poll_match().slot == 40);
  assert(fingerprint_enroll_finger(10, true, prompt, connected));
  assert(finger_profiles_count(sensor_templates) == 40);
  uint64_t full = sensor_templates;
  assert(fingerprint_delete_finger(2));
  assert(sensor_templates == (full & ~finger_profiles_block(2)));
  assert(fingerprint_enroll_finger(2, false, prompt, connected));
  assert(sensor_templates == full);
  assert(!fingerprint_enroll_finger(0, true, prompt, connected));
  assert(!fingerprint_enroll_finger(11, true, prompt, connected));
  // NVS failure must prevent any destructive replacement.
  fail_save = true;
  assert(!fingerprint_enroll_finger(1, true, prompt, connected));
  assert(sensor_templates == full); fail_save = false;
  // A sparse partial block reserves that block regardless of the total count.
  sensor_templates = (UINT64_C(1) << 2) | (UINT64_C(1) << 17) | 1;
  assert(fingerprint_inventory(&inventory));
  assert(!fingerprint_enroll_finger(1, false, prompt, connected));
  assert(!fingerprint_enroll_finger(5, false, prompt, connected));
  assert(!fingerprint_enroll_finger(10, false, prompt, connected));
  assert(finger_profiles_count(sensor_templates) == 3);
  // A corrupt journal cannot authorize templates or trigger destructive cleanup.
  disk_profiles.version = 99;
  fingerprint_init();
  assert(!profiles_ready);
  search_slot = 2; assert(!fingerprint_authorize_poll_match().slot);
  assert(!fingerprint_enroll_finger(1, true, prompt, connected));
  assert(finger_profiles_count(sensor_templates) == 3);
  disk_profiles.version = 1; fingerprint_init();
  assert(profiles_ready);
  sensor_templates = full;
  // An impossible index/capacity combination must fail closed.
  sensor_capacity = 20;
  assert(!fingerprint_inventory(&inventory));
  assert(!fingerprint_enroll_finger(1, true, prompt, connected));
  assert(sensor_templates == full);
  return 0;
}

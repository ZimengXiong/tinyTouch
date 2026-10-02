#define main led_regression_main
#include "led_test.c"
#undef main

static void service_after(unsigned ms) {
  vTaskDelay(pdMS_TO_TICKS(ms));
  fingerprint_led_service();
}

static void expect_control(const char *value) {
  assert(strcmp(fingerprint_led_control_status(), value) == 0);
}

static void enroll_prompt(const char *event) {
  if (strcmp(event, "LIFT") == 0) reject_capture = true;
  if (strcmp(event, "TOUCH") == 0 || strcmp(event, "TOUCH_AGAIN") == 0) reject_capture = false;
}

int main(void) {
  // Upgrade an existing .29/.30 off preference without altering credentials
  // or templates. The sensor starts in its factory automatic-lighting mode.
  defaults(&disk_config); have_config = have_led = true; disk_led = 0;
  disk_config.typing_delay_ms = 19;
  stored_config_t original = disk_config;
  sensor_templates = UINT64_C(1) << 1;
  device_config_init(); fingerprint_init();
  expect_control("reconnect");
  assert(manual_commands == 1 && disk_manual_stage == 1);
  assert(sensor_manual_saved && !sensor_manual_active && physical_led == 0);
  unsigned flashes = automatic_lights;
  assert(poll_match_with_led().slot == 1);
  assert(automatic_lights > flashes); // Accepted command alone is NOT darkness.
  assert(physical_led == 0);

  // OTA, watchdog and brownout resets do not prove the external sensor lost
  // power. Never clear the reconnect notice or rewrite its flash on these.
  const esp_reset_reason_t warm[] = {ESP_RST_SW, ESP_RST_WDT, ESP_RST_BROWNOUT};
  for (unsigned i = 0; i < sizeof(warm) / sizeof(warm[0]); i++) {
    reset_reason = warm[i]; fingerprint_init();
    expect_control("reconnect");
    assert(manual_commands == 1 && disk_manual_stage == 1);
  }
  sensor_power_cycle(); fingerprint_init();
  expect_control("manual");
  assert(disk_manual_stage == 2 && manual_commands == 1);
  flashes = automatic_lights;

  // Both background modes use this matcher. Foreground AUTH and enrollment
  // must also remain dark throughout, not just at the last host LED command.
  for (unsigned mode = DEVICE_MODE_PIV; mode <= DEVICE_MODE_HID; mode++) {
    assert(device_config_set_mode((device_mode_t)mode));
    assert(poll_match_with_led().slot == 1 && physical_led == 0);
    no_match = true;
    assert(!poll_match_with_led().slot && physical_led == 0);
    no_match = false;
  }
  assert(fingerprint_authorize_prompted(NULL) && physical_led == 0);
  no_match = true;
  assert(!fingerprint_authorize_prompted(NULL) && physical_led == 0);
  no_match = false;
  unsigned blue = led_colors[FP_LED_BLUE], green = led_colors[FP_LED_GREEN], red = led_colors[FP_LED_RED];
  assert(fingerprint_enroll_finger(2, false, enroll_prompt, NULL));
  assert(sensor_templates == ((UINT64_C(1) << 1) | finger_profiles_block(2)));
  assert(led_colors[FP_LED_BLUE] == blue && led_colors[FP_LED_GREEN] == green && led_colors[FP_LED_RED] == red);
  assert(automatic_lights == flashes && physical_led == 0);

  // All transitions retain manual ownership. Manual mode means firmware must
  // supply negative as well as positive result feedback for on and only-auth.
  for (unsigned from = 0; from <= 2; from++) {
    for (unsigned to = 0; to <= 2; to++) {
      assert(fingerprint_set_led_mode((device_led_mode_t)from));
      assert(fingerprint_set_led_mode((device_led_mode_t)to));
      assert(physical_led == (to == DEVICE_LED_ON ? FP_LED_BLUE : 0));
      assert(poll_match_with_led().slot == 1);
      assert(physical_led == (to == DEVICE_LED_OFF ? 0 : FP_LED_GREEN));
      fingerprint_led_idle();
      no_match = true; assert(!poll_match_with_led().slot); no_match = false;
      assert(physical_led == (to == DEVICE_LED_OFF ? 0 : FP_LED_RED));
      fingerprint_led_idle();
      assert(fingerprint_authorize_prompted(NULL));
      assert(physical_led == (to == DEVICE_LED_ON ? FP_LED_BLUE : 0));
    }
  }
  assert(manual_commands == 1 && automatic_lights == flashes);

  // A negative ACK must not masquerade as physical success. Retry idle
  // cleanup without another touch, but never capture during that maintenance.
  assert(fingerprint_set_led_mode(DEVICE_LED_ON));
  assert(poll_match_with_led().slot == 1 && physical_led == FP_LED_GREEN);
  reject_led = 3;
  assert(!fingerprint_set_led_mode(DEVICE_LED_OFF));
  assert(device_config_led_mode() == DEVICE_LED_OFF && physical_led == FP_LED_GREEN);
  assert(fingerprint_led_update_pending() && fingerprint_is_ready());
  unsigned captures = capture_commands;
  int commands = led_commands;
  fingerprint_led_service(); assert(led_commands == commands);
  service_after(2000);
  assert(physical_led == 0 && !fingerprint_led_update_pending());
  assert(capture_commands == captures);

  // Reproduce the old background double rejection and foreground single
  // rejection. Both now retain cleanup work even after the caller returns.
  physical_led = FP_LED_GREEN; reject_led = 2;
  assert(poll_match_with_led().slot == 1);
  vTaskDelay(350); fingerprint_led_idle();
  assert(physical_led == FP_LED_GREEN && fingerprint_led_update_pending());
  service_after(100); assert(physical_led == 0);
  physical_led = FP_LED_GREEN; reject_led = 1;
  assert(fingerprint_authorize_prompted(NULL));
  assert(physical_led == FP_LED_GREEN && fingerprint_led_update_pending());
  service_after(100); assert(physical_led == 0);

  // LED-only timeouts preserve fingerprint health, schedule bounded backoff,
  // and do not delay a successful match behind repeated synchronous retries.
  physical_led = FP_LED_GREEN; drop_led = 1;
  TickType_t started = clock_ticks;
  assert(poll_match_with_led().slot == 1);
  assert((TickType_t)(clock_ticks - started) < 300);
  assert(fingerprint_is_ready() && fingerprint_led_update_pending());
  service_after(100); assert(physical_led == 0);
  reject_led = 100; fingerprint_led_idle();
  service_after(100); service_after(100);
  commands = led_commands;
  for (unsigned i = 0; i < 50; i++) fingerprint_led_service();
  assert(led_commands == commands && fingerprint_is_ready());
  reject_led = 0; service_after(2000);
  assert(!fingerprint_led_update_pending());
  commands = led_commands;
  for (unsigned i = 0; i < 20; i++) service_after(2000);
  assert(led_commands == commands); // Settled LEDs generate no idle traffic.

  // Pending old green must never be replayed after the user selects off.
  assert(fingerprint_set_led_mode(DEVICE_LED_ON)); reject_led = 1;
  assert(poll_match_with_led().slot == 1);
  assert(fingerprint_led_update_pending());
  assert(fingerprint_set_led_mode(DEVICE_LED_OFF));
  service_after(2000); assert(physical_led == 0);
  assert(fingerprint_recover() && physical_led == 0 && manual_commands == 1);
  reset_reason = ESP_RST_SW; fingerprint_init();
  expect_control("manual"); assert(manual_commands == 1);

  // Late sensor boot can recover through COUNT/STATUS instead of the recovery
  // function. That path must still apply both manual control and idle darkness.
  reject_verify = true; physical_led = FP_LED_BLUE; fingerprint_init();
  assert(!fingerprint_is_ready() && fingerprint_led_update_pending());
  commands = led_commands; service_after(2000); assert(led_commands == commands);
  reject_verify = false;
  assert(fingerprint_count() >= 0 && fingerprint_is_ready());
  service_after(2000); service_after(100);
  expect_control("manual"); assert(physical_led == 0 && !fingerprint_led_update_pending());

  // Retry arithmetic must also work when the RTOS tick counter wraps.
  clock_ticks = UINT32_MAX - 50;
  reject_led = 1; fingerprint_led_idle();
  service_after(100); assert(!fingerprint_led_update_pending());

  // Unsupported/rejected manual mode is visible, does not brick fingerprint
  // auth, is not hammered in the background, and never claims SET succeeded.
  have_manual_stage = false; sensor_manual_saved = sensor_manual_active = false;
  reject_manual = 0x18; fingerprint_init();
  expect_control("rejected");
  unsigned writes = manual_commands;
  for (unsigned i = 0; i < 10; i++) service_after(2000);
  assert(manual_commands == writes);
  assert(!fingerprint_set_led_mode(DEVICE_LED_OFF));
  assert(poll_match_with_led().slot == 1 && fingerprint_is_ready());
  reject_manual = 0; assert(fingerprint_set_led_mode(DEVICE_LED_OFF));
  expect_control("reconnect");

  have_manual_stage = false; reject_manual = 0x01; fingerprint_init();
  expect_control("pending"); writes = manual_commands;
  reject_manual = 0; service_after(2000);
  expect_control("reconnect"); assert(manual_commands == writes + 1);

  have_manual_stage = false; drop_manual = 10; fingerprint_init();
  service_after(2000); service_after(2000);
  expect_control("unavailable"); writes = manual_commands;
  for (unsigned i = 0; i < 10; i++) service_after(2000);
  assert(manual_commands == writes && fingerprint_is_ready());
  drop_manual = 0; assert(fingerprint_set_led_mode(DEVICE_LED_OFF));
  expect_control("reconnect");

  // Timeout before the migration ACK is retried. Failed NVS commit after ACK
  // retries only the marker; it does not repeatedly write the sensor's flash.
  have_manual_stage = false; drop_manual = 1; fingerprint_init();
  expect_control("pending"); assert(fingerprint_is_ready());
  fail_manual_save = true; service_after(2000);
  expect_control("storage-error"); writes = manual_commands;
  service_after(2000); assert(manual_commands == writes);
  fail_manual_save = false; service_after(2000);
  expect_control("reconnect"); assert(manual_commands == writes);

  // Power loss between sensor ACK and marker commit safely repeats the
  // idempotent migration, keeping reconnect required until a later cold boot.
  have_manual_stage = false; fail_manual_save = true; fingerprint_init();
  expect_control("storage-error"); writes = manual_commands;
  fail_manual_save = false; sensor_power_cycle(); fingerprint_init();
  assert(manual_commands == writes + 1); expect_control("reconnect");
  fail_manual_save = true; sensor_power_cycle(); fingerprint_init();
  expect_control("storage-error"); writes = manual_commands;
  fail_manual_save = false; service_after(2000);
  expect_control("manual"); assert(manual_commands == writes);
  assert(poll_match_with_led().slot == 1 && physical_led == 0);
  assert(disk_config.typing_delay_ms == original.typing_delay_ms);
  assert(sensor_templates == ((UINT64_C(1) << 1) | finger_profiles_block(2)));
  return 0;
}

#define main led_regression_main
#define enrollment_connected driver_connection_hook
#include "led_test.c"
#undef main
#undef enrollment_connected
#include <stdio.h>
#include <stdlib.h>

static int64_t authorized_until;
static bool serial_connected = true;
static char last_reply[256];
static int64_t esp_timer_get_time(void) { return clock_ticks; }
static bool tud_cdc_connected(void) { return serial_connected; }
static void reply(const char *line) { snprintf(last_reply, sizeof(last_reply), "%s", line); }
static void enroll_prompt(const char *event) {
  if (strcmp(event, "LIFT") == 0) reject_capture = true;
  if (strcmp(event, "TOUCH") == 0 || strcmp(event, "TOUCH_AGAIN") == 0) reject_capture = false;
}
#include "console_under_test.h"

static void command(const char *text) {
  char input[96]; snprintf(input, sizeof(input), "%s", text);
  fingerprint_command(input);
}

int main(void) {
  defaults(&disk_config); have_config = true;
  device_config_init(); fingerprint_init();
  char locked_led[] = "LED 2";
  set_value(locked_led); assert(strcmp(last_reply, "ERR LOCKED run=AUTH") == 0);
  assert(device_config_led_mode() == DEVICE_LED_ON);
  char locked_delay[] = "PIV_DELAY 100";
  set_value(locked_delay); assert(strcmp(last_reply, "ERR LOCKED run=AUTH") == 0);
  assert(device_config_piv_delay_ms() == 25);
  authorized_until = INT64_MAX;
  // Preferences and previews use the same authorization boundary as LED mode.
  authorized_until = 0;
  led_preview("5 1 100"); assert(strcmp(last_reply, "ERR LOCKED run=AUTH") == 0);
  char locked_color[] = "LED_IDLE_COLOR 5";
  set_value(locked_color); assert(strcmp(last_reply, "ERR LOCKED run=AUTH") == 0);
  authorized_until = INT64_MAX;
  char custom_color[] = "LED_IDLE_COLOR 5";
  set_value(custom_color); assert(strcmp(last_reply, "OK SET") == 0);
  expect_led(5);
  led_preview("3 2 100"); assert(strcmp(last_reply, "OK LED PREVIEW") == 0);
  expect_led(5);
  const char *bad_preview[] = {"8 1 100", "1 4 100", "1 1 99", "1 1 5001", "1 1 100 junk", "1 1"};
  for (unsigned i = 0; i < sizeof(bad_preview) / sizeof(bad_preview[0]); i++) {
    led_preview(bad_preview[i]); assert(strncmp(last_reply, "ERR LED", 7) == 0);
  }
  char restore_color[] = "LED_IDLE_COLOR 1";
  set_value(restore_color); assert(strcmp(last_reply, "OK SET") == 0);
  char valid_delay[] = "PIV_DELAY 100";
  set_value(valid_delay); assert(strcmp(last_reply, "OK SET") == 0);
  assert(device_config_piv_delay_ms() == 100 && disk_piv_delay == 100);
  const char *bad_delay[] = {"PIV_DELAY 5001", "PIV_DELAY -1", "PIV_DELAY 65536", "PIV_DELAY 50 junk"};
  for (unsigned i = 0; i < sizeof(bad_delay) / sizeof(bad_delay[0]); i++) {
    char value[32]; snprintf(value, sizeof(value), "%s", bad_delay[i]);
    set_value(value); assert(strcmp(last_reply, "ERR SET") == 0);
    assert(device_config_piv_delay_ms() == 100);
  }
  char reconnect_led[] = "LED 0";
  set_value(reconnect_led); assert(strcmp(last_reply, "ERR SET LED reconnect_required") == 0);
  assert(device_config_led_mode() == DEVICE_LED_OFF);
  sensor_power_cycle(); fingerprint_init();
  for (unsigned mode = 0; mode <= 2; mode++) {
    char value[16]; snprintf(value, sizeof(value), "LED %u", mode);
    set_value(value); assert(strcmp(last_reply, "OK SET") == 0);
    assert(device_config_led_mode() == (device_led_mode_t)mode);
    expect_led(mode == DEVICE_LED_ON ? FP_LED_BLUE : 0);
  }
  const char *bad_led[] = {"LED 3", "LED -1", "LED 65536", "LED only-auth", "LED", "LED 2 junk"};
  for (unsigned i = 0; i < sizeof(bad_led) / sizeof(bad_led[0]); i++) {
    char value[32]; snprintf(value, sizeof(value), "%s", bad_led[i]);
    set_value(value); assert(strcmp(last_reply, "ERR SET") == 0);
    assert(device_config_led_mode() == DEVICE_LED_ONLY_AUTH);
  }
  const char *bad_settings[] = {"LED_IDLE_COLOR 256", "LED_IDLE_EFFECT 4", "LED_IDLE_CYCLES 256",
                               "LED_FEEDBACK_MS 49", "PIV_AUTO_TYPE 2", "UNKNOWN 1"};
  for (unsigned i = 0; i < sizeof(bad_settings) / sizeof(bad_settings[0]); i++) {
    char value[64]; snprintf(value, sizeof(value), "%s", bad_settings[i]);
    set_value(value); assert(strcmp(last_reply, "ERR SET") == 0);
  }
  fail_save = true;
  char failed_led[] = "LED 0";
  set_value(failed_led); assert(strcmp(last_reply, "ERR SET") == 0);
  assert(device_config_led_mode() == DEVICE_LED_ONLY_AUTH); fail_save = false;
  authorized_until = 0;
  sensor_templates = UINT64_C(0x3e); // physical slots 1 through 5
  command("LIST");
  assert(strcmp(last_reply, "OK FINGER LIST groups=1:4,2:1 available=8 capacity=40 pending=0") == 0);
  command("ENROLL_GROUP 1 REPLACE");
  assert(strcmp(last_reply, "ERR LOCKED run=AUTH") == 0);
  assert(sensor_templates == UINT64_C(0x3e));
  authorized_until = INT64_MAX;
  const char *invalid[] = {"ENROLL 2", "DELETE 2", "ENROLL_GROUP 0", "ENROLL_GROUP 11",
                           "ENROLL_GROUP -1", "ENROLL_GROUP 1 INVALID", "DELETE_GROUP 11"};
  for (unsigned i = 0; i < sizeof(invalid) / sizeof(invalid[0]); i++) {
    command(invalid[i]); assert(strncmp(last_reply, "ERR ", 4) == 0);
    assert(sensor_templates == UINT64_C(0x3e));
  }
  command("ENROLL_GROUP 2");
  assert(strcmp(last_reply, "ERR FINGER enrollment_failed") == 0);
  assert(sensor_templates == UINT64_C(0x3e));
  command("ENROLL_GROUP 3");
  assert(strcmp(last_reply, "OK FINGER ENROLL_GROUP") == 0);
  uint64_t expected = UINT64_C(0x3e) | finger_profiles_block(3);
  assert(sensor_templates == expected);
  command("LIST");
  assert(strstr(last_reply, "groups=1:4,2:1,3:4 available=7"));
  authorized_until = 0;
  command("DELETE_GROUP 2"); assert(strncmp(last_reply, "ERR LOCKED", 10) == 0);
  assert(sensor_templates == expected);
  authorized_until = INT64_MAX;
  command("DELETE_GROUP 2"); assert(strcmp(last_reply, "OK FINGER") == 0);
  assert(sensor_templates == (expected & ~(UINT64_C(1) << 5)));
  // Closing and reopening the serial session cannot resume the old enrollment.
  enrollment_running = true;
  assert(enrollment_connected());
  tud_cdc_line_state_cb(0, false, false);
  tud_cdc_line_state_cb(0, true, false);
  assert(!enrollment_connected());
  enrollment_running = false; enrollment_disconnected = false;
  tud_cdc_line_state_cb(0, false, false);
  assert(enrollment_connected());
  serial_connected = false; assert(!enrollment_connected());
  return 0;
}

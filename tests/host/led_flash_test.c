#define main led_regression_main
#include "led_test.c"
#undef main

int main(void) {
  defaults(&disk_config);
  have_config = have_led = true;
  disk_led = DEVICE_LED_OFF;
  sensor_templates = UINT64_C(1) << 1;
  stored_config_t original = disk_config;
  device_config_init(); fingerprint_init();

  // Reproduce the reported flash. A final off command hides the transient
  // animation from tests that inspect only the last physical LED state.
  unsigned green = visible_green;
  assert(fingerprint_authorize_poll_match().slot == 1);
  assert(physical_led == 0 && visible_green > green);
  assert(strcmp(fingerprint_led_control_status(), "reconnect") == 0);

  sensor_power_cycle(); fingerprint_init();
  assert(strcmp(fingerprint_led_control_status(), "manual") == 0);
  green = visible_green;
  unsigned red = visible_red;
  unsigned automatic = automatic_lights;
  for (unsigned mode = DEVICE_MODE_PIV; mode <= DEVICE_MODE_HID; mode++) {
    assert(device_config_set_mode((device_mode_t)mode));
    for (unsigned attempt = 0; attempt < 3; attempt++) {
      assert(fingerprint_authorize_poll_match().slot == 1);
      fingerprint_led_idle();
      assert(fingerprint_authorize_prompted(NULL));
      no_match = true;
      assert(!fingerprint_authorize_poll_match().slot);
      no_match = false;
    }
  }
  assert(physical_led == 0);
  assert(visible_green == green && visible_red == red);
  assert(automatic_lights == automatic);
  assert(sensor_templates == (UINT64_C(1) << 1));
  assert(device_config_typing_delay_ms() == original.typing_delay_ms);

  // Authentication-only remains a distinct setting with intentional feedback.
  assert(fingerprint_set_led_mode(DEVICE_LED_ONLY_AUTH));
  assert(fingerprint_authorize_poll_match().slot == 1);
  assert(visible_green > green);
  fingerprint_led_idle(); assert(physical_led == 0);
  assert(fingerprint_set_led_mode(DEVICE_LED_OFF));
  green = visible_green;
  assert(fingerprint_authorize_poll_match().slot == 1);
  assert(visible_green == green && physical_led == 0);
  return 0;
}

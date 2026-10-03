#define main led_regression_main
#include "led_test.c"
#undef main

int main(void) {
  assert(led_regression_main() == 0);
  stored_config_t original = disk_config;
  assert(fingerprint_set_option(DEVICE_OPTION_LED_IDLE_COLOR, 5)); expect_led(5);
  assert(fingerprint_set_option(DEVICE_OPTION_LED_IDLE_END_COLOR, 3));
  assert(fingerprint_set_option(DEVICE_OPTION_LED_IDLE_EFFECT, 1));
  assert(last_led[0] == 1 && last_led[1] == 5 && last_led[2] == 3 && last_led[3] == 0);
  assert(fingerprint_set_option(DEVICE_OPTION_LED_IDLE_CYCLES, 12)); assert(last_led[3] == 12);
  device_config_init(); fingerprint_init();
  assert(last_led[0] == 1 && last_led[1] == 5 && last_led[2] == 3 && last_led[3] == 12);
  assert(memcmp(&original, &disk_config, sizeof(original)) == 0);
  for (unsigned color = 0; color <= 7; color++) {
    assert(fingerprint_set_option(DEVICE_OPTION_LED_SUCCESS_COLOR, color));
    unsigned count = led_colors[color];
    show_result(true);
    assert(led_colors[color] > count);
  }
  // ONLY_AUTH identifies roles, not color values. Idle red is suppressed and
  // success blue remains visible; neither color can invert lighting policy.
  assert(fingerprint_set_option(DEVICE_OPTION_LED_IDLE_COLOR, 4));
  assert(fingerprint_set_option(DEVICE_OPTION_LED_SUCCESS_COLOR, 1));
  assert(fingerprint_set_led_mode(DEVICE_LED_ONLY_AUTH)); expect_led(0);
  unsigned blue = led_colors[1]; show_result(true); expect_led(0);
  assert(led_colors[1] == blue + 1);
  device_options_t saved = disk_options;
  assert(fingerprint_preview_led(5, 2, 100)); expect_led(0);
  assert(memcmp(&saved, &disk_options, sizeof(saved)) == 0);
  assert(!fingerprint_preview_led(8, 2, 100));
  assert(!fingerprint_preview_led(1, 4, 100));
  assert(!fingerprint_preview_led(1, 1, 99));
  reject_led = 1;
  assert(!fingerprint_preview_led(5, 2, 100)); expect_led(0);
  fail_save = true;
  assert(!fingerprint_set_option(DEVICE_OPTION_LED_IDLE_COLOR, 6));
  assert(device_config_options().led_idle_color == 4); fail_save = false;
  assert(!device_config_set_option(DEVICE_OPTION_LED_IDLE_COLOR, 256));
  assert(!device_config_set_option(DEVICE_OPTION_LED_IDLE_EFFECT, 4));
  assert(!device_config_set_option(DEVICE_OPTION_LED_IDLE_CYCLES, 256));
  assert(!device_config_set_option(DEVICE_OPTION_LED_FEEDBACK_MS, 49));
  assert(!device_config_set_option(DEVICE_OPTION_LED_FEEDBACK_MS, 2001));
  assert(!device_config_set_option(DEVICE_OPTION_PIV_AUTO_TYPE, 2));
  assert(!device_config_set_option((device_option_t)999, 1));
  assert(device_config_set_option(DEVICE_OPTION_LED_FEEDBACK_MS, 50));
  assert(device_config_set_option(DEVICE_OPTION_PIV_AUTO_TYPE, 0));
  device_config_init(); assert(device_config_options().piv_auto_type == 0);
  // Corrupt extra preferences fall back without touching schema-6 credentials.
  disk_options.version = 255;
  device_config_init(); assert(device_config_options().led_idle_color == 1);
  assert(memcmp(&original, &disk_config, sizeof(original)) == 0);
  assert(device_config_set_option(DEVICE_OPTION_LED_IDLE_COLOR, 7));
  assert(device_config_factory_reset());
  device_config_init();
  assert(device_config_options().led_idle_color == 1);
  assert(device_config_options().led_feedback_ms == 350);
  assert(device_config_options().piv_auto_type == 1);
  assert(device_config_led_mode() == DEVICE_LED_ON);
  return 0;
}

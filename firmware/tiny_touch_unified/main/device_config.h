#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef enum {
  DEVICE_MODE_PIV = 0,
  DEVICE_MODE_HID = 1,
} device_mode_t;

#define DEVICE_CONFIG_MAX_HID_HOSTS 8
#define DEVICE_CONFIG_HID_KEY_ID_SIZE 8

typedef struct {
  uint8_t id[DEVICE_CONFIG_HID_KEY_ID_SIZE];
  uint8_t key[32];
} device_hid_host_t;

void device_config_init(void);
device_mode_t device_config_mode(void);
const char *device_config_mode_name(void);
bool device_config_set_mode(device_mode_t mode);
size_t device_config_hid_host_count(void);
size_t device_config_copy_hid_hosts(
    device_hid_host_t hosts[DEVICE_CONFIG_MAX_HID_HOSTS]);
bool device_config_add_hid_host(const uint8_t id[DEVICE_CONFIG_HID_KEY_ID_SIZE],
                                const uint8_t key[32]);
bool device_config_remove_hid_host(const uint8_t id[DEVICE_CONFIG_HID_KEY_ID_SIZE]);
bool device_config_set_fingerprint_profile_views(uint8_t views);
uint16_t device_config_typing_delay_ms(void);
bool device_config_set_typing_delay_ms(uint16_t value);
bool device_config_submit_enter(void);
bool device_config_set_submit_enter(bool value);
uint16_t device_config_touch_cooldown_ms(void);
bool device_config_set_touch_cooldown_ms(uint16_t value);
bool device_config_factory_reset(void);

typedef enum {
  DEVICE_LED_OFF = 0,
  DEVICE_LED_ON = 1,
  DEVICE_LED_ONLY_AUTH = 2,
} device_led_mode_t;

device_led_mode_t device_config_led_mode(void);
const char *device_config_led_mode_name(void);
bool device_config_set_led_mode(device_led_mode_t value);

// Additional preferences use a separate NVS key. The schema-6 credentials and
// enrollment mapping remain readable by both old and new firmware.
typedef struct {
  uint8_t version;
  uint8_t led_idle_color;
  uint8_t led_success_color;
  uint8_t led_failure_color;
  uint8_t led_idle_end_color;
  uint8_t led_idle_effect;
  uint8_t led_idle_cycles;
  uint8_t piv_auto_type;
  uint16_t led_feedback_ms;
} device_options_t;

typedef enum {
  DEVICE_OPTION_LED_IDLE_COLOR,
  DEVICE_OPTION_LED_SUCCESS_COLOR,
  DEVICE_OPTION_LED_FAILURE_COLOR,
  DEVICE_OPTION_LED_IDLE_END_COLOR,
  DEVICE_OPTION_LED_IDLE_EFFECT,
  DEVICE_OPTION_LED_IDLE_CYCLES,
  DEVICE_OPTION_LED_FEEDBACK_MS,
  DEVICE_OPTION_PIV_AUTO_TYPE,
} device_option_t;

device_options_t device_config_options(void);
bool device_config_set_option(device_option_t option, uint16_t value);
bool device_config_piv_touch_enabled(void);
bool device_config_set_piv_touch_enabled(bool value);
uint16_t device_config_piv_delay_ms(void);
bool device_config_set_piv_delay_ms(uint16_t value);

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

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
bool device_config_hid_key_configured(void);
bool device_config_get_hid_key(uint8_t key[32]);
bool device_config_set_hid_key(const uint8_t key[32]);
size_t device_config_hid_host_count(void);
bool device_config_get_hid_host(size_t index, device_hid_host_t *host);
bool device_config_add_hid_host(const uint8_t id[DEVICE_CONFIG_HID_KEY_ID_SIZE],
                                const uint8_t key[32]);
bool device_config_remove_hid_host(const uint8_t id[DEVICE_CONFIG_HID_KEY_ID_SIZE]);
uint8_t device_config_fingerprint_profile_views(void);
bool device_config_set_fingerprint_profile_views(uint8_t views);
void device_config_reload(void);

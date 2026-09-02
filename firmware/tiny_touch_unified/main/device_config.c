#include "device_config.h"

#include <assert.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "mbedtls/sha256.h"
#include "nvs.h"

#define CONFIG_NAMESPACE "tt6"
#define CONFIG_KEY "config"
#define CONFIG_VERSION 6

typedef struct {
  uint8_t version;
  uint8_t mode;
  uint8_t fingerprint_profile_views;
  uint8_t submit_enter;
  uint16_t typing_delay_ms;
  uint16_t touch_cooldown_ms;
  uint8_t hid_host_count;
  device_hid_host_t hid_hosts[DEVICE_CONFIG_MAX_HID_HOSTS];
} stored_config_t;

static stored_config_t config;
static SemaphoreHandle_t config_mutex;

static void lock(void) { assert(xSemaphoreTake(config_mutex, portMAX_DELAY) == pdTRUE); }
static void unlock(void) { assert(xSemaphoreGive(config_mutex) == pdTRUE); }

static void defaults(stored_config_t *value) {
  memset(value, 0, sizeof(*value));
  value->version = CONFIG_VERSION;
  value->mode = DEVICE_MODE_PIV;
  value->submit_enter = 1;
  value->typing_delay_ms = 7;
  value->touch_cooldown_ms = 800;
}

static void derive_key_id(const uint8_t key[32], uint8_t id[DEVICE_CONFIG_HID_KEY_ID_SIZE]) {
  uint8_t digest[32];
  mbedtls_sha256(key, 32, digest, 0);
  memcpy(id, digest, DEVICE_CONFIG_HID_KEY_ID_SIZE);
  memset(digest, 0, sizeof(digest));
}

static bool valid(const stored_config_t *value) {
  if (value->version != CONFIG_VERSION || value->mode > DEVICE_MODE_HID ||
      value->fingerprint_profile_views > 5 || value->submit_enter > 1 ||
      value->typing_delay_ms < 1 || value->typing_delay_ms > 100 ||
      value->touch_cooldown_ms < 100 || value->touch_cooldown_ms > 5000 ||
      value->hid_host_count > DEVICE_CONFIG_MAX_HID_HOSTS) return false;
  if (value->mode == DEVICE_MODE_HID && value->hid_host_count == 0) return false;
  for (size_t i = 0; i < value->hid_host_count; i++) {
    uint8_t id[DEVICE_CONFIG_HID_KEY_ID_SIZE];
    derive_key_id(value->hid_hosts[i].key, id);
    bool matches = memcmp(id, value->hid_hosts[i].id, sizeof(id)) == 0;
    memset(id, 0, sizeof(id));
    if (!matches) return false;
  }
  return true;
}

static bool save_locked(const stored_config_t *candidate) {
  nvs_handle_t handle;
  if (nvs_open(CONFIG_NAMESPACE, NVS_READWRITE, &handle) != ESP_OK) return false;
  esp_err_t result = nvs_set_blob(handle, CONFIG_KEY, candidate, sizeof(*candidate));
  if (result == ESP_OK) result = nvs_commit(handle);
  nvs_close(handle);
  return result == ESP_OK;
}

static bool replace_locked(const stored_config_t *candidate) {
  if (!valid(candidate) || !save_locked(candidate)) return false;
  config = *candidate;
  return true;
}

void device_config_init(void) {
  config_mutex = xSemaphoreCreateMutex();
  assert(config_mutex != NULL);
  stored_config_t loaded = {0};
  size_t length = sizeof(loaded);
  nvs_handle_t handle;
  bool opened = nvs_open(CONFIG_NAMESPACE, NVS_READONLY, &handle) == ESP_OK;
  bool loaded_ok = opened && nvs_get_blob(handle, CONFIG_KEY, &loaded, &length) == ESP_OK &&
                   length == sizeof(loaded) && valid(&loaded);
  if (opened) nvs_close(handle);
  lock();
  if (loaded_ok) config = loaded;
  else { defaults(&config); assert(save_locked(&config)); }
  unlock();
}

device_mode_t device_config_mode(void) {
  lock(); device_mode_t value = (device_mode_t)config.mode; unlock(); return value;
}

const char *device_config_mode_name(void) {
  return device_config_mode() == DEVICE_MODE_HID ? "hid" : "piv";
}

bool device_config_set_mode(device_mode_t mode) {
  lock(); stored_config_t candidate = config; candidate.mode = mode;
  bool ok = replace_locked(&candidate); unlock(); return ok;
}

size_t device_config_hid_host_count(void) {
  lock(); size_t value = config.hid_host_count; unlock(); return value;
}

size_t device_config_copy_hid_hosts(device_hid_host_t hosts[DEVICE_CONFIG_MAX_HID_HOSTS]) {
  if (!hosts) return 0;
  lock(); size_t count = config.hid_host_count;
  memcpy(hosts, config.hid_hosts, count * sizeof(hosts[0])); unlock(); return count;
}

bool device_config_add_hid_host(const uint8_t id[DEVICE_CONFIG_HID_KEY_ID_SIZE],
                                const uint8_t key[32]) {
  if (!id || !key) return false;
  uint8_t derived[DEVICE_CONFIG_HID_KEY_ID_SIZE]; derive_key_id(key, derived);
  bool valid_id = memcmp(id, derived, sizeof(derived)) == 0;
  memset(derived, 0, sizeof(derived)); if (!valid_id) return false;
  lock(); stored_config_t candidate = config; size_t index = candidate.hid_host_count;
  for (size_t i = 0; i < candidate.hid_host_count; i++) {
    if (memcmp(candidate.hid_hosts[i].id, id, sizeof(candidate.hid_hosts[i].id)) == 0) { index = i; break; }
  }
  if (index == DEVICE_CONFIG_MAX_HID_HOSTS) { unlock(); return false; }
  memcpy(candidate.hid_hosts[index].id, id, sizeof(candidate.hid_hosts[index].id));
  memcpy(candidate.hid_hosts[index].key, key, sizeof(candidate.hid_hosts[index].key));
  if (index == candidate.hid_host_count) candidate.hid_host_count++;
  bool ok = replace_locked(&candidate); unlock(); return ok;
}

bool device_config_remove_hid_host(const uint8_t id[DEVICE_CONFIG_HID_KEY_ID_SIZE]) {
  if (!id) return false;
  lock(); stored_config_t candidate = config; size_t index = candidate.hid_host_count;
  for (size_t i = 0; i < candidate.hid_host_count; i++) {
    if (memcmp(candidate.hid_hosts[i].id, id, sizeof(candidate.hid_hosts[i].id)) == 0) { index = i; break; }
  }
  if (index == candidate.hid_host_count) { unlock(); return false; }
  memmove(&candidate.hid_hosts[index], &candidate.hid_hosts[index + 1],
          (candidate.hid_host_count - index - 1) * sizeof(candidate.hid_hosts[0]));
  candidate.hid_host_count--; memset(&candidate.hid_hosts[candidate.hid_host_count], 0,
                                    sizeof(candidate.hid_hosts[0]));
  if (candidate.mode == DEVICE_MODE_HID && candidate.hid_host_count == 0) candidate.mode = DEVICE_MODE_PIV;
  bool ok = replace_locked(&candidate); unlock(); return ok;
}

bool device_config_set_fingerprint_profile_views(uint8_t views) {
  lock(); stored_config_t candidate = config; candidate.fingerprint_profile_views = views;
  bool ok = replace_locked(&candidate); unlock(); return ok;
}

uint16_t device_config_typing_delay_ms(void) { lock(); uint16_t value = config.typing_delay_ms; unlock(); return value; }
bool device_config_set_typing_delay_ms(uint16_t value) { lock(); stored_config_t c = config; c.typing_delay_ms = value; bool ok = replace_locked(&c); unlock(); return ok; }
bool device_config_submit_enter(void) { lock(); bool value = config.submit_enter; unlock(); return value; }
bool device_config_set_submit_enter(bool value) { lock(); stored_config_t c = config; c.submit_enter = value; bool ok = replace_locked(&c); unlock(); return ok; }
uint16_t device_config_touch_cooldown_ms(void) { lock(); uint16_t value = config.touch_cooldown_ms; unlock(); return value; }
bool device_config_set_touch_cooldown_ms(uint16_t value) { lock(); stored_config_t c = config; c.touch_cooldown_ms = value; bool ok = replace_locked(&c); unlock(); return ok; }

bool device_config_factory_reset(void) {
  lock(); stored_config_t candidate; defaults(&candidate); bool ok = replace_locked(&candidate); unlock(); return ok;
}

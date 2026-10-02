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
static device_led_mode_t led_mode = DEVICE_LED_ON;
static device_options_t options;

static device_options_t option_defaults(void) {
  return (device_options_t){
    .version = 1, .led_idle_color = 1, .led_success_color = 2,
    .led_failure_color = 4, .led_idle_end_color = 1,
    .led_idle_effect = 3, .led_idle_cycles = 0, .piv_auto_type = 1,
    .led_feedback_ms = 350,
  };
}

static bool valid_options(const device_options_t *value) {
  uint8_t effect = value->led_idle_effect;
  return value->version == 1 && value->led_idle_color <= 7 &&
         value->led_success_color <= 7 && value->led_failure_color <= 7 &&
         value->led_idle_end_color <= 7 && value->piv_auto_type <= 1 &&
         value->led_feedback_ms >= 50 && value->led_feedback_ms <= 2000 &&
         (effect == 1 || effect == 2 || effect == 3 || effect == 5 || effect == 6);
}

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
  uint8_t stored_led = 1;
  options = option_defaults();
  device_options_t loaded_options;
  size_t options_length = sizeof(loaded_options);
  if (loaded_ok && nvs_get_blob(handle, "custom", &loaded_options, &options_length) == ESP_OK &&
      options_length == sizeof(loaded_options) && valid_options(&loaded_options))
    options = loaded_options;
  led_mode = DEVICE_LED_ON;
  // Keep the existing key and its 0/1 values compatible with saved preferences.
  if (loaded_ok && nvs_get_u8(handle, "led_enabled", &stored_led) == ESP_OK &&
      stored_led <= DEVICE_LED_ONLY_AUTH)
    led_mode = (device_led_mode_t)stored_led;
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

device_led_mode_t device_config_led_mode(void) {
  // Sensor initialization precedes configuration during destructive recovery.
  if (!config_mutex) return DEVICE_LED_ON;
  lock(); device_led_mode_t value = led_mode; unlock(); return value;
}

const char *device_config_led_mode_name(void) {
  switch (device_config_led_mode()) {
    case DEVICE_LED_OFF: return "off";
    case DEVICE_LED_ONLY_AUTH: return "only-auth";
    default: return "on";
  }
}

bool device_config_set_led_mode(device_led_mode_t value) {
  if (value != DEVICE_LED_OFF && value != DEVICE_LED_ON && value != DEVICE_LED_ONLY_AUTH)
    return false;
  lock();
  nvs_handle_t handle;
  esp_err_t result = nvs_open(CONFIG_NAMESPACE, NVS_READWRITE, &handle);
  if (result == ESP_OK) {
    result = nvs_set_u8(handle, "led_enabled", (uint8_t)value);
    if (result == ESP_OK) result = nvs_commit(handle);
    nvs_close(handle);
  }
  if (result == ESP_OK) led_mode = value;
  unlock();
  return result == ESP_OK;
}

bool device_config_factory_reset(void) {
  if (!device_config_set_led_mode(DEVICE_LED_ON)) return false;
  lock();
  stored_config_t candidate; defaults(&candidate);
  device_options_t reset_options = option_defaults();
  nvs_handle_t handle;
  esp_err_t result = nvs_open(CONFIG_NAMESPACE, NVS_READWRITE, &handle);
  if (result == ESP_OK) {
    result = nvs_set_blob(handle, "custom", &reset_options, sizeof(reset_options));
    if (result == ESP_OK) result = nvs_set_blob(handle, CONFIG_KEY, &candidate, sizeof(candidate));
    if (result == ESP_OK) result = nvs_commit(handle);
    nvs_close(handle);
  }
  if (result == ESP_OK) { config = candidate; options = reset_options; }
  unlock(); return result == ESP_OK;
}

device_options_t device_config_options(void) {
  if (!config_mutex) return option_defaults();
  lock(); device_options_t value = options; unlock(); return value;
}

bool device_config_set_option(device_option_t option, uint16_t value) {
  lock(); device_options_t candidate = options;
  switch (option) {
    case DEVICE_OPTION_LED_IDLE_COLOR:
    case DEVICE_OPTION_LED_SUCCESS_COLOR:
    case DEVICE_OPTION_LED_FAILURE_COLOR:
    case DEVICE_OPTION_LED_IDLE_END_COLOR:
      if (value > 7) { unlock(); return false; }
      if (option == DEVICE_OPTION_LED_IDLE_COLOR) candidate.led_idle_color = value;
      else if (option == DEVICE_OPTION_LED_SUCCESS_COLOR) candidate.led_success_color = value;
      else if (option == DEVICE_OPTION_LED_FAILURE_COLOR) candidate.led_failure_color = value;
      else candidate.led_idle_end_color = value;
      break;
    case DEVICE_OPTION_LED_IDLE_EFFECT:
      if (value > UINT8_MAX) { unlock(); return false; }
      candidate.led_idle_effect = value; break;
    case DEVICE_OPTION_LED_IDLE_CYCLES:
      if (value > UINT8_MAX) { unlock(); return false; }
      candidate.led_idle_cycles = value; break;
    case DEVICE_OPTION_LED_FEEDBACK_MS: candidate.led_feedback_ms = value; break;
    case DEVICE_OPTION_PIV_AUTO_TYPE:
      if (value > 1) { unlock(); return false; }
      candidate.piv_auto_type = value; break;
    default: unlock(); return false;
  }
  if (!valid_options(&candidate)) { unlock(); return false; }
  nvs_handle_t handle;
  esp_err_t result = nvs_open(CONFIG_NAMESPACE, NVS_READWRITE, &handle);
  if (result == ESP_OK) {
    result = nvs_set_blob(handle, "custom", &candidate, sizeof(candidate));
    if (result == ESP_OK) result = nvs_commit(handle);
    nvs_close(handle);
  }
  if (result == ESP_OK) options = candidate;
  unlock(); return result == ESP_OK;
}

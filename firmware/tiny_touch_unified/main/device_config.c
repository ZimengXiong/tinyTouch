#include "device_config.h"

#include <assert.h>
#include <string.h>

#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "mbedtls/sha256.h"
#include "nvs.h"

static const char *TAG = "device_config";

static device_mode_t current_mode = DEVICE_MODE_PIV;
static device_hid_host_t hid_hosts[DEVICE_CONFIG_MAX_HID_HOSTS];
static size_t hid_host_count;
static uint8_t fingerprint_profile_views;
static uint16_t typing_delay_ms = 7;
static bool submit_enter = true;
static uint16_t touch_cooldown_ms = 800;
static SemaphoreHandle_t config_mutex;

static void config_lock(void) {
  assert(config_mutex != NULL);
  assert(xSemaphoreTake(config_mutex, portMAX_DELAY) == pdTRUE);
}

static void config_unlock(void) {
  assert(xSemaphoreGive(config_mutex) == pdTRUE);
}

static bool save_u16(const char *key, uint16_t value) {
  nvs_handle_t handle;
  if (nvs_open("device", NVS_READWRITE, &handle) != ESP_OK) return false;
  esp_err_t result = nvs_set_u16(handle, key, value);
  if (result == ESP_OK) result = nvs_commit(handle);
  nvs_close(handle);
  return result == ESP_OK;
}

static bool save_u8(const char *key, uint8_t value) {
  nvs_handle_t handle;
  if (nvs_open("device", NVS_READWRITE, &handle) != ESP_OK) return false;
  esp_err_t result = nvs_set_u8(handle, key, value);
  if (result == ESP_OK) result = nvs_commit(handle);
  nvs_close(handle);
  return result == ESP_OK;
}

static void derive_key_id(const uint8_t key[32], uint8_t id[DEVICE_CONFIG_HID_KEY_ID_SIZE]) {
  uint8_t digest[32];
  mbedtls_sha256(key, 32, digest, 0);
  memcpy(id, digest, DEVICE_CONFIG_HID_KEY_ID_SIZE);
  memset(digest, 0, sizeof(digest));
}

static bool hid_hosts_valid(const device_hid_host_t *hosts, size_t count) {
  if (!hosts || count == 0 || count > DEVICE_CONFIG_MAX_HID_HOSTS) return false;
  for (size_t i = 0; i < count; i++) {
    uint8_t expected_id[DEVICE_CONFIG_HID_KEY_ID_SIZE];
    derive_key_id(hosts[i].key, expected_id);
    bool id_matches = memcmp(hosts[i].id, expected_id, sizeof(expected_id)) == 0;
    memset(expected_id, 0, sizeof(expected_id));
    if (!id_matches) return false;
    for (size_t previous = 0; previous < i; previous++) {
      if (memcmp(hosts[i].id, hosts[previous].id, sizeof(hosts[i].id)) == 0) {
        return false;
      }
    }
  }
  return true;
}

static bool save_hid_hosts(void) {
  nvs_handle_t handle;
  if (nvs_open("device", NVS_READWRITE, &handle) != ESP_OK) return false;
  esp_err_t result = hid_host_count
                         ? nvs_set_blob(handle, "hid_hosts", hid_hosts,
                                        hid_host_count * sizeof(hid_hosts[0]))
                         : nvs_erase_key(handle, "hid_hosts");
  if (result == ESP_ERR_NVS_NOT_FOUND) result = ESP_OK;
  if (result == ESP_OK) result = nvs_erase_key(handle, "hid_key");
  if (result == ESP_ERR_NVS_NOT_FOUND) result = ESP_OK;
  if (result == ESP_OK) result = nvs_set_u8(handle, "mode", (uint8_t)current_mode);
  if (result == ESP_OK) result = nvs_commit(handle);
  nvs_close(handle);
  return result == ESP_OK;
}

static void reload_locked(void) {
  current_mode = DEVICE_MODE_PIV;
  memset(hid_hosts, 0, sizeof(hid_hosts));
  hid_host_count = 0;
  fingerprint_profile_views = 0;
  typing_delay_ms = 7;
  submit_enter = true;
  touch_cooldown_ms = 800;

  nvs_handle_t handle;
  if (nvs_open("device", NVS_READONLY, &handle) != ESP_OK) return;

  uint8_t stored_mode = DEVICE_MODE_PIV;
  if (nvs_get_u8(handle, "mode", &stored_mode) == ESP_OK &&
      stored_mode <= DEVICE_MODE_HID) {
    current_mode = (device_mode_t)stored_mode;
  }

  uint8_t stored_views = 0;
  if (nvs_get_u8(handle, "finger_views", &stored_views) == ESP_OK && stored_views <= 5) {
    fingerprint_profile_views = stored_views;
  }

  uint16_t stored_u16 = 0;
  if (nvs_get_u16(handle, "type_delay", &stored_u16) == ESP_OK &&
      stored_u16 >= 1 && stored_u16 <= 100) {
    typing_delay_ms = stored_u16;
  }
  uint8_t stored_u8 = 0;
  if (nvs_get_u8(handle, "submit_enter", &stored_u8) == ESP_OK && stored_u8 <= 1) {
    submit_enter = stored_u8 != 0;
  }
  if (nvs_get_u16(handle, "touch_cool", &stored_u16) == ESP_OK &&
      stored_u16 >= 100 && stored_u16 <= 5000) {
    touch_cooldown_ms = stored_u16;
  }

  bool migrated_legacy_key = false;
  size_t hosts_length = sizeof(hid_hosts);
  if (nvs_get_blob(handle, "hid_hosts", hid_hosts, &hosts_length) == ESP_OK &&
      hosts_length > 0 && hosts_length % sizeof(hid_hosts[0]) == 0) {
    hid_host_count = hosts_length / sizeof(hid_hosts[0]);
    if (!hid_hosts_valid(hid_hosts, hid_host_count)) {
      ESP_LOGE(TAG, "stored HID host records failed integrity validation");
      memset(hid_hosts, 0, sizeof(hid_hosts));
      hid_host_count = 0;
    }
  } else {
    uint8_t legacy_key[32];
    size_t key_length = sizeof(legacy_key);
    if (nvs_get_blob(handle, "hid_key", legacy_key, &key_length) == ESP_OK &&
        key_length == sizeof(legacy_key)) {
      derive_key_id(legacy_key, hid_hosts[0].id);
      memcpy(hid_hosts[0].key, legacy_key, sizeof(legacy_key));
      hid_host_count = 1;
      migrated_legacy_key = true;
    }
    memset(legacy_key, 0, sizeof(legacy_key));
  }
  nvs_close(handle);

  if (current_mode == DEVICE_MODE_HID && hid_host_count == 0) {
    ESP_LOGE(TAG, "HID mode has no valid host credential; falling back to PIV mode");
    current_mode = DEVICE_MODE_PIV;
  }
  if (migrated_legacy_key) {
    if (save_hid_hosts()) {
      ESP_LOGI(TAG, "migrated the legacy HID credential to the host registry");
    } else {
      ESP_LOGW(TAG, "could not persist the legacy HID credential migration");
    }
  }
}

void device_config_init(void) {
  config_mutex = xSemaphoreCreateMutex();
  assert(config_mutex != NULL);
  config_lock();
  reload_locked();
  config_unlock();
}

void device_config_reload(void) {
  config_lock();
  reload_locked();
  config_unlock();
}

device_mode_t device_config_mode(void) {
  config_lock();
  device_mode_t mode = current_mode;
  config_unlock();
  return mode;
}

const char *device_config_mode_name(void) {
  return device_config_mode() == DEVICE_MODE_HID ? "hid" : "piv";
}

bool device_config_set_mode(device_mode_t mode) {
  if (mode != DEVICE_MODE_PIV && mode != DEVICE_MODE_HID) return false;
  config_lock();
  if (mode == DEVICE_MODE_HID && hid_host_count == 0) {
    config_unlock();
    return false;
  }

  nvs_handle_t handle;
  if (nvs_open("device", NVS_READWRITE, &handle) != ESP_OK) {
    config_unlock();
    return false;
  }
  esp_err_t result = nvs_set_u8(handle, "mode", (uint8_t)mode);
  if (result == ESP_OK) result = nvs_commit(handle);
  nvs_close(handle);
  if (result == ESP_OK) current_mode = mode;
  config_unlock();
  return result == ESP_OK;
}

bool device_config_hid_key_configured(void) {
  config_lock();
  bool configured = hid_host_count > 0;
  config_unlock();
  return configured;
}

bool device_config_get_hid_key(uint8_t key[32]) {
  if (!key) return false;
  config_lock();
  bool found = hid_host_count > 0;
  if (found) memcpy(key, hid_hosts[0].key, sizeof(hid_hosts[0].key));
  config_unlock();
  return found;
}

bool device_config_set_hid_key(const uint8_t key[32]) {
  if (!key) return false;
  config_lock();
  device_hid_host_t previous[DEVICE_CONFIG_MAX_HID_HOSTS];
  size_t previous_count = hid_host_count;
  memcpy(previous, hid_hosts, sizeof(previous));
  memset(hid_hosts, 0, sizeof(hid_hosts));
  derive_key_id(key, hid_hosts[0].id);
  memcpy(hid_hosts[0].key, key, sizeof(hid_hosts[0].key));
  hid_host_count = 1;
  if (save_hid_hosts()) {
    memset(previous, 0, sizeof(previous));
    config_unlock();
    return true;
  }
  memcpy(hid_hosts, previous, sizeof(hid_hosts));
  hid_host_count = previous_count;
  memset(previous, 0, sizeof(previous));
  config_unlock();
  return false;
}

size_t device_config_hid_host_count(void) {
  config_lock();
  size_t count = hid_host_count;
  config_unlock();
  return count;
}

bool device_config_get_hid_host(size_t index, device_hid_host_t *host) {
  if (!host) return false;
  config_lock();
  bool found = index < hid_host_count;
  if (found) memcpy(host, &hid_hosts[index], sizeof(*host));
  config_unlock();
  return found;
}

size_t device_config_copy_hid_hosts(
    device_hid_host_t hosts[DEVICE_CONFIG_MAX_HID_HOSTS]) {
  if (!hosts) return 0;
  config_lock();
  size_t count = hid_host_count;
  memcpy(hosts, hid_hosts, count * sizeof(hosts[0]));
  config_unlock();
  return count;
}

bool device_config_add_hid_host(const uint8_t id[DEVICE_CONFIG_HID_KEY_ID_SIZE],
                                const uint8_t key[32]) {
  if (!id || !key) return false;
  uint8_t derived_id[DEVICE_CONFIG_HID_KEY_ID_SIZE];
  derive_key_id(key, derived_id);
  if (memcmp(id, derived_id, sizeof(derived_id)) != 0) {
    memset(derived_id, 0, sizeof(derived_id));
    return false;
  }
  memset(derived_id, 0, sizeof(derived_id));
  config_lock();
  size_t index = hid_host_count;
  for (size_t i = 0; i < hid_host_count; i++) {
    if (memcmp(hid_hosts[i].id, id, sizeof(hid_hosts[i].id)) == 0) {
      index = i;
      break;
    }
  }
  if (index == DEVICE_CONFIG_MAX_HID_HOSTS) {
    config_unlock();
    return false;
  }
  device_hid_host_t previous = hid_hosts[index];
  size_t previous_count = hid_host_count;
  memcpy(hid_hosts[index].id, id, sizeof(hid_hosts[index].id));
  memcpy(hid_hosts[index].key, key, sizeof(hid_hosts[index].key));
  if (index == hid_host_count) hid_host_count++;
  if (save_hid_hosts()) {
    memset(&previous, 0, sizeof(previous));
    config_unlock();
    return true;
  }
  hid_hosts[index] = previous;
  hid_host_count = previous_count;
  config_unlock();
  return false;
}

bool device_config_remove_hid_host(const uint8_t id[DEVICE_CONFIG_HID_KEY_ID_SIZE]) {
  if (!id) return false;
  config_lock();
  for (size_t i = 0; i < hid_host_count; i++) {
    if (memcmp(hid_hosts[i].id, id, sizeof(hid_hosts[i].id)) != 0) continue;
    device_hid_host_t previous[DEVICE_CONFIG_MAX_HID_HOSTS];
    size_t previous_count = hid_host_count;
    device_mode_t previous_mode = current_mode;
    memcpy(previous, hid_hosts, sizeof(previous));
    if (i + 1 < hid_host_count) {
      memmove(&hid_hosts[i], &hid_hosts[i + 1],
              (hid_host_count - i - 1) * sizeof(hid_hosts[0]));
    }
    hid_host_count--;
    memset(&hid_hosts[hid_host_count], 0, sizeof(hid_hosts[0]));
    if (hid_host_count == 0 && current_mode == DEVICE_MODE_HID) {
      current_mode = DEVICE_MODE_PIV;
    }
    if (save_hid_hosts()) {
      memset(previous, 0, sizeof(previous));
      config_unlock();
      return true;
    }
    memcpy(hid_hosts, previous, sizeof(hid_hosts));
    hid_host_count = previous_count;
    current_mode = previous_mode;
    memset(previous, 0, sizeof(previous));
    config_unlock();
    return false;
  }
  config_unlock();
  return false;
}

uint8_t device_config_fingerprint_profile_views(void) {
  config_lock();
  uint8_t views = fingerprint_profile_views;
  config_unlock();
  return views;
}

bool device_config_set_fingerprint_profile_views(uint8_t views) {
  if (views > 5) return false;
  config_lock();
  nvs_handle_t handle;
  if (nvs_open("device", NVS_READWRITE, &handle) != ESP_OK) {
    config_unlock();
    return false;
  }
  esp_err_t result = views ? nvs_set_u8(handle, "finger_views", views)
                           : nvs_erase_key(handle, "finger_views");
  if (result == ESP_ERR_NVS_NOT_FOUND) result = ESP_OK;
  if (result == ESP_OK) result = nvs_commit(handle);
  nvs_close(handle);
  if (result == ESP_OK) fingerprint_profile_views = views;
  config_unlock();
  return result == ESP_OK;
}

uint16_t device_config_typing_delay_ms(void) {
  config_lock();
  uint16_t value = typing_delay_ms;
  config_unlock();
  return value;
}

bool device_config_set_typing_delay_ms(uint16_t value) {
  if (value < 1 || value > 100) return false;
  config_lock();
  bool saved = save_u16("type_delay", value);
  if (saved) typing_delay_ms = value;
  config_unlock();
  return saved;
}

bool device_config_submit_enter(void) {
  config_lock();
  bool value = submit_enter;
  config_unlock();
  return value;
}

bool device_config_set_submit_enter(bool value) {
  config_lock();
  bool saved = save_u8("submit_enter", value ? 1 : 0);
  if (saved) submit_enter = value;
  config_unlock();
  return saved;
}

uint16_t device_config_touch_cooldown_ms(void) {
  config_lock();
  uint16_t value = touch_cooldown_ms;
  config_unlock();
  return value;
}

bool device_config_set_touch_cooldown_ms(uint16_t value) {
  if (value < 100 || value > 5000) return false;
  config_lock();
  bool saved = save_u16("touch_cool", value);
  if (saved) touch_cooldown_ms = value;
  config_unlock();
  return saved;
}

#include "device_config.h"

#include <string.h>

#include "nvs.h"
#include "mbedtls/sha256.h"

static device_mode_t current_mode = DEVICE_MODE_PIV;
static device_hid_host_t hid_hosts[DEVICE_CONFIG_MAX_HID_HOSTS];
static size_t hid_host_count;
static uint8_t fingerprint_profile_views;

static void derive_key_id(const uint8_t key[32], uint8_t id[DEVICE_CONFIG_HID_KEY_ID_SIZE]) {
  uint8_t digest[32];
  mbedtls_sha256(key, 32, digest, 0);
  memcpy(id, digest, DEVICE_CONFIG_HID_KEY_ID_SIZE);
  memset(digest, 0, sizeof(digest));
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

void device_config_reload(void) {
  current_mode = DEVICE_MODE_PIV;
  memset(hid_hosts, 0, sizeof(hid_hosts));
  hid_host_count = 0;
  fingerprint_profile_views = 0;

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

  size_t hosts_length = sizeof(hid_hosts);
  if (nvs_get_blob(handle, "hid_hosts", hid_hosts, &hosts_length) == ESP_OK &&
      hosts_length > 0 && hosts_length % sizeof(hid_hosts[0]) == 0) {
    hid_host_count = hosts_length / sizeof(hid_hosts[0]);
    if (hid_host_count > DEVICE_CONFIG_MAX_HID_HOSTS) hid_host_count = 0;
  } else {
    uint8_t legacy_key[32];
    size_t key_length = sizeof(legacy_key);
    if (nvs_get_blob(handle, "hid_key", legacy_key, &key_length) == ESP_OK &&
        key_length == sizeof(legacy_key)) {
      derive_key_id(legacy_key, hid_hosts[0].id);
      memcpy(hid_hosts[0].key, legacy_key, sizeof(legacy_key));
      hid_host_count = 1;
    }
    memset(legacy_key, 0, sizeof(legacy_key));
  }
  nvs_close(handle);
}

void device_config_init(void) {
  device_config_reload();
}

device_mode_t device_config_mode(void) {
  return current_mode;
}

const char *device_config_mode_name(void) {
  return current_mode == DEVICE_MODE_HID ? "hid" : "piv";
}

bool device_config_set_mode(device_mode_t mode) {
  if (mode != DEVICE_MODE_PIV && mode != DEVICE_MODE_HID) return false;
  if (mode == DEVICE_MODE_HID && hid_host_count == 0) return false;

  nvs_handle_t handle;
  if (nvs_open("device", NVS_READWRITE, &handle) != ESP_OK) return false;
  esp_err_t result = nvs_set_u8(handle, "mode", (uint8_t)mode);
  if (result == ESP_OK) result = nvs_commit(handle);
  nvs_close(handle);
  if (result == ESP_OK) current_mode = mode;
  return result == ESP_OK;
}

bool device_config_hid_key_configured(void) {
  return hid_host_count > 0;
}

bool device_config_get_hid_key(uint8_t key[32]) {
  if (hid_host_count == 0) return false;
  memcpy(key, hid_hosts[0].key, sizeof(hid_hosts[0].key));
  return true;
}

bool device_config_set_hid_key(const uint8_t key[32]) {
  device_hid_host_t previous[DEVICE_CONFIG_MAX_HID_HOSTS];
  size_t previous_count = hid_host_count;
  memcpy(previous, hid_hosts, sizeof(previous));
  memset(hid_hosts, 0, sizeof(hid_hosts));
  derive_key_id(key, hid_hosts[0].id);
  memcpy(hid_hosts[0].key, key, sizeof(hid_hosts[0].key));
  hid_host_count = 1;
  if (save_hid_hosts()) {
    memset(previous, 0, sizeof(previous));
    return true;
  }
  memcpy(hid_hosts, previous, sizeof(hid_hosts));
  hid_host_count = previous_count;
  memset(previous, 0, sizeof(previous));
  return false;
}

size_t device_config_hid_host_count(void) {
  return hid_host_count;
}

bool device_config_get_hid_host(size_t index, device_hid_host_t *host) {
  if (!host || index >= hid_host_count) return false;
  memcpy(host, &hid_hosts[index], sizeof(*host));
  return true;
}

bool device_config_add_hid_host(const uint8_t id[DEVICE_CONFIG_HID_KEY_ID_SIZE],
                                const uint8_t key[32]) {
  uint8_t derived_id[DEVICE_CONFIG_HID_KEY_ID_SIZE];
  derive_key_id(key, derived_id);
  if (memcmp(id, derived_id, sizeof(derived_id)) != 0) {
    memset(derived_id, 0, sizeof(derived_id));
    return false;
  }
  memset(derived_id, 0, sizeof(derived_id));
  size_t index = hid_host_count;
  for (size_t i = 0; i < hid_host_count; i++) {
    if (memcmp(hid_hosts[i].id, id, sizeof(hid_hosts[i].id)) == 0) {
      index = i;
      break;
    }
  }
  if (index == DEVICE_CONFIG_MAX_HID_HOSTS) return false;
  device_hid_host_t previous = hid_hosts[index];
  size_t previous_count = hid_host_count;
  memcpy(hid_hosts[index].id, id, sizeof(hid_hosts[index].id));
  memcpy(hid_hosts[index].key, key, sizeof(hid_hosts[index].key));
  if (index == hid_host_count) hid_host_count++;
  if (save_hid_hosts()) {
    memset(&previous, 0, sizeof(previous));
    return true;
  }
  hid_hosts[index] = previous;
  hid_host_count = previous_count;
  return false;
}

bool device_config_remove_hid_host(const uint8_t id[DEVICE_CONFIG_HID_KEY_ID_SIZE]) {
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
      return true;
    }
    memcpy(hid_hosts, previous, sizeof(hid_hosts));
    hid_host_count = previous_count;
    current_mode = previous_mode;
    memset(previous, 0, sizeof(previous));
    return false;
  }
  return false;
}

uint8_t device_config_fingerprint_profile_views(void) {
  return fingerprint_profile_views;
}

bool device_config_set_fingerprint_profile_views(uint8_t views) {
  if (views > 5) return false;
  nvs_handle_t handle;
  if (nvs_open("device", NVS_READWRITE, &handle) != ESP_OK) return false;
  esp_err_t result = views ? nvs_set_u8(handle, "finger_views", views)
                           : nvs_erase_key(handle, "finger_views");
  if (result == ESP_ERR_NVS_NOT_FOUND) result = ESP_OK;
  if (result == ESP_OK) result = nvs_commit(handle);
  nvs_close(handle);
  if (result == ESP_OK) fingerprint_profile_views = views;
  return result == ESP_OK;
}

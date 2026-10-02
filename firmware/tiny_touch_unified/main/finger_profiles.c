#include "finger_profiles.h"
#include <string.h>
#include "nvs.h"

static bool profiles_valid(const finger_profiles_t *profiles) {
  if (profiles->version != 1) return false;
  if (!profiles->pending) return true;
  for (unsigned finger = 1; finger <= FINGER_PROFILE_COUNT; finger++) {
    if (profiles->pending == finger_profiles_block(finger)) return true;
  }
  return false;
}

bool finger_profiles_load(finger_profiles_t *profiles) {
  memset(profiles, 0, sizeof(*profiles));
  profiles->version = 1;
  nvs_handle_t handle;
  esp_err_t result = nvs_open("tt6", NVS_READONLY, &handle);
  if (result == ESP_ERR_NVS_NOT_FOUND) return true;
  if (result != ESP_OK) return false;
  finger_profiles_t stored;
  size_t length = sizeof(stored);
  result = nvs_get_blob(handle, "fingers", &stored, &length);
  nvs_close(handle);
  if (result == ESP_ERR_NVS_NOT_FOUND) return true;
  if (result != ESP_OK || length != sizeof(stored) || !profiles_valid(&stored)) return false;
  *profiles = stored;
  return true;
}

bool finger_profiles_save(const finger_profiles_t *profiles) {
  if (!profiles_valid(profiles)) return false;
  nvs_handle_t handle;
  if (nvs_open("tt6", NVS_READWRITE, &handle) != ESP_OK) return false;
  esp_err_t result = nvs_set_blob(handle, "fingers", profiles, sizeof(*profiles));
  if (result == ESP_OK) result = nvs_commit(handle);
  nvs_close(handle);
  return result == ESP_OK;
}

unsigned finger_profiles_count(uint64_t mask) {
  unsigned count = 0;
  while (mask) { count += mask & 1; mask >>= 1; }
  return count;
}

uint64_t finger_profiles_block(unsigned finger) {
  if (!finger || finger > FINGER_PROFILE_COUNT) return 0;
  uint64_t mask = 0;
  for (unsigned logical = (finger - 1) * FINGER_PROFILE_VIEWS + 1;
       logical <= finger * FINGER_PROFILE_VIEWS; logical++) {
    unsigned physical = logical == FINGER_TEMPLATE_LIMIT ? 0 : logical;
    mask |= UINT64_C(1) << physical;
  }
  return mask;
}

bool finger_profiles_block_fits(unsigned finger, unsigned capacity) {
  uint64_t block = finger_profiles_block(finger);
  if (!block || !capacity) return false;
  if (capacity >= FINGER_TEMPLATE_LIMIT) return true;
  return (block >> capacity) == 0;
}

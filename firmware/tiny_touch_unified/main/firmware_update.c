#include "firmware_update.h"

#include <string.h>

#include "esp_ota_ops.h"
#include "mbedtls/sha256.h"

static const esp_partition_t *partition;
static esp_ota_handle_t handle;
static size_t expected_size;
static size_t written_size;
static uint8_t expected_hash[32];
static mbedtls_sha256_context hash_context;
static bool hash_started;
typedef enum {
  OTA_IDLE,
  OTA_WRITING,
  OTA_STAGED,
} ota_state_t;

static ota_state_t state;

void firmware_update_abort(void) {
  if (state == OTA_WRITING) esp_ota_abort(handle);
  if (hash_started) mbedtls_sha256_free(&hash_context);
  partition = NULL;
  handle = 0;
  expected_size = 0;
  written_size = 0;
  memset(expected_hash, 0, sizeof(expected_hash));
  hash_started = false;
  state = OTA_IDLE;
}

bool firmware_update_begin(size_t size, const uint8_t expected_sha256[32]) {
  if (!expected_sha256) return false;
  firmware_update_abort();
  partition = esp_ota_get_next_update_partition(NULL);
  if (!partition || size == 0 || size > partition->size ||
      esp_ota_begin(partition, size, &handle) != ESP_OK) {
    partition = NULL;
    return false;
  }
  mbedtls_sha256_init(&hash_context);
  if (mbedtls_sha256_starts(&hash_context, 0) != 0) {
    esp_ota_abort(handle);
    partition = NULL;
    return false;
  }
  memcpy(expected_hash, expected_sha256, sizeof(expected_hash));
  expected_size = size;
  hash_started = true;
  state = OTA_WRITING;
  return true;
}

bool firmware_update_write(size_t offset, const uint8_t *data, size_t length) {
  if (state != OTA_WRITING || !data || offset != written_size || length == 0 ||
      length > FIRMWARE_UPDATE_CHUNK_MAX || length > expected_size - written_size ||
      esp_ota_write(handle, data, length) != ESP_OK ||
      mbedtls_sha256_update(&hash_context, data, length) != 0) {
    firmware_update_abort();
    return false;
  }
  written_size += length;
  return true;
}

bool firmware_update_commit(void) {
  if (state != OTA_WRITING || written_size != expected_size) return false;
  uint8_t actual_hash[32];
  bool ok = mbedtls_sha256_finish(&hash_context, actual_hash) == 0 &&
            memcmp(actual_hash, expected_hash, sizeof(actual_hash)) == 0;
  mbedtls_sha256_free(&hash_context);
  hash_started = false;
  if (ok) ok = esp_ota_end(handle) == ESP_OK && esp_ota_set_boot_partition(partition) == ESP_OK;
  memset(actual_hash, 0, sizeof(actual_hash));
  partition = NULL;
  handle = 0;
  expected_size = 0;
  written_size = 0;
  memset(expected_hash, 0, sizeof(expected_hash));
  state = ok ? OTA_STAGED : OTA_IDLE;
  return ok;
}

bool firmware_update_active(void) { return state == OTA_WRITING; }
bool firmware_update_staged(void) { return state == OTA_STAGED; }
size_t firmware_update_written(void) { return written_size; }

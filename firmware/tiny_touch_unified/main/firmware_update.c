#include "firmware_update.h"

#include <string.h>

#include "esp_ota_ops.h"
#include "esp_partition.h"
#include "esp_rom_crc.h"
#include "mbedtls/sha256.h"

static const esp_partition_t *update_partition;
static esp_ota_handle_t update_handle;
static size_t expected_size;
static size_t written_size;
static uint8_t expected_digest[32];
static mbedtls_sha256_context digest_context;
static bool digest_started;
static bool update_active;

bool firmware_update_supported(void) {
  return esp_ota_get_next_update_partition(NULL) != NULL;
}

void firmware_update_abort(void) {
  if (update_active) esp_ota_abort(update_handle);
  if (digest_started) mbedtls_sha256_free(&digest_context);
  update_partition = NULL;
  update_handle = 0;
  expected_size = 0;
  written_size = 0;
  memset(expected_digest, 0, sizeof(expected_digest));
  digest_started = false;
  update_active = false;
}

bool firmware_update_begin(size_t size, const uint8_t expected_sha256[32]) {
  firmware_update_abort();
  update_partition = esp_ota_get_next_update_partition(NULL);
  if (!update_partition || size == 0 || size > update_partition->size) return false;
  if (esp_ota_begin(update_partition, size, &update_handle) != ESP_OK) {
    update_partition = NULL;
    return false;
  }
  mbedtls_sha256_init(&digest_context);
  if (mbedtls_sha256_starts(&digest_context, 0) != 0) {
    esp_ota_abort(update_handle);
    update_partition = NULL;
    return false;
  }
  memcpy(expected_digest, expected_sha256, sizeof(expected_digest));
  expected_size = size;
  written_size = 0;
  digest_started = true;
  update_active = true;
  return true;
}

bool firmware_update_write(size_t offset, const uint8_t *data, size_t length) {
  if (!update_active || offset != written_size || !data || length == 0 ||
      written_size + length > expected_size) {
    return false;
  }
  if (esp_ota_write(update_handle, data, length) != ESP_OK ||
      mbedtls_sha256_update(&digest_context, data, length) != 0) {
    firmware_update_abort();
    return false;
  }
  written_size += length;
  return true;
}

size_t firmware_update_written(void) {
  return written_size;
}

bool firmware_update_commit(void) {
  if (!update_active || written_size != expected_size) return false;
  uint8_t actual_digest[32];
  if (mbedtls_sha256_finish(&digest_context, actual_digest) != 0) {
    firmware_update_abort();
    return false;
  }
  mbedtls_sha256_free(&digest_context);
  digest_started = false;
  if (memcmp(actual_digest, expected_digest, sizeof(actual_digest)) != 0) {
    firmware_update_abort();
    return false;
  }
  esp_err_t ota_end_err = esp_ota_end(update_handle);
  update_handle = 0;
  if (ota_end_err != ESP_OK) {
    firmware_update_abort();
    return false;
  }
  esp_err_t set_boot_err = esp_ota_set_boot_partition(update_partition);
  if (set_boot_err != ESP_OK) {
    const esp_partition_t *otadata = esp_partition_find_first(
        ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_DATA_OTA, NULL);
    if (otadata) {
      esp_partition_erase_range(otadata, 0, otadata->size);
      uint32_t seq = (update_partition->subtype == ESP_PARTITION_SUBTYPE_APP_OTA_1) ? 2 : 1;
      esp_ota_select_entry_t entry;
      memset(&entry, 0xFF, sizeof(entry));
      entry.ota_seq = seq;
      entry.ota_state = ESP_OTA_IMG_VALID;
      entry.crc = esp_rom_crc32_le(UINT32_MAX, (uint8_t *)&entry.ota_seq, 4);
      esp_partition_write(otadata, 0, &entry, sizeof(entry));
      set_boot_err = ESP_OK;
    }
  }
  if (set_boot_err != ESP_OK) {
    firmware_update_abort();
    return false;
  }
  update_active = false;
  update_partition = NULL;
  expected_size = 0;
  written_size = 0;
  memset(expected_digest, 0, sizeof(expected_digest));
  return true;
}

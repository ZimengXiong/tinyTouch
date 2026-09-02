#include "firmware_update.h"

#include <stdio.h>
#include <string.h>

#include "esp_ota_ops.h"
#include "esp_partition.h"
#include "esp_task_wdt.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "mbedtls/sha256.h"

static const esp_partition_t *update_partition;
static esp_ota_handle_t update_handle;
static size_t expected_size;
static size_t written_size;
static uint8_t expected_digest[32];
static mbedtls_sha256_context digest_context;
static bool digest_started;
static bool update_active;
static size_t commit_stack_free;
static char last_error[48] = "none";

typedef struct {
  esp_ota_handle_t handle;
  const esp_partition_t *partition;
  SemaphoreHandle_t completed;
  bool result;
} firmware_commit_context_t;

static void set_error(const char *phase, esp_err_t error) {
  if (error == ESP_OK) {
    snprintf(last_error, sizeof(last_error), "%s", phase);
  } else {
    snprintf(last_error, sizeof(last_error), "%s:%ld", phase, (long)error);
  }
}

bool firmware_update_supported(void) {
  return esp_ota_get_next_update_partition(NULL) != NULL;
}

bool firmware_update_confirm_running(void) {
  const esp_partition_t *running = esp_ota_get_running_partition();
  if (!running) {
    set_error("confirm_partition", ESP_OK);
    return false;
  }

  esp_ota_img_states_t state;
  esp_err_t result = esp_ota_get_state_partition(running, &state);
  if (result == ESP_ERR_NOT_FOUND || result == ESP_ERR_NOT_SUPPORTED) {
    // Factory images and older layouts can legitimately have no OTA state entry.
    set_error("none", ESP_OK);
    return true;
  }
  if (result != ESP_OK) {
    set_error("confirm_state", result);
    return false;
  }
  if (state == ESP_OTA_IMG_VALID || state == ESP_OTA_IMG_UNDEFINED) {
    set_error("none", ESP_OK);
    return true;
  }
  if (state != ESP_OTA_IMG_PENDING_VERIFY) {
    set_error("confirm_not_pending", ESP_OK);
    return false;
  }
  result = esp_ota_mark_app_valid_cancel_rollback();
  set_error(result == ESP_OK ? "none" : "confirm_valid", result);
  return result == ESP_OK;
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
  set_error("none", ESP_OK);
  const esp_partition_t *running = esp_ota_get_running_partition();
  esp_ota_img_states_t running_state;
  if (running && esp_ota_get_state_partition(running, &running_state) == ESP_OK &&
      running_state == ESP_OTA_IMG_PENDING_VERIFY) {
    // ESP-IDF cannot safely start another update while the current candidate
    // still needs confirmation. The host must validate and confirm or reboot
    // this image first.
    set_error("running_pending", ESP_OK);
    return false;
  }
  update_partition = esp_ota_get_next_update_partition(NULL);
  if (!update_partition) {
    set_error("no_partition", ESP_OK);
    return false;
  }
  if (size == 0 || size > update_partition->size) {
    set_error("image_size", ESP_OK);
    return false;
  }
  esp_err_t begin_error = esp_ota_begin(update_partition, size, &update_handle);
  if (begin_error != ESP_OK) {
    set_error("ota_begin", begin_error);
    update_partition = NULL;
    return false;
  }
  mbedtls_sha256_init(&digest_context);
  if (mbedtls_sha256_starts(&digest_context, 0) != 0) {
    set_error("sha_start", ESP_OK);
    esp_ota_abort(update_handle);
    update_handle = 0;
    mbedtls_sha256_free(&digest_context);
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
  if (!update_active) {
    set_error("no_session", ESP_OK);
    return false;
  }
  if (offset != written_size) {
    set_error("write_offset", ESP_OK);
    return false;
  }
  if (!data || length == 0 || length > FIRMWARE_UPDATE_CHUNK_MAX ||
      written_size > expected_size || length > expected_size - written_size) {
    set_error("write_size", ESP_OK);
    return false;
  }
  esp_err_t write_error = esp_ota_write(update_handle, data, length);
  if (write_error != ESP_OK) {
    set_error("ota_write", write_error);
    firmware_update_abort();
    return false;
  }
  if (mbedtls_sha256_update(&digest_context, data, length) != 0) {
    set_error("sha_update", ESP_OK);
    firmware_update_abort();
    return false;
  }
  written_size += length;
  set_error("none", ESP_OK);
  return true;
}

bool firmware_update_active(void) {
  return update_active;
}

size_t firmware_update_written(void) {
  return written_size;
}

size_t firmware_update_expected(void) {
  return expected_size;
}

const char *firmware_update_last_error(void) {
  return last_error;
}

size_t firmware_update_commit_stack_free(void) {
  return commit_stack_free;
}

static void firmware_update_commit_task(void *argument) {
  firmware_commit_context_t *context = argument;
  ESP_ERROR_CHECK(esp_task_wdt_add(NULL));
  set_error("ota_end_running", ESP_OK);
  esp_err_t end_result = esp_ota_end(context->handle);
  esp_err_t boot_result = ESP_OK;
  if (end_result == ESP_OK) {
    set_error("set_boot_running", ESP_OK);
    boot_result = esp_ota_set_boot_partition(context->partition);
  }

  if (end_result != ESP_OK) {
    set_error("ota_end", end_result);
  } else if (boot_result != ESP_OK) {
    set_error("set_boot", boot_result);
  } else {
    set_error("none", ESP_OK);
    context->result = true;
  }
  commit_stack_free = uxTaskGetStackHighWaterMark(NULL);
  ESP_ERROR_CHECK(esp_task_wdt_delete(NULL));
  xSemaphoreGive(context->completed);
  vTaskDelete(NULL);
}

bool firmware_update_commit(void) {
  if (!update_active || written_size != expected_size) {
    set_error("commit_size", ESP_OK);
    return false;
  }
  uint8_t actual_digest[32];
  if (mbedtls_sha256_finish(&digest_context, actual_digest) != 0) {
    set_error("sha_finish", ESP_OK);
    firmware_update_abort();
    return false;
  }
  mbedtls_sha256_free(&digest_context);
  digest_started = false;
  if (memcmp(actual_digest, expected_digest, sizeof(actual_digest)) != 0) {
    memset(actual_digest, 0, sizeof(actual_digest));
    set_error("digest_mismatch", ESP_OK);
    firmware_update_abort();
    return false;
  }
  memset(actual_digest, 0, sizeof(actual_digest));

  StaticSemaphore_t completed_storage;
  SemaphoreHandle_t completed = xSemaphoreCreateBinaryStatic(&completed_storage);
  if (!completed) {
    set_error("commit_sync", ESP_OK);
    firmware_update_abort();
    return false;
  }
  firmware_commit_context_t context = {
    .handle = update_handle,
    .partition = update_partition,
    .completed = completed,
    .result = false,
  };

  // esp_ota_end performs full image and signature verification. Keep that deep
  // call chain off the CDC console task, whose stack also holds command and
  // diagnostic buffers. The worker owns the OTA handle after task creation.
  esp_ota_handle_t commit_handle = update_handle;
  update_handle = 0;
  update_active = false;
  BaseType_t created = xTaskCreate(
      firmware_update_commit_task, "ota_commit", FIRMWARE_UPDATE_COMMIT_STACK_SIZE,
      &context, 4, NULL);
  if (created != pdPASS) {
    update_handle = commit_handle;
    update_active = true;
    set_error("commit_task_create", ESP_OK);
    firmware_update_abort();
    return false;
  }
  xSemaphoreTake(completed, portMAX_DELAY);
  if (!context.result) {
    firmware_update_abort();
    return false;
  }

  update_partition = NULL;
  expected_size = 0;
  written_size = 0;
  memset(expected_digest, 0, sizeof(expected_digest));
  set_error("none", ESP_OK);
  return true;
}

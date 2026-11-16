#pragma once

#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

typedef int *SemaphoreHandle_t;
typedef int esp_err_t;
typedef int nvs_handle_t;

#define ESP_OK 0
#define ESP_FAIL -1
#define ESP_ERR_NVS_NOT_FOUND 0x1102
#define NVS_READONLY 0
#define NVS_READWRITE 1
#define portMAX_DELAY 0xffffffffu
#define configASSERT(expression) assert(expression)
#define pdMS_TO_TICKS(ms) ((TickType_t)(ms))
#define portTICK_PERIOD_MS 1

typedef uint32_t TickType_t;
static TickType_t tick_count;
static inline TickType_t xTaskGetTickCount(void) { return tick_count; }

// One in-memory NVS blob, with write failure injection for power-loss cases.
static uint8_t stored_blob[128];
static size_t stored_length;
static bool stored_present;
static int failing_writes;
static int fail_at_write;
static int writes;

static inline bool write_fails(void) {
  writes++;
  if (writes == fail_at_write) return true;
  if (failing_writes > 0) { failing_writes--; return true; }
  return false;
}
static int mutex_depth;

static inline SemaphoreHandle_t xSemaphoreCreateMutex(void) {
  static int mutex;
  return &mutex;
}
static inline int xSemaphoreTake(SemaphoreHandle_t mutex, uint32_t ticks) {
  (void)mutex; (void)ticks;
  assert(mutex_depth == 0);
  mutex_depth++;
  return 1;
}
static inline int xSemaphoreGive(SemaphoreHandle_t mutex) {
  (void)mutex;
  assert(mutex_depth == 1);
  mutex_depth--;
  return 1;
}
static inline void esp_fill_random(void *out, size_t length) {
  static uint8_t next = 1;
  uint8_t *bytes = (uint8_t *)out;
  for (size_t i = 0; i < length; i++) bytes[i] = next++;
}
// Deterministic stand-in for SHA-256. Tests cover policy, not the digest.
static inline int mbedtls_sha256(const unsigned char *data, size_t length,
                                 unsigned char output[32], int is224) {
  (void)is224;
  uint32_t state = 2166136261u;
  for (size_t round = 0; round < 32; round++) {
    for (size_t i = 0; i < length; i++) state = (state ^ data[i]) * 16777619u;
    output[round] = (unsigned char)(state >> 24);
  }
  return 0;
}
static inline esp_err_t nvs_open(const char *name, int mode, nvs_handle_t *handle) {
  assert(strcmp(name, "piv_keys") == 0);
  (void)mode;
  *handle = 1;
  return ESP_OK;
}
static inline void nvs_close(nvs_handle_t handle) { (void)handle; }
static inline esp_err_t nvs_commit(nvs_handle_t handle) { (void)handle; return ESP_OK; }
static inline esp_err_t nvs_get_blob(nvs_handle_t handle, const char *key, void *out, size_t *length) {
  (void)handle;
  assert(strcmp(key, "pin_policy") == 0);
  if (!stored_present) return ESP_ERR_NVS_NOT_FOUND;
  if (*length < stored_length) return ESP_FAIL;
  memcpy(out, stored_blob, stored_length);
  *length = stored_length;
  return ESP_OK;
}
static inline esp_err_t nvs_set_blob(nvs_handle_t handle, const char *key, const void *data, size_t length) {
  (void)handle;
  assert(strcmp(key, "pin_policy") == 0 && length <= sizeof(stored_blob));
  if (write_fails()) return ESP_FAIL;
  memcpy(stored_blob, data, length);
  stored_length = length;
  stored_present = true;
  return ESP_OK;
}
static inline esp_err_t nvs_erase_key(nvs_handle_t handle, const char *key) {
  (void)handle;
  assert(strcmp(key, "pin_policy") == 0);
  if (write_fails()) return ESP_FAIL;
  if (!stored_present) return ESP_ERR_NVS_NOT_FOUND;
  memset(stored_blob, 0, sizeof(stored_blob));
  stored_present = false;
  return ESP_OK;
}

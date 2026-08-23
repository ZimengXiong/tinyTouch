#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "nvs.h"
#include "nvs_flash.h"

#include "fingerprint.h"
#include "config_console.h"
#include "device_config.h"
#include "piv.h"
#include "touch_pin_hid.h"
#include "usb_ccid.h"

#ifdef TINYTOUCH_RECOVERY_BUILD
static const char *TAG = "tiny_touch";

static bool recovery_already_completed(void) {
  nvs_handle_t handle;
  if (nvs_open("recovery", NVS_READONLY, &handle) != ESP_OK) return false;
  uint8_t completed = 0;
  esp_err_t result = nvs_get_u8(handle, "completed", &completed);
  nvs_close(handle);
  return result == ESP_OK && completed == 1;
}

static void mark_recovery_completed(void) {
  nvs_handle_t handle;
  ESP_ERROR_CHECK(nvs_open("recovery", NVS_READWRITE, &handle));
  ESP_ERROR_CHECK(nvs_set_u8(handle, "completed", 1));
  ESP_ERROR_CHECK(nvs_commit(handle));
  nvs_close(handle);
}

static void run_recovery_once(void) {
  if (recovery_already_completed()) {
    ESP_LOGI(TAG, "recovery already completed; starting unified firmware");
    return;
  }

  ESP_LOGW(TAG, "RECOVERY: erasing fingerprint templates and device settings");
  bool sensor_empty = false;
  for (int attempt = 1; attempt <= 15; attempt++) {
    int count = fingerprint_count();
    if (count == 0) {
      sensor_empty = true;
      break;
    }
    if (count > 0 && fingerprint_delete_all() && fingerprint_count() == 0) {
      sensor_empty = true;
      break;
    }
    ESP_LOGW(TAG, "RECOVERY: sensor erase attempt %d of 15 failed", attempt);
    vTaskDelay(pdMS_TO_TICKS(1000));
  }

  if (!sensor_empty) {
    ESP_LOGE(TAG, "RECOVERY FAILED: fingerprint sensor did not respond; settings preserved");
    return;
  }

  ESP_ERROR_CHECK(nvs_flash_erase());
  ESP_ERROR_CHECK(nvs_flash_init());
  mark_recovery_completed();
  ESP_LOGW(TAG, "RECOVERY COMPLETE: fingerprints, keys, and settings erased");
}
#endif

void app_main(void) {
  esp_err_t nvs_result = nvs_flash_init();
  if (nvs_result == ESP_ERR_NVS_NO_FREE_PAGES ||
      nvs_result == ESP_ERR_NVS_NEW_VERSION_FOUND) {
    ESP_ERROR_CHECK(nvs_flash_erase());
    nvs_result = nvs_flash_init();
  }
  ESP_ERROR_CHECK(nvs_result);
  fingerprint_init();
#ifdef TINYTOUCH_RECOVERY_BUILD
  run_recovery_once();
#endif
  device_config_init();
  piv_init();
  usb_ccid_start(piv_handle_apdu);
  config_console_start();
  touch_pin_hid_start();

  // tinyusb_driver_install owns the only tud_task() service loop. The
  // configuration console and fingerprint/HID workers run in their own tasks.
}

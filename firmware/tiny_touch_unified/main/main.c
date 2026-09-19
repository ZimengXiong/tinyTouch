#include "esp_err.h"
#include "esp_log.h"
#include "esp_ota_ops.h"
#include "esp_partition.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "nvs_flash.h"

#include <string.h>

#include "config_console.h"
#include "device_config.h"
#include "fingerprint.h"
#include "piv.h"
#include "touch_pin_hid.h"
#include "usb_ccid.h"
#include "usb_ncm.h"
#include "web_dashboard.h"

#ifdef TINYTOUCH_RECOVERY_BUILD
static const char RECOVERY_REQUEST[] = "tinyTouch recovery request v1";
static const esp_partition_t *recovery_partition(void) {
  return esp_partition_find_first(ESP_PARTITION_TYPE_DATA, 0x40, "recovery");
}

static void recover_device(void) {
  const esp_partition_t *partition = recovery_partition();
  char request[sizeof(RECOVERY_REQUEST)] = {0};
  if (!partition ||
      esp_partition_read(partition, 0, request, sizeof(request)) != ESP_OK ||
      memcmp(request, RECOVERY_REQUEST, sizeof(request)) != 0) return;

  ESP_LOGW("tiny_touch", "RECOVERY: clearing fingerprint sensor");
  for (int attempt = 1; attempt <= 15; attempt++) {
    int count = fingerprint_count();
    if (count == 0 ||
        (count > 0 && fingerprint_delete_all() && fingerprint_count() == 0)) {
      ESP_ERROR_CHECK(nvs_flash_erase());
      ESP_ERROR_CHECK(nvs_flash_init());
      ESP_ERROR_CHECK(esp_partition_erase_range(partition, 0, partition->size));
      ESP_LOGW("tiny_touch", "RECOVERY COMPLETE: sensor and device state cleared");
      return;
    }
    ESP_LOGW("tiny_touch", "RECOVERY: sensor clear attempt %d of 15 failed", attempt);
    vTaskDelay(pdMS_TO_TICKS(1000));
  }

  ESP_LOGE("tiny_touch", "RECOVERY FAILED: preserving device state");
}
#endif

void app_main(void) {
  esp_err_t nvs_result = nvs_flash_init();
#ifndef TINYTOUCH_RECOVERY_BUILD
  ESP_ERROR_CHECK(nvs_result);
#else
  if (nvs_result != ESP_OK) {
    ESP_LOGW("tiny_touch", "RECOVERY: device state unavailable before sensor clear");
  }
#endif
  fingerprint_init();
#ifdef TINYTOUCH_RECOVERY_BUILD
  recover_device();
#endif
  device_config_init();
  // Prime the sensor's live-detection state before the HID task begins. This
  // is the same probe STATUS performs; doing it at boot avoids requiring a
  // host status command after USB reconnect before the first fingerprint.
  (void)fingerprint_count();
  piv_init();
  usb_ccid_start(piv_handle_apdu);
  usb_ncm_start();
  web_dashboard_start();
  config_console_start();
  touch_pin_hid_start();
  // All persistent state and runtime services initialized successfully. Keep
  // this OTA slot across later power cycles instead of rolling back once.
  (void)esp_ota_mark_app_valid_cancel_rollback();
}

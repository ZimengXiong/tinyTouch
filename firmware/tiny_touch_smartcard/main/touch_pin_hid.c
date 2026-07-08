#include "touch_pin_hid.h"

#include "class/hid/hid_device.h"
#include "esp_log.h"
#include "fingerprint.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "piv.h"
#include "usb_descriptors.h"

static const char *TAG = "touch_pin_hid";

static void wait_hid_ready(void) {
  while (!tud_hid_ready()) vTaskDelay(pdMS_TO_TICKS(10));
}

static void send_report(uint8_t const report[6]) {
  wait_hid_ready();
  tud_hid_keyboard_report(0, 0, report);
  vTaskDelay(pdMS_TO_TICKS(5));
}

static void release_keys(void) {
  wait_hid_ready();
  tud_hid_keyboard_report(0, 0, NULL);
  vTaskDelay(pdMS_TO_TICKS(5));
}

static void send_key(uint8_t key) {
  uint8_t report[6] = {key, 0, 0, 0, 0, 0};
  send_report(report);
  release_keys();
}

static void type_dummy_pin(void) {
  for (int i = 0; i < 6; i++) send_key(HID_KEY_0);
  send_key(HID_KEY_ENTER);
}

static void touch_pin_task(void *arg) {
  (void)arg;
  TickType_t last_success = 0;

  while (true) {
    if (tud_hid_ready() &&
        (xTaskGetTickCount() - last_success) > pdMS_TO_TICKS(3000)) {
      if (fingerprint_authorize_poll_once()) {
        ESP_LOGI(TAG, "finger matched; typing PIN submit");
        piv_note_biometric_verified();
        type_dummy_pin();
        last_success = xTaskGetTickCount();
      }
    }

    vTaskDelay(pdMS_TO_TICKS(250));
  }
}

void touch_pin_hid_start(void) {
  xTaskCreate(touch_pin_task, "touch_pin_hid", 4096, NULL, 4, NULL);
}

uint8_t const *tud_hid_descriptor_report_cb(uint8_t instance) {
  (void)instance;
  return tiny_touch_hid_report_descriptor;
}

uint16_t tud_hid_get_report_cb(uint8_t instance, uint8_t report_id,
                               hid_report_type_t report_type, uint8_t *buffer,
                               uint16_t reqlen) {
  (void)instance;
  (void)report_id;
  (void)report_type;
  (void)buffer;
  (void)reqlen;
  return 0;
}

void tud_hid_set_report_cb(uint8_t instance, uint8_t report_id,
                           hid_report_type_t report_type,
                           uint8_t const *buffer, uint16_t bufsize) {
  (void)instance;
  (void)report_id;
  (void)report_type;
  (void)buffer;
  (void)bufsize;
}

#include "esp_err.h"
#include "esp_ota_ops.h"
#include "nvs_flash.h"

#include "config_console.h"
#include "device_config.h"
#include "fingerprint.h"
#include "piv.h"
#include "touch_pin_hid.h"
#include "usb_ccid.h"

void app_main(void) {
  ESP_ERROR_CHECK(nvs_flash_init());
  device_config_init();
  fingerprint_init();
  // Prime the sensor's live-detection state before the HID task begins. This
  // is the same probe STATUS performs; doing it at boot avoids requiring a
  // host status command after USB reconnect before the first fingerprint.
  (void)fingerprint_count();
  piv_init();
  usb_ccid_start(piv_handle_apdu);
  config_console_start();
  touch_pin_hid_start();
  // All persistent state and runtime services initialized successfully. Keep
  // this OTA slot across later power cycles instead of rolling back once.
  (void)esp_ota_mark_app_valid_cancel_rollback();
}

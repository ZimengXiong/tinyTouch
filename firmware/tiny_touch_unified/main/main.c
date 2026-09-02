#include "esp_err.h"
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
  piv_init();
  usb_ccid_start(piv_handle_apdu);
  config_console_start();
  touch_pin_hid_start();
}

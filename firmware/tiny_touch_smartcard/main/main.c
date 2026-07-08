#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "fingerprint.h"
#include "piv.h"
#include "touch_pin_hid.h"
#include "usb_ccid.h"

void app_main(void) {
  fingerprint_init();
  piv_init();
  usb_ccid_start(piv_handle_apdu);
  touch_pin_hid_start();

  while (true) {
    usb_ccid_task();
    vTaskDelay(pdMS_TO_TICKS(1));
  }
}

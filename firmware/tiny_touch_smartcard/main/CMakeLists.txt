idf_component_register(
  SRCS
    "main.c"
    "fingerprint.c"
    "piv.c"
    "touch_pin_hid.c"
    "usb_ccid.c"
    "usb_descriptors.c"
  INCLUDE_DIRS "."
  REQUIRES driver esp_timer esp_tinyusb mbedtls
)

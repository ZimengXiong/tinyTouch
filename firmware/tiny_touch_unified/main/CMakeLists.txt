idf_component_register(
  SRCS
    "main.c"
    "config_console.c"
    "device_config.c"
    "fingerprint.c"
    "firmware_update.c"
    "piv.c"
    "touch_pin_hid.c"
    "usb_ccid.c"
    "usb_descriptors.c"
  INCLUDE_DIRS "."
  REQUIRES app_update driver esp_timer esp_tinyusb mbedtls nvs_flash
  PRIV_REQUIRES esp_driver_uart
)

target_compile_definitions(
  ${COMPONENT_LIB} PRIVATE
  TINYTOUCH_FIRMWARE_VERSION="${TINYTOUCH_VERSION}"
  TINYTOUCH_BUILD_ID="${TINYTOUCH_BUILD_ID}"
  TINYTOUCH_PROTOCOL_VERSION=6
)

idf_component_register(
  SRCS
    "main.c"
    "config_console.c"
    "device_config.c"
    "fingerprint.c"
    "firmware_update.c"
    "piv.c"
    "runtime_health.c"
    "touch_pin_hid.c"
    "usb_ccid.c"
    "usb_descriptors.c"
    "usb_runtime.c"
  INCLUDE_DIRS "."
  REQUIRES app_update driver esp_timer esp_tinyusb mbedtls nvs_flash
  PRIV_REQUIRES esp_driver_uart
)

if(TINYTOUCH_RECOVERY_BUILD)
  target_compile_definitions(${COMPONENT_LIB} PRIVATE TINYTOUCH_RECOVERY_BUILD=1)
endif()
if(TINYTOUCH_LOCAL_OTA_TEST)
  target_compile_definitions(${COMPONENT_LIB} PRIVATE TINYTOUCH_LOCAL_OTA_TEST=1)
endif()
if(TINYTOUCH_OTA_VALIDATION_RELEASE)
  target_compile_definitions(${COMPONENT_LIB} PRIVATE TINYTOUCH_OTA_VALIDATION_RELEASE=1)
endif()

target_compile_definitions(
  ${COMPONENT_LIB} PRIVATE
  TINYTOUCH_FIRMWARE_VERSION="${TINYTOUCH_VERSION}"
  TINYTOUCH_BUILD_ID="${TINYTOUCH_BUILD_ID}"
  TINYTOUCH_PROTOCOL_VERSION=5
)

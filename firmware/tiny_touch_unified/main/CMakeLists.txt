set(DASHBOARD_HTML "${CMAKE_CURRENT_LIST_DIR}/web/index.html")
set(DASHBOARD_HTML_GZ "${CMAKE_CURRENT_LIST_DIR}/web/index.html.gz")
if(EXISTS "${DASHBOARD_HTML}")
  execute_process(
    COMMAND gzip -n -9 -c "${DASHBOARD_HTML}"
    OUTPUT_FILE "${DASHBOARD_HTML_GZ}"
    RESULT_VARIABLE DASHBOARD_GZIP_RESULT
  )
  if(NOT DASHBOARD_GZIP_RESULT EQUAL 0)
    message(FATAL_ERROR "gzip failed while compressing the on-device dashboard")
  endif()
endif()

idf_component_register(
  SRCS
    "main.c"
    "config_console.c"
    "dashboard_api.c"
    "device_config.c"
    "fingerprint.c"
    "firmware_update.c"
    "piv.c"
    "touch_pin_hid.c"
    "usb_ccid.c"
    "usb_descriptors.c"
    "usb_ncm.c"
    "web_dashboard.c"
  INCLUDE_DIRS "."
  REQUIRES app_update driver esp_event esp_http_server esp_netif esp_partition esp_timer esp_tinyusb lwip mbedtls nvs_flash
  PRIV_REQUIRES esp_driver_uart
  EMBED_FILES "web/index.html.gz"
)

target_compile_definitions(
  ${COMPONENT_LIB} PRIVATE
  TINYTOUCH_FIRMWARE_VERSION="${TINYTOUCH_VERSION}"
  TINYTOUCH_BUILD_ID="${TINYTOUCH_BUILD_ID}"
  TINYTOUCH_PROTOCOL_VERSION=6
)

if(TINYTOUCH_DEVELOPMENT_SKIP_FINGERPRINT_AUTH)
  target_compile_definitions(${COMPONENT_LIB} PRIVATE
    TINYTOUCH_DEVELOPMENT_SKIP_FINGERPRINT_AUTH=1)
endif()

if(TINYTOUCH_RECOVERY_BUILD)
  target_compile_definitions(${COMPONENT_LIB} PRIVATE TINYTOUCH_RECOVERY_BUILD=1)
endif()

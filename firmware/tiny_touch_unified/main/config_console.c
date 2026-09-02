#include "config_console.h"

#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "mbedtls/base64.h"
#include "nvs_flash.h"
#include "tusb.h"

#include "device_config.h"
#include "fingerprint.h"
#include "firmware_update.h"
#include "piv.h"
#include "touch_pin_hid.h"

#ifndef TINYTOUCH_FIRMWARE_VERSION
#define TINYTOUCH_FIRMWARE_VERSION "development"
#endif
#ifndef TINYTOUCH_BUILD_ID
#define TINYTOUCH_BUILD_ID "development"
#endif

#define AUTH_WINDOW_US (120LL * 1000000LL)
#define OTA_WINDOW_US (30LL * 1000000LL)

static char command[5632];
static size_t command_length;
static bool command_overflow;
static char ota_token[33];
static int64_t authorized_until;
static int64_t ota_last_activity;
static SemaphoreHandle_t write_lock;

static void wipe(void *data, size_t length) {
  volatile uint8_t *cursor = data;
  while (length--) *cursor++ = 0;
}

void config_console_send_line(const char *line) {
  if (write_lock) xSemaphoreTake(write_lock, portMAX_DELAY);
  tud_cdc_write_str(line);
  tud_cdc_write_str("\r\n");
  tud_cdc_write_flush();
  if (write_lock) xSemaphoreGive(write_lock);
}

static void reply(const char *text) { config_console_send_line(text); }

static bool hex_value(char value) {
  return (value >= '0' && value <= '9') || (value >= 'a' && value <= 'f') ||
         (value >= 'A' && value <= 'F');
}

static bool decode_hex(const char *text, uint8_t *out, size_t length) {
  if (strlen(text) != length * 2) return false;
  for (size_t i = 0; i < length; i++) {
    if (!hex_value(text[i * 2]) || !hex_value(text[i * 2 + 1])) return false;
    unsigned value = 0;
    if (sscanf(text + i * 2, "%2x", &value) != 1) return false;
    out[i] = (uint8_t)value;
  }
  return true;
}

static bool parse_u32(const char *text, uint32_t maximum, uint32_t *value) {
  char *end = NULL;
  unsigned long parsed = strtoul(text, &end, 10);
  if (!text[0] || !end || *end || parsed > maximum) return false;
  *value = (uint32_t)parsed;
  return true;
}

static bool authorized(void) { return esp_timer_get_time() < authorized_until; }

static bool require_authorized(void) {
  if (authorized()) return true;
  reply("ERR LOCKED run=AUTH");
  return false;
}

static void touch_prompt(void) { reply("EVENT TOUCH"); }

static void authorize(void) {
  int count = fingerprint_count();
  bool ok = count == 0 || (count > 0 && fingerprint_authorize_prompted(touch_prompt));
  if (!ok) { reply("ERR AUTH"); return; }
  authorized_until = esp_timer_get_time() + AUTH_WINDOW_US;
  reply(count == 0 ? "OK AUTH first_setup=1" : "OK AUTH");
}

static bool token_matches(const char *token) {
  return ota_token[0] && strlen(token) == 32 && strcmp(token, ota_token) == 0;
}

static void clear_ota(void) {
  wipe(ota_token, sizeof(ota_token));
  ota_last_activity = 0;
}

static void status(void) {
  char line[320];
  int count = fingerprint_count();
  snprintf(line, sizeof(line),
           "OK STATUS protocol=6 firmware=%s build=%s mode=%s piv=%s sensor=%s fingerprints=%d "
           "hosts=%u ota=%s",
           TINYTOUCH_FIRMWARE_VERSION, TINYTOUCH_BUILD_ID, device_config_mode_name(),
           piv_uses_provisioned_keys() ? "ready" : "unconfigured",
           fingerprint_is_ready() ? "ready" : "offline", count,
           (unsigned)device_config_hid_host_count(), firmware_update_staged() ? "staged" :
           (firmware_update_active() ? "writing" : "idle"));
  reply(line);
}

static void set_mode(const char *mode) {
  if (!require_authorized()) return;
  bool ok = strcmp(mode, "PIV") == 0 ? device_config_set_mode(DEVICE_MODE_PIV) :
            strcmp(mode, "HID") == 0 ? device_config_set_mode(DEVICE_MODE_HID) : false;
  reply(ok ? "OK SET MODE" : "ERR SET MODE");
}

static void set_value(char *arguments) {
  if (!require_authorized()) return;
  char *value = strchr(arguments, ' ');
  uint32_t number = 0;
  bool ok = value != NULL;
  if (ok) { *value++ = '\0'; ok = parse_u32(value, UINT16_MAX, &number); }
  if (ok && strcmp(arguments, "TYPE_DELAY") == 0) ok = device_config_set_typing_delay_ms(number);
  else if (ok && strcmp(arguments, "SUBMIT_ENTER") == 0 && number <= 1) ok = device_config_set_submit_enter(number);
  else if (ok && strcmp(arguments, "COOLDOWN") == 0) ok = device_config_set_touch_cooldown_ms(number);
  else ok = false;
  reply(ok ? "OK SET" : "ERR SET");
}

static void host_add(char *arguments) {
  if (!require_authorized()) return;
  char *key = strchr(arguments, ' ');
  uint8_t id[DEVICE_CONFIG_HID_KEY_ID_SIZE] = {0};
  uint8_t secret[32] = {0};
  bool ok = key != NULL;
  if (ok) { *key++ = '\0'; ok = decode_hex(arguments, id, sizeof(id)) &&
                                      decode_hex(key, secret, sizeof(secret)) &&
                                      device_config_add_hid_host(id, secret); }
  wipe(secret, sizeof(secret));
  reply(ok ? "OK HOST ADD" : "ERR HOST ADD");
}

static void host_remove(const char *arguments) {
  if (!require_authorized()) return;
  uint8_t id[DEVICE_CONFIG_HID_KEY_ID_SIZE] = {0};
  bool ok = decode_hex(arguments, id, sizeof(id)) && device_config_remove_hid_host(id);
  reply(ok ? "OK HOST REMOVE" : "ERR HOST REMOVE");
}

static void host_list(void) {
  static const char hex[] = "0123456789abcdef";
  device_hid_host_t hosts[DEVICE_CONFIG_MAX_HID_HOSTS];
  size_t count = device_config_copy_hid_hosts(hosts);
  char ids[DEVICE_CONFIG_MAX_HID_HOSTS * (DEVICE_CONFIG_HID_KEY_ID_SIZE * 2 + 1)] = {0};
  size_t offset = 0;
  for (size_t host = 0; host < count; host++) {
    if (offset) ids[offset++] = ',';
    for (size_t byte = 0; byte < DEVICE_CONFIG_HID_KEY_ID_SIZE; byte++) {
      ids[offset++] = hex[hosts[host].id[byte] >> 4];
      ids[offset++] = hex[hosts[host].id[byte] & 0x0f];
    }
  }
  wipe(hosts, sizeof(hosts));
  char line[192];
  snprintf(line, sizeof(line), "OK HOST LIST ids=%s capacity=%u", ids,
           (unsigned)DEVICE_CONFIG_MAX_HID_HOSTS);
  reply(line);
}

static void fingerprint_command(char *arguments) {
  if (!require_authorized()) return;
  uint32_t slot = 0;
  bool ok = false;
  if (strncmp(arguments, "ENROLL ", 7) == 0 && parse_u32(arguments + 7, UINT16_MAX, &slot)) {
    ok = fingerprint_enroll((uint16_t)slot, NULL);
  } else if (strncmp(arguments, "DELETE ", 7) == 0 && parse_u32(arguments + 7, UINT16_MAX, &slot)) {
    ok = fingerprint_delete((uint16_t)slot) && device_config_set_fingerprint_profile_views(0);
  } else if (strcmp(arguments, "CLEAR") == 0) {
    ok = fingerprint_delete_all() && device_config_set_fingerprint_profile_views(0);
  }
  reply(ok ? "OK FINGER" : "ERR FINGER");
}

static void factory_reset(void) {
  if (!require_authorized()) return;
  bool ok = fingerprint_delete_all() && nvs_flash_erase() == ESP_OK &&
            nvs_flash_init() == ESP_OK && device_config_factory_reset();
  if (ok) {
    piv_reload_keys();
    authorized_until = 0;
  }
  reply(ok ? "OK RESET FACTORY" : "ERR RESET FACTORY");
}

static void piv_create(void) {
  if (!require_authorized()) return;
  reply(piv_create_identity() ? "OK PIV CREATE" : "ERR PIV CREATE");
}

static void ota_begin(char *arguments) {
  if (!require_authorized() || ota_token[0]) { if (ota_token[0]) reply("ERR OTA BUSY"); return; }
  char *size = strchr(arguments, ' ');
  if (!size || size - arguments != 32) { reply("ERR OTA BEGIN"); return; }
  *size++ = '\0'; char *digest = strchr(size, ' ');
  uint8_t hash[32] = {0}; uint32_t image_size = 0;
  bool ok = digest != NULL;
  if (ok) { *digest++ = '\0'; ok = decode_hex(arguments, hash, 16) &&
                                     parse_u32(size, UINT32_MAX, &image_size) &&
                                     decode_hex(digest, hash, sizeof(hash)) &&
                                     firmware_update_begin(image_size, hash); }
  if (ok) { memcpy(ota_token, arguments, sizeof(ota_token) - 1); ota_last_activity = esp_timer_get_time(); authorized_until = 0; }
  wipe(hash, sizeof(hash)); reply(ok ? "OK OTA BEGIN next=0" : "ERR OTA BEGIN");
}

static void ota_write(char *arguments) {
  char *offset = strchr(arguments, ' ');
  if (!offset) { reply("ERR OTA WRITE"); return; }
  *offset++ = '\0'; char *encoded = strchr(offset, ' ');
  if (!encoded || !token_matches(arguments)) { reply("ERR OTA WRITE"); return; }
  *encoded++ = '\0'; uint32_t at = 0; static uint8_t bytes[FIRMWARE_UPDATE_CHUNK_MAX]; size_t length = 0;
  bool ok = parse_u32(offset, UINT32_MAX, &at) &&
            mbedtls_base64_decode(bytes, sizeof(bytes), &length, (const unsigned char *)encoded,
                                  strlen(encoded)) == 0 &&
            firmware_update_write(at, bytes, length);
  if (ok) ota_last_activity = esp_timer_get_time();
  char line[64];
  if (ok) snprintf(line, sizeof(line), "OK OTA WRITE next=%u", (unsigned)firmware_update_written());
  else snprintf(line, sizeof(line), "ERR OTA WRITE");
  reply(line);
}

static void ota_commit(const char *token) {
  if (!token_matches(token)) { reply("ERR OTA COMMIT"); return; }
  bool ok = firmware_update_commit();
  clear_ota();
  reply(ok ? "OK OTA STAGED power_cycle=required" : "ERR OTA COMMIT");
}

static void handle_command(void) {
  if (strcmp(command, "PING") == 0) reply("PONG 6");
  else if (strcmp(command, "STATUS") == 0) status();
  else if (strcmp(command, "AUTH") == 0) authorize();
  else if (strncmp(command, "SET MODE ", 9) == 0) set_mode(command + 9);
  else if (strncmp(command, "SET ", 4) == 0) set_value(command + 4);
  else if (strncmp(command, "HOST ADD ", 9) == 0) host_add(command + 9);
  else if (strncmp(command, "HOST REMOVE ", 12) == 0) host_remove(command + 12);
  else if (strcmp(command, "HOST LIST") == 0) host_list();
  else if (strncmp(command, "FINGER ", 7) == 0) fingerprint_command(command + 7);
  else if (strcmp(command, "PIV CREATE") == 0) piv_create();
  else if (strcmp(command, "RESET FACTORY") == 0) factory_reset();
  else if (strncmp(command, "OTA BEGIN ", 10) == 0) ota_begin(command + 10);
  else if (strncmp(command, "OTA WRITE ", 10) == 0) ota_write(command + 10);
  else if (strncmp(command, "OTA ABORT ", 10) == 0 && token_matches(command + 10)) {
    firmware_update_abort(); clear_ota(); reply("OK OTA ABORT");
  } else if (strncmp(command, "OTA COMMIT ", 11) == 0) ota_commit(command + 11);
  else reply("ERR COMMAND");
}

static void console_task(void *arg) {
  (void)arg;
  char buffer[128];
  while (true) {
    if (ota_token[0] && esp_timer_get_time() - ota_last_activity > OTA_WINDOW_US) {
      firmware_update_abort(); clear_ota();
    }
    bool activity = false;
    while (tud_cdc_available()) {
      uint32_t count = tud_cdc_read(buffer, sizeof(buffer)); activity = count != 0;
      for (uint32_t i = 0; i < count; i++) {
        if (buffer[i] == '\r') continue;
        if (buffer[i] == '\n') {
          command[command_length] = '\0';
          if (!command_overflow && command_length) {
            if (strncmp(command, "PW ", 3) == 0 || strncmp(command, "PW2 ", 4) == 0) touch_pin_hid_submit_response(command);
            else handle_command();
          } else if (command_overflow) reply("ERR LINE");
          command_length = 0; command_overflow = false;
        } else if (command_length + 1 < sizeof(command)) command[command_length++] = buffer[i];
        else command_overflow = true;
      }
    }
    if (!activity) vTaskDelay(pdMS_TO_TICKS(2));
  }
}

void config_console_start(void) {
  write_lock = xSemaphoreCreateMutex();
  configASSERT(write_lock);
  BaseType_t created = xTaskCreate(console_task, "console", 6144, NULL, 3, NULL);
  configASSERT(created == pdPASS);
}

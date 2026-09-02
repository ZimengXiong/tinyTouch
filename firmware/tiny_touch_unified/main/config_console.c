#include "config_console.h"

#include <limits.h>
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "fingerprint.h"
#include "firmware_update.h"
#include "device_config.h"
#include "piv.h"
#include "runtime_health.h"
#include "touch_pin_hid.h"
#include "esp_timer.h"
#include "esp_ota_ops.h"
#include "esp_partition.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "mbedtls/base64.h"
#include "nvs.h"
#include "nvs_flash.h"
#include "soc/rtc_cntl_reg.h"
#include "soc/soc.h"
#include "tusb.h"

#ifndef TINYTOUCH_FIRMWARE_VERSION
#define TINYTOUCH_FIRMWARE_VERSION "development"
#endif
#ifndef TINYTOUCH_PROTOCOL_VERSION
#define TINYTOUCH_PROTOCOL_VERSION 1
#endif
#ifndef TINYTOUCH_BUILD_ID
#define TINYTOUCH_BUILD_ID "development"
#endif

static char command[5632];
static size_t command_len;
static bool command_overflow;
static SemaphoreHandle_t cdc_write_mutex;
static int64_t config_authorized_until;
static int64_t update_authorized_until;
static int64_t update_last_activity;
static char update_token[33];
static const char *update_last_reason = "none";

typedef struct {
  uint8_t data[2400];
  size_t length;
} provision_buffer_t;

static provision_buffer_t provision_cert9a;
static provision_buffer_t provision_key9a;
static provision_buffer_t provision_cert9d;
static provision_buffer_t provision_key9d;

static const char *ota_state_name(esp_ota_img_states_t state) {
  switch (state) {
    case ESP_OTA_IMG_NEW: return "new";
    case ESP_OTA_IMG_PENDING_VERIFY: return "pending";
    case ESP_OTA_IMG_VALID: return "valid";
    case ESP_OTA_IMG_INVALID: return "invalid";
    case ESP_OTA_IMG_ABORTED: return "aborted";
    case ESP_OTA_IMG_UNDEFINED: return "undefined";
    default: return "unknown";
  }
}

static const char *partition_label(const esp_partition_t *partition) {
  return partition ? partition->label : "none";
}

static void ota_partition_summary(const esp_partition_t *partition,
                                  char *output, size_t output_size) {
  esp_app_desc_t description;
  esp_ota_img_states_t state;
  const char *version = "invalid";
  const char *state_name = "missing";
  if (partition && esp_ota_get_partition_description(partition, &description) == ESP_OK) {
    version = description.version;
  }
  if (partition && esp_ota_get_state_partition(partition, &state) == ESP_OK) {
    state_name = ota_state_name(state);
  }
  snprintf(output, output_size, "%s:%s:%s", partition_label(partition), version, state_name);
}

static void ota_diagnostics(char *output, size_t output_size) {
  const esp_partition_t *running = esp_ota_get_running_partition();
  const esp_partition_t *boot = esp_ota_get_boot_partition();
  const esp_partition_t *next = esp_ota_get_next_update_partition(NULL);
  const esp_partition_t *slot0 = esp_partition_find_first(
      ESP_PARTITION_TYPE_APP, ESP_PARTITION_SUBTYPE_APP_OTA_0, NULL);
  const esp_partition_t *slot1 = esp_partition_find_first(
      ESP_PARTITION_TYPE_APP, ESP_PARTITION_SUBTYPE_APP_OTA_1, NULL);
  char slot0_summary[96];
  char slot1_summary[96];
  esp_ota_img_states_t running_state;
  const char *running_state_name = "missing";
  if (running && esp_ota_get_state_partition(running, &running_state) == ESP_OK) {
    running_state_name = ota_state_name(running_state);
  }
  ota_partition_summary(slot0, slot0_summary, sizeof(slot0_summary));
  ota_partition_summary(slot1, slot1_summary, sizeof(slot1_summary));
  const char *session_state = "idle";
  if (update_token[0]) {
    session_state = firmware_update_active() ? "active" : "failed";
  }
  snprintf(output, output_size,
           "ota_running=%s ota_boot=%s ota_next=%s ota_state=%s "
           "ota_slot0=%s ota_slot1=%s rollback=enabled reset_reason=%d "
           "update_session=%s update_next=%u update_expected=%u update_chunk=%u "
           "update_commit_stack_free=%u update_last_reason=%s update_error=%s",
           partition_label(running), partition_label(boot), partition_label(next),
           running_state_name, slot0_summary, slot1_summary, (int)esp_reset_reason(),
           session_state, (unsigned)firmware_update_written(),
           (unsigned)firmware_update_expected(), (unsigned)FIRMWARE_UPDATE_CHUNK_MAX,
           (unsigned)firmware_update_commit_stack_free(),
           update_last_reason, firmware_update_last_error());
}

static void secure_wipe(void *data, size_t length) {
  volatile uint8_t *cursor = data;
  while (length--) *cursor++ = 0;
}

void config_console_send_line(const char *line) {
  if (cdc_write_mutex) xSemaphoreTake(cdc_write_mutex, portMAX_DELAY);
  const char *parts[] = {line, "\r\n"};
  for (size_t part = 0; part < 2; part++) {
    size_t length = strlen(parts[part]);
    size_t offset = 0;
    TickType_t started = xTaskGetTickCount();
    while (offset < length &&
           (xTaskGetTickCount() - started) < pdMS_TO_TICKS(2000)) {
      uint32_t written = tud_cdc_write(parts[part] + offset, length - offset);
      offset += written;
      tud_cdc_write_flush();
      if (offset < length) vTaskDelay(pdMS_TO_TICKS(2));
    }
  }
  tud_cdc_write_flush();
  if (cdc_write_mutex) xSemaphoreGive(cdc_write_mutex);
}

static void send_line(const char *line) {
  config_console_send_line(line);
}

static bool config_authorized(void) {
  return esp_timer_get_time() < config_authorized_until;
}

static void authorize_config(void) {
  config_authorized_until = esp_timer_get_time() + 120LL * 1000000LL;
}

static bool require_config_authorization(void) {
  if (config_authorized()) return true;
  send_line("ERR CONFIG_LOCKED run=CONFIG_UNLOCK");
  return false;
}

static int hex_value(char value) {
  if (value >= '0' && value <= '9') return value - '0';
  if (value >= 'a' && value <= 'f') return value - 'a' + 10;
  if (value >= 'A' && value <= 'F') return value - 'A' + 10;
  return -1;
}

static bool decode_hex(const char *hex, uint8_t *output, size_t output_length) {
  if (strlen(hex) != output_length * 2) return false;
  for (size_t i = 0; i < output_length; i++) {
    int high = hex_value(hex[i * 2]);
    int low = hex_value(hex[i * 2 + 1]);
    if (high < 0 || low < 0) return false;
    output[i] = (uint8_t)((high << 4) | low);
  }
  return true;
}

static bool decode_hid_key(const char *hex, uint8_t key[32]) {
  return decode_hex(hex, key, 32);
}

static bool parse_unsigned(const char *text, unsigned long maximum,
                           unsigned long *value) {
  if (!text || !value || text[0] < '0' || text[0] > '9') return false;
  char *end = NULL;
  unsigned long parsed = strtoul(text, &end, 10);
  if (!end || *end != '\0' || parsed > maximum) return false;
  *value = parsed;
  return true;
}

static bool first_setup_allowed(int fingerprint_count) {
  return fingerprint_count == 0 && !piv_uses_provisioned_keys() &&
         !device_config_hid_key_configured();
}

static bool valid_update_token(const char *token) {
  return update_token[0] && strlen(token) == 32 && strcmp(token, update_token) == 0;
}

static bool begin_firmware_update(char *arguments) {
  char *size_text = strchr(arguments, ' ');
  if (!size_text || size_text - arguments != 32) return false;
  *size_text++ = '\0';
  char *digest_hex = strchr(size_text, ' ');
  if (!digest_hex) return false;
  *digest_hex++ = '\0';
  unsigned long size = 0;
  uint8_t token_bytes[16];
  uint8_t digest[32];
  bool ok = decode_hex(arguments, token_bytes, sizeof(token_bytes)) &&
            parse_unsigned(size_text, SIZE_MAX, &size) && size > 0 &&
            decode_hex(digest_hex, digest, sizeof(digest)) &&
            firmware_update_begin((size_t)size, digest);
  if (ok) {
    memcpy(update_token, arguments, 33);
    update_last_activity = esp_timer_get_time();
  }
  memset(token_bytes, 0, sizeof(token_bytes));
  memset(digest, 0, sizeof(digest));
  return ok;
}

static bool append_firmware_chunk(char *arguments, size_t *next_offset) {
  char *offset_text = strchr(arguments, ' ');
  if (!offset_text) return false;
  *offset_text++ = '\0';
  char *encoded = strchr(offset_text, ' ');
  if (!encoded) return false;
  *encoded++ = '\0';
  unsigned long offset = 0;
  static uint8_t decoded[FIRMWARE_UPDATE_CHUNK_MAX];
  size_t decoded_length = 0;
  bool ok = valid_update_token(arguments) &&
            parse_unsigned(offset_text, SIZE_MAX, &offset) &&
            mbedtls_base64_decode(decoded, sizeof(decoded), &decoded_length,
                                  (const unsigned char *)encoded, strlen(encoded)) == 0 &&
            decoded_length > 0 && firmware_update_write((size_t)offset, decoded, decoded_length);
  if (ok) {
    update_last_activity = esp_timer_get_time();
    *next_offset = firmware_update_written();
  }
  return ok;
}

static void clear_update_session(void) {
  update_token[0] = '\0';
  update_last_activity = 0;
}

static void bytes_to_hex(const uint8_t *data, size_t length, char *output) {
  static const char digits[] = "0123456789abcdef";
  for (size_t i = 0; i < length; i++) {
    output[i * 2] = digits[data[i] >> 4];
    output[i * 2 + 1] = digits[data[i] & 0x0f];
  }
  output[length * 2] = '\0';
}

static void enrollment_prompt(const char *message) {
  char line[48];
  snprintf(line, sizeof(line), "PROMPT %s", message);
  send_line(line);
}

static void reset_provisioning(void) {
  secure_wipe(&provision_cert9a, sizeof(provision_cert9a));
  secure_wipe(&provision_key9a, sizeof(provision_key9a));
  secure_wipe(&provision_cert9d, sizeof(provision_cert9d));
  secure_wipe(&provision_key9d, sizeof(provision_key9d));
}

static provision_buffer_t *provision_buffer(const char *name) {
  if (strcmp(name, "cert9a") == 0) return &provision_cert9a;
  if (strcmp(name, "key9a") == 0) return &provision_key9a;
  if (strcmp(name, "cert9d") == 0) return &provision_cert9d;
  if (strcmp(name, "key9d") == 0) return &provision_key9d;
  return NULL;
}

static bool append_provision_chunk(char *arguments) {
  char *separator = strchr(arguments, ' ');
  if (!separator) return false;
  *separator = '\0';
  provision_buffer_t *buffer = provision_buffer(arguments);
  if (!buffer) return false;
  const unsigned char *encoded = (const unsigned char *)(separator + 1);
  size_t encoded_length = strlen(separator + 1);
  uint8_t decoded[480];
  size_t decoded_length = 0;
  bool ok = mbedtls_base64_decode(decoded, sizeof(decoded), &decoded_length,
                                  encoded, encoded_length) == 0 &&
            buffer->length + decoded_length + 1 <= sizeof(buffer->data);
  if (ok) {
    memcpy(buffer->data + buffer->length, decoded, decoded_length);
    buffer->length += decoded_length;
    buffer->data[buffer->length] = '\0';
  }
  secure_wipe(decoded, sizeof(decoded));
  return ok;
}

static bool provision_buffers_valid(void) {
  return provision_cert9a.length && provision_key9a.length &&
         provision_cert9d.length && provision_key9d.length &&
         strstr((char *)provision_cert9a.data, "BEGIN CERTIFICATE") &&
         strstr((char *)provision_key9a.data, "BEGIN PRIVATE KEY") &&
         strstr((char *)provision_cert9d.data, "BEGIN CERTIFICATE") &&
         strstr((char *)provision_key9d.data, "BEGIN PRIVATE KEY");
}

static bool commit_provisioning(void) {
  if (!provision_buffers_valid()) return false;
  nvs_handle_t handle;
  if (nvs_open("piv_keys", NVS_READWRITE, &handle) != ESP_OK) return false;
  esp_err_t result = nvs_set_blob(handle, "cert9a", provision_cert9a.data,
                                  provision_cert9a.length + 1);
  if (result == ESP_OK) result = nvs_set_blob(handle, "key9a", provision_key9a.data,
                                               provision_key9a.length + 1);
  if (result == ESP_OK) result = nvs_set_blob(handle, "cert9d", provision_cert9d.data,
                                               provision_cert9d.length + 1);
  if (result == ESP_OK) result = nvs_set_blob(handle, "key9d", provision_key9d.data,
                                               provision_key9d.length + 1);
  if (result == ESP_OK) result = nvs_commit(handle);
  nvs_close(handle);
  if (result == ESP_OK) {
    piv_reload_keys();
    reset_provisioning();
  }
  return result == ESP_OK;
}

static bool factory_reset(void) {
  if (!piv_pairing_mode_active() || !fingerprint_delete_all()) return false;
  if (nvs_flash_erase() != ESP_OK || nvs_flash_init() != ESP_OK) return false;
  piv_set_pairing_mode(false);
  piv_reload_keys();
  device_config_reload();
  config_authorized_until = 0;
  reset_provisioning();
  return true;
}

static void handle_command(void) {
  char line[1536];
  if (strcmp(command, "PING") == 0) {
    send_line("PONG");
  } else if (strcmp(command, "STATUS") == 0) {
    char ota_info[480];
    char runtime_info[320];
    ota_diagnostics(ota_info, sizeof(ota_info));
    runtime_health_format(runtime_info, sizeof(runtime_info));
    int count = fingerprint_count();
    if (count < 0) {
      snprintf(line, sizeof(line),
               "OK STATUS firmware=unified firmware_version=%s protocol=%d mode=%s "
               "sensor=no_response fingerprints=unknown fingerprint_profile=%u keys=%s hid_key=%s hid_hosts=%u "
               "typing_delay_ms=%u submit_enter=%u touch_cooldown_ms=%u ota=%s build=%s %s %s",
               TINYTOUCH_FIRMWARE_VERSION, TINYTOUCH_PROTOCOL_VERSION,
               device_config_mode_name(), (unsigned)device_config_fingerprint_profile_views(),
               piv_uses_provisioned_keys() ? "nvs" : "unconfigured",
               device_config_hid_key_configured() ? "configured" : "unconfigured",
               (unsigned)device_config_hid_host_count(),
               (unsigned)device_config_typing_delay_ms(), device_config_submit_enter() ? 1 : 0,
               (unsigned)device_config_touch_cooldown_ms(),
               firmware_update_supported() ? "ready" : "migration_required", TINYTOUCH_BUILD_ID,
               ota_info, runtime_info);
      send_line(line);
    } else {
      snprintf(line, sizeof(line),
               "OK STATUS firmware=unified firmware_version=%s protocol=%d mode=%s "
               "sensor=ok fingerprints=%d fingerprint_profile=%u keys=%s hid_key=%s hid_hosts=%u "
               "typing_delay_ms=%u submit_enter=%u touch_cooldown_ms=%u ota=%s build=%s %s %s",
               TINYTOUCH_FIRMWARE_VERSION, TINYTOUCH_PROTOCOL_VERSION,
               device_config_mode_name(), count,
               (unsigned)device_config_fingerprint_profile_views(),
               piv_uses_provisioned_keys() ? "nvs" : "unconfigured",
               device_config_hid_key_configured() ? "configured" : "unconfigured",
               (unsigned)device_config_hid_host_count(),
               (unsigned)device_config_typing_delay_ms(), device_config_submit_enter() ? 1 : 0,
               (unsigned)device_config_touch_cooldown_ms(),
               firmware_update_supported() ? "ready" : "migration_required", TINYTOUCH_BUILD_ID,
               ota_info, runtime_info);
      send_line(line);
    }
  } else if (strcmp(command, "VERSION") == 0) {
    snprintf(line, sizeof(line), "OK VERSION firmware=%s protocol=%d",
             TINYTOUCH_FIRMWARE_VERSION, TINYTOUCH_PROTOCOL_VERSION);
    send_line(line);
  } else if (strcmp(command, "CONFIG_UNLOCK") == 0) {
    int count = fingerprint_count();
    if (count < 0) {
      send_line("ERR CONFIG_UNLOCK sensor");
    } else if (first_setup_allowed(count)) {
      authorize_config();
      send_line("OK CONFIG_UNLOCK first_setup seconds=120");
    } else if (count == 0) {
      send_line("ERR CONFIG_UNLOCK fingerprint");
    } else {
      send_line("PROMPT TOUCH");
      if (fingerprint_authorize_once()) {
        authorize_config();
        send_line("OK CONFIG_UNLOCK fingerprint seconds=120");
      } else {
        send_line("ERR CONFIG_UNLOCK fingerprint");
      }
    }
  } else if (strcmp(command, "UPDATE_UNLOCK") == 0) {
    int count = fingerprint_count();
    if (count < 0) {
      send_line("ERR UPDATE_UNLOCK sensor");
    } else if (first_setup_allowed(count)) {
      update_authorized_until = esp_timer_get_time() + 30LL * 1000000LL;
      send_line("OK UPDATE_UNLOCK first_setup seconds=30");
    } else if (count == 0) {
      send_line("ERR UPDATE_UNLOCK fingerprint_required");
    } else {
      send_line("PROMPT TOUCH");
      if (fingerprint_authorize_once()) {
        update_authorized_until = esp_timer_get_time() + 30LL * 1000000LL;
        send_line("OK UPDATE_UNLOCK seconds=30");
      } else {
        send_line("ERR UPDATE_UNLOCK fingerprint");
      }
    }
  } else if (strncmp(command, "MODE ", 5) == 0) {
    if (!require_config_authorization()) return;
    bool ok = false;
    if (strcmp(command + 5, "piv") == 0) {
      ok = device_config_set_mode(DEVICE_MODE_PIV);
    } else if (strcmp(command + 5, "hid") == 0) {
      ok = device_config_set_mode(DEVICE_MODE_HID);
    }
    snprintf(line, sizeof(line), ok ? "OK MODE mode=%s" : "ERR MODE mode=%s", command + 5);
    send_line(line);
  } else if (strncmp(command, "SETTING ", 8) == 0) {
    if (!require_config_authorization()) return;
    char *name = command + 8;
    char *value_text = strchr(name, ' ');
    bool ok = value_text != NULL;
    unsigned long value = 0;
    if (ok) {
      *value_text++ = '\0';
      ok = parse_unsigned(value_text, ULONG_MAX, &value);
    }
    if (ok && strcmp(name, "typing_delay_ms") == 0) {
      ok = value >= 1 && value <= 100 &&
           device_config_set_typing_delay_ms((uint16_t)value);
    } else if (ok && strcmp(name, "submit_enter") == 0) {
      ok = value <= 1 && device_config_set_submit_enter(value != 0);
    } else if (ok && strcmp(name, "touch_cooldown_ms") == 0) {
      ok = value >= 100 && value <= 5000 &&
           device_config_set_touch_cooldown_ms((uint16_t)value);
    } else {
      ok = false;
    }
    snprintf(line, sizeof(line), ok ? "OK SETTING name=%s value=%lu" :
                                      "ERR SETTING name=%s value=%lu", name, value);
    send_line(line);
  } else if (strncmp(command, "HID_KEY ", 8) == 0) {
    if (!require_config_authorization()) return;
    uint8_t key[32];
    bool ok = decode_hid_key(command + 8, key) && device_config_set_hid_key(key);
    memset(key, 0, sizeof(key));
    send_line(ok ? "OK HID_KEY" : "ERR HID_KEY");
  } else if (strcmp(command, "HID_KEY_IDS") == 0) {
    char ids[DEVICE_CONFIG_MAX_HID_HOSTS * 17 + 1] = {0};
    size_t offset = 0;
    for (size_t i = 0; i < device_config_hid_host_count(); i++) {
      device_hid_host_t host;
      if (!device_config_get_hid_host(i, &host)) continue;
      if (offset) ids[offset++] = ',';
      bytes_to_hex(host.id, sizeof(host.id), ids + offset);
      offset += sizeof(host.id) * 2;
      memset(&host, 0, sizeof(host));
    }
    snprintf(line, sizeof(line), "OK HID_KEY_IDS ids=%s capacity=%d",
             offset ? ids : "none", DEVICE_CONFIG_MAX_HID_HOSTS);
    send_line(line);
  } else if (strncmp(command, "HID_KEY_ADD ", 12) == 0) {
    if (!require_config_authorization()) return;
    char *id_hex = command + 12;
    char *key_hex = strchr(id_hex, ' ');
    uint8_t id[DEVICE_CONFIG_HID_KEY_ID_SIZE];
    uint8_t key[32];
    bool ok = key_hex != NULL;
    if (ok) {
      *key_hex++ = '\0';
      ok = decode_hex(id_hex, id, sizeof(id)) && decode_hid_key(key_hex, key) &&
           device_config_add_hid_host(id, key);
    }
    memset(id, 0, sizeof(id));
    memset(key, 0, sizeof(key));
    send_line(ok ? "OK HID_KEY_ADD" : "ERR HID_KEY_ADD");
  } else if (strncmp(command, "HID_KEY_REMOVE ", 15) == 0) {
    if (!require_config_authorization()) return;
    uint8_t id[DEVICE_CONFIG_HID_KEY_ID_SIZE];
    bool ok = decode_hex(command + 15, id, sizeof(id)) &&
              device_config_remove_hid_host(id);
    memset(id, 0, sizeof(id));
    send_line(ok ? "OK HID_KEY_REMOVE" : "ERR HID_KEY_REMOVE");
  } else if (strncmp(command, "ENROLL ", 7) == 0) {
    if (!require_config_authorization()) return;
    unsigned long slot = 0;
    bool ok = parse_unsigned(command + 7, UINT16_MAX, &slot) &&
              fingerprint_enroll((uint16_t)slot, enrollment_prompt);
    if (ok) authorize_config();
    snprintf(line, sizeof(line), ok ? "OK ENROLL slot=%lu" : "ERR ENROLL slot=%lu", slot);
    send_line(line);
  } else if (strncmp(command, "PROFILE_COMPLETE ", 17) == 0) {
    if (!require_config_authorization()) return;
    unsigned long views = 0;
    bool ok = parse_unsigned(command + 17, UINT8_MAX, &views) && views == 4 &&
              device_config_set_fingerprint_profile_views((uint8_t)views);
    send_line(ok ? "OK PROFILE_COMPLETE views=4" : "ERR PROFILE_COMPLETE");
  } else if (strncmp(command, "DELETE ", 7) == 0) {
    if (!require_config_authorization()) return;
    unsigned long slot = 0;
    bool ok = parse_unsigned(command + 7, UINT16_MAX, &slot) &&
              fingerprint_delete((uint16_t)slot);
    if (ok) ok = device_config_set_fingerprint_profile_views(0);
    snprintf(line, sizeof(line), ok ? "OK DELETE slot=%lu" : "ERR DELETE slot=%lu", slot);
    send_line(line);
  } else if (strcmp(command, "DELETE_ALL") == 0) {
    if (!require_config_authorization()) return;
    bool ok = fingerprint_delete_all();
    if (ok) ok = device_config_set_fingerprint_profile_views(0);
    send_line(ok ? "OK DELETE_ALL" : "ERR DELETE_ALL");
  } else if (strcmp(command, "PAIRING_MODE") == 0) {
    send_line("PROMPT TOUCH");
    if (fingerprint_authorize_once()) {
      piv_set_pairing_mode(true);
      send_line("OK PAIRING_MODE seconds=120");
    } else {
      piv_set_pairing_mode(false);
      send_line("ERR PAIRING_MODE fingerprint");
    }
  } else if (strcmp(command, "PAIRING_MODE_OFF") == 0) {
    piv_set_pairing_mode(false);
    send_line("OK PAIRING_MODE_OFF");
  } else if (strcmp(command, "PROVISION_BEGIN") == 0) {
    if (!require_config_authorization()) return;
    reset_provisioning();
    send_line("OK PROVISION_BEGIN");
  } else if (strncmp(command, "PROVISION_CHUNK ", 16) == 0) {
    if (!require_config_authorization()) return;
    send_line(append_provision_chunk(command + 16) ?
              "OK PROVISION_CHUNK" : "ERR PROVISION_CHUNK");
  } else if (strcmp(command, "PROVISION_COMMIT") == 0) {
    if (!require_config_authorization()) return;
    send_line(commit_provisioning() ? "OK PROVISION_COMMIT" : "ERR PROVISION_COMMIT");
  } else if (strncmp(command, "UPDATE_BEGIN ", 13) == 0) {
    if (update_token[0]) {
      send_line("ERR UPDATE_SESSION");
      return;
    }
    if (esp_timer_get_time() >= update_authorized_until) {
      send_line("ERR UPDATE_LOCKED run=UPDATE_UNLOCK");
      return;
    }
    bool ok = begin_firmware_update(command + 13);
    if (ok) {
      update_authorized_until = 0;
      update_last_reason = "none";
    } else {
      update_last_reason = "begin_failed";
    }
    send_line(ok ? "OK UPDATE_BEGIN next=0" : "ERR UPDATE_BEGIN");
  } else if (strncmp(command, "UPDATE_CHUNK ", 13) == 0) {
    char *arguments = command + 13;
    if (!update_token[0] || strncmp(arguments, update_token, 32) != 0 || arguments[32] != ' ') {
      send_line("ERR UPDATE_SESSION");
      return;
    }
    size_t next_offset = 0;
    bool ok = append_firmware_chunk(arguments, &next_offset);
    update_last_reason = ok ? "none" :
                         (firmware_update_active() ? "chunk_rejected" : "write_failed");
    snprintf(line, sizeof(line),
             ok ? "OK UPDATE_CHUNK next=%u" : "ERR UPDATE_CHUNK next=%u active=%u error=%s",
             (unsigned)(ok ? next_offset : firmware_update_written()),
             firmware_update_active() ? 1U : 0U, firmware_update_last_error());
    send_line(line);
  } else if (strncmp(command, "UPDATE_STATUS ", 14) == 0) {
    if (!valid_update_token(command + 14)) {
      send_line("ERR UPDATE_SESSION");
      return;
    }
    update_last_activity = esp_timer_get_time();
    if (firmware_update_active()) {
      snprintf(line, sizeof(line), "OK UPDATE_STATUS next=%u",
               (unsigned)firmware_update_written());
    } else {
      snprintf(line, sizeof(line), "ERR UPDATE_STATUS active=0 error=%s",
               firmware_update_last_error());
    }
    send_line(line);
  } else if (strncmp(command, "UPDATE_ABORT ", 13) == 0) {
    if (!valid_update_token(command + 13)) {
      send_line("ERR UPDATE_SESSION");
      return;
    }
    firmware_update_abort();
    update_last_reason = "host_abort";
    clear_update_session();
    send_line("OK UPDATE_ABORT");
  } else if (strncmp(command, "UPDATE_COMMIT ", 14) == 0) {
    if (!valid_update_token(command + 14)) {
      send_line("ERR UPDATE_SESSION");
      return;
    }
    bool ok = firmware_update_commit();
    if (!ok) firmware_update_abort();
    update_last_reason = ok ? "none" : "commit_failed";
    clear_update_session();
    if (ok) {
      snprintf(line, sizeof(line), "OK UPDATE_COMMIT stack_free=%u",
               (unsigned)firmware_update_commit_stack_free());
      send_line(line);
    } else {
      snprintf(line, sizeof(line), "ERR UPDATE_COMMIT active=0 error=%s",
               firmware_update_last_error());
      send_line(line);
    }
    if (ok) {
      vTaskDelay(pdMS_TO_TICKS(150));
      esp_restart();
    }
  } else if (strncmp(command, "CONFIRM_FIRMWARE ", 17) == 0) {
    bool build_matches = strcmp(command + 17, TINYTOUCH_BUILD_ID) == 0;
    bool sensor_ok = fingerprint_count() >= 0;
    bool ok = build_matches && sensor_ok && firmware_update_confirm_running();
    send_line(ok ? "OK CONFIRM_FIRMWARE" : "ERR CONFIRM_FIRMWARE");
  } else if (strcmp(command, "FACTORY_RESET") == 0) {
    send_line(factory_reset() ? "OK FACTORY_RESET" : "ERR FACTORY_RESET");
  } else if (strcmp(command, "USB_RECONNECT") == 0) {
    send_line("OK USB_RECONNECT");
    vTaskDelay(pdMS_TO_TICKS(100));
    tud_disconnect();
    vTaskDelay(pdMS_TO_TICKS(500));
    tud_connect();
  } else if (strcmp(command, "REBOOT") == 0) {
    send_line("OK REBOOT");
    vTaskDelay(pdMS_TO_TICKS(100));
    esp_restart();
  } else if (strcmp(command, "BOOTLOADER") == 0) {
    if (!require_config_authorization()) return;
    send_line("OK BOOTLOADER");
    vTaskDelay(pdMS_TO_TICKS(100));
    REG_WRITE(RTC_CNTL_OPTION1_REG, RTC_CNTL_FORCE_DOWNLOAD_BOOT);
    esp_restart();
  } else {
    send_line("ERR UNKNOWN_COMMAND");
  }
}

static void console_task(void *arg) {
  (void)arg;
  char buffer[128];
  while (true) {
    if (update_token[0] && esp_timer_get_time() - update_last_activity > 30LL * 1000000LL) {
      if (firmware_update_active()) {
        firmware_update_abort();
        update_last_reason = "timeout";
      }
      clear_update_session();
    }
    bool had_activity = false;
    while (tud_cdc_available()) {
      uint32_t count = tud_cdc_read(buffer, sizeof(buffer));
      if (count == 0) break;
      had_activity = true;
      for (uint32_t i = 0; i < count; i++) {
        char c = buffer[i];
        if (c == '\r') continue;
        if (c == '\n') {
          if (command_overflow) {
            send_line("ERR LINE_TOO_LONG");
          } else {
            command[command_len] = '\0';
          }
          if (!command_overflow && command_len) {
            if (strncmp(command, "PW ", 3) == 0 || strncmp(command, "PW2 ", 4) == 0) {
              touch_pin_hid_submit_response(command);
            } else {
              handle_command();
            }
          }
          command_len = 0;
          command_overflow = false;
        } else if (command_len + 1 < sizeof(command)) {
          command[command_len++] = c;
        } else {
          command_overflow = true;
        }
      }
    }
    if (!had_activity) {
      vTaskDelay(pdMS_TO_TICKS(2));
    }
  }
}

void config_console_start(void) {
  command_len = 0;
  command_overflow = false;
  config_authorized_until = 0;
  update_authorized_until = 0;
  clear_update_session();
  cdc_write_mutex = xSemaphoreCreateMutex();
  configASSERT(cdc_write_mutex != NULL);
  BaseType_t created = xTaskCreate(console_task, "config_console", 4096, NULL, 3, NULL);
  configASSERT(created == pdPASS);
}

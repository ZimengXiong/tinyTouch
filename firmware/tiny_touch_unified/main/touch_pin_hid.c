#include "touch_pin_hid.h"

#include <stdio.h>
#include <string.h>

#include "class/hid/hid_device.h"
#include "config_console.h"
#include "device_config.h"
#include "esp_log.h"
#include "esp_random.h"
#include "esp_system.h"
#include "fingerprint.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "mbedtls/aes.h"
#include "mbedtls/md.h"
#include "piv.h"
#include "runtime_health.h"
#include "usb_descriptors.h"

static const char *TAG = "touch_hid";
static const uint8_t ascii_to_keycode[128][2] = {HID_ASCII_TO_KEYCODE};
static QueueHandle_t password_responses;
static uint32_t event_counter;
static volatile bool hid_suspended;
static volatile bool hid_needs_release;
static volatile bool hid_reconnect_pending;
static volatile TickType_t hid_suspended_at;
static volatile bool hid_remote_wakeup_enabled;
static volatile bool hid_remote_wakeup_attempted;

#define USB_LONG_SUSPEND_MS 5000
#define USB_WAKE_TIMEOUT_MS 2000
#define SUSPENDED_SENSOR_POLL_MS 250

static void secure_wipe(void *data, size_t length) {
  volatile uint8_t *cursor = data;
  while (length--) *cursor++ = 0;
}

static bool wait_hid_ready(void) {
  TickType_t started = xTaskGetTickCount();
  while (!tud_hid_ready()) {
    if (hid_suspended ||
        (TickType_t)(xTaskGetTickCount() - started) >= pdMS_TO_TICKS(2000)) {
      return false;
    }
    vTaskDelay(pdMS_TO_TICKS(10));
  }
  return true;
}

static bool send_key(uint8_t modifier, uint8_t key) {
  uint8_t report[6] = {key, 0, 0, 0, 0, 0};
  if (!wait_hid_ready()) return false;
  if (!tud_hid_keyboard_report(0, modifier, report)) return false;
  vTaskDelay(pdMS_TO_TICKS(device_config_typing_delay_ms()));
  if (!wait_hid_ready()) return false;
  if (!tud_hid_keyboard_report(0, 0, NULL)) return false;
  vTaskDelay(pdMS_TO_TICKS(device_config_typing_delay_ms()));
  return true;
}

static bool type_dummy_pin(void) {
  for (int i = 0; i < 6; i++) {
    // Numeric-keypad usages are independent of the active keyboard layout.
    if (!send_key(0, HID_KEY_KEYPAD_1)) return false;
  }
  return send_key(0, HID_KEY_ENTER);
}

static bool type_ascii(const uint8_t *data, size_t length) {
  // Validate the complete payload before emitting any key. A malformed helper
  // response must never leave a password prefix in the focused field.
  for (size_t i = 0; i < length; i++) {
    if (data[i] >= 128 || ascii_to_keycode[data[i]][1] == 0) return false;
  }
  for (size_t i = 0; i < length; i++) {
    uint8_t modifier = ascii_to_keycode[data[i]][0] ? KEYBOARD_MODIFIER_LEFTSHIFT : 0;
    if (!send_key(modifier, ascii_to_keycode[data[i]][1])) return false;
  }
  return device_config_submit_enter() ? send_key(0, HID_KEY_ENTER) : true;
}

static void bytes_to_hex(const uint8_t *data, size_t length, char *output) {
  static const char digits[] = "0123456789abcdef";
  for (size_t i = 0; i < length; i++) {
    output[i * 2] = digits[data[i] >> 4];
    output[i * 2 + 1] = digits[data[i] & 0x0f];
  }
  output[length * 2] = '\0';
}

static int hex_value(char value) {
  if (value >= '0' && value <= '9') return value - '0';
  if (value >= 'a' && value <= 'f') return value - 'a' + 10;
  if (value >= 'A' && value <= 'F') return value - 'A' + 10;
  return -1;
}

static bool hex_to_bytes(const char *hex, uint8_t *output, size_t output_length) {
  if (strlen(hex) != output_length * 2) return false;
  for (size_t i = 0; i < output_length; i++) {
    int high = hex_value(hex[i * 2]);
    int low = hex_value(hex[i * 2 + 1]);
    if (high < 0 || low < 0) return false;
    output[i] = (uint8_t)((high << 4) | low);
  }
  return true;
}

static bool hmac_sha256(const uint8_t key[32], const char *message, uint8_t output[32]) {
  const mbedtls_md_info_t *info = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
  return info && mbedtls_md_hmac(info, key, 32, (const uint8_t *)message,
                                 strlen(message), output) == 0;
}

static bool constant_time_equal(const uint8_t *left, const uint8_t *right, size_t length) {
  uint8_t difference = 0;
  for (size_t i = 0; i < length; i++) difference |= left[i] ^ right[i];
  return difference == 0;
}

static bool decrypt_password(const uint8_t pairing_key[32], const char *expected_nonce,
                             char *response, uint8_t *password, size_t *password_length) {
  char *save = NULL;
  char *kind = strtok_r(response, " ", &save);
  char *nonce = strtok_r(NULL, " ", &save);
  char *iv_hex = strtok_r(NULL, " ", &save);
  char *ciphertext_hex = strtok_r(NULL, " ", &save);
  char *mac_hex = strtok_r(NULL, " ", &save);
  if (!kind || !nonce || !iv_hex || !ciphertext_hex || !mac_hex ||
      strtok_r(NULL, " ", &save) || strcmp(kind, "PW") != 0 ||
      strcmp(nonce, expected_nonce) != 0) return false;

  size_t ciphertext_length = strlen(ciphertext_hex) / 2;
  if ((strlen(ciphertext_hex) & 1) || ciphertext_length > *password_length) return false;

  uint8_t got_mac[32];
  uint8_t expected_mac[32];
  uint8_t iv[16];
  uint8_t ciphertext[160];
  char material[512];
  if (!hex_to_bytes(mac_hex, got_mac, sizeof(got_mac)) ||
      !hex_to_bytes(iv_hex, iv, sizeof(iv)) ||
      !hex_to_bytes(ciphertext_hex, ciphertext, ciphertext_length)) return false;
  if (snprintf(material, sizeof(material), "PW|%s|%s|%s", nonce, iv_hex,
               ciphertext_hex) >= sizeof(material) ||
      !hmac_sha256(pairing_key, material, expected_mac) ||
      !constant_time_equal(got_mac, expected_mac, sizeof(got_mac))) return false;

  char session_material[64];
  uint8_t session_key[32];
  snprintf(session_material, sizeof(session_material), "SESSION|%s", nonce);
  if (!hmac_sha256(pairing_key, session_material, session_key)) return false;

  mbedtls_aes_context aes;
  mbedtls_aes_init(&aes);
  size_t offset = 0;
  uint8_t stream_block[16] = {0};
  int result = mbedtls_aes_setkey_enc(&aes, session_key, 256);
  if (result == 0) {
    result = mbedtls_aes_crypt_ctr(&aes, ciphertext_length, &offset, iv,
                                   stream_block, ciphertext, password);
  }
  mbedtls_aes_free(&aes);
  secure_wipe(session_key, sizeof(session_key));
  secure_wipe(ciphertext, sizeof(ciphertext));
  secure_wipe(stream_block, sizeof(stream_block));
  if (result != 0) return false;
  *password_length = ciphertext_length;
  return true;
}

static bool decrypt_password_v2(const char *expected_nonce, char *response,
                                uint8_t *password, size_t *password_length) {
  char *save = NULL;
  char *kind = strtok_r(response, " ", &save);
  char *key_id_hex = strtok_r(NULL, " ", &save);
  char *nonce = strtok_r(NULL, " ", &save);
  char *iv_hex = strtok_r(NULL, " ", &save);
  char *ciphertext_hex = strtok_r(NULL, " ", &save);
  char *mac_hex = strtok_r(NULL, " ", &save);
  if (!kind || !key_id_hex || !nonce || !iv_hex || !ciphertext_hex || !mac_hex ||
      strtok_r(NULL, " ", &save) || strcmp(kind, "PW2") != 0 ||
      strcmp(nonce, expected_nonce) != 0) return false;

  uint8_t key_id[DEVICE_CONFIG_HID_KEY_ID_SIZE];
  device_hid_host_t host = {0};
  bool found = false;
  if (!hex_to_bytes(key_id_hex, key_id, sizeof(key_id))) return false;
  for (size_t i = 0; i < device_config_hid_host_count(); i++) {
    if (device_config_get_hid_host(i, &host) &&
        constant_time_equal(host.id, key_id, sizeof(key_id))) {
      found = true;
      break;
    }
  }
  secure_wipe(key_id, sizeof(key_id));
  if (!found) {
    secure_wipe(&host, sizeof(host));
    return false;
  }

  size_t ciphertext_length = strlen(ciphertext_hex) / 2;
  uint8_t got_mac[32];
  uint8_t expected_mac[32];
  uint8_t iv[16];
  uint8_t ciphertext[160];
  uint8_t session_key[32];
  uint8_t stream_block[16] = {0};
  char material[544];
  char session_material[64];
  bool ok = false;
  if ((strlen(ciphertext_hex) & 1) || ciphertext_length > *password_length ||
      !hex_to_bytes(mac_hex, got_mac, sizeof(got_mac)) ||
      !hex_to_bytes(iv_hex, iv, sizeof(iv)) ||
      !hex_to_bytes(ciphertext_hex, ciphertext, ciphertext_length) ||
      snprintf(material, sizeof(material), "PW2|%s|%s|%s|%s", key_id_hex, nonce,
               iv_hex, ciphertext_hex) >= sizeof(material) ||
      !hmac_sha256(host.key, material, expected_mac) ||
      !constant_time_equal(got_mac, expected_mac, sizeof(got_mac))) goto done;

  snprintf(session_material, sizeof(session_material), "SESSION|%s", nonce);
  if (!hmac_sha256(host.key, session_material, session_key)) goto done;
  mbedtls_aes_context aes;
  mbedtls_aes_init(&aes);
  size_t offset = 0;
  int result = mbedtls_aes_setkey_enc(&aes, session_key, 256);
  if (result == 0) {
    result = mbedtls_aes_crypt_ctr(&aes, ciphertext_length, &offset, iv,
                                   stream_block, ciphertext, password);
  }
  mbedtls_aes_free(&aes);
  if (result == 0) {
    *password_length = ciphertext_length;
    ok = true;
  }

done:
  secure_wipe(&host, sizeof(host));
  secure_wipe(got_mac, sizeof(got_mac));
  secure_wipe(expected_mac, sizeof(expected_mac));
  secure_wipe(ciphertext, sizeof(ciphertext));
  secure_wipe(session_key, sizeof(session_key));
  secure_wipe(stream_block, sizeof(stream_block));
  return ok;
}

static bool request_and_type_password(fingerprint_match_t match) {
  uint8_t pairing_key[32];
  uint8_t nonce_bytes[16];
  uint8_t event_mac[32];
  char nonce[33];
  char mac_hex[65];
  char material[128];
  char event[896];
  char response[640];
  uint8_t password[160];
  size_t password_length = sizeof(password);
  bool result = false;

  size_t host_count = device_config_hid_host_count();
  if (!device_config_get_hid_key(pairing_key) || host_count == 0) return false;
  esp_fill_random(nonce_bytes, sizeof(nonce_bytes));
  bytes_to_hex(nonce_bytes, sizeof(nonce_bytes), nonce);
  event_counter++;
  xQueueReset(password_responses);
  if (host_count == 1) {
    snprintf(material, sizeof(material), "EV|%s|%lu|%u|%u", nonce,
             (unsigned long)event_counter, match.slot, match.score);
    if (!hmac_sha256(pairing_key, material, event_mac)) goto done;
    bytes_to_hex(event_mac, sizeof(event_mac), mac_hex);
    snprintf(event, sizeof(event), "EV %s %lu %u %u %s", nonce,
             (unsigned long)event_counter, match.slot, match.score, mac_hex);
    config_console_send_line(event);
    if (xQueueReceive(password_responses, response, pdMS_TO_TICKS(6000)) != pdTRUE ||
        !decrypt_password(pairing_key, nonce, response, password, &password_length)) goto done;
  } else {
    int used = snprintf(event, sizeof(event), "EV2 %s %lu %u %u", nonce,
                        (unsigned long)event_counter, match.slot, match.score);
    for (size_t i = 0; i < host_count && used > 0 && used < sizeof(event); i++) {
      device_hid_host_t host;
      char id_hex[DEVICE_CONFIG_HID_KEY_ID_SIZE * 2 + 1];
      if (!device_config_get_hid_host(i, &host)) goto done;
      bytes_to_hex(host.id, sizeof(host.id), id_hex);
      snprintf(material, sizeof(material), "EV2|%s|%s|%lu|%u|%u", id_hex, nonce,
               (unsigned long)event_counter, match.slot, match.score);
      if (!hmac_sha256(host.key, material, event_mac)) {
        secure_wipe(&host, sizeof(host));
        goto done;
      }
      bytes_to_hex(event_mac, sizeof(event_mac), mac_hex);
      used += snprintf(event + used, sizeof(event) - used, " %s:%s", id_hex, mac_hex);
      secure_wipe(&host, sizeof(host));
    }
    if (used <= 0 || used >= sizeof(event)) goto done;
    config_console_send_line(event);
    if (xQueueReceive(password_responses, response, pdMS_TO_TICKS(1500)) == pdTRUE &&
        decrypt_password_v2(nonce, response, password, &password_length)) {
      result = type_ascii(password, password_length);
      goto done;
    }
    password_length = sizeof(password);
    snprintf(material, sizeof(material), "EV|%s|%lu|%u|%u", nonce,
             (unsigned long)event_counter, match.slot, match.score);
    if (!hmac_sha256(pairing_key, material, event_mac)) goto done;
    bytes_to_hex(event_mac, sizeof(event_mac), mac_hex);
    snprintf(event, sizeof(event), "EV %s %lu %u %u %s", nonce,
             (unsigned long)event_counter, match.slot, match.score, mac_hex);
    config_console_send_line(event);
    if (xQueueReceive(password_responses, response, pdMS_TO_TICKS(4500)) != pdTRUE ||
        !decrypt_password(pairing_key, nonce, response, password, &password_length)) goto done;
  }
  result = type_ascii(password, password_length);

done:
  secure_wipe(pairing_key, sizeof(pairing_key));
  secure_wipe(nonce_bytes, sizeof(nonce_bytes));
  secure_wipe(event_mac, sizeof(event_mac));
  secure_wipe(password, sizeof(password));
  return result;
}

static bool suspended_sensor_poll_available(void) {
  return hid_suspended && hid_remote_wakeup_enabled &&
         !hid_remote_wakeup_attempted;
}

static void handle_fingerprint_match(fingerprint_match_t match) {
  bool success = false;
  if (device_config_mode() == DEVICE_MODE_HID) {
    ESP_LOGI(TAG, "finger matched; requesting HID password");
    success = request_and_type_password(match);
    if (!success) ESP_LOGW(TAG, "HID helper request failed");
  } else {
    ESP_LOGI(TAG, "finger matched; authorizing PIV and typing PIN");
    piv_note_user_presence();
    success = type_dummy_pin();
    if (!success) ESP_LOGW(TAG, "HID report interrupted by USB suspend");
  }
  runtime_health_note_auth(success);
}

static void touch_hid_task(void *arg) {
  (void)arg;
  TickType_t last_success = 0;
  TickType_t last_poll = 0;
  TickType_t pending_since = 0;
  bool wait_for_lift = false;
  fingerprint_match_t pending_match = {0};

  while (true) {
    if (hid_reconnect_pending && !hid_suspended) {
      hid_reconnect_pending = false;
      ESP_LOGW(TAG, "host resumed after long USB suspend; reconnecting");
      runtime_health_note_usb_reconnect();
      tud_disconnect();
      vTaskDelay(pdMS_TO_TICKS(250));
      tud_connect();
      hid_needs_release = true;
      continue;
    }
    TickType_t now = xTaskGetTickCount();
    if (pending_match.slot && hid_suspended &&
        (TickType_t)(now - pending_since) >= pdMS_TO_TICKS(USB_WAKE_TIMEOUT_MS)) {
      ESP_LOGW(TAG, "host did not resume after fingerprint remote wake");
      pending_match = (fingerprint_match_t){0};
      last_success = now;
      wait_for_lift = true;
    }
    if (hid_needs_release && tud_hid_ready()) {
      tud_hid_keyboard_report(0, 0, NULL);
      hid_needs_release = false;
    }
    if (pending_match.slot && !hid_suspended && tud_hid_ready()) {
      handle_fingerprint_match(pending_match);
      pending_match = (fingerprint_match_t){0};
      last_success = xTaskGetTickCount();
      wait_for_lift = true;
      vTaskDelay(pdMS_TO_TICKS(250));
      fingerprint_led_idle();
      continue;
    }
    bool present = fingerprint_present_hint();
    if (wait_for_lift && !present &&
        (TickType_t)(now - last_success) >
            pdMS_TO_TICKS(device_config_touch_cooldown_ms())) {
      wait_for_lift = false;
    }
    TickType_t min_interval = hid_suspended ? pdMS_TO_TICKS(SUSPENDED_SENSOR_POLL_MS) :
                              present ? pdMS_TO_TICKS(15) : pdMS_TO_TICKS(50);
    if (!wait_for_lift && !pending_match.slot &&
        (tud_hid_ready() || suspended_sensor_poll_available()) &&
        (TickType_t)(now - last_poll) >= min_interval) {
      fingerprint_match_t match = fingerprint_authorize_poll_match();
      if (match.slot == 0) {
        last_poll = xTaskGetTickCount();
        vTaskDelay(pdMS_TO_TICKS(10));
        continue;
      }
      pending_match = match;
      pending_since = xTaskGetTickCount();
      if (hid_suspended) {
        // Some supported sensor modules do not provide a usable touch signal.
        // Match before requesting wake so an unrecognized touch cannot wake the
        // host. Remote wake still requires host permission.
        hid_remote_wakeup_attempted = true;
        if (!tud_remote_wakeup()) {
          ESP_LOGW(TAG, "finger matched, but USB remote wake failed");
          pending_match = (fingerprint_match_t){0};
          last_success = xTaskGetTickCount();
          wait_for_lift = true;
        }
      }
    }
    last_poll = xTaskGetTickCount();
    vTaskDelay(pdMS_TO_TICKS(10));
  }
}

void touch_pin_hid_start(void) {
  password_responses = xQueueCreate(1, 640);
  xTaskCreate(touch_hid_task, "touch_hid", 6144, NULL, 4, NULL);
}

void touch_pin_hid_usb_attached(void) {
  hid_suspended = false;
  hid_reconnect_pending = false;
  hid_needs_release = true;
  hid_remote_wakeup_enabled = false;
  hid_remote_wakeup_attempted = false;
  runtime_health_note_usb(RUNTIME_USB_ATTACHED);
}

// The host can suspend the USB bus while the HID task is between a key-down
// and key-up report.  Mark the report state dirty so the task emits a release
// after resume instead of leaving the host with a stuck key.
void tud_suspend_cb(bool remote_wakeup_en) {
  if (!hid_suspended) {
    hid_suspended_at = xTaskGetTickCount();
  }
  hid_suspended = true;
  hid_needs_release = true;
  hid_remote_wakeup_enabled = remote_wakeup_en;
  hid_remote_wakeup_attempted = false;
  runtime_health_note_usb(RUNTIME_USB_SUSPENDED);
}

void tud_resume_cb(void) {
  if (hid_suspended &&
      (TickType_t)(xTaskGetTickCount() - hid_suspended_at) >=
          pdMS_TO_TICKS(USB_LONG_SUSPEND_MS)) {
    hid_reconnect_pending = true;
  }
  hid_suspended = false;
  hid_needs_release = true;
  hid_remote_wakeup_enabled = false;
  hid_remote_wakeup_attempted = false;
  runtime_health_note_usb(RUNTIME_USB_ATTACHED);
}

bool touch_pin_hid_submit_response(const char *response) {
  if (!password_responses ||
      (strncmp(response, "PW ", 3) != 0 && strncmp(response, "PW2 ", 4) != 0) ||
      strlen(response) >= 640) {
    return false;
  }
  char queued[640] = {0};
  strlcpy(queued, response, sizeof(queued));
  return xQueueSend(password_responses, queued, 0) == pdTRUE;
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

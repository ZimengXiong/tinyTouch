#include "fingerprint.h"
#include "device_config.h"

#include <string.h>

#include "driver/gpio.h"
#include "driver/uart.h"
#include "esp_log.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "nvs.h"

static const char *TAG = "fingerprint";

static const uart_port_t FP_UART = UART_NUM_1;
static const int FP_TX_PIN = 43;
static const int FP_RX_PIN = 44;
static const int FP_INT_PIN = 2;
static const int INT_ACTIVE_VALUE = 1;
static const uint16_t END_SLOT = FINGER_TEMPLATE_LIMIT - 1;
static const uint32_t FINGER_WAIT_MS = 7000;
static const uint8_t FP_LED_BLUE = 0x01;
static const uint8_t FP_LED_GREEN = 0x02;
static const uint8_t FP_LED_RED = 0x04;
static const uint8_t FP_LED_FUNC_STEADY = 3;

static SemaphoreHandle_t fp_mutex;
static volatile bool prompted_authorization_active;
static bool sensor_ready;
static finger_profiles_t profiles;
static bool profiles_ready;
static bool (*enrollment_connected)(void);
static bool cleanup_pending_locked(void);

// PS_BlnAmSw is persistent, but becomes effective only after sensor power loss.
// Keep its migration separate from the user's LED preference and config blob.
static uint8_t led_manual_stage; // 0: not requested, 1: reconnect needed, 2: cold boot observed
static bool led_stage_loaded, led_stage_saved;
static const char *led_control = "pending";
static bool led_control_attempted;
static unsigned led_manual_attempts;
static TickType_t led_control_attempt_at;
static uint8_t led_requested_color;
static bool led_update_pending;
static unsigned led_failures;
static TickType_t led_attempt_at;

static bool template_usable(uint16_t slot) {
  return profiles_ready && slot < FINGER_TEMPLATE_LIMIT &&
         !(profiles.pending & (UINT64_C(1) << slot));
}

static uint16_t event_slot(uint16_t slot) {
  // Keep legacy event IDs 1-39; physical slot zero has the nonzero ID 40.
  return slot ? slot : FINGER_TEMPLATE_LIMIT;
}

static portMUX_TYPE sensor_state_lock = portMUX_INITIALIZER_UNLOCKED;

static bool sensor_ready_snapshot(void) {
  portENTER_CRITICAL(&sensor_state_lock);
  bool ready = sensor_ready;
  portEXIT_CRITICAL(&sensor_state_lock);
  return ready;
}

static void set_sensor_ready(bool ready) {
  portENTER_CRITICAL(&sensor_state_lock);
  sensor_ready = ready;
  portEXIT_CRITICAL(&sensor_state_lock);
}

static void note_transport_success(void) {
  set_sensor_ready(true);
}

static void note_transport_failure(void) { set_sensor_ready(false); }

static uint16_t fp_checksum(uint8_t packet_id, const uint8_t *payload, size_t payload_len) {
  uint16_t length = payload_len + 2;
  uint32_t total = packet_id + (length >> 8) + (length & 0xff);
  for (size_t i = 0; i < payload_len; i++) total += payload[i];
  return (uint16_t)total;
}

static bool fp_response_checksum_valid(const uint8_t *packet, size_t packet_len) {
  if (packet_len < 11) return false;
  uint16_t response_len = ((uint16_t)packet[7] << 8) | packet[8];
  if (response_len < 2 || packet_len != 9 + response_len) return false;
  size_t payload_len = response_len - 2;
  uint16_t expected = fp_checksum(packet[6], packet + 9, payload_len);
  uint16_t received = ((uint16_t)packet[packet_len - 2] << 8) |
                      packet[packet_len - 1];
  return received == expected;
}

static bool fp_command(uint8_t instruction, const uint8_t *params, size_t param_len,
                       uint8_t *confirm, uint8_t *data, size_t *data_len,
                       uint32_t timeout_ms) {
  uint8_t drain[64];
  while (uart_read_bytes(FP_UART, drain, sizeof(drain), 0) > 0) {}

  uint8_t payload[32];
  if (param_len + 1 > sizeof(payload)) return false;
  payload[0] = instruction;
  if (param_len) memcpy(payload + 1, params, param_len);

  const size_t payload_len = param_len + 1;
  const uint16_t length = payload_len + 2;
  const uint16_t sum = fp_checksum(0x01, payload, payload_len);
  const uint8_t header[] = {
    0xef, 0x01, 0xff, 0xff, 0xff, 0xff, 0x01,
    (uint8_t)(length >> 8), (uint8_t)(length & 0xff)
  };

  if (uart_write_bytes(FP_UART, header, sizeof(header)) != sizeof(header) ||
      uart_write_bytes(FP_UART, payload, payload_len) != payload_len) {
    note_transport_failure();
    return false;
  }
  uint8_t sum_bytes[] = {(uint8_t)(sum >> 8), (uint8_t)(sum & 0xff)};
  if (uart_write_bytes(FP_UART, sum_bytes, sizeof(sum_bytes)) != sizeof(sum_bytes)) {
    note_transport_failure();
    return false;
  }

  uint8_t response[96];
  size_t pos = 0;
  const size_t data_cap = (data && data_len) ? *data_len : 0;
  size_t out_len = 0;
  bool saw_ack = false;
  TickType_t post_ack_until = 0;
  TickType_t start = xTaskGetTickCount();
  TickType_t deadline = pdMS_TO_TICKS(timeout_ms);
  if (data && data_len) *data_len = 0;

  while ((xTaskGetTickCount() - start) < deadline) {
    // Drain every complete packet already in memory before waiting for more
    // UART bytes. Sensors may return an ACK and its following data packet in a
    // single read; blocking between them can otherwise turn valid buffered
    // data into a timeout.
    while (true) {
      while (pos >= 2 && !(response[0] == 0xef && response[1] == 0x01)) {
        memmove(response, response + 1, --pos);
      }
      if (pos < 9) break;

      uint8_t packet_id = response[6];
      uint16_t resp_len = ((uint16_t)response[7] << 8) | response[8];
      size_t expected = 9 + resp_len;
      if (response[2] != 0xff || response[3] != 0xff ||
          response[4] != 0xff || response[5] != 0xff || resp_len < 2) {
        ESP_LOGW(TAG, "fingerprint response has invalid address/length");
        note_transport_failure();
        return false;
      }
      if (expected > sizeof(response)) {
        note_transport_failure();
        return false;
      }
      if (pos < expected) break;

      size_t response_payload_len = resp_len - 2;
      if (!fp_response_checksum_valid(response, expected)) {
        ESP_LOGW(TAG, "fingerprint response checksum mismatch");
        note_transport_failure();
        return false;
      }

      if (packet_id == 0x07) {
        if (response_payload_len < 1) {
          note_transport_failure();
          return false;
        }
        note_transport_success();
        *confirm = response[9];
        saw_ack = true;
        size_t actual_len = response_payload_len - 1;
        if (data && data_len && actual_len) {
          size_t copy_len = actual_len;
          if (copy_len > data_cap - out_len) copy_len = data_cap - out_len;
          memcpy(data + out_len, response + 10, copy_len);
          out_len += copy_len;
          *data_len = out_len;
        }
        if (*confirm != 0x00 || !data || !data_len || out_len >= data_cap) return true;
        post_ack_until = xTaskGetTickCount() + pdMS_TO_TICKS(120);
      } else if (packet_id == 0x02 && data && data_len) {
        size_t actual_len = response_payload_len;
        if (actual_len) {
          size_t copy_len = actual_len;
          if (copy_len > data_cap - out_len) copy_len = data_cap - out_len;
          memcpy(data + out_len, response + 9, copy_len);
          out_len += copy_len;
          *data_len = out_len;
        }
        if (saw_ack && out_len >= data_cap) return true;
      }

      size_t remaining = pos - expected;
      if (remaining) memmove(response, response + expected, remaining);
      pos = remaining;
    }

    if (saw_ack && post_ack_until && xTaskGetTickCount() > post_ack_until) return true;

    int n = uart_read_bytes(FP_UART, response + pos, sizeof(response) - pos,
                            pdMS_TO_TICKS(10));
    if (n > 0) pos += (size_t)n;
  }

  if (!saw_ack) note_transport_failure();
  return saw_ack;
}

static bool fp_take(uint32_t timeout_ms) {
  return fp_mutex && xSemaphoreTake(fp_mutex, pdMS_TO_TICKS(timeout_ms)) == pdTRUE;
}

static void fp_give(void) {
  if (fp_mutex) xSemaphoreGive(fp_mutex);
}

static bool configure_manual_lighting(void) {
  led_control_attempted = true;
  led_control_attempt_at = xTaskGetTickCount();
  nvs_handle_t handle;
  if (!led_stage_loaded) {
    esp_err_t result = nvs_open("tt_led", NVS_READWRITE, &handle);
    if (result == ESP_OK) {
      result = nvs_get_u8(handle, "manual", &led_manual_stage);
      nvs_close(handle);
      if (result == ESP_ERR_NVS_NOT_FOUND) { led_manual_stage = 0; result = ESP_OK; }
    }
    if (result != ESP_OK || led_manual_stage > 2) {
      led_control = "storage-error";
      return false;
    }
    led_stage_loaded = led_stage_saved = true;
    if (led_manual_stage == 1 && esp_reset_reason() == ESP_RST_POWERON) {
      // A software/OTA/watchdog reset does not cycle the external sensor.
      led_manual_stage = 2;
      led_stage_saved = false;
    }
  }
  if (!led_manual_stage) {
    // ZW111 manual, section 3.5.6: 0x60/0x00 disables the sensor's own
    // success/failure animation after sensor power loss. A 0x3c off command
    // alone cannot prevent a brief green flash during authentication.
    const uint8_t manual = 0x00;
    uint8_t confirm = 0xff;
    bool was_ready = sensor_ready_snapshot();
    led_manual_attempts++;
    bool answered = fp_command(0x60, &manual, 1, &confirm, NULL, NULL, 200);
    // Lighting support is not fingerprint transport health. A rejected or
    // unsupported lighting command must not disable otherwise working unlocks.
    set_sensor_ready(was_ready);
    if (!answered || confirm != 0x00) {
      // A packet error can be transient. Other negative confirmations need
      // explicit retry/recovery; do not keep writing an unsupported setting.
      led_control = answered && confirm != 0x01 ? "rejected" : "pending";
      // A missing ACK could mean the write succeeded. Bound automatic retries
      // to avoid repeatedly writing sensor flash on modules that never reply.
      if (strcmp(led_control, "pending") == 0 && led_manual_attempts >= 3)
        led_control = "unavailable";
      ESP_LOGW(TAG, "manual LED control failed confirm=0x%02x", confirm);
      return false;
    }
    led_manual_stage = 1;
    led_stage_saved = false;
  }
  if (!led_stage_saved) {
    esp_err_t result = nvs_open("tt_led", NVS_READWRITE, &handle);
    if (result == ESP_OK) {
      result = nvs_set_u8(handle, "manual", led_manual_stage);
      if (result == ESP_OK) result = nvs_commit(handle);
      nvs_close(handle);
    }
    if (result != ESP_OK) { led_control = "storage-error"; return false; }
    led_stage_saved = true;
  }
  led_control = led_manual_stage == 1 ? "reconnect" : "manual";
  return true;
}

static bool aura_command(uint8_t effect, uint8_t start, uint8_t end, uint8_t cycles) {
  // Hi-Link PS_ControlBLN (0x3c), ordinary RGB format. Extended brightness,
  // speed and marquee formats are sensor-specific and are not sent here.
  uint8_t params[] = {effect, start, end, cycles};
  uint8_t confirm = 0xff;
  bool was_ready = sensor_ready_snapshot();
  bool ok = fp_command(0x3c, params, sizeof(params), &confirm, NULL, NULL, 200) && confirm == 0x00;
  set_sensor_ready(was_ready);
  led_attempt_at = xTaskGetTickCount();
  led_update_pending = !ok;
  if (ok) led_failures = 0;
  else {
    if (!led_failures) ESP_LOGW(TAG, "LED update pending confirm=0x%02x", confirm);
    if (led_failures < 3) led_failures++;
  }
  return ok;
}

static bool apply_aura(void);

static bool set_aura(uint8_t color) {
  led_requested_color = color;
  return apply_aura();
}

void fingerprint_led_service(void) {
  // Called even while waiting for a finger to lift, so a rejected cleanup is
  // not forgotten. One bounded command per pass; no UART traffic when settled.
  if (!fp_take(0)) return;
  TickType_t now = xTaskGetTickCount();
  if (sensor_ready_snapshot() && (!led_stage_loaded || !led_manual_stage || !led_stage_saved) &&
      strcmp(led_control, "rejected") != 0 &&
      strcmp(led_control, "unavailable") != 0 &&
      (!led_control_attempted || (TickType_t)(now - led_control_attempt_at) >= pdMS_TO_TICKS(2000))) {
    (void)configure_manual_lighting();
  } else if (sensor_ready_snapshot() && led_update_pending &&
             (TickType_t)(now - led_attempt_at) >= pdMS_TO_TICKS(led_failures < 3 ? 100 : 2000)) {
    (void)apply_aura();
  }
  fp_give();
}

const char *fingerprint_led_control_status(void) {
  if (!fp_take(0)) return "busy";
  const char *value = led_control;
  fp_give();
  return value;
}

bool fingerprint_led_update_pending(void) {
  if (!fp_take(0)) return true;
  bool value = led_update_pending;
  fp_give();
  return value;
}

static bool apply_aura(void) {
  uint8_t color = led_requested_color;
  device_led_mode_t mode = device_config_led_mode();
  // Determine the role before mapping colors. A blue success color must still
  // show in ONLY_AUTH mode, and an idle red color must remain suppressed.
  bool idle = color == FP_LED_BLUE;
  if (mode == DEVICE_LED_OFF || (mode == DEVICE_LED_ONLY_AUTH && color == FP_LED_BLUE))
    return aura_command(FP_LED_FUNC_STEADY, 0, 0, 0);
  device_options_t value = device_config_options();
  if (idle) return aura_command(value.led_idle_effect, value.led_idle_color,
                               value.led_idle_effect == 1 ? value.led_idle_end_color : value.led_idle_color,
                               value.led_idle_cycles);
  if (color == FP_LED_GREEN) color = value.led_success_color;
  else if (color == FP_LED_RED) color = value.led_failure_color;
  return aura_command(FP_LED_FUNC_STEADY, color, color, 0);
}

static void show_result(bool ok) {
  set_aura(ok ? FP_LED_GREEN : FP_LED_RED);
  vTaskDelay(pdMS_TO_TICKS(device_config_options().led_feedback_ms));
  set_aura(FP_LED_BLUE);
}

void fingerprint_led_idle(void) {
  if (!fp_take(1000)) return;
  set_aura(FP_LED_BLUE);
  fp_give();
}

bool fingerprint_set_led_mode(device_led_mode_t mode) {
  if (!fp_take(1000)) return false;
  bool ok = device_config_set_led_mode(mode);
  if (ok) {
    led_manual_attempts = 0;
    bool controlled = configure_manual_lighting();
    // The sensor can discard a command while its own animation is running.
    // Reassert a steady colour and only report success after acknowledgment.
    for (int attempt = 0; attempt < 3; attempt++) {
      ok = set_aura(FP_LED_BLUE);
      if (ok) break;
      if (attempt < 2) vTaskDelay(pdMS_TO_TICKS(50));
    }
    ok = ok && controlled;
  }
  fp_give();
  return ok;
}

bool fingerprint_set_option(device_option_t option, uint16_t value) {
  if (!fp_take(1000)) return false;
  bool ok = device_config_set_option(option, value);
  if (ok) {
    for (int attempt = 0; attempt < 3; attempt++) {
      ok = set_aura(FP_LED_BLUE);
      if (ok) break;
      if (attempt < 2) vTaskDelay(pdMS_TO_TICKS(50));
    }
  }
  fp_give(); return ok;
}

bool fingerprint_preview_led(uint8_t color, uint8_t effect, uint16_t duration_ms) {
  if (color > 7 || (effect != 1 && effect != 2 && effect != 3 && effect != 5 && effect != 6) ||
      duration_ms < 100 || duration_ms > 5000 || !fp_take(1000)) return false;
  // Explicit preview can illuminate an otherwise disabled ring. It never saves
  // preferences and always restores the configured idle state before returning.
  bool ok = aura_command(effect, color, color, 0);
  if (ok) vTaskDelay(pdMS_TO_TICKS(duration_ms));
  bool restored = set_aura(FP_LED_BLUE);
  fp_give(); return ok && restored;
}

static bool finger_present(void) {
  return gpio_get_level(FP_INT_PIN) == INT_ACTIVE_VALUE;
}

bool fingerprint_present_hint(void) {
  return finger_present();
}

static fingerprint_match_t fingerprint_match_captured(bool quiet) {
  fingerprint_match_t no_match = {0};
  uint8_t confirm = 0xff;
  uint8_t img2tz[] = {0x01};
  if (!fp_command(0x02, img2tz, sizeof(img2tz), &confirm, NULL, NULL, 2000) || confirm != 0x00) {
    if (!quiet) {
      ESP_LOGW(TAG, "img2tz failed confirm=0x%02x", confirm);
      show_result(false);
    }
    return no_match;
  }

  uint16_t count = FINGER_TEMPLATE_LIMIT;
  uint8_t search_params[] = {
    0x01,
    0, 0,
    (uint8_t)(count >> 8), (uint8_t)(count & 0xff)
  };
  uint8_t search_data[4];
  size_t search_len = sizeof(search_data);
  if (!fp_command(0x04, search_params, sizeof(search_params), &confirm, search_data, &search_len, 2000)) {
    if (!quiet) ESP_LOGW(TAG, "search command failed");
  } else if (confirm == 0x00 && search_len == sizeof(search_data)) {
    uint16_t score = ((uint16_t)search_data[2] << 8) | search_data[3];
    uint16_t slot = ((uint16_t)search_data[0] << 8) | search_data[1];
    bool ok = score > 0 && template_usable(slot);
    ESP_LOGI(TAG, "fingerprint search: %s slot=%u score=%u", ok ? "ok" : "failed",
             slot, score);
    if (!quiet) {
      show_result(ok);
    }
    if (ok) return (fingerprint_match_t){.slot = event_slot(slot), .score = score};
    return no_match;
  } else if (!quiet) {
    ESP_LOGW(TAG, "search failed confirm=0x%02x len=%u", confirm, (unsigned)search_len);
  }

  for (uint16_t slot = 0; slot <= END_SLOT; slot++) {
    if (!template_usable(slot)) continue;
    uint8_t load_params[] = {0x02, (uint8_t)(slot >> 8), (uint8_t)(slot & 0xff)};
    confirm = 0xff;
    if (!fp_command(0x07, load_params, sizeof(load_params), &confirm, NULL, NULL, 1000) ||
        confirm != 0x00) {
      if (!quiet) ESP_LOGW(TAG, "load slot %u failed confirm=0x%02x", slot, confirm);
      continue;
    }

    uint8_t match_data[2];
    size_t match_len = sizeof(match_data);
    confirm = 0xff;
    if (!fp_command(0x03, NULL, 0, &confirm, match_data, &match_len, 1000)) {
      if (!quiet) ESP_LOGW(TAG, "match slot %u command failed", slot);
      continue;
    }
    if (confirm == 0x00 && match_len == sizeof(match_data)) {
      uint16_t score = ((uint16_t)match_data[0] << 8) | match_data[1];
      if (score > 0) {
        ESP_LOGI(TAG, "fingerprint match: ok slot=%u score=%u", slot, score);
        if (!quiet) show_result(true);
        return (fingerprint_match_t){.slot = event_slot(slot), .score = score};
      }
    }
    if (!quiet) {
      ESP_LOGW(TAG, "match slot %u failed confirm=0x%02x len=%u", slot, confirm, (unsigned)match_len);
    }
  }

  if (!quiet) show_result(false);
  return no_match;
}

fingerprint_match_t fingerprint_authorize_poll_match(void) {
  fingerprint_match_t no_match = {0};
  if (!fp_take(0)) return no_match;
  uint8_t confirm = 0xff;
  if (!fp_command(0x01, NULL, 0, &confirm, NULL, NULL, 350) || confirm != 0x00) {
    if (device_config_led_mode() != DEVICE_LED_ON) set_aura(0);
    fp_give();
    return no_match;
  }
  fingerprint_match_t match = fingerprint_match_captured(true);
  device_led_mode_t mode = device_config_led_mode();
  if (mode != DEVICE_LED_OFF && prompted_authorization_active) {
    // Foreground AUTH has no HID result timer to restore the idle light.
    show_result(match.slot != 0);
  } else if (match.slot) set_aura(FP_LED_GREEN);
  else if (mode != DEVICE_LED_OFF) set_aura(FP_LED_RED);
  else if (mode == DEVICE_LED_OFF) set_aura(0);
  fp_give();
  return match;
}

void fingerprint_init(void) {
  gpio_config_t io = {
    .pin_bit_mask = 1ULL << FP_INT_PIN,
    .mode = GPIO_MODE_INPUT,
    .pull_up_en = GPIO_PULLUP_DISABLE,
    .pull_down_en = GPIO_PULLDOWN_ENABLE,
    .intr_type = GPIO_INTR_DISABLE,
  };
  ESP_ERROR_CHECK(gpio_config(&io));

  uart_config_t cfg = {
    .baud_rate = 57600,
    .data_bits = UART_DATA_8_BITS,
    .parity = UART_PARITY_DISABLE,
    .stop_bits = UART_STOP_BITS_1,
    .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
    .source_clk = UART_SCLK_DEFAULT,
  };
  ESP_ERROR_CHECK(uart_driver_install(FP_UART, 1024, 0, 0, NULL, 0));
  ESP_ERROR_CHECK(uart_param_config(FP_UART, &cfg));
  ESP_ERROR_CHECK(uart_set_pin(FP_UART, FP_TX_PIN, FP_RX_PIN,
                               UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE));
  fp_mutex = xSemaphoreCreateMutex();
  configASSERT(fp_mutex != NULL);
  profiles_ready = finger_profiles_load(&profiles);
  led_manual_stage = 0;
  led_stage_loaded = led_stage_saved = led_control_attempted = false;
  led_control = "pending";
  led_manual_attempts = 0;
  // A later COUNT/STATUS probe can restore transport health without entering
  // fingerprint_recover(). Keep idle lighting pending even if boot verify fails.
  led_requested_color = FP_LED_BLUE;
  led_update_pending = true;
  led_attempt_at = xTaskGetTickCount();
  led_failures = 0;

  uint8_t params[] = {0x00, 0x00, 0x00, 0x00};
  bool ok = false;
  for (int attempt = 1; attempt <= 3 && !ok; attempt++) {
    uint8_t confirm = 0xff;
    configASSERT(fp_take(2000));
    ok = fp_command(0x13, params, sizeof(params), &confirm, NULL, NULL, 2000) &&
         confirm == 0x00;
    set_sensor_ready(ok);
    fp_give();
    if (!ok && attempt < 3) vTaskDelay(pdMS_TO_TICKS(250));
  }
  ESP_LOGI(TAG, "sensor verify: %s", ok ? "ok" : "failed");
  if (ok) {
    configASSERT(fp_take(1000));
    (void)configure_manual_lighting();
    if (profiles_ready) (void)cleanup_pending_locked();
    fp_give();
    fingerprint_led_idle();
  }
}

bool fingerprint_is_ready(void) {
  return sensor_ready_snapshot();
}

bool fingerprint_recover(void) {
  if (!fp_take(3000)) return false;

  // A USB reconnect must not be required to recover one interrupted UART
  // transaction. Flush stale bytes, reapply the known sensor baud rate, and
  // verify the sensor in place. This does not erase state or restart either
  // processor.
  uart_flush_input(FP_UART);
  uart_set_baudrate(FP_UART, 57600);
  const uint8_t params[] = {0x00, 0x00, 0x00, 0x00};
  bool ok = false;
  for (int attempt = 0; attempt < 3 && !ok; attempt++) {
    uint8_t confirm = 0xff;
    ok = fp_command(0x13, params, sizeof(params), &confirm, NULL, NULL, 1200) &&
         confirm == 0x00;
    if (!ok && attempt < 2) vTaskDelay(pdMS_TO_TICKS(100));
  }
  set_sensor_ready(ok);
  if (ok) {
    led_manual_attempts = 0;
    (void)configure_manual_lighting();
    set_aura(FP_LED_BLUE);
  }
  fp_give();
  return ok;
}

bool fingerprint_authorize_prompted(void (*prompt)(void)) {
  // TOUCH_OUT is not reliable enough to gate a foreground capture on every
  // supported module. Reuse HID's quiet matcher and keep polling until the
  // user presents a valid enrolled finger or the authorization window ends.
  prompted_authorization_active = true;
  if (prompt) prompt();
  TickType_t deadline = xTaskGetTickCount() + pdMS_TO_TICKS(FINGER_WAIT_MS);
  bool ok = false;
  while (xTaskGetTickCount() < deadline) {
    if (fingerprint_authorize_poll_match().slot != 0) {
      ok = true;
      break;
    }
    vTaskDelay(pdMS_TO_TICKS(120));
  }
  prompted_authorization_active = false;
  return ok;
}

bool fingerprint_prompted_authorization_active(void) {
  return prompted_authorization_active;
}

int fingerprint_count(void) {
  for (unsigned attempt = 0; attempt < 3; attempt++) {
    if (fp_take(2000)) {
      uint8_t confirm = 0xff;
      uint8_t data[2];
      size_t data_len = sizeof(data);
      bool ok = fp_command(0x1d, NULL, 0, &confirm, data, &data_len, 2000) &&
                confirm == 0x00 && data_len == sizeof(data);
      fp_give();
      if (ok) return ((int)data[0] << 8) | data[1];
    }
    if (attempt < 2) {
      fingerprint_recover();
      vTaskDelay(pdMS_TO_TICKS(150));
    }
  }
  return -1;
}

static bool wait_capture_template(uint8_t buffer_id, uint32_t timeout_ms) {
  TickType_t start = xTaskGetTickCount();
  TickType_t deadline = pdMS_TO_TICKS(timeout_ms);
  while ((xTaskGetTickCount() - start) < deadline) {
    if (enrollment_connected && !enrollment_connected()) return false;
    uint8_t confirm = 0xff;
    bool captured = fp_command(0x01, NULL, 0, &confirm, NULL, NULL, 600) && confirm == 0x00;
    if (device_config_led_mode() != DEVICE_LED_ON) set_aura(0);
    if (captured) {
      uint8_t params[] = {buffer_id};
      if (fp_command(0x02, params, sizeof(params), &confirm, NULL, NULL, 2000) &&
          confirm == 0x00) {
        return true;
      }
      ESP_LOGW(TAG, "enrollment conversion failed confirm=0x%02x; retrying", confirm);
    }
    vTaskDelay(pdMS_TO_TICKS(120));
  }
  return false;
}

static bool wait_finger_removed(uint32_t timeout_ms) {
  TickType_t start = xTaskGetTickCount();
  TickType_t deadline = pdMS_TO_TICKS(timeout_ms);
  unsigned absent_samples = 0;
  while ((xTaskGetTickCount() - start) < deadline) {
    if (enrollment_connected && !enrollment_connected()) return false;
    uint8_t confirm = 0xff;
    bool answered = fp_command(0x01, NULL, 0, &confirm, NULL, NULL, 500);
    if (device_config_led_mode() != DEVICE_LED_ON) set_aura(0);
    if (answered && confirm == 0x02) {
      if (++absent_samples >= 3) return true;
    } else if (answered && confirm == 0x00) {
      absent_samples = 0;
    } else {
      // A UART timeout or sensor error is not evidence that the finger lifted.
      absent_samples = 0;
      ESP_LOGW(TAG, "finger-removal check failed confirm=0x%02x", confirm);
    }
    vTaskDelay(pdMS_TO_TICKS(100));
  }
  return false;
}

static bool enroll_template_locked(uint16_t slot, void (*prompt)(const char *message)) {
  bool ok = false;
  set_aura(FP_LED_BLUE);
  if (prompt) prompt("TOUCH");
  if (!wait_capture_template(1, 15000)) goto done;
  if (prompt) prompt("LIFT");
  if (!wait_finger_removed(10000)) goto done;
  vTaskDelay(pdMS_TO_TICKS(250));
  if (prompt) prompt("TOUCH_AGAIN");
  if (!wait_capture_template(2, 15000)) goto done;

  uint8_t confirm = 0xff;
  if (!fp_command(0x05, NULL, 0, &confirm, NULL, NULL, 2000) || confirm != 0x00) goto done;
  uint8_t store[] = {0x01, (uint8_t)(slot >> 8), (uint8_t)slot};
  ok = fp_command(0x06, store, sizeof(store), &confirm, NULL, NULL, 2000) && confirm == 0x00;

done:
  show_result(ok);
  return ok;
}

bool fingerprint_delete_all(void) {
  if (!fp_take(1000)) return false;
  uint8_t confirm = 0xff;
  bool ok = fp_command(0x0d, NULL, 0, &confirm, NULL, NULL, 2000) && confirm == 0x00;
  if (ok) {
    finger_profiles_t empty = {.version = 1};
    ok = finger_profiles_save(&empty);
    if (ok) { profiles = empty; profiles_ready = true; }
  }
  fp_give();
  return ok;
}

static bool inventory_locked(fingerprint_inventory_t *inventory) {
  if (!profiles_ready) return false;
  uint8_t confirm = 0xff, parameters[16];
  size_t length = sizeof(parameters);
  if (!fp_command(0x0f, NULL, 0, &confirm, parameters, &length, 1000) ||
      confirm != 0 || length != sizeof(parameters)) return false;
  unsigned capacity = ((unsigned)parameters[4] << 8) | parameters[5];
  if (capacity == 0 || capacity > 256) return false;
  uint8_t page = 0, index[32];
  length = sizeof(index);
  if (!fp_command(0x1f, &page, 1, &confirm, index, &length, 1000) ||
      confirm != 0 || length != sizeof(index)) return false;
  uint64_t occupied = 0;
  unsigned total = 0;
  for (unsigned slot = 0; slot < 256; slot++) {
    if (!(index[slot / 8] & (1u << (slot % 8)))) continue;
    if (slot >= capacity) return false;
    total++;
    if (slot < FINGER_TEMPLATE_LIMIT) occupied |= UINT64_C(1) << slot;
  }
  // A mismatched index must never be mistaken for free space.
  uint8_t count[2]; length = sizeof(count);
  if (!fp_command(0x1d, NULL, 0, &confirm, count, &length, 1000) ||
      confirm != 0 || length != sizeof(count) || total != (((unsigned)count[0] << 8) | count[1]))
    return false;
  inventory->capacity = capacity < FINGER_TEMPLATE_LIMIT ? capacity : FINGER_TEMPLATE_LIMIT;
  inventory->occupied = occupied;
  inventory->profiles = profiles;
  return true;
}

bool fingerprint_inventory(fingerprint_inventory_t *inventory) {
  if (!inventory || !fp_take(1000)) return false;
  bool ok = inventory_locked(inventory);
  fp_give();
  return ok;
}

static bool save_profiles_locked(const finger_profiles_t *next) {
  if (!finger_profiles_save(next)) return false;
  profiles = *next;
  return true;
}

static bool delete_mask_locked(uint64_t mask) {
  fingerprint_inventory_t inventory;
  if (!inventory_locked(&inventory)) return false;
  for (unsigned slot = 0; slot < inventory.capacity; slot++) {
    if (!(mask & inventory.occupied & (UINT64_C(1) << slot))) continue;
    uint8_t params[] = {0, slot, 0, 1}, confirm = 0xff;
    if (!fp_command(0x0c, params, sizeof(params), &confirm, NULL, NULL, 1000) || confirm != 0)
      return false;
  }
  return inventory_locked(&inventory) && !(inventory.occupied & mask);
}

static bool cleanup_pending_locked(void) {
  if (!profiles.pending) return true;
  if (!delete_mask_locked(profiles.pending)) return false;
  finger_profiles_t next = profiles;
  next.pending = 0;
  return save_profiles_locked(&next);
}

bool fingerprint_enroll_finger(unsigned finger, bool replace, void (*prompt)(const char *),
                               bool (*connected)(void)) {
  if (!finger || finger > FINGER_PROFILE_COUNT || !fp_take(1000)) return false;
  bool ok = false;
  prompted_authorization_active = true;
  enrollment_connected = connected;
  if ((connected && !connected()) || !profiles_ready || !cleanup_pending_locked()) goto done;
  fingerprint_inventory_t inventory;
  if (!inventory_locked(&inventory)) goto done;
  uint64_t selected = finger_profiles_block(finger);
  if (!finger_profiles_block_fits(finger, inventory.capacity) ||
      (!replace && (inventory.occupied & selected))) goto done;
  if (connected && !connected()) goto done;
  finger_profiles_t next = profiles;
  next.pending = selected;
  if (!save_profiles_locked(&next)) goto done;
  if ((inventory.occupied & selected) && !delete_mask_locked(selected)) goto rollback;
  unsigned view = 0;
  for (unsigned i = 1; i <= FINGER_TEMPLATE_LIMIT; i++) {
    unsigned slot = i == FINGER_TEMPLATE_LIMIT ? 0 : i;
    if (!(selected & (UINT64_C(1) << slot))) continue;
    if (prompt) prompt("LIFT");
    if (!wait_finger_removed(10000)) goto rollback;
    char event[] = "VIEW 1";
    event[5] += view++;
    if (prompt) prompt(event);
    if (!enroll_template_locked(slot, prompt)) goto rollback;
  }
  if ((connected && !connected()) || !inventory_locked(&inventory) ||
      (inventory.occupied & selected) != selected) goto rollback;
  next.pending = 0;
  if (!save_profiles_locked(&next)) goto rollback;
  ok = true;
  goto done;
rollback:
  // The journal remains saved if cleanup fails; those templates cannot match
  // and the next boot or enrollment will retry deleting only reserved slots.
  (void)cleanup_pending_locked();
done:
  enrollment_connected = NULL;
  prompted_authorization_active = false;
  fp_give();
  return ok;
}

bool fingerprint_delete_finger(unsigned finger) {
  if (!finger || finger > FINGER_PROFILE_COUNT || !fp_take(1000)) return false;
  bool ok = false;
  if (profiles_ready && cleanup_pending_locked()) {
    fingerprint_inventory_t inventory;
    if (inventory_locked(&inventory) && finger_profiles_block_fits(finger, inventory.capacity)) {
      finger_profiles_t next = profiles;
      next.pending = finger_profiles_block(finger);
      ok = save_profiles_locked(&next) && cleanup_pending_locked();
    }
  }
  fp_give();
  return ok;
}

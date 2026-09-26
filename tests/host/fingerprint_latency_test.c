#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <string.h>

typedef uint32_t TickType_t;
#define pdMS_TO_TICKS(ms) ((ms) / 10)
#define portMAX_DELAY UINT32_MAX
#define ESP_LOGW(...) ((void)0)
static const int FP_UART = 1;
static const uint8_t FP_LED_FUNC_STEADY = 3, FP_LED_GREEN = 2, FP_LED_RED = 4, FP_LED_BLUE = 1;
static TickType_t ticks;
static uint8_t incoming[128];
static size_t incoming_len, incoming_pos, fragment_limit = 128;
static bool enabled, ready, mutex_available = true, mutex_held, idle_led = true;
static int writes, aura_count, notifications;
static uint8_t last_aura;
static void *result_led_task = (void *)1;
static bool result_led_pending, result_led_visible;
static uint8_t result_led_color;
static TickType_t result_led_until;

static TickType_t xTaskGetTickCount(void) { return ticks; }
static void note_transport_success(void) { ready = true; }
static void note_transport_failure(void) { ready = false; }
static bool device_config_idle_led(void) { return idle_led; }
static bool fp_take(uint32_t timeout) {
  (void)timeout;
  if (!mutex_available) return false;
  assert(!mutex_held); mutex_held = true; return true;
}
static void fp_give(void) { assert(mutex_held); mutex_held = false; }
static void xTaskNotifyGive(void *task) { assert(task == result_led_task); notifications++; }
static int uart_write_bytes(int uart, const void *data, size_t length) {
  assert(uart == FP_UART);
  const uint8_t *bytes = data;
  writes++;
  if (writes % 3 == 2 && bytes[0] == 0x3c) { aura_count++; last_aura = bytes[2]; }
  if (writes % 3 == 0) { enabled = true; incoming_pos = 0; }
  return (int)length;
}
static int uart_read_bytes(int uart, void *out, size_t length, TickType_t wait) {
  assert(uart == FP_UART);
  size_t count = enabled ? incoming_len - incoming_pos : 0;
  if (count > length) count = length;
  if (count > fragment_limit) count = fragment_limit;
  if (count) memcpy(out, incoming + incoming_pos, count);
  incoming_pos += count;
  // Model IDF's fill-or-timeout behavior. An available short packet should
  // never pay this wait just because the application's buffer is larger.
  if (count < length) ticks += wait;
  return (int)count;
}

/* uart */
/* led */
/* schedule */

typedef struct { uint16_t slot, score; } fingerprint_match_t;
static fingerprint_match_t expected_match = {1, 99};
static fingerprint_match_t fingerprint_match_captured(bool quiet) {
  assert(quiet && mutex_held); return expected_match;
}
/* matcher */

static void reset(void) {
  ticks = 0; incoming_len = incoming_pos = 0; fragment_limit = 128;
  enabled = ready = mutex_held = false; mutex_available = idle_led = true;
  writes = aura_count = notifications = 0; result_led_pending = result_led_visible = false;
}
static void packet(uint8_t id, const uint8_t *payload, size_t size) {
  uint8_t header[] = {0xef, 0x01, 0xff, 0xff, 0xff, 0xff, id, 0, (uint8_t)(size + 2)};
  uint16_t sum = fp_checksum(id, payload, size);
  memcpy(incoming + incoming_len, header, sizeof(header)); incoming_len += sizeof(header);
  memcpy(incoming + incoming_len, payload, size); incoming_len += size;
  incoming[incoming_len++] = sum >> 8; incoming[incoming_len++] = sum & 255;
}
static void ack(void) { const uint8_t ok[] = {0}; packet(7, ok, sizeof(ok)); }

int main(void) {
  uint8_t confirm, data[4]; size_t size;
  reset(); ack();
  assert(fp_command(1, NULL, 0, &confirm, NULL, NULL, 350) && confirm == 0);
  assert(ticks == 0 && ready);

  // Both combined ACK+data and byte-fragmented packets retain all payload.
  for (size_t fragment = 1; fragment <= 128; fragment *= 128) {
    reset(); fragment_limit = fragment; ack();
    const uint8_t payload[] = {1, 2, 3, 4}; packet(2, payload, sizeof(payload)); size = sizeof(data);
    assert(fp_command(4, NULL, 0, &confirm, data, &size, 350));
    assert(size == 4 && memcmp(data, payload, 4) == 0 && ticks == 0);
  }
  reset(); const uint8_t combined[] = {0, 4, 3, 2, 1}; packet(7, combined, sizeof(combined)); size = 4;
  assert(fp_command(4, NULL, 0, &confirm, data, &size, 350) && data[0] == 4 && ticks == 0);

  reset(); ack(); incoming[incoming_len - 1] ^= 1;
  assert(!fp_command(1, NULL, 0, &confirm, NULL, NULL, 350) && !ready);
  reset(); ack(); incoming[7] = 1;
  assert(!fp_command(1, NULL, 0, &confirm, NULL, NULL, 350));
  reset();
  assert(!fp_command(1, NULL, 0, &confirm, NULL, NULL, 350) && ticks == 35);
  reset(); ack(); size = 4;
  assert(fp_command(4, NULL, 0, &confirm, data, &size, 350) && size == 0 && ticks <= 14);

  // Capture returns before any LED UART command or result dwell.
  reset(); ack();
  fingerprint_match_t match = fingerprint_authorize_poll_match();
  assert(match.slot == 1 && aura_count == 0 && ticks == 0 && notifications == 1);
  assert(service_result_led() == 35 && last_aura == FP_LED_GREEN);
  ticks = 34; assert(service_result_led() == 1 && aura_count == 1);
  ticks = 35; assert(service_result_led() == portMAX_DELAY && last_aura == FP_LED_BLUE);

  reset(); ack(); idle_led = false; schedule_result_led(false);
  assert(service_result_led() == 35 && last_aura == FP_LED_RED);
  ticks = 35; assert(service_result_led() == portMAX_DELAY && last_aura == 0);

  // Foreground LED changes cancel queued and already-visible result feedback.
  reset(); ack(); schedule_result_led(true); set_aura(FP_LED_BLUE);
  assert(service_result_led() == portMAX_DELAY && aura_count == 1);
  schedule_result_led(true); assert(service_result_led() == 35);
  set_aura(FP_LED_BLUE); ticks = 40;
  assert(service_result_led() == portMAX_DELAY && aura_count == 3);

  reset(); ack(); schedule_result_led(true); mutex_available = false;
  assert(service_result_led() == 1 && aura_count == 0);
  mutex_available = true; set_aura(FP_LED_BLUE);
  assert(service_result_led() == portMAX_DELAY && aura_count == 1);

  reset(); ack(); ticks = UINT32_MAX - 10; schedule_result_led(true);
  assert(service_result_led() == 35); ticks += 35;
  assert(service_result_led() == portMAX_DELAY && last_aura == FP_LED_BLUE);
  return 0;
}

// Compile the production implementation with a simulated UART sensor.
#include <stdio.h>
#include "../../firmware/tiny_touch_unified/main/fingerprint.c"

static TickType_t now;
static bool busy, timeout, corrupt;
static uint8_t image_status, conversion_status;
static uint16_t search_slot, search_score;
static unsigned commands[256], write_phase, releases;
static uint8_t response[32];
static size_t response_len;

SemaphoreHandle_t xSemaphoreCreateMutex(void) { return (void *)1; }
int xSemaphoreTake(SemaphoreHandle_t mutex, TickType_t ticks) {
  (void)mutex;
  assert(ticks == 0);
  return !busy;
}
int xSemaphoreGive(SemaphoreHandle_t mutex) {
  (void)mutex;
  releases++;
  return 1;
}
TickType_t xTaskGetTickCount(void) { return now; }
void vTaskDelay(TickType_t ticks) { now += ticks; }

int uart_write_bytes(uart_port_t port, const void *data, size_t len) {
  (void)port;
  const uint8_t *bytes = data;
  if (write_phase++ % 3 != 1) return (int)len;
  uint8_t instruction = bytes[0];
  commands[instruction]++;
  if (timeout) return (int)len;
  uint8_t status = instruction == 0x01 ? image_status :
                   instruction == 0x02 ? conversion_status : 0;
  uint8_t payload[] = {status, search_slot >> 8, search_slot & 255,
                      search_score >> 8, search_score & 255};
  size_t payload_len = instruction == 0x04 ? 5 : 1;
  uint8_t header[] = {0xef, 1, 255, 255, 255, 255, 7, 0,
                     (uint8_t)(payload_len + 2)};
  memcpy(response, header, sizeof(header));
  memcpy(response + 9, payload, payload_len);
  uint16_t sum = fp_checksum(7, payload, payload_len);
  response[9 + payload_len] = sum >> 8;
  response[10 + payload_len] = (sum & 255) ^ corrupt;
  response_len = 11 + payload_len;
  return (int)len;
}
int uart_read_bytes(uart_port_t port, void *data, size_t len, TickType_t ticks) {
  (void)port;
  now += ticks;
  if (!response_len) return 0;
  assert(len >= response_len);
  memcpy(data, response, response_len);
  int result = (int)response_len;
  response_len = 0;
  return result;
}
int uart_driver_install(int p, int r, int t, int s, void *q, int f) { return 0; }
int uart_param_config(int p, const uart_config_t *c) { return 0; }
int uart_set_pin(int p, int t, int r, int rt, int ct) { return 0; }
int uart_flush_input(int p) { return 0; }
int uart_set_baudrate(int p, uint32_t b) { return 0; }

static void reset(void) {
  now = 0;
  busy = timeout = corrupt = false;
  image_status = conversion_status = 0;
  search_slot = 2;
  search_score = 100;
  memset(commands, 0, sizeof(commands));
  write_phase = releases = response_len = 0;
  fp_mutex = (void *)1;
  set_sensor_ready(true);
}

int main(void) {
  reset();
  image_status = 2;
  fingerprint_poll_t poll = fingerprint_poll(true);
  assert(poll.presence == FINGERPRINT_POLL_ABSENT && !poll.match.slot);
  assert(commands[1] == 1 && !commands[2] && !commands[0x3c]);
  assert(releases == 1);

  // Lift checks must never convert, match, or change the result light.
  reset();
  poll = fingerprint_poll(false);
  assert(poll.presence == FINGERPRINT_POLL_PRESENT && !poll.match.slot);
  assert(commands[1] == 1 && !commands[2] && !commands[4] && !commands[0x3c]);

  reset();
  poll = fingerprint_poll(true);
  assert(poll.presence == FINGERPRINT_POLL_PRESENT);
  assert(poll.match.slot == 2 && poll.match.score == 100);
  assert(commands[2] == 1 && commands[4] == 1 && commands[0x3c] == 1);

  reset();
  conversion_status = 6;
  poll = fingerprint_poll(true);
  assert(poll.presence == FINGERPRINT_POLL_PRESENT && !poll.match.slot);
  assert(!commands[4] && !commands[0x3c]);

  // Every sensor error is unknown, not evidence that a finger lifted.
  for (unsigned status = 1; status <= 255; status++) {
    if (status == 2) continue;
    reset();
    image_status = status;
    poll = fingerprint_poll(true);
    assert(poll.presence == FINGERPRINT_POLL_UNKNOWN && !poll.match.slot);
    assert(!commands[2] && !commands[0x3c]);
  }

  reset();
  busy = true;
  poll = fingerprint_poll(false);
  assert(poll.presence == FINGERPRINT_POLL_UNKNOWN && !poll.match.slot);
  assert(!commands[1] && !releases && now == 0);

  reset();
  timeout = true;
  poll = fingerprint_poll(false);
  assert(poll.presence == FINGERPRINT_POLL_UNKNOWN && !poll.match.slot);
  assert(now == 350 && !fingerprint_is_ready() && releases == 1);

  reset();
  corrupt = true;
  image_status = 2;
  poll = fingerprint_poll(false);
  assert(poll.presence == FINGERPRINT_POLL_UNKNOWN && !fingerprint_is_ready());

  reset();
  fingerprint_match_t match = fingerprint_authorize_poll_match();
  assert(match.slot == 2 && match.score == 100);
  puts("Polling behavior checks passed");
  return 0;
}

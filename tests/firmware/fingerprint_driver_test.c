#include "../../firmware/tiny_touch_unified/main/fingerprint.h"
#include "../../firmware/tiny_touch_unified/main/fingerprint_led.h"
#include "stubs/test_esp.h"
#include <string.h>

static TickType_t now;
static TickType_t busy_until;
static bool locked;
static uint8_t tx[64], rx[32];
static size_t tx_size, rx_size;
static uint8_t led_confirmation;
static uint8_t light[4];
static unsigned led_commands;
static void (*led_task)(void *);

void test_log(const char *tag, const char *format, ...) { (void)tag; (void)format; }
TickType_t xTaskGetTickCount(void) { return now; }
void vTaskDelay(TickType_t ticks) { now += ticks; }
BaseType_t xTaskCreate(void (*task)(void *), const char *name, uint32_t stack,
                      void *arg, unsigned priority, void *handle) {
  (void)name; (void)stack; (void)arg; (void)priority; (void)handle;
  led_task = task;
  return pdPASS;
}
SemaphoreHandle_t xSemaphoreCreateMutex(void) { return &locked; }
BaseType_t xSemaphoreTake(SemaphoreHandle_t mutex, TickType_t timeout) {
  assert(!*mutex);
  if (now < busy_until) {
    if (timeout < busy_until - now) return 0;
    now = busy_until;
  }
  *mutex = true;
  return pdTRUE;
}
void xSemaphoreGive(SemaphoreHandle_t mutex) { assert(*mutex); *mutex = false; }
int gpio_config(const gpio_config_t *config) { (void)config; return 0; }
int gpio_get_level(int pin) { (void)pin; return 1; }
int uart_driver_install(uart_port_t port, int r, int t, int n, void *q, int f) {
  (void)port; (void)r; (void)t; (void)n; (void)q; (void)f; return 0;
}
int uart_param_config(uart_port_t port, const uart_config_t *config) {
  (void)port; (void)config; return 0;
}
int uart_set_pin(uart_port_t port, int tx_pin, int rx_pin, int rts, int cts) {
  (void)port; (void)tx_pin; (void)rx_pin; (void)rts; (void)cts; return 0;
}
int uart_flush_input(uart_port_t port) { (void)port; rx_size = 0; return 0; }
int uart_set_baudrate(uart_port_t port, uint32_t rate) { (void)port; (void)rate; return 0; }

int uart_write_bytes(uart_port_t port, const void *data, size_t size) {
  (void)port;
  assert(locked);
  assert(tx_size + size <= sizeof(tx));
  memcpy(tx + tx_size, data, size);
  tx_size += size;
  if (tx_size < 9) return (int)size;
  size_t expected = 9 + ((size_t)tx[7] << 8) + tx[8];
  if (tx_size < expected) return (int)size;
  assert(tx_size == expected);
  uint16_t sum = 0;
  for (size_t i = 6; i < tx_size - 2; i++) sum += tx[i];
  assert(tx[tx_size - 2] == (sum >> 8) && tx[tx_size - 1] == (sum & 255));

  uint8_t confirmation = 0;
  if (tx[9] == 0x3c) {
    assert(tx_size == 16);
    memcpy(light, tx + 10, 4);
    led_commands++;
    confirmation = led_confirmation;
  }
  const uint8_t header[] = {0xef, 1, 255, 255, 255, 255, 7, 0, 3};
  memcpy(rx, header, sizeof(header));
  rx[9] = confirmation;
  rx_size = 10;
  if (tx[9] == 0x04) {
    const uint8_t match[] = {0, 1, 0, 64};
    memcpy(rx + rx_size, match, sizeof(match));
    rx_size += sizeof(match);
    rx[8] += sizeof(match);
  }
  sum = 0;
  for (size_t i = 6; i < rx_size; i++) sum += rx[i];
  rx[rx_size++] = sum >> 8;
  rx[rx_size++] = sum & 255;
  tx_size = 0;
  return (int)size;
}

int uart_read_bytes(uart_port_t port, void *data, uint32_t size, TickType_t timeout) {
  (void)port;
  assert(locked);
  if (rx_size == 0) { now += timeout; return 0; }
  size_t copied = rx_size < size ? rx_size : size;
  memcpy(data, rx, copied);
  memmove(rx, rx + copied, rx_size - copied);
  rx_size -= copied;
  return (int)copied;
}

int main(int argc, char **argv) {
  assert(argc == 2);
  fingerprint_init();
  assert(led_task != NULL && fingerprint_is_ready());
  if (strcmp(argv[1], "touch_during_led_command") == 0) {
    busy_until = now + 120;
    fingerprint_match_t match = fingerprint_authorize_poll_match();
    assert(match.slot == 1 && match.score == 64);
    assert(now >= busy_until);
    assert(light[0] == 3 && light[1] == 2);
  } else if (strcmp(argv[1], "rejected_restore") == 0) {
    assert(fingerprint_authorize_poll_match().slot == 1);
    led_confirmation = 1;
    fingerprint_led_idle();
    unsigned before_retry = led_commands;
    led_confirmation = 0;
    now += 100;
    assert(xSemaphoreTake(&locked, 0) == pdTRUE);
    fingerprint_led_service(true, now);
    xSemaphoreGive(&locked);
    assert(led_commands == before_retry + 1);
    assert(light[0] == 3 && light[1] == 1 && light[2] == 1);
  } else {
    return 2;
  }
  return 0;
}

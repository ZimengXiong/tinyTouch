#include "led_stubs.h"
#include "../../firmware/tiny_touch_unified/main/device_config.c"
#include "../../firmware/tiny_touch_unified/main/finger_profiles.c"
#include "../../firmware/tiny_touch_unified/main/fingerprint.c"

static TickType_t clock_ticks;
static int mutexes[32], mutex_count;
static stored_config_t disk_config;
static bool have_config, have_led, fail_save;
static uint8_t disk_led, pending_led;
static finger_profiles_t disk_profiles, staged_profiles;
static bool have_profiles, stage_profiles;
static uint64_t sensor_templates;
static unsigned sensor_capacity = 40, search_slot = 1;
static int fail_delete = -1, fail_store = -1;
static bool wrong_count;
static uint8_t request[64], response[96], last_led[4];
static size_t request_len, response_len;
static int led_commands, reject_led;
static bool reject_capture, no_match;

TickType_t xTaskGetTickCount(void) { return clock_ticks++; }
void vTaskDelay(TickType_t ticks) { clock_ticks += ticks; }
SemaphoreHandle_t xSemaphoreCreateMutex(void) { return &mutexes[mutex_count++]; }
int xSemaphoreTake(SemaphoreHandle_t mutex, TickType_t ticks) {
  (void)ticks; if (*mutex) return 0; *mutex = 1; return pdTRUE;
}
int xSemaphoreGive(SemaphoreHandle_t mutex) { assert(*mutex); *mutex = 0; return pdTRUE; }
int nvs_open(const char *name, int mode, nvs_handle_t *handle) {
  assert(strcmp(name, "tt6") == 0); (void)mode; *handle = 1; return ESP_OK;
}
int nvs_get_blob(nvs_handle_t handle, const char *key, void *data, size_t *length) {
  (void)handle;
  if (strcmp(key, "fingers") == 0) {
    if (!have_profiles) return ESP_ERR_NVS_NOT_FOUND;
    assert(*length == sizeof(disk_profiles)); memcpy(data, &disk_profiles, *length); return ESP_OK;
  }
  assert(strcmp(key, "config") == 0);
  if (!have_config) return -1;
  assert(*length == sizeof(disk_config)); memcpy(data, &disk_config, *length); return ESP_OK;
}
int nvs_set_blob(nvs_handle_t handle, const char *key, const void *data, size_t length) {
  (void)handle;
  if (strcmp(key, "fingers") == 0) {
    if (fail_save) return -2;
    assert(length == sizeof(staged_profiles)); memcpy(&staged_profiles, data, length);
    stage_profiles = true; return ESP_OK;
  }
  assert(strcmp(key, "config") == 0); assert(length == sizeof(disk_config));
  if (fail_save) return -1;
  memcpy(&disk_config, data, length); have_config = true; return ESP_OK;
}
int nvs_get_u8(nvs_handle_t handle, const char *key, uint8_t *value) {
  (void)handle; assert(strcmp(key, "led_enabled") == 0);
  if (!have_led) return -1; *value = disk_led; return ESP_OK;
}
int nvs_set_u8(nvs_handle_t handle, const char *key, uint8_t value) {
  (void)handle; assert(strcmp(key, "led_enabled") == 0); pending_led = value; return ESP_OK;
}
int nvs_commit(nvs_handle_t handle) {
  (void)handle; if (fail_save) return -1;
  if (stage_profiles) { disk_profiles = staged_profiles; have_profiles = true; stage_profiles = false; }
  disk_led = pending_led; have_led = true; return ESP_OK;
}
void nvs_close(nvs_handle_t handle) { (void)handle; }
int mbedtls_sha256(const unsigned char *data, size_t length, unsigned char output[32], int is224) {
  (void)is224; assert(length == 32); memcpy(output, data, 32); return ESP_OK;
}
int uart_write_bytes(uart_port_t port, const void *data, size_t size) {
  (void)port; memcpy(request + request_len, data, size); request_len += size;
  if (request_len < 9 || request_len < (size_t)(9 + request[8])) return (int)size;
  uint8_t instruction = request[9], confirm = 0, payload[32] = {0};
  size_t extra = 0;
  if (instruction == 0x3c) {
    memcpy(last_led, request + 10, 4); led_commands++;
    if (reject_led > 0) { reject_led--; confirm = 1; }
  } else if (instruction == 0x01 && reject_capture) confirm = 2;
  else if (instruction == 0x04) {
    extra = 4; payload[1] = search_slot; payload[3] = no_match ? 0 : 90;
  } else if (instruction == 0x0f) {
    extra = 16; payload[5] = sensor_capacity;
  } else if (instruction == 0x1f) {
    extra = 32;
    for (unsigned slot = 0; slot < 40; slot++) {
      if (sensor_templates & (UINT64_C(1) << slot)) payload[slot / 8] |= 1u << (slot % 8);
    }
  } else if (instruction == 0x1d) {
    extra = 2; payload[1] = finger_profiles_count(sensor_templates) + wrong_count;
  } else if (instruction == 0x06) {
    unsigned slot = request[12];
    assert(slot < sensor_capacity);
    if ((int)slot == fail_store) confirm = 1;
    else sensor_templates |= UINT64_C(1) << slot;
  } else if (instruction == 0x0c) {
    unsigned slot = request[11];
    if ((int)slot == fail_delete) confirm = 1;
    else sensor_templates &= ~(UINT64_C(1) << slot);
  } else if (instruction == 0x0d) sensor_templates = 0;
  uint8_t ack[] = {0xef, 1, 255, 255, 255, 255, 7, 0, (uint8_t)(3 + extra), confirm};
  response_len = 12 + extra;
  memcpy(response, ack, sizeof(ack));
  memcpy(response + 10, payload, extra);
  uint16_t sum = fp_checksum(7, response + 9, 1 + extra);
  response[response_len - 2] = sum >> 8; response[response_len - 1] = sum & 255;
  request_len = 0; return (int)size;
}
int uart_read_bytes(uart_port_t port, void *data, size_t size, TickType_t ticks) {
  (void)port; (void)ticks; size_t n = response_len < size ? response_len : size;
  memcpy(data, response, n); memmove(response, response + n, response_len - n);
  response_len -= n; return (int)n;
}
int uart_driver_install(int port, int rx, int tx, int qs, void *queue, int flags) {
  (void)port; (void)rx; (void)tx; (void)qs; (void)queue; (void)flags; return ESP_OK;
}
int uart_param_config(int port, const uart_config_t *cfg) { (void)port; (void)cfg; return ESP_OK; }
int uart_set_pin(int port, int tx, int rx, int rts, int cts) {
  (void)port; (void)tx; (void)rx; (void)rts; (void)cts; return ESP_OK;
}
int uart_flush_input(int port) { (void)port; response_len = 0; return ESP_OK; }
int uart_set_baudrate(int port, int rate) { (void)port; (void)rate; return ESP_OK; }
int gpio_config(const gpio_config_t *cfg) { (void)cfg; return ESP_OK; }
int gpio_get_level(int pin) { (void)pin; return 1; }
static void expect_led(uint8_t color) {
  assert(last_led[0] == 3 && last_led[1] == color && last_led[2] == color && last_led[3] == 0);
}

int main(void) {
  // A schema-6 device with a paired host upgrades without rewriting its blob.
  defaults(&disk_config); disk_config.mode = DEVICE_MODE_HID;
  disk_config.hid_host_count = 1;
  memset(disk_config.hid_hosts[0].key, 42, 32);
  memset(disk_config.hid_hosts[0].id, 42, 8);
  disk_config.typing_delay_ms = 23; have_config = true;
  stored_config_t before = disk_config;
  device_config_init(); assert(device_config_led_enabled());
  fingerprint_init(); expect_led(FP_LED_BLUE);
  assert(fingerprint_set_led_enabled(false)); expect_led(0);
  assert(memcmp(&before, &disk_config, sizeof(before)) == 0);
  assert(device_config_mode() == DEVICE_MODE_HID && device_config_typing_delay_ms() == 23);
  // Reboot loads the saved preference before the sensor's first LED command.
  device_config_init(); fingerprint_init(); expect_led(0);
  assert(!device_config_led_enabled());
  assert(fingerprint_authorize_poll_match().slot == 1); expect_led(0);
  no_match = true; assert(fingerprint_authorize_poll_match().slot == 0); expect_led(0);
  no_match = false; reject_capture = true;
  assert(fingerprint_authorize_poll_match().slot == 0); expect_led(0); reject_capture = false;
  show_result(true); expect_led(0); show_result(false); expect_led(0);
  assert(fingerprint_recover()); expect_led(0);
  // A failed commit must not change the live preference or claim success.
  fail_save = true; int previous = led_commands;
  assert(!fingerprint_set_led_enabled(true)); assert(!device_config_led_enabled());
  assert(led_commands == previous); fail_save = false;
  // Transient sensor rejection retries; persistent rejection is an error.
  reject_led = 2; assert(fingerprint_set_led_enabled(true)); expect_led(FP_LED_BLUE);
  assert(led_commands == previous + 3);
  reject_led = 3; assert(!fingerprint_set_led_enabled(false)); assert(!device_config_led_enabled());
  assert(fingerprint_set_led_enabled(true));
  assert(fingerprint_authorize_poll_match().slot == 1); expect_led(FP_LED_GREEN);
  fingerprint_led_idle(); expect_led(FP_LED_BLUE);
  assert(device_config_factory_reset()); assert(device_config_led_enabled());
  assert(device_config_mode() == DEVICE_MODE_PIV && device_config_hid_host_count() == 0);
  return 0;
}

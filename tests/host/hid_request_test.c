#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define DEVICE_CONFIG_MAX_HID_HOSTS 8
#define DEVICE_CONFIG_HID_KEY_ID_SIZE 8
#define pdTRUE 1
#define pdMS_TO_TICKS(ms) ((ms) / 10)
typedef struct { uint8_t id[8], key[32]; } device_hid_host_t;
typedef struct { uint16_t slot, score; } fingerprint_match_t;
static uint32_t event_counter, session;
static void *password_responses = (void *)1;
static size_t host_count = 1;
static int receives, events, typed, cancel_on_receive;
static bool v2_reply;

static uint32_t hid_session_id(void) { return session; }
static size_t device_config_copy_hid_hosts(device_hid_host_t *hosts) {
  memset(hosts, 0, host_count * sizeof(*hosts)); return host_count;
}
static void esp_fill_random(void *out, size_t length) { memset(out, 1, length); }
static void bytes_to_hex(const uint8_t *data, size_t length, char *out) {
  (void)data; memset(out, '1', length * 2); out[length * 2] = 0;
}
static bool hmac_sha256(const uint8_t *key, const char *material, uint8_t *out) {
  (void)key; (void)material; memset(out, 1, 32); return true;
}
static void xQueueReset(void *queue) { assert(queue == password_responses); }
static int xQueueReceive(void *queue, void *out, uint32_t ticks) {
  assert(queue == password_responses && ticks > 0); receives++;
  if (receives == cancel_on_receive) session++;
  strcpy(out, "authenticated fixture response");
  return pdTRUE;
}
static bool decrypt_password(const uint8_t *key, const char *nonce, char *response,
                             uint8_t *password, size_t *length) {
  (void)key; (void)nonce; (void)response; password[0] = 'a'; *length = 1; return true;
}
static bool decrypt_password_v2(const char *nonce, char *response,
                                const device_hid_host_t *hosts, size_t count,
                                uint8_t *password, size_t *length) {
  (void)hosts; (void)count;
  return v2_reply && decrypt_password(NULL, nonce, response, password, length);
}
static bool type_ascii_in_session(const uint8_t *password, size_t length, uint32_t expected) {
  assert(password[0] == 'a' && length == 1 && expected == session);
  typed++; return true;
}
static void touch_pin_hid_log_event(const char *event, int value) { (void)event; (void)value; }
static void config_console_send_line(const char *line) {
  assert(strncmp(line, "EV", 2) == 0); events++;
}
static void secure_wipe(void *out, size_t length) { memset(out, 0, length); }

/* request */

static void reset(size_t hosts) {
  host_count = hosts; receives = events = typed = cancel_on_receive = 0;
  v2_reply = false;
}

int main(void) {
  fingerprint_match_t match = {1, 99};
  reset(1);
  assert(request_and_type_password(match) && typed == 1 && events == 1);
  reset(1); cancel_on_receive = 1;
  assert(!request_and_type_password(match) && typed == 0 && events == 1);
  reset(2); v2_reply = true;
  assert(request_and_type_password(match) && typed == 1 && events == 1);
  reset(2); cancel_on_receive = 1;
  assert(!request_and_type_password(match) && typed == 0 && events == 1);
  reset(2); // Existing legacy fallback still works on the same connection.
  assert(request_and_type_password(match) && typed == 1 && events == 2);
  reset(2); cancel_on_receive = 2;
  assert(!request_and_type_password(match) && typed == 0 && events == 2);
  return 0;
}

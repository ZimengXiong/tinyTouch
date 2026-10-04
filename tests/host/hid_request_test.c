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
typedef uint32_t TickType_t;
#define taskENTER_CRITICAL(lock) ((void)(lock))
#define taskEXIT_CRITICAL(lock) ((void)(lock))
static int hid_transfer_lock;
static uint32_t event_counter, hid_session;
static char password_request_nonce[33];
static TickType_t ticks;
static void *password_responses = (void *)1;
static size_t host_count = 1;
static int receives, events, typed, cancel_on_receive;
static bool v2_reply, late_v2_reply, queue_full;
static int invalid_replies;

static uint32_t hid_session_id(void) { return hid_session; }
static TickType_t xTaskGetTickCount(void) { return ticks; }
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
static void xQueueReset(void *queue) { assert(queue == password_responses); queue_full = false; }
static int xQueueSend(void *queue, const void *item, TickType_t timeout) {
  assert(queue == password_responses && timeout == 0);
  assert(strncmp(item, "PW", 2) == 0);
  if (queue_full) return 0;
  queue_full = true; return pdTRUE;
}
static int xQueueReceive(void *queue, void *out, uint32_t timeout) {
  assert(queue == password_responses && timeout > 0); receives++;
  if (receives == cancel_on_receive) hid_session++;
  if (host_count > 1 && events == 1 && !v2_reply) {
    // Model the timeout before the existing legacy event is emitted.
    // Use the fixture clock rather than wall time.
    ticks += timeout; return 0;
  }
  const char *kind = host_count > 1 && (v2_reply || (events == 2 && late_v2_reply)) ? "PW2" : "PW";
  snprintf(out, 640, "%s %s fixture", kind, invalid_replies > 0 ? "invalid" : "valid");
  if (invalid_replies > 0) invalid_replies--;
  ticks++;
  return pdTRUE;
}
static bool decrypt_password(const uint8_t *key, const char *nonce, char *response,
                             uint8_t *password, size_t *length) {
  (void)key; (void)nonce;
  assert(*length == 160);
  if (strstr(response, "invalid")) { *length = 0; return false; }
  password[0] = 'a'; *length = 1; return true;
}
static bool decrypt_password_v2(const char *nonce, char *response,
                                const device_hid_host_t *hosts, size_t count,
                                uint8_t *password, size_t *length) {
  (void)hosts; (void)count;
  return decrypt_password(NULL, nonce, response, password, length);
}
static bool type_ascii_in_session(const uint8_t *password, size_t length, uint32_t expected) {
  assert(password[0] == 'a' && length == 1 && expected == hid_session);
  typed++; return true;
}
static void touch_pin_hid_log_event(const char *event, int value) { (void)event; (void)value; }
static void config_console_send_line(const char *line) {
  assert(strncmp(line, "EV", 2) == 0); events++;
}
static void secure_wipe(void *out, size_t length) { memset(out, 0, length); }

/* request */
/* submit */

static void reset(size_t hosts) {
  host_count = hosts; receives = events = typed = cancel_on_receive = 0;
  v2_reply = late_v2_reply = queue_full = false; ticks = 0; invalid_replies = 0;
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

  // Invalid authenticated framing does not consume a later valid response.
  reset(1); invalid_replies = 1;
  assert(request_and_type_password(match) && typed == 1 && receives == 2);
  reset(1); invalid_replies = 10000;
  assert(!request_and_type_password(match) && typed == 0 && receives == 600);
  reset(2); late_v2_reply = true;
  assert(request_and_type_password(match) && typed == 1 && events == 2);

  // Only replies for the outstanding nonce may occupy the single-item queue.
  const char nonce[] = "11111111111111111111111111111111";
  reset(1); assert(begin_password_request(nonce, hid_session));
  assert(!touch_pin_hid_submit_response("PW 22222222222222222222222222222222 iv ct mac"));
  assert(!touch_pin_hid_submit_response("PW short iv ct mac"));
  assert(!touch_pin_hid_submit_response("PW2 short 11111111111111111111111111111111 iv ct mac"));
  assert(!queue_full);
  assert(touch_pin_hid_submit_response("PW 11111111111111111111111111111111 iv ct mac"));
  assert(!touch_pin_hid_submit_response("PW 11111111111111111111111111111111 iv ct mac"));
  xQueueReset(password_responses);
  assert(touch_pin_hid_submit_response("PW2 0000000000000000 11111111111111111111111111111111 iv ct mac"));
  end_password_request(); xQueueReset(password_responses);
  assert(!touch_pin_hid_submit_response("PW 11111111111111111111111111111111 iv ct mac"));
  return 0;
}

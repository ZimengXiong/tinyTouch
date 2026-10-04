#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <limits.h>

typedef uint32_t TickType_t;
typedef void *SemaphoreHandle_t;
typedef void *esp_timer_handle_t;
typedef int portMUX_TYPE;
typedef int hid_report_type_t;
typedef struct { uint8_t bytes[8]; } hid_keyboard_report_t;
#define HID_REPORT_TYPE_INPUT 1
#define HID_REPORT_TYPE_OUTPUT 2
#define portMUX_INITIALIZER_UNLOCKED 0
#define taskENTER_CRITICAL(lock) ((void)(lock))
#define taskEXIT_CRITICAL(lock) ((void)(lock))
#define pdMS_TO_TICKS(ms) ((ms) / 10)
#define ESP_OK 0
#define KEYBOARD_MODIFIER_LEFTSHIFT 2
#define HID_KEY_ENTER 40

/* globals */

static const uint8_t ascii_to_keycode[128][2] = {
  ['a'] = {0, 4}, ['A'] = {1, 4}, ['b'] = {0, 5}, ['1'] = {0, 30},
};
static int64_t now, usb_at = -1, timer_at = -1, detach_at = -1;
static int64_t usb_delay = 1000;
static bool connected = true, token, submit_enter = true;
static int delay_ms = 1, reports, completed, fail_report, reject_report;
static int reconnect_after_reports;
static void *password_responses;
static volatile bool usb_sensor_probe_pending;
static volatile TickType_t usb_sensor_probe_at;
static int cancelled_requests;
static uint8_t keys_sent[64], modifiers_sent[64];
static int64_t sent_at[64];

static void hid_wake(void *arg);
void touch_pin_hid_usb_detached(void);
void touch_pin_hid_usb_attached(void);
void tud_hid_report_complete_cb(uint8_t instance, const uint8_t *report, uint16_t len);
void tud_hid_report_failed_cb(uint8_t instance, hid_report_type_t type,
                             const uint8_t *report, uint16_t len);
static TickType_t xTaskGetTickCount(void) { return (TickType_t)(now / 10000); }
static int64_t esp_timer_get_time(void) { return now; }
static int device_config_typing_delay_ms(void) { return delay_ms; }
static bool device_config_submit_enter(void) { return submit_enter; }
static bool tud_hid_ready(void) {
  if (reconnect_after_reports && reports == reconnect_after_reports) {
    reconnect_after_reports = 0;
    touch_pin_hid_usb_attached();
  }
  return connected && usb_at < 0;
}
static void touch_pin_hid_log_event(const char *event, int value) { (void)event; (void)value; }
static void xQueueOverwrite(void *queue, const void *item) {
  assert(queue == password_responses && ((const char *)item)[0] == 0);
  cancelled_requests++;
}
static void xSemaphoreGive(SemaphoreHandle_t sem) { (void)sem; token = true; }
static bool xSemaphoreTake(SemaphoreHandle_t sem, TickType_t ticks) {
  (void)sem;
  int64_t deadline = now + ticks * 10000LL;
  while (!token) {
    int64_t next = INT64_MAX;
    if (usb_at >= 0 && usb_at < next) next = usb_at;
    if (timer_at >= 0 && timer_at < next) next = timer_at;
    if (detach_at >= 0 && detach_at < next) next = detach_at;
    if (next > deadline) { now = deadline; return false; }
    now = next;
    if (detach_at == now) {
      detach_at = usb_at = -1;
      connected = false;
      touch_pin_hid_usb_detached();
    }
    if (usb_at == now) {
      usb_at = -1;
      completed++;
      if (reports == fail_report) tud_hid_report_failed_cb(0, HID_REPORT_TYPE_INPUT, NULL, 0);
      else tud_hid_report_complete_cb(0, NULL, 8);
    }
    if (timer_at == now) { timer_at = -1; hid_wake(NULL); }
  }
  token = false;
  return true;
}
static int esp_timer_start_once(esp_timer_handle_t timer, int64_t delay) {
  (void)timer;
  assert(timer_at < 0 && delay > 0);
  timer_at = now + delay;
  return ESP_OK;
}
static int esp_timer_stop(esp_timer_handle_t timer) {
  (void)timer;
  timer_at = -1;
  return ESP_OK;
}
static bool tud_hid_keyboard_report(uint8_t id, uint8_t modifier, const uint8_t *keys) {
  assert(id == 0 && tud_hid_ready() && reports < 64);
  sent_at[reports] = now;
  keys_sent[reports] = keys ? keys[0] : 0;
  modifiers_sent[reports++] = modifier;
  if (reports == reject_report) return false;
  usb_at = now + usb_delay;
  return true;
}

/* implementation */
/* callbacks */

static void reset(void) {
  now = 0; usb_at = timer_at = detach_at = -1; usb_delay = 1000;
  connected = true; token = false; submit_enter = true; delay_ms = 1;
  reports = completed = fail_report = reject_report = 0;
  reconnect_after_reports = 0; password_responses = (void *)2;
  cancelled_requests = 0;
  memset(keys_sent, 0, sizeof(keys_sent));
  hid_signal = (void *)1;
  hid_set_transfer(HID_IDLE);
}

int main(void) {
  // Repeated keys, case changes, Enter and its final release all reach USB.
  const uint8_t password[] = {'a', 'a', 'A', 'b'};
  for (int delay = 1; delay <= 100; delay += delay == 1 ? 6 : 93) {
    reset(); delay_ms = delay; token = true; // a stale notification is harmless
    assert(type_ascii(password, sizeof(password)));
    assert(reports == 10 && completed == 10);
    assert(now == reports * delay * 1000LL);
    for (int i = 0; i < reports; i++) {
      assert(keys_sent[i] == ((i & 1) ? 0 : (i < 6 ? 4 : i == 6 ? 5 : HID_KEY_ENTER)));
      if (i) assert(sent_at[i] - sent_at[i - 1] >= delay * 1000);
    }
    assert(modifiers_sent[4] == KEYBOARD_MODIFIER_LEFTSHIFT);
    assert(modifiers_sent[5] == 0 && timer_at < 0);
  }
  reset(); submit_enter = false;
  assert(type_ascii(password, 1) && reports == 2 && completed == 2);

  // The documented 20-character transport model, including Enter and key-up.
  const uint8_t long_password[] = "aAb1aAb1aAb1aAb1aAb1";
  reset();
  assert(type_ascii(long_password, sizeof(long_password) - 1));
  assert(reports == 42 && completed == 42 && now == 42000);
  reset(); delay_ms = 7;
  assert(type_ascii(long_password, sizeof(long_password) - 1));
  assert(reports == 42 && completed == 42 && now == 294000);

  reset(); const uint8_t malformed[] = {'a', 0x80};
  assert(!type_ascii(malformed, sizeof(malformed)) && reports == 0);
  reset(); const uint8_t unsupported[] = {'a', 0};
  assert(!type_ascii(unsupported, sizeof(unsupported)) && reports == 0);

  // A failed press or release aborts the password and attempts only key-up.
  for (int failure = 1; failure <= 4; failure++) {
    reset(); fail_report = failure;
    assert(!type_ascii(password, sizeof(password)));
    assert(reports == failure + 1 && keys_sent[reports - 1] == 0);
    for (int i = 0; i < reports; i++) assert(keys_sent[i] != HID_KEY_ENTER);
  }
  reset(); reject_report = 1;
  assert(!type_ascii(password, sizeof(password)) && reports == 2 && keys_sent[1] == 0);

  reset(); detach_at = 500;
  assert(!type_ascii(password, sizeof(password)) && reports == 1 && now == 500);

  // A timed-out transfer cannot be mistaken for the next report's completion.
  reset(); usb_delay = 3000000;
  assert(!send_key(0, 4, hid_session_id()) && reports == 1 && hid_transfer_status() == HID_PENDING);
  assert(now == 2000000);
  usb_delay = 1000;
  assert(send_key(0, 5, hid_session_id()) && reports == 3 && completed == 3);
  assert(sent_at[1] == 3000000 && now == 3002000);

  // An unmount cancels a lost completion, allowing a later connection to type.
  reset(); usb_delay = 10000000;
  assert(!send_key(0, 4, hid_session_id()));
  touch_pin_hid_usb_detached(); usb_at = -1; usb_delay = 1000;
  assert(send_key(0, 5, hid_session_id()));

  // A remount between two characters cancels the old stream even when the
  // new endpoint is ready. A fresh touch can use the new connection.
  reset(); reconnect_after_reports = 2;
  assert(!type_ascii(password, sizeof(password)) && reports == 2);
  assert(cancelled_requests == 1);
  assert(type_ascii(password, 1) && reports == 6);
  reset(); reconnect_after_reports = 2;
  assert(!type_ascii(password, 1) && reports == 2); // No Enter on the new bus.

  // Attachment callbacks can precede queue and semaphore initialization.
  hid_signal = password_responses = NULL;
  touch_pin_hid_usb_attached();
  return 0;
}

#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <setjmp.h>
#include <string.h>

typedef uint32_t TickType_t;
typedef struct { uint16_t slot, score; } fingerprint_match_t;
#define pdMS_TO_TICKS(ms) ((TickType_t)(ms) / 10U)
static TickType_t ticks, stop_at;
static bool usb_sensor_probe_pending;
static TickType_t usb_sensor_probe_at;
static int touch_at, lift_at, second_touch_at, prompt_at, prompt_until;
static int retry_count, attempts, matches, begins, cancels;
static bool mismatch, sensor_ready;
static jmp_buf finished;

static TickType_t xTaskGetTickCount(void) { return ticks; }
static void vTaskDelay(TickType_t delay) {
  assert(delay > 0); ticks += delay;
  if (ticks >= stop_at) longjmp(finished, 1);
}
static bool fingerprint_present_hint(void) {
  return (int)ticks >= second_touch_at ||
      ((int)ticks >= touch_at && (lift_at < 0 || (int)ticks < lift_at));
}
static bool fingerprint_prompted_authorization_active(void) {
  return prompt_at >= 0 && (int)ticks >= prompt_at && (int)ticks < prompt_until;
}
static int fingerprint_count(void) { return 1; }
static void fingerprint_led_service(void) {}
static bool fingerprint_is_ready(void) { return sensor_ready; }
static bool fingerprint_recover(void) { sensor_ready = true; return true; }
static void fingerprint_wait_for_touch(void) { vTaskDelay(1); }
static int device_config_touch_cooldown_ms(void) { return 50; }
static void touch_pin_hid_log_event(const char *event, int value) { (void)event; (void)value; }
static void usb_ccid_touch_begin(void) { begins++; }
static void usb_ccid_touch_cancel(void) { cancels++; }
static bool fingerprint_try_poll_match(fingerprint_match_t *match) {
  attempts++; *match = (fingerprint_match_t){0};
  if (attempts <= retry_count) return false;
  if (!mismatch) *match = (fingerprint_match_t){1, 99};
  return true;
}
static void handle_fingerprint_match(fingerprint_match_t match) {
  assert(match.slot == 1); matches++;
}

/* state */
/* task */

static void reset(void) {
  ticks = 0; stop_at = 200; touch_at = 10; lift_at = prompt_at = -1;
  second_touch_at = 10000;
  prompt_until = 0; retry_count = attempts = matches = begins = cancels = 0;
  mismatch = usb_sensor_probe_pending = false; sensor_ready = true;
}
static void run(void) {
  if (!setjmp(finished)) touch_hid_task(NULL);
}

int main(void) {
  // A static high input at startup never authorizes a capture.
  reset(); touch_at = 0; run();
  assert(attempts == 0 && matches == 0 && begins == 0);

  // Fresh presence starts matching independently of USB endpoint polling.
  // Busy UART/image-not-ready retries do not consume the touch edge.
  reset(); retry_count = 3; run();
  assert(attempts == 4 && matches == 1 && begins == 1 && cancels == 0);

  // One completed mismatch consumes the edge even with a held finger.
  reset(); mismatch = true; run();
  assert(attempts == 1 && matches == 0 && cancels == 1);

  // A permanently busy sensor has a bounded retry window.
  reset(); retry_count = 10000; run();
  assert(attempts <= 75 && attempts > 1 && matches == 0 && cancels == 1);

  // Lift and foreground authorization each cancel a pending background
  // capture. Returning from AUTH with the same finger held cannot type.
  reset(); retry_count = 10000; lift_at = 15; run();
  assert(attempts == 5 && matches == 0 && cancels == 1);
  reset(); retry_count = 10000; prompt_at = 15; prompt_until = 30; run();
  assert(attempts == 5 && matches == 0 && cancels == 1);

  // Cooldown and an observed lift allow a subsequent touch to match once.
  reset(); lift_at = 15; second_touch_at = 30; run();
  assert(matches == 2 && attempts == 2);
  reset(); sensor_ready = false; run();
  assert(attempts == 1 && matches == 1);
  return 0;
}

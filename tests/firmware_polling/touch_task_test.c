#include <assert.h>
#include <setjmp.h>
#include <stdio.h>
#include <string.h>
#include "freertos/task.h"
#include "esp_log.h"
#include "../../firmware/tiny_touch_unified/main/fingerprint.h"

static const char *TAG = "test";
static bool usb_sensor_probe_pending;
static TickType_t usb_sensor_probe_at;
enum { DEVICE_MODE_HID, DEVICE_MODE_PIV };
static int device_config_mode(void);
static uint32_t device_config_touch_cooldown_ms(void);
static bool request_and_type_password(fingerprint_match_t match);
static void touch_pin_hid_log_event(const char *event, int value);
static void piv_note_user_presence(void);
static bool type_ascii(const uint8_t *data, size_t length);
#include "touch_task.inc"

typedef struct {
  fingerprint_presence_t presence;
  bool matching;
} sample_t;
#define A {FINGERPRINT_POLL_ABSENT, false}
#define P {FINGERPRINT_POLL_PRESENT, false}
#define U {FINGERPRINT_POLL_UNKNOWN, false}
#define M {FINGERPRINT_POLL_PRESENT, true}
#define I {FINGERPRINT_POLL_ABSENT, true}
static sample_t samples[64];
static size_t sample_count, sample_index;
static TickType_t now, poll_times[64], match_time, idle_time, type_time;
static uint32_t generation, cooldown;
static unsigned typed, idle_leds, recovered, touches, matches, no_matches, presence;
static bool ready, active, match_success, inject_feedback, inject_poll;
static bool inject_between, injected;
static TickType_t active_until;
static bool hold_foreground;
static unsigned led_busy_calls;
static int mode;
static unsigned scenario;
static jmp_buf completed;

TickType_t xTaskGetTickCount(void) { return now; }
void vTaskDelay(TickType_t ticks) {
  now += ticks;
  assert(now < 10000);
  if (active && now >= active_until) {
    active = false;
    generation++;
  }
  if (!injected && ((inject_feedback && ticks == 350) ||
      (inject_between && sample_index == 1 && ticks == 10))) {
    generation += hold_foreground ? 1 : 2;
    if (hold_foreground) {
      active = true;
      active_until = now + 500;
    }
    injected = true;
  }
}
uint32_t fingerprint_foreground_generation(void) { return generation; }
bool fingerprint_prompted_authorization_active(void) { return active; }
bool fingerprint_is_ready(void) { return ready; }
bool fingerprint_recover(void) { recovered++; ready = true; return true; }
int fingerprint_count(void) { return 4; }
fingerprint_poll_t fingerprint_poll(bool match_image) {
  if (sample_index == sample_count) longjmp(completed, 1);
  sample_t sample = samples[sample_index];
  if (sample.matching != match_image) {
    fprintf(stderr, "Scenario %u, sample %zu at %u ms: expected matching=%d, got %d\n",
            scenario, sample_index, now, sample.matching, match_image);
    assert(sample.matching == match_image);
  }
  poll_times[sample_index++] = now;
  fingerprint_poll_t result = {.presence = sample.presence};
  if (sample.presence == FINGERPRINT_POLL_PRESENT && match_image) {
    match_time = now;
    if (match_success) result.match = (fingerprint_match_t){2, 100};
    if (inject_poll) generation += 2;
  }
  return result;
}
void fingerprint_led_idle(void) { idle_leds++; idle_time = now; }
bool fingerprint_background_led_idle(void) {
  assert(!active);
  if (led_busy_calls) { led_busy_calls--; return false; }
  fingerprint_led_idle();
  return true;
}
static int device_config_mode(void) { return mode; }
static uint32_t device_config_touch_cooldown_ms(void) { return cooldown; }
static bool request_and_type_password(fingerprint_match_t match) {
  assert(match.slot == 2 && match.score == 100);
  typed++; type_time = now; return true;
}
static void touch_pin_hid_log_event(const char *event, int value) {
  (void)value;
  touches += strcmp(event, "touch_detected") == 0;
  matches += strcmp(event, "finger_matched") == 0;
  no_matches += strcmp(event, "finger_no_match") == 0;
}
static void piv_note_user_presence(void) { presence++; }
static bool type_ascii(const uint8_t *data, size_t length) {
  assert(length == 6 && memcmp(data, "111111", 6) == 0);
  typed++; type_time = now; return true;
}
static void reset(const sample_t *script, size_t count) {
  scenario++;
  memcpy(samples, script, count * sizeof(*script));
  sample_count = count;
  sample_index = now = generation = cooldown = 0;
  typed = idle_leds = recovered = touches = matches = no_matches = presence = 0;
  match_time = idle_time = type_time = 0;
  ready = match_success = true;
  active = inject_feedback = inject_poll = inject_between = injected = false;
  hold_foreground = false;
  active_until = 0;
  led_busy_calls = 0;
  mode = DEVICE_MODE_HID;
}
#define RESET(...) do { const sample_t script[] = {__VA_ARGS__}; \
  reset(script, sizeof(script) / sizeof(script[0])); } while (0)
static void run(void) { if (setjmp(completed) == 0) touch_hid_task(NULL); }

int main(void) {
  RESET(A, M, P, P, P, P);
  run();
  assert(typed == 1 && touches == 1 && matches == 1 && idle_leds == 1);
  assert(idle_time - match_time == 350 && type_time == idle_time);

  // A held finger and unknown sensor results must never rearm.
  RESET(P, U, P, U, A, M, P, U, P, U, P);
  run();
  assert(typed == 1);

  RESET(A, M, A, M);
  run();
  assert(typed == 2 && idle_leds == 2);
  assert(poll_times[3] - poll_times[2] == 100);

  RESET(A, M, A, A, A, A, A, A, M);
  cooldown = 500;
  run();
  assert(typed == 2 && poll_times[1] == 200);
  assert(poll_times[8] - poll_times[1] == 950);

  RESET(A, I, I, I, I, I);
  run();
  assert(!typed && !idle_leds && !touches);
  for (size_t i = 0; i < sample_count; i++) assert(poll_times[i] == (i + 1) * 100);

  RESET(A, M, P, P);
  match_success = false;
  run();
  assert(!typed && no_matches == 1 && idle_leds == 1);

  RESET(A, M, P, P);
  inject_feedback = true;
  run();
  assert(injected && !typed && idle_leds == 1);

  // A canceled match must restore blue after foreground ownership ends.
  RESET(A, M, P, P);
  inject_feedback = hold_foreground = true;
  run();
  assert(!typed && idle_leds == 1 && idle_time >= active_until);

  RESET(A, M, P, P);
  inject_poll = true;
  run();
  assert(!typed && !touches && idle_leds == 1);

  RESET(A, P, P, A, M);
  inject_between = true;
  run();
  assert(injected && typed == 1);

  RESET(P, P, A, M);
  ready = false;
  run();
  assert(recovered == 1 && typed == 1);

  RESET(A, M);
  active = true;
  active_until = 500;
  run();
  assert(poll_times[0] >= 500 && typed == 1);

  RESET(A, M, P);
  mode = DEVICE_MODE_PIV;
  run();
  assert(typed == 1 && presence == 1 && idle_leds == 1);
  // LED contention cannot discard an already verified touch.
  RESET(A, M, P);
  led_busy_calls = 3;
  run();
  assert(typed == 1 && idle_leds == 1 && type_time < idle_time);

  // Even the maximum configured cooldown does not delay initial arming.
  RESET(A, M);
  cooldown = 60000;
  run();
  assert(typed == 1 && poll_times[0] == 100 && poll_times[1] == 200);
  puts("Touch task behavior checks passed");
  return 0;
}

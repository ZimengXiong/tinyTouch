#include "fingerprint_led.h"

static const uint32_t IDLE_MS = 3000;
static const uint32_t RESULT_MS = 350;
static const uint32_t FADE_MS = 1400;
static const uint32_t RETRY_MS = 100;

typedef enum {
  LED_IDLE,
  LED_RESULT,
  LED_FADING,
  LED_OFF,
} led_phase_t;

static struct {
  led_phase_t phase;
  uint8_t color;
  bool applied;
  bool present;
  uint32_t absent_since;
  uint32_t phase_since;
  uint32_t last_attempt;
} led;

static void observe_presence(bool present, uint32_t now_ms) {
  if (present || led.present) led.absent_since = now_ms;
  led.present = present;
}

static void request_phase(led_phase_t phase, uint32_t now_ms) {
  led.phase = phase;
  led.phase_since = now_ms;
  led.applied = false;
  led.last_attempt = now_ms - RETRY_MS;
}

static void apply_pending(uint32_t now_ms) {
  if (led.applied || (uint32_t)(now_ms - led.last_attempt) < RETRY_MS) return;

  uint8_t params[] = {3, led.color, led.color, 0};
  switch (led.phase) {
    case LED_IDLE:
      params[1] = params[2] = FP_LED_BLUE;
      break;
    case LED_RESULT:
      break;
    case LED_FADING:
      // ZW101/ZW111 command 0x3c: function, start colour, end colour, cycles.
      params[0] = 6;
      params[1] = params[2] = FP_LED_BLUE;
      params[3] = 1;
      break;
    case LED_OFF:
      params[0] = 4;
      params[1] = params[2] = 0;
      break;
  }
  led.last_attempt = now_ms;
  led.applied = fingerprint_led_command(params);
}

void fingerprint_led_init(uint32_t now_ms) {
  led.present = false;
  led.absent_since = now_ms;
  led.color = FP_LED_BLUE;
  request_phase(LED_IDLE, now_ms);
}

void fingerprint_led_show(uint8_t color, bool present, uint32_t now_ms) {
  observe_presence(present, now_ms);
  // A capture may change the sensor's light independently of our last command.
  // Always reassert the requested colour, including a repeated idle request.
  if (color != FP_LED_BLUE || led.phase == LED_OFF || led.phase == LED_FADING) {
    led.absent_since = now_ms;
  }
  led.color = color;
  request_phase(color == FP_LED_BLUE ? LED_IDLE : LED_RESULT, now_ms);
  apply_pending(now_ms);
}

void fingerprint_led_service(bool present, uint32_t now_ms) {
  observe_presence(present, now_ms);
  switch (led.phase) {
    case LED_RESULT:
      if ((uint32_t)(now_ms - led.phase_since) >= RESULT_MS) {
        request_phase(LED_IDLE, now_ms);
      }
      break;
    case LED_IDLE:
      if (!present && (uint32_t)(now_ms - led.absent_since) >= IDLE_MS) {
        request_phase(LED_FADING, now_ms);
      }
      break;
    case LED_FADING:
      if (present) {
        request_phase(LED_IDLE, now_ms);
      } else if ((uint32_t)(now_ms - led.phase_since) >= FADE_MS) {
        // End the hardware program explicitly, even if its ACK was lost or
        // the module ignores the one-cycle count. Retry until acknowledged.
        request_phase(LED_OFF, now_ms);
      }
      break;
    case LED_OFF:
      if (present) request_phase(LED_IDLE, now_ms);
      break;
  }
  apply_pending(now_ms);
}

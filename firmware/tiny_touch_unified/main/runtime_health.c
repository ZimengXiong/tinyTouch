#include "runtime_health.h"

#include <inttypes.h>
#include <stdio.h>
#include <string.h>

#include "esp_system.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"

typedef struct {
  runtime_usb_state_t usb_state;
  uint32_t usb_suspends;
  uint32_t usb_resumes;
  uint32_t usb_reconnects;
  uint32_t auth_successes;
  uint32_t auth_failures;
  uint32_t sensor_protocol_errors;
  int64_t booted_at_us;
  int64_t last_activity_us;
  esp_reset_reason_t reset_reason;
} runtime_health_state_t;

static runtime_health_state_t health;
static portMUX_TYPE health_lock = portMUX_INITIALIZER_UNLOCKED;

static int64_t now_us(void) {
  return esp_timer_get_time();
}

static const char *usb_state_name(runtime_usb_state_t state) {
  switch (state) {
    case RUNTIME_USB_STARTING: return "starting";
    case RUNTIME_USB_ATTACHED: return "attached";
    case RUNTIME_USB_SUSPENDED: return "suspended";
    case RUNTIME_USB_RECOVERING: return "recovering";
    default: return "invalid";
  }
}

void runtime_health_init(void) {
  runtime_health_state_t initial = {
    .usb_state = RUNTIME_USB_STARTING,
    .booted_at_us = now_us(),
    .last_activity_us = now_us(),
    .reset_reason = esp_reset_reason(),
  };
  portENTER_CRITICAL(&health_lock);
  health = initial;
  portEXIT_CRITICAL(&health_lock);
}

void runtime_health_note_usb(runtime_usb_state_t state) {
  portENTER_CRITICAL(&health_lock);
  if (state == RUNTIME_USB_SUSPENDED && health.usb_state != RUNTIME_USB_SUSPENDED) {
    health.usb_suspends++;
  } else if (state == RUNTIME_USB_ATTACHED &&
             health.usb_state == RUNTIME_USB_SUSPENDED) {
    health.usb_resumes++;
  }
  health.usb_state = state;
  health.last_activity_us = now_us();
  portEXIT_CRITICAL(&health_lock);
}

void runtime_health_note_usb_reconnect(void) {
  portENTER_CRITICAL(&health_lock);
  health.usb_reconnects++;
  health.usb_state = RUNTIME_USB_RECOVERING;
  health.last_activity_us = now_us();
  portEXIT_CRITICAL(&health_lock);
}

void runtime_health_note_auth(bool success) {
  portENTER_CRITICAL(&health_lock);
  if (success) {
    health.auth_successes++;
  } else {
    health.auth_failures++;
  }
  health.last_activity_us = now_us();
  portEXIT_CRITICAL(&health_lock);
}

void runtime_health_note_sensor_protocol_error(void) {
  portENTER_CRITICAL(&health_lock);
  health.sensor_protocol_errors++;
  health.last_activity_us = now_us();
  portEXIT_CRITICAL(&health_lock);
}

void runtime_health_format(char *output, size_t output_size) {
  if (!output || output_size == 0) return;
  runtime_health_state_t snapshot;
  portENTER_CRITICAL(&health_lock);
  snapshot = health;
  portEXIT_CRITICAL(&health_lock);
  int64_t current = now_us();
  uint64_t uptime_ms = current > snapshot.booted_at_us
      ? (uint64_t)(current - snapshot.booted_at_us) / 1000 : 0;
  uint64_t idle_ms = current > snapshot.last_activity_us
      ? (uint64_t)(current - snapshot.last_activity_us) / 1000 : 0;
  snprintf(output, output_size,
           "runtime_usb=%s runtime_uptime_ms=%" PRIu64
           " runtime_idle_ms=%" PRIu64 " runtime_reset=%d"
           " runtime_suspends=%" PRIu32 " runtime_resumes=%" PRIu32
           " runtime_reconnects=%" PRIu32 " runtime_auth_ok=%" PRIu32
           " runtime_auth_fail=%" PRIu32 " runtime_sensor_errors=%" PRIu32,
           usb_state_name(snapshot.usb_state), uptime_ms, idle_ms,
           (int)snapshot.reset_reason, snapshot.usb_suspends,
           snapshot.usb_resumes, snapshot.usb_reconnects,
           snapshot.auth_successes, snapshot.auth_failures,
           snapshot.sensor_protocol_errors);
}

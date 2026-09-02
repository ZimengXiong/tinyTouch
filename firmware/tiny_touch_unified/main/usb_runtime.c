#include "usb_runtime.h"

#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "runtime_health.h"
#include "tusb.h"

#define USB_LONG_SUSPEND_MS 5000

typedef struct {
  bool suspended;
  bool remote_wakeup_enabled;
  bool remote_wakeup_attempted;
  bool reconnect_pending;
  bool release_pending;
  TickType_t suspended_at;
} usb_runtime_state_t;

static const char *TAG = "usb_runtime";
static usb_runtime_state_t state;
static portMUX_TYPE state_lock = portMUX_INITIALIZER_UNLOCKED;

void usb_runtime_init(void) {
  portENTER_CRITICAL(&state_lock);
  state = (usb_runtime_state_t){.release_pending = true};
  portEXIT_CRITICAL(&state_lock);
}

void usb_runtime_on_attached(void) {
  portENTER_CRITICAL(&state_lock);
  state.suspended = false;
  state.remote_wakeup_enabled = false;
  state.remote_wakeup_attempted = false;
  state.reconnect_pending = false;
  state.release_pending = true;
  portEXIT_CRITICAL(&state_lock);
  runtime_health_note_usb(RUNTIME_USB_ATTACHED);
}

bool usb_runtime_is_suspended(void) {
  portENTER_CRITICAL(&state_lock);
  bool suspended = state.suspended;
  portEXIT_CRITICAL(&state_lock);
  return suspended;
}

bool usb_runtime_can_poll_sensor(void) {
  portENTER_CRITICAL(&state_lock);
  bool available = state.suspended && state.remote_wakeup_enabled &&
                   !state.remote_wakeup_attempted;
  portEXIT_CRITICAL(&state_lock);
  return available;
}

bool usb_runtime_request_remote_wakeup(void) {
  portENTER_CRITICAL(&state_lock);
  bool allowed = state.suspended && state.remote_wakeup_enabled &&
                 !state.remote_wakeup_attempted;
  if (allowed) state.remote_wakeup_attempted = true;
  portEXIT_CRITICAL(&state_lock);
  return allowed && tud_remote_wakeup();
}

bool usb_runtime_service_reconnect(void) {
  portENTER_CRITICAL(&state_lock);
  bool should_reconnect = state.reconnect_pending && !state.suspended;
  if (should_reconnect) state.reconnect_pending = false;
  portEXIT_CRITICAL(&state_lock);
  if (!should_reconnect) return false;

  ESP_LOGW(TAG, "recovering USB after long host suspend");
  runtime_health_note_usb_reconnect();
  tud_disconnect();
  vTaskDelay(pdMS_TO_TICKS(250));
  tud_connect();
  portENTER_CRITICAL(&state_lock);
  state.release_pending = true;
  portEXIT_CRITICAL(&state_lock);
  return true;
}

bool usb_runtime_take_release_request(void) {
  portENTER_CRITICAL(&state_lock);
  bool requested = state.release_pending;
  state.release_pending = false;
  portEXIT_CRITICAL(&state_lock);
  return requested;
}

void usb_runtime_request_release(void) {
  portENTER_CRITICAL(&state_lock);
  state.release_pending = true;
  portEXIT_CRITICAL(&state_lock);
}

void tud_suspend_cb(bool remote_wakeup_en) {
  portENTER_CRITICAL(&state_lock);
  if (!state.suspended) state.suspended_at = xTaskGetTickCountFromISR();
  state.suspended = true;
  state.release_pending = true;
  state.remote_wakeup_enabled = remote_wakeup_en;
  state.remote_wakeup_attempted = false;
  portEXIT_CRITICAL(&state_lock);
  runtime_health_note_usb(RUNTIME_USB_SUSPENDED);
}

void tud_resume_cb(void) {
  TickType_t now = xTaskGetTickCountFromISR();
  portENTER_CRITICAL(&state_lock);
  if (state.suspended &&
      (TickType_t)(now - state.suspended_at) >= pdMS_TO_TICKS(USB_LONG_SUSPEND_MS)) {
    state.reconnect_pending = true;
  }
  state.suspended = false;
  state.release_pending = true;
  state.remote_wakeup_enabled = false;
  state.remote_wakeup_attempted = false;
  portEXIT_CRITICAL(&state_lock);
  runtime_health_note_usb(RUNTIME_USB_ATTACHED);
}

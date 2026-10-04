#include "usb_ccid.h"

#include <string.h>

#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "device_config.h"
#include "firmware_update.h"
#include "piv.h"
#include "tinyusb.h"
#include "tinyusb_default_config.h"
#include "tusb.h"
#include "device/usbd_pvt.h"
#include "touch_pin_hid.h"
#include "usb_descriptors.h"

static const char *TAG = "usb_ccid";

#define CCID_EP_OUT 0x01
#define CCID_EP_IN 0x81
#define CCID_BUF_SIZE 2048
#define CCID_PACKET_SIZE 64

static uint8_t rx_buf[CCID_BUF_SIZE];
static uint8_t rx_packet[CCID_PACKET_SIZE];
static size_t rx_length;
static uint8_t tx_buf[CCID_BUF_SIZE];
static uint8_t rhport_active;
static ccid_apdu_handler_t apdu_handler;
static bool in_busy;
static bool in_zlp_pending;
static bool touch_enabled;
static bool piv_exposed;
static bool piv_selected;
static bool reconnect_requested;
static bool login_response_pending;
static int64_t touch_until;
static int64_t setup_until;
static portMUX_TYPE policy_lock = portMUX_INITIALIZER_UNLOCKED;
static SemaphoreHandle_t reconnect_done;
static SemaphoreHandle_t console_mutex;

#define TOUCH_WINDOW_US (20LL * 1000000)
#define SETUP_WINDOW_US (60LL * 1000000)

// All detach/attach paths share this callback, including resume and setup.
// Running on the USB task prevents descriptor and CCID transfer races.
static void reconnect_on_usb_task(void *argument) {
  bool expose = *(bool *)argument;
  tud_disconnect();
  vTaskDelay(pdMS_TO_TICKS(250));
  tiny_touch_set_piv_descriptor(expose);
  piv_reset_transport_state();
  taskENTER_CRITICAL(&policy_lock);
  piv_exposed = expose;
  piv_selected = false;
  login_response_pending = false;
  taskEXIT_CRITICAL(&policy_lock);
  tud_connect();
  xSemaphoreGive(reconnect_done);
}

static void update_usb_policy(void) {
  if (xSemaphoreTake(console_mutex, 0) != pdTRUE) return;
  // OTA spans multiple commands. Do not drop CDC between its write chunks.
  if (firmware_update_active()) {
    xSemaphoreGive(console_mutex);
    return;
  }
  int64_t now = esp_timer_get_time();
  bool piv_mode = device_config_mode() == DEVICE_MODE_PIV;
  taskENTER_CRITICAL(&policy_lock);
  bool expose = !touch_enabled || (piv_mode && (now < touch_until || now < setup_until));
  bool reconnect = reconnect_requested || expose != piv_exposed;
  reconnect_requested = false;
  taskEXIT_CRITICAL(&policy_lock);
  if (!reconnect) {
    xSemaphoreGive(console_mutex);
    return;
  }
  // The local argument remains alive until the USB callback completes.
  usbd_defer_func(reconnect_on_usb_task, &expose, false);
  xSemaphoreTake(reconnect_done, portMAX_DELAY);
  xSemaphoreGive(console_mutex);
  touch_pin_hid_log_event(expose ? "piv_visible" : "piv_hidden", 0);
}

static void usb_policy_task(void *argument) {
  (void)argument;
  while (true) {
    update_usb_policy();
    vTaskDelay(pdMS_TO_TICKS(50));
  }
}

static void usb_event_cb(tinyusb_event_t *event, void *arg) {
  (void)arg;
  if (event->id == TINYUSB_EVENT_ATTACHED) touch_pin_hid_usb_attached();
  else if (event->id == TINYUSB_EVENT_DETACHED) touch_pin_hid_usb_detached();
#ifdef CONFIG_TINYUSB_RESUME_CALLBACK
  else if (event->id == TINYUSB_EVENT_RESUMED) {
    // macOS can retain a stale composite node after wake. Reconnect with the
    // current visibility policy; wake alone must never expose a hidden card.
    taskENTER_CRITICAL(&policy_lock);
    reconnect_requested = true;
    taskEXIT_CRITICAL(&policy_lock);
  }
#endif
}

static uint32_t le32(const uint8_t *p) {
  return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static void put_le32(uint8_t *p, uint32_t v) {
  p[0] = (uint8_t)v;
  p[1] = (uint8_t)(v >> 8);
  p[2] = (uint8_t)(v >> 16);
  p[3] = (uint8_t)(v >> 24);
}

static bool send_ccid(uint8_t msg_type, uint8_t slot, uint8_t seq, uint8_t status,
                      uint8_t error, const uint8_t *data, size_t data_len) {
  if (data_len > sizeof(tx_buf) - 10 || in_busy) return false;
  tx_buf[0] = msg_type;
  put_le32(tx_buf + 1, data_len);
  tx_buf[5] = slot;
  tx_buf[6] = seq;
  tx_buf[7] = status;
  tx_buf[8] = error;
  tx_buf[9] = 0x00;
  if (data_len) memcpy(tx_buf + 10, data, data_len);
  bool queued = usbd_edpt_xfer(rhport_active, CCID_EP_IN, tx_buf, data_len + 10);
  if (queued) {
    in_busy = true;
    // CCID requires a terminating ZLP for full-size Bulk-IN messages.
    in_zlp_pending = (data_len + 10) % CCID_PACKET_SIZE == 0;
  }
  return queued;
}

static bool send_parameters(uint8_t slot, uint8_t seq) {
  const uint8_t t1_params[] = {0x11, 0x10, 0x00, 0x45, 0x00, 0xfe, 0x00};
  return send_ccid(0x82, slot, seq, 0x00, 0x00, t1_params, sizeof(t1_params));
}

static void handle_message(uint8_t *msg, size_t msg_len) {
  if (msg_len < 10) return;

  uint8_t type = msg[0];
  uint32_t len = le32(msg + 1);
  uint8_t slot = msg[5];
  uint8_t seq = msg[6];
  if (slot != 0) {
    send_ccid(0x81, slot, seq, 0x42, 0x05, NULL, 0);
    return;
  }
  if (len != msg_len - 10 || len > sizeof(rx_buf) - 10) {
    send_ccid(0x81, slot, seq, 0x42, 0x01, NULL, 0);
    return;
  }

  switch (type) {
    case 0x62: {
      // TCK is the XOR of every byte from T0 through the final interface byte.
      // Strict readers, including Windows, reject the former 0x01 value.
      const uint8_t atr[] = {0x3b, 0x80, 0x01, 0x81};
      send_ccid(0x80, slot, seq, 0x00, 0x00, atr, sizeof(atr));
      break;
    }
    case 0x63:
    case 0x65:
      send_ccid(0x81, slot, seq, 0x00, 0x00, NULL, 0);
      break;
    case 0x61:
    case 0x6c:
    case 0x6d:
      send_parameters(slot, seq);
      break;
    case 0x6f: {
      if (device_config_mode() != DEVICE_MODE_PIV) {
        const uint8_t unavailable[] = {0x69, 0x85};
        send_ccid(0x80, slot, seq, 0x00, 0x00, unavailable, sizeof(unavailable));
        break;
      }
      size_t resp_len = sizeof(tx_buf) - 10;
      bool ok = apdu_handler &&
                apdu_handler(msg + 10, len, tx_buf + 10, &resp_len, sizeof(tx_buf) - 10);
      if (!ok) {
        const uint8_t fail[] = {0x6f, 0x00};
        send_ccid(0x80, slot, seq, 0x00, 0x00, fail, sizeof(fail));
      } else {
        if (!send_ccid(0x80, slot, seq, 0x00, 0x00, tx_buf + 10, resp_len)) {
          taskENTER_CRITICAL(&policy_lock);
          login_response_pending = false;
          taskEXIT_CRITICAL(&policy_lock);
        }
        if (len >= 2 && msg[11] == 0xa4 && resp_len >= 2 &&
            tx_buf[10 + resp_len - 2] == 0x90 && tx_buf[10 + resp_len - 1] == 0x00) {
          taskENTER_CRITICAL(&policy_lock);
          piv_selected = true;
          taskEXIT_CRITICAL(&policy_lock);
        }
      }
      break;
    }
    default:
      ESP_LOGW(TAG, "unsupported CCID message 0x%02x", type);
      send_ccid(0x81, slot, seq, 0x42, 0x00, NULL, 0);
      break;
  }
}

static void ccid_init(void) {}
static void ccid_reset(uint8_t rhport) {
  (void)rhport;
  in_busy = false;
  in_zlp_pending = false;
  rx_length = 0;
  taskENTER_CRITICAL(&policy_lock);
  piv_selected = false;
  login_response_pending = false;
  taskEXIT_CRITICAL(&policy_lock);
  piv_reset_transport_state();
}

static uint16_t ccid_open(uint8_t rhport, tusb_desc_interface_t const *itf_desc,
                          uint16_t max_len) {
  // Claim only our reserved placeholder or the actual smart-card interface.
  // Custom drivers run before built-in HID/CDC drivers in TinyUSB.
  if (itf_desc->bInterfaceNumber != 0) return 0;
  if (itf_desc->bInterfaceClass == TUSB_CLASS_VENDOR_SPECIFIC &&
      itf_desc->bNumEndpoints == 0) return sizeof(tusb_desc_interface_t);
  if (itf_desc->bInterfaceClass != 0x0b || itf_desc->bNumEndpoints != 2) return 0;
  uint8_t const *p_desc = (uint8_t const *)itf_desc;
  uint16_t drv_len = sizeof(tusb_desc_interface_t) + 54;
  uint16_t required_len = drv_len + 2 * sizeof(tusb_desc_endpoint_t);
  if (max_len < required_len) return 0;
  p_desc += drv_len;

  tusb_desc_endpoint_t const *ep_out = (tusb_desc_endpoint_t const *)p_desc;
  tusb_desc_endpoint_t const *ep_in = (tusb_desc_endpoint_t const *)(p_desc + sizeof(tusb_desc_endpoint_t));
  if (!usbd_edpt_open(rhport, ep_out) ||
      !usbd_edpt_open(rhport, ep_in)) return 0;

  rhport_active = rhport;
  in_busy = false;
  in_zlp_pending = false;
  rx_length = 0;
  usbd_edpt_xfer(rhport, CCID_EP_OUT, rx_packet, sizeof(rx_packet));
  return required_len;
}

static bool ccid_control_xfer_cb(uint8_t rhport, uint8_t stage, tusb_control_request_t const *request) {
  (void)rhport;
  (void)stage;
  (void)request;
  return false;
}

static bool ccid_xfer_cb(uint8_t rhport, uint8_t ep_addr, xfer_result_t result,
                         uint32_t xferred_bytes) {
  if (result != XFER_RESULT_SUCCESS) {
    rx_length = 0;
    if (ep_addr == CCID_EP_IN) {
      in_busy = false;
      in_zlp_pending = false;
      taskENTER_CRITICAL(&policy_lock);
      login_response_pending = false;
      taskEXIT_CRITICAL(&policy_lock);
    }
    if (!in_busy && (ep_addr == CCID_EP_OUT || ep_addr == CCID_EP_IN)) {
      usbd_edpt_xfer(rhport, CCID_EP_OUT, rx_packet, sizeof(rx_packet));
    }
    return true;
  }
  if (ep_addr == CCID_EP_OUT) {
    if (xferred_bytes > sizeof(rx_packet) || xferred_bytes > sizeof(rx_buf) - rx_length) {
      rx_length = 0;
    } else {
      memcpy(rx_buf + rx_length, rx_packet, xferred_bytes);
      rx_length += xferred_bytes;
      // A host need not send a ZLP after a full-size OUT message. Dispatch
      // when dwLength is satisfied, or reject a truncated short transfer.
      bool complete = rx_length >= 10 &&
          (le32(rx_buf + 1) <= rx_length - 10 || le32(rx_buf + 1) > sizeof(rx_buf) - 10);
      if (complete || xferred_bytes < sizeof(rx_packet)) {
        handle_message(rx_buf, rx_length);
        rx_length = 0;
      }
    }
    // Keep tx_buf immutable until the IN transfer completes. Only accept the
    // next command immediately if no response was queued.
    if (!in_busy) usbd_edpt_xfer(rhport, CCID_EP_OUT, rx_packet, sizeof(rx_packet));
  } else if (ep_addr == CCID_EP_IN) {
    bool complete = true;
    if (in_zlp_pending) {
      in_zlp_pending = false;
      if (usbd_edpt_xfer(rhport, CCID_EP_IN, NULL, 0)) return true;
      complete = false;
    }
    in_busy = false;
    // The host has received the final login/Keychain response. Changing the
    // descriptor earlier could discard that response and break login.
    taskENTER_CRITICAL(&policy_lock);
    if (complete && login_response_pending) touch_until = 0;
    login_response_pending = false;
    taskEXIT_CRITICAL(&policy_lock);
    rx_length = 0;
    usbd_edpt_xfer(rhport, CCID_EP_OUT, rx_packet, sizeof(rx_packet));
  }
  return true;
}

static usbd_class_driver_t const ccid_driver = {
#if CFG_TUSB_DEBUG >= 2
  .name = "CCID",
#endif
  .init = ccid_init,
  .reset = ccid_reset,
  .open = ccid_open,
  .control_xfer_cb = ccid_control_xfer_cb,
  .xfer_cb = ccid_xfer_cb,
  .sof = NULL,
};

usbd_class_driver_t const *usbd_app_driver_get_cb(uint8_t *driver_count) {
  *driver_count = 1;
  return &ccid_driver;
}

void usb_ccid_start(ccid_apdu_handler_t handler) {
  apdu_handler = handler;
  // Apply the persisted preference at boot. SET can therefore acknowledge and
  // verify over CDC without disconnecting the command that saved the setting.
  touch_enabled = device_config_piv_touch_enabled();
  piv_exposed = !touch_enabled;
  tiny_touch_set_piv_descriptor(piv_exposed);
  reconnect_done = xSemaphoreCreateBinary();
  configASSERT(reconnect_done);
  console_mutex = xSemaphoreCreateMutex();
  configASSERT(console_mutex);
  tiny_touch_init_serial();
  tinyusb_config_t tusb_cfg = TINYUSB_DEFAULT_CONFIG();
  tusb_cfg.descriptor.device = &tiny_touch_device_descriptor;
  tusb_cfg.descriptor.string = tiny_touch_string_descriptors;
  tusb_cfg.descriptor.string_count = tiny_touch_string_descriptor_count;
  tusb_cfg.descriptor.full_speed_config = tiny_touch_configuration_descriptor;
  tusb_cfg.event_cb = usb_event_cb;
  ESP_ERROR_CHECK(tinyusb_driver_install(&tusb_cfg));
  configASSERT(xTaskCreate(usb_policy_task, "usb_policy", 3072, NULL, 2, NULL) == pdPASS);
}

void usb_ccid_rescan(void) {
  // USB RECONNECT is also available without authorization. Preserve current
  // visibility; only PIV OPEN/CREATE may grant a setup discovery window.
  taskENTER_CRITICAL(&policy_lock);
  reconnect_requested = true;
  taskEXIT_CRITICAL(&policy_lock);
}

void usb_ccid_open_setup(void) {
  // Renew setup without resetting a visible card's current authorization.
  // A hidden card will be exposed by the normal policy transition.
  taskENTER_CRITICAL(&policy_lock);
  setup_until = esp_timer_get_time() + SETUP_WINDOW_US;
  taskEXIT_CRITICAL(&policy_lock);
}

bool usb_ccid_touch_enabled(void) { return touch_enabled; }

void usb_ccid_begin_console_command(void) {
  xSemaphoreTake(console_mutex, portMAX_DELAY);
}

void usb_ccid_end_console_command(void) {
  xSemaphoreGive(console_mutex);
}

bool usb_ccid_piv_visible(void) {
  taskENTER_CRITICAL(&policy_lock);
  bool exposed = piv_exposed;
  taskEXIT_CRITICAL(&policy_lock);
  return exposed;
}

void usb_ccid_touch_begin(void) {
  if (!touch_enabled || device_config_mode() != DEVICE_MODE_PIV) return;
  taskENTER_CRITICAL(&policy_lock);
  touch_until = esp_timer_get_time() + TOUCH_WINDOW_US;
  login_response_pending = false;
  taskEXIT_CRITICAL(&policy_lock);
}

void usb_ccid_touch_cancel(void) {
  taskENTER_CRITICAL(&policy_lock);
  touch_until = 0;
  login_response_pending = false;
  taskEXIT_CRITICAL(&policy_lock);
}

void usb_ccid_login_complete(void) {
  if (!touch_enabled || device_config_mode() != DEVICE_MODE_PIV) return;
  taskENTER_CRITICAL(&policy_lock);
  login_response_pending = true;
  taskEXIT_CRITICAL(&policy_lock);
}

bool usb_ccid_wait_for_piv(void) {
  if (!touch_enabled) return true;
  int64_t deadline = esp_timer_get_time() + 8000000;
  while (esp_timer_get_time() < deadline) {
    taskENTER_CRITICAL(&policy_lock);
    bool ready = piv_exposed && piv_selected && esp_timer_get_time() < touch_until;
    taskEXIT_CRITICAL(&policy_lock);
    if (device_config_mode() != DEVICE_MODE_PIV) return false;
    if (ready && tud_mounted() && tud_hid_ready()) {
      // Wait for the login UI to switch fields after PIV selection. This
      // saved delay does not replace enumeration and HID readiness checks.
      uint16_t delay_ms = device_config_piv_delay_ms();
      // Use elapsed time so the scheduler cannot round 25 ms down to 20 ms.
      int64_t pin_deadline = esp_timer_get_time() + (int64_t)delay_ms * 1000;
      while (esp_timer_get_time() < pin_deadline) vTaskDelay(1);
      taskENTER_CRITICAL(&policy_lock);
      ready = piv_exposed && piv_selected && esp_timer_get_time() < touch_until;
      taskEXIT_CRITICAL(&policy_lock);
      return ready && device_config_mode() == DEVICE_MODE_PIV &&
             tud_mounted() && tud_hid_ready();
    }
    vTaskDelay(pdMS_TO_TICKS(10));
  }
  return false;
}

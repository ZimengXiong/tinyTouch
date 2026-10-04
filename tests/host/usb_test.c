#include "usb_stubs.h"
#include "../../firmware/tiny_touch_unified/main/usb_descriptors.c"
#include "../../firmware/tiny_touch_unified/main/usb_ccid.c"

static int64_t now_us;
static int semaphore, reconnects, resets, endpoints, apdus;
static int command_semaphore;
static bool configured, connected, hid_ready, preference;
static device_mode_t mode = DEVICE_MODE_PIV;
static bool deferred;
static bool ota_active;
static bool complete_login;
static bool transfer_queued = true;
static uint16_t delay_ms = 50;
static uint8_t *out_buffer;
static uint16_t out_capacity, in_length;
static int out_requests, in_requests;
static size_t response_size = 2;

int64_t esp_timer_get_time(void) { return now_us; }
void vTaskDelay(uint32_t ticks) { now_us += (int64_t)ticks * 10000; }
int xTaskCreate(void (*fn)(void *), const char *n, int s, void *a, int p, void *t) {
  (void)fn; (void)n; (void)s; (void)a; (void)p; (void)t; return pdPASS;
}
SemaphoreHandle_t xSemaphoreCreateBinary(void) { semaphore = 0; return &semaphore; }
SemaphoreHandle_t xSemaphoreCreateMutex(void) { command_semaphore = 1; return &command_semaphore; }
int xSemaphoreTake(SemaphoreHandle_t s, uint32_t timeout) {
  if (!*s && timeout == 0) return 0;
  assert(*s); *s = 0; return 1;
}
int xSemaphoreGive(SemaphoreHandle_t s) { *s = 1; return 1; }
bool tud_disconnect(void) {
  assert(deferred); connected = configured = hid_ready = false; reconnects++; return true;
}
bool tud_connect(void) { assert(deferred); connected = true; return true; }
bool tud_mounted(void) { return configured; }
bool tud_hid_ready(void) { return hid_ready; }
bool usbd_edpt_xfer(uint8_t r, uint8_t e, uint8_t *b, uint16_t n) {
  (void)r;
  if (!transfer_queued) return false;
  if (e == CCID_EP_OUT) { out_buffer = b; out_capacity = n; out_requests++; }
  else { assert(e == CCID_EP_IN); in_length = n; in_requests++; }
  return true;
}
bool usbd_edpt_open(uint8_t r, const tusb_desc_endpoint_t *e) {
  (void)r; assert(e->bDescriptorType == 5); endpoints++; return true;
}
void usbd_defer_func(void (*fn)(void *), void *arg, bool isr) {
  assert(!isr); deferred = true; fn(arg); deferred = false;
}
int tinyusb_driver_install(const tinyusb_config_t *c) {
  assert(c->descriptor.full_speed_config == tiny_touch_configuration_descriptor);
  connected = true; return 0;
}
int esp_read_mac(uint8_t *m, int kind) { (void)kind; memset(m, 42, 6); return 0; }
device_mode_t device_config_mode(void) { return mode; }
bool device_config_piv_touch_enabled(void) { return preference; }
uint16_t device_config_piv_delay_ms(void) { return delay_ms; }
bool firmware_update_active(void) { return ota_active; }
void piv_reset_transport_state(void) { resets++; }
void touch_pin_hid_log_event(const char *event, int value) { (void)event; (void)value; }
void touch_pin_hid_usb_attached(void) {}
static bool apdu(const uint8_t *a, size_t n, uint8_t *r, size_t *rn, size_t cap) {
  (void)a; (void)n; assert(cap >= response_size);
  memset(r, 0, response_size); r[response_size - 2] = 0x90;
  *rn = response_size; apdus++;
  if (complete_login) usb_ccid_login_complete();
  return true;
}

static void check_descriptor(bool exposed) {
  uint8_t *d = tiny_touch_configuration_descriptor;
  size_t len = d[2] | (d[3] << 8);
  assert(d[4] == 4 && len <= sizeof(tiny_touch_configuration_descriptor));
  int interfaces = 0, cards = 0, hid = 0, cdc = 0;
  for (size_t off = 9; off < len; off += d[off]) {
    assert(d[off] >= 2 && off + d[off] <= len);
    if (d[off + 1] != TUSB_DESC_INTERFACE) continue;
    interfaces++;
    tusb_desc_interface_t *itf = (void *)(d + off);
    if (itf->bInterfaceClass == 0x0b) cards++;
    if (itf->bInterfaceClass == 3) { assert(itf->bInterfaceNumber == 1); hid++; }
    if (itf->bInterfaceClass == 2) { assert(itf->bInterfaceNumber == 2); cdc++; }
    uint16_t claimed = ccid_open(0, itf, len - off);
    if (itf->bInterfaceNumber == 0) assert(claimed == (exposed ? 77 : 9));
    else assert(claimed == 0);
  }
  assert(interfaces == 4 && cards == exposed && hid == 1 && cdc == 1);
}

int main(void) {
  // Default firmware still exposes the legacy composite device.
  usb_ccid_start(apdu); check_descriptor(true);
  assert(!usb_ccid_touch_enabled() && usb_ccid_wait_for_piv());
  int old = reconnects;
  update_usb_policy(); assert(reconnects == old);

  // Enabling the saved setting takes effect only at the next boot.
  preference = true; update_usb_policy(); assert(usb_ccid_piv_visible());
  usb_ccid_start(apdu); check_descriptor(false);
  assert(usb_ccid_touch_enabled() && !usb_ccid_piv_visible());
  endpoints = 0; check_descriptor(false); assert(endpoints == 0);
  usb_ccid_touch_begin(); update_usb_policy(); check_descriptor(true);
  assert(usb_ccid_piv_visible() && resets > 0);
  assert(!usb_ccid_wait_for_piv()); // No enumeration: never type a PIN.
  usb_ccid_touch_cancel(); update_usb_policy(); check_descriptor(false);

  // A discovered token becomes ready only after a successful PIV SELECT.
  usb_ccid_touch_begin(); update_usb_policy();
  configured = hid_ready = true;
  uint8_t select[] = {0x6f, 4,0,0,0, 0,1,0,0,0, 0,0xa4,4,0};
  in_busy = false; handle_message(select, sizeof(select));
  assert(apdus == 1 && piv_selected);
  int64_t started = now_us;
  assert(usb_ccid_wait_for_piv());
  assert(now_us - started == 50000);
  delay_ms = 25;
  started = now_us;
  assert(usb_ccid_wait_for_piv() && now_us - started >= 25000 && now_us - started < 35000);
  delay_ms = 100;
  started = now_us;
  assert(usb_ccid_wait_for_piv() && now_us - started == 100000);
  delay_ms = 0;
  started = now_us;
  assert(usb_ccid_wait_for_piv() && now_us == started);
  delay_ms = 50;
  // Host traffic and a held finger cannot prolong the touch lease.
  now_us = touch_until;
  usb_ccid_begin_console_command();
  update_usb_policy(); check_descriptor(true); // Don't lose AUTH/enrollment replies.
  usb_ccid_end_console_command();
  ota_active = true; update_usb_policy(); check_descriptor(true);
  ota_active = false;
  update_usb_policy(); check_descriptor(false);
  assert(!piv_selected && !usb_ccid_piv_visible());

  // Invalid fingerprints cancel early; discovery does not create presence.
  usb_ccid_touch_begin(); update_usb_policy();
  usb_ccid_touch_cancel(); update_usb_policy(); check_descriptor(false);

  // Login completion hides before the timeout, after the final IN response.
  uint8_t authenticate[] = {0x6f, 4,0,0,0, 0,2,0,0,0, 0,0x87,7,0x9d};
  usb_ccid_touch_begin(); update_usb_policy(); check_descriptor(true);
  complete_login = false;
  in_busy = false; handle_message(authenticate, sizeof(authenticate));
  ccid_xfer_cb(0, CCID_EP_IN, XFER_RESULT_SUCCESS, 12);
  update_usb_policy(); check_descriptor(true); // First slot alone cannot hide.
  complete_login = true;
  handle_message(authenticate, sizeof(authenticate));
  old = reconnects;
  update_usb_policy(); assert(reconnects == old && in_busy);
  assert(login_response_pending && now_us < touch_until);
  ccid_xfer_cb(0, CCID_EP_IN, XFER_RESULT_SUCCESS, 12);
  assert(!login_response_pending && touch_until == 0);
  update_usb_policy(); check_descriptor(false);
  assert(reconnects == old + 1);
  complete_login = false;

  // A new touch can complete a second login after the previous card hides.
  usb_ccid_touch_begin(); update_usb_policy(); check_descriptor(true);
  complete_login = true;
  in_busy = false; handle_message(authenticate, sizeof(authenticate));
  ccid_xfer_cb(0, CCID_EP_IN, XFER_RESULT_SUCCESS, 12);
  update_usb_policy(); check_descriptor(false);

  // An unqueued or failed response cannot trigger an early disconnect.
  usb_ccid_touch_begin(); update_usb_policy();
  transfer_queued = false;
  in_busy = false; handle_message(authenticate, sizeof(authenticate));
  update_usb_policy(); check_descriptor(true);
  assert(!login_response_pending);
  transfer_queued = true;
  handle_message(authenticate, sizeof(authenticate));
  assert(login_response_pending);
  ccid_xfer_cb(0, CCID_EP_IN, 1, 0);
  update_usb_policy(); check_descriptor(true);
  assert(!login_response_pending);
  complete_login = false;
  handle_message(select, sizeof(select));
  ccid_xfer_cb(0, CCID_EP_IN, XFER_RESULT_SUCCESS, 12);
  update_usb_policy(); check_descriptor(true); // Unrelated reply cannot hide.
  now_us = touch_until; update_usb_policy(); check_descriptor(false);
  assert(!login_response_pending);
  complete_login = false;

  // Setup can expose the card without background PIN typing, then expires.
  usb_ccid_rescan(); update_usb_policy(); check_descriptor(false); // Unauthenticated reconnect.
  usb_ccid_open_setup(); usb_ccid_rescan(); update_usb_policy(); check_descriptor(true);
  old = reconnects;
  // Completing a touch login must not truncate an active setup window.
  usb_ccid_touch_begin();
  usb_ccid_login_complete();
  ccid_xfer_cb(0, CCID_EP_IN, XFER_RESULT_SUCCESS, 12);
  update_usb_policy(); check_descriptor(true);
  assert(reconnects == old && touch_until == 0);
  now_us = setup_until - 1;
  usb_ccid_open_setup(); update_usb_policy();
  assert(reconnects == old && setup_until > now_us);
  now_us = setup_until; update_usb_policy(); check_descriptor(false);
  usb_ccid_open_setup(); update_usb_policy(); check_descriptor(true);
  now_us = setup_until; update_usb_policy(); check_descriptor(false);

  // Wake restores current policy and does not make an idle token visible.
  tinyusb_event_t resumed = {.id = TINYUSB_EVENT_RESUMED};
  old = reconnects; usb_event_cb(&resumed, NULL); update_usb_policy();
  assert(reconnects == old + 1); check_descriptor(false);

  // HID mode never exposes PIV due to touch/setup when the option is active.
  mode = DEVICE_MODE_HID; usb_ccid_touch_begin(); usb_ccid_rescan();
  update_usb_policy(); check_descriptor(false);
  assert(!usb_ccid_wait_for_piv());
  old = apdus;
  in_busy = false; handle_message(select, sizeof(select)); assert(apdus == old);

  // Continuous PIV mode keeps its interface after a completed login.
  preference = false; mode = DEVICE_MODE_PIV;
  usb_ccid_start(apdu); check_descriptor(true);
  usb_ccid_login_complete();
  assert(!login_response_pending);
  ccid_xfer_cb(0, CCID_EP_IN, XFER_RESULT_SUCCESS, 12);
  update_usb_policy(); check_descriptor(true);
  return 0;
}

void touch_pin_hid_usb_detached(void) {}

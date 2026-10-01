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

int64_t esp_timer_get_time(void) { return now_us; }
void vTaskDelay(uint32_t ms) { now_us += (int64_t)ms * 1000; }
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
  (void)r; (void)e; (void)b; (void)n; return true;
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
bool firmware_update_active(void) { return ota_active; }
void piv_reset_transport_state(void) { resets++; }
void touch_pin_hid_log_event(const char *event, int value) { (void)event; (void)value; }
void touch_pin_hid_usb_attached(void) {}
static bool apdu(const uint8_t *a, size_t n, uint8_t *r, size_t *rn, size_t cap) {
  (void)a; (void)n; assert(cap >= 2); r[0] = 0x90; r[1] = 0; *rn = 2; apdus++; return true;
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
  assert(apdus == 1 && piv_selected && usb_ccid_wait_for_piv());
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

  // Setup can expose the card without background PIN typing, then expires.
  usb_ccid_rescan(); update_usb_policy(); check_descriptor(false); // Unauthenticated reconnect.
  usb_ccid_open_setup(); usb_ccid_rescan(); update_usb_policy(); check_descriptor(true);
  old = reconnects;
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
  in_busy = false; handle_message(select, sizeof(select)); assert(apdus == 1);
  return 0;
}

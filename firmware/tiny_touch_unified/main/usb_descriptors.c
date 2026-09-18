#include "tusb.h"

#include <stdio.h>
#include <string.h>

#include "device_config.h"
#include "esp_mac.h"
#include "usb_descriptors.h"

#define USB_VID 0x303a
#define USB_PID 0x4001
#define USB_BCD 0x0200

#define ITF_NUM_CCID 0
#define ITF_NUM_HID 1
#define ITF_NUM_CDC 2
#define ITF_NUM_PIV_TOTAL 4

#define EPNUM_CCID_OUT 0x01
#define EPNUM_CCID_IN 0x81
#define EPNUM_PIV_HID 0x82
#define EPNUM_PIV_CDC_NOTIF 0x83
#define EPNUM_PIV_CDC_OUT 0x04
#define EPNUM_PIV_CDC_IN 0x84
#define CCID_DESC_LEN (9 + 54 + 7 + 7)
#define PIV_CONFIG_TOTAL_LEN \
  (TUD_CONFIG_DESC_LEN + CCID_DESC_LEN + TUD_HID_DESC_LEN + TUD_CDC_DESC_LEN)

#define ITF_NUM_HID_HID 0
#define ITF_NUM_HID_CDC 1
#define ITF_NUM_HID_CDC_DATA 2
#define ITF_NUM_HID_NCM 3
#define ITF_NUM_HID_NCM_DATA 4
#define ITF_NUM_HID_TOTAL 5

/* ESP32-S3 has only 5 IN endpoints including EP0. HID+full-CDC+ECM needs 6 IN
 * (keyboard + CDC notif + CDC data + ECM notif + ECM data). Omit CDC notif in
 * HID mode so ECM bulk can open; TinyUSB CDC treats notif as optional. */
#define EPNUM_HID_HID 0x81
#define EPNUM_HID_CDC_OUT 0x02
#define EPNUM_HID_CDC_IN 0x82
#define EPNUM_HID_NCM_NOTIF 0x83
#define EPNUM_HID_NCM_OUT 0x03
#define EPNUM_HID_NCM_IN 0x84

/* CDC ACM without notification endpoint (saves one IN FIFO). */
#define TUD_CDC_DESCRIPTOR_NO_NOTIF(_itfnum, _stridx, _epout, _epin, _epsize) \
  /* Interface Association */\
  8, TUSB_DESC_INTERFACE_ASSOCIATION, _itfnum, 2, TUSB_CLASS_CDC, CDC_COMM_SUBCLASS_ABSTRACT_CONTROL_MODEL, CDC_COMM_PROTOCOL_NONE, 0,\
  /* CDC Control Interface */\
  9, TUSB_DESC_INTERFACE, _itfnum, 0, 0, TUSB_CLASS_CDC, CDC_COMM_SUBCLASS_ABSTRACT_CONTROL_MODEL, CDC_COMM_PROTOCOL_NONE, _stridx,\
  /* CDC Header */\
  5, TUSB_DESC_CS_INTERFACE, CDC_FUNC_DESC_HEADER, U16_TO_U8S_LE(0x0120),\
  /* CDC Call */\
  5, TUSB_DESC_CS_INTERFACE, CDC_FUNC_DESC_CALL_MANAGEMENT, 0, (uint8_t)((_itfnum) + 1),\
  /* CDC ACM */\
  4, TUSB_DESC_CS_INTERFACE, CDC_FUNC_DESC_ABSTRACT_CONTROL_MANAGEMENT, 2,\
  /* CDC Union */\
  5, TUSB_DESC_CS_INTERFACE, CDC_FUNC_DESC_UNION, _itfnum, (uint8_t)((_itfnum) + 1),\
  /* CDC Data Interface */\
  9, TUSB_DESC_INTERFACE, (uint8_t)((_itfnum)+1), 0, 2, TUSB_CLASS_CDC_DATA, 0, 0, 0,\
  /* Endpoint Out */\
  7, TUSB_DESC_ENDPOINT, _epout, TUSB_XFER_BULK, U16_TO_U8S_LE(_epsize), 0,\
  /* Endpoint In */\
  7, TUSB_DESC_ENDPOINT, _epin, TUSB_XFER_BULK, U16_TO_U8S_LE(_epsize), 0

#define TUD_CDC_DESC_NO_NOTIF_LEN (8+9+5+5+4+5+9+7+7)

#define HID_CONFIG_TOTAL_LEN \
  (TUD_CONFIG_DESC_LEN + TUD_HID_DESC_LEN + TUD_CDC_DESC_NO_NOTIF_LEN + TUD_CDC_ECM_DESC_LEN)

#ifndef CFG_TUD_NET_MTU
#define CFG_TUD_NET_MTU 1514
#endif

uint8_t const tiny_touch_hid_report_descriptor[] = {
  TUD_HID_REPORT_DESC_KEYBOARD()
};

const tusb_desc_device_t tiny_touch_device_descriptor = {
  .bLength = sizeof(tusb_desc_device_t),
  .bDescriptorType = TUSB_DESC_DEVICE,
  .bcdUSB = USB_BCD,
  .bDeviceClass = 0x00,
  .bDeviceSubClass = 0x00,
  .bDeviceProtocol = 0x00,
  .bMaxPacketSize0 = CFG_TUD_ENDPOINT0_SIZE,
  .idVendor = USB_VID,
  .idProduct = USB_PID,
  .bcdDevice = 0x0100,
  .iManufacturer = 0x01,
  .iProduct = 0x02,
  .iSerialNumber = 0x03,
  .bNumConfigurations = 0x01,
};

// PIV mode keeps the stable CCID+HID+CDC topology. HID mode swaps CCID for
// CDC-ECM so the on-device dashboard can appear as USB Ethernet; that change
// requires a reconnect (or power cycle) after SET MODE.
static const uint8_t piv_configuration_descriptor[] = {
  TUD_CONFIG_DESCRIPTOR(1, ITF_NUM_PIV_TOTAL, 0, PIV_CONFIG_TOTAL_LEN,
                        TUSB_DESC_CONFIG_ATT_REMOTE_WAKEUP, 100),

  9, TUSB_DESC_INTERFACE, ITF_NUM_CCID, 0, 2, 0x0b, 0x00, 0x00, 0,

  54, 0x21,
  0x10, 0x01,
  0x00,
  0x07,
  0x02, 0x00, 0x00, 0x00,
  0x80, 0x25, 0x00, 0x00,
  0x80, 0x25, 0x00, 0x00,
  0x00,
  0x80, 0x25, 0x00, 0x00,
  0x80, 0x25, 0x00, 0x00,
  0x00,
  0xfe, 0x00, 0x00, 0x00,
  0x00, 0x00, 0x00, 0x00,
  0x00, 0x00, 0x00, 0x00,
  0x3e, 0x00, 0x02, 0x00,
  0x00, 0x08, 0x00, 0x00,
  0x00,
  0x00,
  0x00, 0x00,
  0x00,
  0x01,

  7, TUSB_DESC_ENDPOINT, EPNUM_CCID_OUT, TUSB_XFER_BULK, 64, 0x00, 0,
  7, TUSB_DESC_ENDPOINT, EPNUM_CCID_IN, TUSB_XFER_BULK, 64, 0x00, 0,
  TUD_HID_DESCRIPTOR(ITF_NUM_HID, 0, HID_ITF_PROTOCOL_KEYBOARD,
                     sizeof(tiny_touch_hid_report_descriptor), EPNUM_PIV_HID, 8, 10),

  TUD_CDC_DESCRIPTOR(ITF_NUM_CDC, 0, EPNUM_PIV_CDC_NOTIF, 8,
                     EPNUM_PIV_CDC_OUT, EPNUM_PIV_CDC_IN, 64),
};

static const uint8_t hid_configuration_descriptor[] = {
  TUD_CONFIG_DESCRIPTOR(1, ITF_NUM_HID_TOTAL, 0, HID_CONFIG_TOTAL_LEN,
                        TUSB_DESC_CONFIG_ATT_REMOTE_WAKEUP, 100),
  TUD_HID_DESCRIPTOR(ITF_NUM_HID_HID, 0, HID_ITF_PROTOCOL_KEYBOARD,
                     sizeof(tiny_touch_hid_report_descriptor), EPNUM_HID_HID, 8, 10),
  TUD_CDC_DESCRIPTOR_NO_NOTIF(ITF_NUM_HID_CDC, 0, EPNUM_HID_CDC_OUT, EPNUM_HID_CDC_IN, 64),
  TUD_CDC_ECM_DESCRIPTOR(ITF_NUM_HID_NCM, 5, 4, EPNUM_HID_NCM_NOTIF, 8,
                         EPNUM_HID_NCM_OUT, EPNUM_HID_NCM_IN, 64, CFG_TUD_NET_MTU),
};

static uint8_t live_configuration[256];
static char tiny_touch_serial[20] = "TT-PIV-PROTOTYPE";
static char tiny_touch_mac_string[13] = "020000000001";

char const *tiny_touch_string_descriptors[] = {
  (const char[]){0x09, 0x04},
  "tinyTouch",
  "tinyTouch",
  tiny_touch_serial,
  tiny_touch_mac_string,
  "TinyTouch Dashboard",
};

const int tiny_touch_string_descriptor_count =
  sizeof(tiny_touch_string_descriptors) / sizeof(tiny_touch_string_descriptors[0]);

void tiny_touch_init_serial(void) {
  uint8_t mac[6];
  if (esp_read_mac(mac, ESP_MAC_WIFI_STA) != ESP_OK) return;
  snprintf(tiny_touch_serial, sizeof(tiny_touch_serial), "TT-%02X%02X%02X%02X%02X%02X",
           mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
  mac[0] |= 0x02;
  snprintf(tiny_touch_mac_string, sizeof(tiny_touch_mac_string), "%02X%02X%02X%02X%02X%02X",
           mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
}

void usb_descriptors_apply_mode(device_mode_t mode) {
  const uint8_t *src = (mode == DEVICE_MODE_HID) ? hid_configuration_descriptor
                                                 : piv_configuration_descriptor;
  size_t len = (mode == DEVICE_MODE_HID) ? sizeof(hid_configuration_descriptor)
                                         : sizeof(piv_configuration_descriptor);
  memcpy(live_configuration, src, len);
}

const uint8_t *usb_descriptors_configuration(void) {
  return live_configuration;
}

uint8_t tusb_get_mac_string_id(void) {
  return 4;
}

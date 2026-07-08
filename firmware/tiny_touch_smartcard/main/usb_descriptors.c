#include "tusb.h"

#define USB_VID 0x303a
#define USB_PID 0x4001
#define USB_BCD 0x0200

#define ITF_NUM_CCID 0
#define ITF_NUM_HID 1
#define ITF_NUM_TOTAL 2

#define EPNUM_CCID_OUT 0x01
#define EPNUM_CCID_IN 0x81
#define EPNUM_CCID_INT 0x82
#define EPNUM_HID 0x83
#define CONFIG_TOTAL_LEN (TUD_CONFIG_DESC_LEN + 9 + 54 + 7 + 7 + 7 + TUD_HID_DESC_LEN)

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

const uint8_t tiny_touch_fs_configuration_descriptor[] = {
  TUD_CONFIG_DESCRIPTOR(1, ITF_NUM_TOTAL, 0, CONFIG_TOTAL_LEN, 0, 100),

  // CCID interface. macOS' CCID stack is conservative; expose the optional
  // interrupt endpoint even though this token does not currently use it.
  9, TUSB_DESC_INTERFACE, ITF_NUM_CCID, 0, 3, 0x0b, 0x00, 0x00, 0,

  // CCID class descriptor, USB CCID 1.10.
  54, 0x21,
  0x10, 0x01,             // bcdCCID
  0x00,                   // bMaxSlotIndex
  0x07,                   // bVoltageSupport: 5V, 3V, 1.8V
  0x02, 0x00, 0x00, 0x00, // dwProtocols: T=1
  0x80, 0x25, 0x00, 0x00, // dwDefaultClock: 9600 kHz
  0x80, 0x25, 0x00, 0x00, // dwMaximumClock
  0x00,                   // bNumClockSupported
  0x80, 0x25, 0x00, 0x00, // dwDataRate
  0x80, 0x25, 0x00, 0x00, // dwMaxDataRate
  0x00,                   // bNumDataRatesSupported
  0xfe, 0x00, 0x00, 0x00, // dwMaxIFSD
  0x00, 0x00, 0x00, 0x00, // dwSynchProtocols
  0x00, 0x00, 0x00, 0x00, // dwMechanical
  0x3e, 0x00, 0x02, 0x00, // dwFeatures: auto setup, short APDU exchange
  0x00, 0x08, 0x00, 0x00, // dwMaxCCIDMessageLength
  0x00,                   // bClassGetResponse
  0x00,                   // bClassEnvelope
  0x00, 0x00,             // wLcdLayout
  0x00,                   // bPINSupport
  0x01,                   // bMaxCCIDBusySlots

  7, TUSB_DESC_ENDPOINT, EPNUM_CCID_OUT, TUSB_XFER_BULK, 64, 0x00, 0,
  7, TUSB_DESC_ENDPOINT, EPNUM_CCID_IN, TUSB_XFER_BULK, 64, 0x00, 0,
  7, TUSB_DESC_ENDPOINT, EPNUM_CCID_INT, TUSB_XFER_INTERRUPT, 8, 0x00, 24,

  TUD_HID_DESCRIPTOR(ITF_NUM_HID, 0, HID_ITF_PROTOCOL_KEYBOARD,
                     sizeof(tiny_touch_hid_report_descriptor), EPNUM_HID, 8, 10),
};

char const *tiny_touch_string_descriptors[] = {
  (const char[]){0x09, 0x04},
  "tinyTouch",
  "tinyTouch PIV",
  "TT-PIV-PROTOTYPE",
};

const int tiny_touch_string_descriptor_count =
  sizeof(tiny_touch_string_descriptors) / sizeof(tiny_touch_string_descriptors[0]);

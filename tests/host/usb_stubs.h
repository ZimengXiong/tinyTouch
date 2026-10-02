#pragma once
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#define CFG_TUD_ENDPOINT0_SIZE 64
#define CFG_TUSB_DEBUG 0
#define TUSB_DESC_DEVICE 1
#define TUSB_DESC_INTERFACE 4
#define TUSB_DESC_ENDPOINT 5
#define TUSB_XFER_BULK 2
#define TUSB_CLASS_VENDOR_SPECIFIC 255
#define TUSB_DESC_CONFIG_ATT_REMOTE_WAKEUP 32
#define HID_ITF_PROTOCOL_KEYBOARD 1
#define TUD_CONFIG_DESC_LEN 9
#define TUD_HID_DESC_LEN 25
#define TUD_CDC_DESC_LEN 66
#define U16_BYTES(x) ((x) & 255), (((x) >> 8) & 255)
#define TUD_CONFIG_DESCRIPTOR(c,n,s,l,a,p) 9,2,U16_BYTES(l),n,c,s,(128|a),(p/2)
#define TUD_HID_REPORT_DESC_KEYBOARD() 0
#define TUD_HID_DESCRIPTOR(i,s,p,l,e,z,t) \
  9,4,i,0,1,3,1,p,s,9,0x21,0x11,1,0,1,0x22,U16_BYTES(l),7,5,e,3,U16_BYTES(z),t
#define TUD_CDC_DESCRIPTOR(i,s,n,nz,o,e,z) \
  8,11,i,2,2,2,0,s,9,4,i,0,1,2,2,0,s,5,0x24,0,0x20,1,5,0x24,1,0,(i+1), \
  4,0x24,2,2,5,0x24,6,i,(i+1),7,5,n,3,U16_BYTES(nz),16, \
  9,4,(i+1),0,2,10,0,0,0,7,5,o,2,U16_BYTES(z),0,7,5,e,2,U16_BYTES(z),0
typedef struct __attribute__((packed)) {
  uint8_t bLength, bDescriptorType;
  uint16_t bcdUSB;
  uint8_t bDeviceClass, bDeviceSubClass, bDeviceProtocol, bMaxPacketSize0;
  uint16_t idVendor, idProduct, bcdDevice;
  uint8_t iManufacturer, iProduct, iSerialNumber, bNumConfigurations;
} tusb_desc_device_t;
typedef struct __attribute__((packed)) {
  uint8_t bLength, bDescriptorType, bInterfaceNumber, bAlternateSetting,
          bNumEndpoints, bInterfaceClass, bInterfaceSubClass, bInterfaceProtocol, iInterface;
} tusb_desc_interface_t;
typedef struct __attribute__((packed)) {
  uint8_t bLength, bDescriptorType, bEndpointAddress, bmAttributes;
  uint16_t wMaxPacketSize;
  uint8_t bInterval;
} tusb_desc_endpoint_t;
typedef struct { int unused; } tusb_control_request_t;
typedef int xfer_result_t;
#define XFER_RESULT_SUCCESS 0
typedef struct {
  void (*init)(void);
  void (*reset)(uint8_t);
  uint16_t (*open)(uint8_t, tusb_desc_interface_t const *, uint16_t);
  bool (*control_xfer_cb)(uint8_t, uint8_t, tusb_control_request_t const *);
  bool (*xfer_cb)(uint8_t, uint8_t, xfer_result_t, uint32_t);
  void *sof;
} usbd_class_driver_t;
typedef struct { int id; } tinyusb_event_t;
typedef struct {
  struct { const void *device; const void *string; int string_count; const uint8_t *full_speed_config; } descriptor;
  void (*event_cb)(tinyusb_event_t *, void *);
} tinyusb_config_t;
#define TINYUSB_DEFAULT_CONFIG() {0}
#define TINYUSB_EVENT_ATTACHED 1
#define TINYUSB_EVENT_RESUMED 2
#define CONFIG_TINYUSB_RESUME_CALLBACK 1
#define ESP_MAC_WIFI_STA 0
#define ESP_OK 0
#define ESP_LOGW(tag,...) ((void)(tag))
#define ESP_ERROR_CHECK(e) assert((e) == 0)
#define pdMS_TO_TICKS(ms) ((ms) / 10)
#define pdPASS 1
#define pdTRUE 1
#define portMAX_DELAY UINT32_MAX
#define configASSERT assert
typedef int portMUX_TYPE;
typedef int *SemaphoreHandle_t;
#define portMUX_INITIALIZER_UNLOCKED 0
#define taskENTER_CRITICAL(lock) ((void)(lock))
#define taskEXIT_CRITICAL(lock) ((void)(lock))
int64_t esp_timer_get_time(void);
void vTaskDelay(uint32_t ticks);
int xTaskCreate(void (*fn)(void *), const char *, int, void *, int, void *);
SemaphoreHandle_t xSemaphoreCreateBinary(void);
SemaphoreHandle_t xSemaphoreCreateMutex(void);
int xSemaphoreTake(SemaphoreHandle_t, uint32_t);
int xSemaphoreGive(SemaphoreHandle_t);
bool tud_disconnect(void);
bool tud_connect(void);
bool tud_mounted(void);
bool tud_hid_ready(void);
bool usbd_edpt_xfer(uint8_t, uint8_t, uint8_t *, uint16_t);
bool usbd_edpt_open(uint8_t, const tusb_desc_endpoint_t *);
void usbd_defer_func(void (*fn)(void *), void *, bool);
int tinyusb_driver_install(const tinyusb_config_t *);
int esp_read_mac(uint8_t *, int);

#define TINYUSB_EVENT_DETACHED 3

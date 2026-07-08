#include "usb_ccid.h"

#include <string.h>

#include "esp_log.h"
#include "tinyusb.h"
#include "tinyusb_default_config.h"
#include "tusb.h"
#include "device/usbd_pvt.h"
#include "usb_descriptors.h"

static const char *TAG = "usb_ccid";

#define CCID_EP_OUT 0x01
#define CCID_EP_IN 0x81
#define CCID_EP_INT 0x82
#define CCID_BUF_SIZE 2048

static uint8_t rx_buf[CCID_BUF_SIZE];
static uint8_t tx_buf[CCID_BUF_SIZE];
static uint8_t rhport_active;
static ccid_apdu_handler_t apdu_handler;
static bool ep_ready;

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
  if (data_len + 10 > sizeof(tx_buf)) return false;
  tx_buf[0] = msg_type;
  put_le32(tx_buf + 1, data_len);
  tx_buf[5] = slot;
  tx_buf[6] = seq;
  tx_buf[7] = status;
  tx_buf[8] = error;
  tx_buf[9] = 0x00;
  if (data_len) memcpy(tx_buf + 10, data, data_len);
  return usbd_edpt_xfer(rhport_active, CCID_EP_IN, tx_buf, data_len + 10);
}

static bool send_parameters(uint8_t slot, uint8_t seq) {
  // CCID T=1 protocol data structure:
  // bmFindexDindex, bmTCCKST1, bGuardTimeT1, bmWaitingIntegersT1, bClockStop, bIFSC, bNadValue.
  const uint8_t t1_params[] = {0x11, 0x10, 0x00, 0x45, 0x00, 0xfe, 0x00};
  return send_ccid(0x82, slot, seq, 0x00, 0x00, t1_params, sizeof(t1_params));
}

static void handle_message(uint8_t *msg, size_t msg_len) {
  if (msg_len < 10) return;

  uint8_t type = msg[0];
  uint32_t len = le32(msg + 1);
  uint8_t slot = msg[5];
  uint8_t seq = msg[6];
  if (len + 10 > msg_len) {
    send_ccid(0x81, slot, seq, 0x42, 0x01, NULL, 0);
    return;
  }

  switch (type) {
    case 0x62: { // PC_to_RDR_IccPowerOn
      const uint8_t atr[] = {0x3b, 0x80, 0x01, 0x01};
      send_ccid(0x80, slot, seq, 0x00, 0x00, atr, sizeof(atr));
      break;
    }
    case 0x63: // PC_to_RDR_IccPowerOff
    case 0x65: // PC_to_RDR_GetSlotStatus
      send_ccid(0x81, slot, seq, 0x00, 0x00, NULL, 0);
      break;
    case 0x61: // PC_to_RDR_SetParameters
    case 0x6c: // PC_to_RDR_GetParameters
    case 0x6d: // PC_to_RDR_ResetParameters
      send_parameters(slot, seq);
      break;
    case 0x6f: { // PC_to_RDR_XfrBlock
      size_t resp_len = sizeof(tx_buf) - 10;
      bool ok = apdu_handler &&
                apdu_handler(msg + 10, len, tx_buf + 10, &resp_len, sizeof(tx_buf) - 10);
      if (!ok) {
        const uint8_t fail[] = {0x6f, 0x00};
        send_ccid(0x80, slot, seq, 0x00, 0x00, fail, sizeof(fail));
      } else {
        send_ccid(0x80, slot, seq, 0x00, 0x00, tx_buf + 10, resp_len);
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
  ep_ready = false;
}

static uint16_t ccid_open(uint8_t rhport, tusb_desc_interface_t const *itf_desc,
                          uint16_t max_len) {
  (void)max_len;
  uint8_t const *p_desc = (uint8_t const *)itf_desc;
  uint16_t drv_len = sizeof(tusb_desc_interface_t) + 54;
  p_desc += drv_len;

  tusb_desc_endpoint_t const *ep_out = (tusb_desc_endpoint_t const *)p_desc;
  tusb_desc_endpoint_t const *ep_in = (tusb_desc_endpoint_t const *)(p_desc + sizeof(tusb_desc_endpoint_t));
  tusb_desc_endpoint_t const *ep_int = (tusb_desc_endpoint_t const *)(p_desc + 2 * sizeof(tusb_desc_endpoint_t));
  if (!usbd_edpt_open(rhport, ep_out) ||
      !usbd_edpt_open(rhport, ep_in) ||
      !usbd_edpt_open(rhport, ep_int)) return 0;

  rhport_active = rhport;
  ep_ready = true;
  usbd_edpt_xfer(rhport, CCID_EP_OUT, rx_buf, sizeof(rx_buf));
  return drv_len + 3 * sizeof(tusb_desc_endpoint_t);
}

static bool ccid_control_xfer_cb(uint8_t rhport, uint8_t stage, tusb_control_request_t const *request) {
  (void)rhport;
  (void)stage;
  (void)request;
  return false;
}

static bool ccid_xfer_cb(uint8_t rhport, uint8_t ep_addr, xfer_result_t result,
                         uint32_t xferred_bytes) {
  if (result != XFER_RESULT_SUCCESS) return true;
  if (ep_addr == CCID_EP_OUT) {
    handle_message(rx_buf, xferred_bytes);
    usbd_edpt_xfer(rhport, CCID_EP_OUT, rx_buf, sizeof(rx_buf));
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
  tinyusb_config_t tusb_cfg = TINYUSB_DEFAULT_CONFIG();
  tusb_cfg.descriptor.device = &tiny_touch_device_descriptor;
  tusb_cfg.descriptor.string = tiny_touch_string_descriptors;
  tusb_cfg.descriptor.string_count = tiny_touch_string_descriptor_count;
  tusb_cfg.descriptor.full_speed_config = tiny_touch_fs_configuration_descriptor;
  ESP_ERROR_CHECK(tinyusb_driver_install(&tusb_cfg));
}

void usb_ccid_task(void) {
  (void)ep_ready;
  tud_task();
}

#include "usb_ncm.h"

#include <stdlib.h>
#include <string.h>

#include "class/net/net_device.h"
#include "dashboard_api.h"
#include "device_config.h"
#include "dhcpserver/dhcpserver.h"
#include "dhcpserver/dhcpserver_options.h"
#include "esp_err.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "lwip/esp_netif_net_stack.h"
#include "lwip/ip4_addr.h"
#include "device/usbd_pvt.h"
#include <stdatomic.h>
#include "tusb.h"

static const char *TAG = "usb_ncm";
static esp_netif_t *s_usb_netif;
static esp_netif_ip_info_t s_usb_ip;
static bool ncm_started;
static atomic_bool link_announced;
static QueueHandle_t s_rx_queue;
static QueueHandle_t s_tx_queue;
static uint8_t s_lwip_mac[6];
static atomic_uint s_rx_frames;
static atomic_uint s_rx_drop;
static atomic_uint s_tx_ok;
static atomic_uint s_tx_fail;
static atomic_uint s_arp_replies;

uint8_t tud_network_mac_address[6];

typedef struct {
  uint16_t len;
  uint8_t *data;
} usb_net_frame_t;

static void l2_free(void *h, void *buffer) {
  (void)h;
  free(buffer);
}

// ECM is enabled in TinyUSB, but esp_tinyusb 2.2.1 builds its network
// adapter only for NCM. Keep a bounded ECM adapter here and perform endpoint
// operations on the TinyUSB task.
static void send_queued(void *unused) {
  (void)unused;
  usb_net_frame_t frame;
  while (xQueuePeek(s_tx_queue, &frame, 0) == pdTRUE) {
    if (tud_mounted() && !tud_network_can_xmit(frame.len)) return;
    if (xQueueReceive(s_tx_queue, &frame, 0) != pdTRUE) return;
    if (tud_mounted()) {
      tud_network_xmit(frame.data, frame.len);
      s_tx_ok++;
    } else {
      s_tx_fail++;
    }
    free(frame.data);
  }
}

uint16_t tud_network_xmit_cb(uint8_t *dst, void *ref, uint16_t len) {
  memcpy(dst, ref, len);
  return len;
}

void tud_network_init_cb(void) {
  link_announced = false;
}

static esp_err_t net_send_copy(const void *buffer, size_t len) {
  if (!s_tx_queue || !buffer || len == 0 || len > CFG_TUD_NET_MTU)
    return ESP_ERR_INVALID_ARG;
  uint8_t *copy = malloc(len);
  if (!copy) return ESP_ERR_NO_MEM;
  memcpy(copy, buffer, len);
  usb_net_frame_t frame = {.data = copy, .len = len};
  if (xQueueSend(s_tx_queue, &frame, 0) != pdTRUE) {
    free(copy);
    s_tx_fail++;
    return ESP_ERR_NO_MEM;
  }
  usbd_defer_func(send_queued, NULL, false);
  return ESP_OK;
}

static esp_err_t netif_transmit(void *h, void *buffer, size_t len) {
  (void)h;
  return net_send_copy(buffer, len);
}

static esp_err_t netif_recv(void *buffer, uint16_t len, void *ctx) {
  (void)ctx;
  if (!s_rx_queue || !buffer || len == 0 || len > CFG_TUD_NET_MTU) {
    return ESP_OK;
  }
  /* Heap-allocate the queue payload — TinyUSB task stack is only 4KB. */
  uint8_t *data = malloc(len);
  if (!data) {
    s_rx_drop++;
    return ESP_ERR_NO_MEM;
  }
  memcpy(data, buffer, len);
  usb_net_frame_t frame = {.len = len, .data = data};
  if (xQueueSend(s_rx_queue, &frame, 0) != pdTRUE) {
    free(data);
    s_rx_drop++;
    return ESP_ERR_NO_MEM;
  }
  s_rx_frames++;
  return ESP_OK;
}

bool tud_network_recv_cb(const uint8_t *buffer, uint16_t len) {
  netif_recv((void *)buffer, len, NULL);
  tud_network_recv_renew();
  return true;
}

static void usb_net_rx_task(void *arg) {
  (void)arg;
  usb_net_frame_t frame;
  while (true) {
    if (xQueueReceive(s_rx_queue, &frame, portMAX_DELAY) != pdTRUE) {
      continue;
    }
    if (!frame.data || frame.len == 0) {
      continue;
    }
    if (s_usb_netif) {
      void *copy = malloc(frame.len);
      if (copy) {
        memcpy(copy, frame.data, frame.len);
        // esp-netif consumes the receive buffer even when input is dropped.
        esp_netif_receive(s_usb_netif, copy, frame.len, NULL);
      }
    }
    free(frame.data);
  }
}

static void net_link_up(void *ctx) {
  (void)ctx;
  send_queued(NULL);
  if (!tud_mounted()) {
    link_announced = false;
  } else if (!link_announced) {
    tud_network_link_state(0, true);
    link_announced = true;
    ESP_LOGI(TAG, "USB Ethernet link active");
  }
}

static void usb_ncm_link_task(void *arg);

void usb_ncm_start(void) {
  if (ncm_started) {
    return;
  }
  if (device_config_mode() != DEVICE_MODE_HID) {
    ESP_LOGI(TAG, "USB Ethernet skipped outside HID mode");
    return;
  }

  esp_err_t err = esp_netif_init();
  if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
    ESP_LOGE(TAG, "esp_netif_init failed: %s", esp_err_to_name(err));
    return;
  }
  err = esp_event_loop_create_default();
  if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
    ESP_LOGE(TAG, "event loop failed: %s", esp_err_to_name(err));
    return;
  }

  s_tx_queue = xQueueCreate(8, sizeof(usb_net_frame_t));
  s_rx_queue = xQueueCreate(8, sizeof(usb_net_frame_t));
  if (!s_rx_queue || !s_tx_queue) {
    ESP_LOGE(TAG, "RX queue alloc failed");
    return;
  }

  s_usb_ip.ip.addr = ESP_IP4TOADDR(DASHBOARD_IP_A, DASHBOARD_IP_B, DASHBOARD_IP_C,
                                   DASHBOARD_IP_D);
  /* Local-only link: never advertise ourselves as a default gateway. */
  s_usb_ip.gw.addr = 0;
  s_usb_ip.netmask.addr = ESP_IP4TOADDR(255, 255, 255, 0);

  esp_netif_inherent_config_t base_cfg = {
      .flags = ESP_NETIF_DHCP_SERVER | ESP_NETIF_FLAG_AUTOUP,
      .ip_info = &s_usb_ip,
      .if_key = "TT_USB",
      .if_desc = "tinyTouch",
      .route_prio = 10,
  };
  esp_netif_driver_ifconfig_t driver_cfg = {
      .handle = (void *)1,
      .transmit = netif_transmit,
      .driver_free_rx_buffer = l2_free,
  };
  esp_netif_config_t cfg = {
      .base = &base_cfg,
      .driver = &driver_cfg,
      .stack = ESP_NETIF_NETSTACK_DEFAULT_ETH,
  };
  s_usb_netif = esp_netif_new(&cfg);
  if (!s_usb_netif) {
    ESP_LOGE(TAG, "esp_netif_new failed");
    return;
  }

  uint8_t usb_mac[6];
  if (esp_read_mac(usb_mac, ESP_MAC_WIFI_STA) != ESP_OK) {
    ESP_LOGE(TAG, "esp_read_mac failed");
    return;
  }
  usb_mac[0] |= 0x02;
  memcpy(s_lwip_mac, usb_mac, 6);
  s_lwip_mac[5] ^= 0x01;
  memcpy(tud_network_mac_address, usb_mac, 6);
  if (esp_netif_set_mac(s_usb_netif, s_lwip_mac) != ESP_OK) {
    ESP_LOGE(TAG, "esp_netif_set_mac failed");
    return;
  }

  dhcps_lease_t lease = {.enable = true};
  IP4_ADDR(&lease.start_ip, DASHBOARD_IP_A, DASHBOARD_IP_B, DASHBOARD_IP_C, 2);
  IP4_ADDR(&lease.end_ip, DASHBOARD_IP_A, DASHBOARD_IP_B, DASHBOARD_IP_C, 2);
  if (esp_netif_dhcps_option(s_usb_netif, ESP_NETIF_OP_SET, REQUESTED_IP_ADDRESS, &lease,
                             sizeof(lease)) != ESP_OK) {
    ESP_LOGW(TAG, "DHCP lease option not set; host may need a manual IP");
  }

  uint32_t lease_time = 60;
  if (esp_netif_dhcps_option(s_usb_netif, ESP_NETIF_OP_SET, IP_ADDRESS_LEASE_TIME,
                             &lease_time, sizeof(lease_time)) != ESP_OK) {
    ESP_LOGW(TAG, "DHCP lease time option not set");
  }

  uint8_t offer_router = 0;
  if (esp_netif_dhcps_option(s_usb_netif, ESP_NETIF_OP_SET, ROUTER_SOLICITATION_ADDRESS,
                             &offer_router, sizeof(offer_router)) != ESP_OK) {
    ESP_LOGW(TAG, "DHCP router offer could not be disabled");
  }

  esp_netif_action_start(s_usb_netif, 0, 0, 0);
  // Do not consume packets until esp-netif has installed its lwIP handle.
  if (xTaskCreate(usb_net_rx_task, "usb_net_rx", 4096, NULL, 5, NULL) != pdPASS) {
    ESP_LOGE(TAG, "RX task create failed");
    return;
  }

  /* Do NOT call tud_network_link_state here: ECM notify EP is not open yet
   * (ep_notif==0) and claiming EP0 corrupts the stack. Announce after mount. */

  ncm_started = true;
  ESP_LOGI(TAG, "USB Ethernet ready at " DASHBOARD_URL);

  if (xTaskCreate(usb_ncm_link_task, "usb_ncm_link", 2048, NULL, 3, NULL) != pdPASS) {
    ESP_LOGW(TAG, "USB Ethernet link task not started; relying on init callback");
  }
}

static void usb_ncm_link_task(void *arg) {
  (void)arg;
  while (true) {
    usb_ncm_task();
    vTaskDelay(pdMS_TO_TICKS(50));
  }
}

bool usb_ncm_ready(void) {
  return ncm_started && s_usb_netif != NULL && esp_netif_is_netif_up(s_usb_netif);
}

void usb_ncm_get_stats(unsigned *rx, unsigned *tx_ok, unsigned *tx_fail, unsigned *arp) {
  if (rx) {
    *rx = s_rx_frames;
  }
  if (tx_ok) {
    *tx_ok = s_tx_ok;
  }
  if (tx_fail) {
    *tx_fail = s_tx_fail + s_rx_drop;
  }
  if (arp) {
    *arp = s_arp_replies;
  }
}

void usb_ncm_task(void) {
  if (!ncm_started) {
    return;
  }
  usbd_defer_func(net_link_up, NULL, false);
}

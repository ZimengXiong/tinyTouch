#include "piv.h"

#include <string.h>

#include "esp_random.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "fingerprint.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "mbedtls/base64.h"
#include "mbedtls/pk.h"
#include "mbedtls/rsa.h"
#include "mbedtls/sha256.h"
#include "nvs.h"

static const char *TAG = "piv";

static const uint8_t PIV_AID[] = {0xa0, 0x00, 0x00, 0x03, 0x08, 0x00, 0x00, 0x10, 0x00};
static const uint8_t PIV_AID_VERSIONED[] = {0xa0, 0x00, 0x00, 0x03, 0x08, 0x00, 0x00, 0x10, 0x00, 0x01, 0x00};
static const uint8_t DISCOVERY_OBJECT[] = {
  0x7e, 0x12,
  0x4f, 0x0b, 0xa0, 0x00, 0x00, 0x03, 0x08, 0x00, 0x00, 0x10, 0x00, 0x01, 0x00,
  0x5f, 0x2f, 0x02, 0x60, 0x00
};
static const uint8_t CCC_OBJECT[] = {
  0x53, 0x24,
  0xf0, 0x15, 0xa0, 0x00, 0x00, 0x01, 0x16, 0xff, 0x02, 0x00, 0x00, 0x00,
              0x00, 0x00, 0x00, 0x00, 0x00, 0x10, 0x00, 0x00, 0x00, 0x00,
  0xf1, 0x01, 0x21,
  0xf2, 0x01, 0x21,
  0xf3, 0x00,
  0xf4, 0x01, 0x00,
  0xf5, 0x01, 0x10
};
static uint8_t CHUID_OBJECT[] = {
  0x53, 0x3b,
  0x30, 0x19, 0xd4, 0xe7, 0x39, 0xda, 0x73, 0x9c, 0xed, 0x39, 0xce, 0x73,
              0x9d, 0x83, 0x68, 0x58, 0x21, 0x08, 0x42, 0x10, 0x84, 0x21,
              0xc8, 0x42, 0x10, 0xc3, 0xeb,
  0x34, 0x10, 0x01, 0x30, 0x19, 0xd4, 0xe7, 0x39, 0xda, 0x73, 0x9c, 0xed,
              0x39, 0xce, 0x73, 0x9d, 0x83, 0x68,
  0x35, 0x08, 0x32, 0x30, 0x33, 0x36, 0x30, 0x37, 0x30, 0x33,
  0x3e, 0x00,
  0xfe, 0x00
};
#define CHUID_GUID_OFFSET 31
static const uint8_t KEY_HISTORY_OBJECT[] = {
  0x53, 0x09,
  0xc1, 0x01, 0x00,
  0xc2, 0x01, 0x00,
  0xc3, 0x01, 0x00
};

static mbedtls_pk_context auth_key;
static mbedtls_pk_context key_mgmt_key;
static bool piv_keys_initialized;
static SemaphoreHandle_t piv_mutex;
static uint8_t cert_9a_der[1536];
static size_t cert_9a_der_len;
static uint8_t cert_9d_der[1536];
static size_t cert_9d_der_len;
static char stored_cert_9a[1800];
static char stored_key_9a[2400];
static char stored_cert_9d[1800];
static char stored_key_9d[2400];
static bool using_provisioned_keys;
static uint8_t pending_response[1800];
static size_t pending_response_len;
static size_t pending_response_off;
static uint8_t chained_apdu_data[700];
static size_t chained_apdu_data_len;
static uint8_t chained_ins;
static uint8_t chained_p1;
static uint8_t chained_p2;
static TickType_t pin_verified_until;
static TickType_t user_presence_until;
static uint8_t user_presence_slots_used;
static TickType_t pairing_mode_until;

static bool deadline_active(TickType_t deadline, TickType_t maximum_window) {
  if (deadline == 0) return false;
  int32_t remaining = (int32_t)(deadline - xTaskGetTickCount());
  return remaining > 0 && remaining <= (int32_t)maximum_window;
}
static const TickType_t PIN_VERIFIED_WINDOW_TICKS = pdMS_TO_TICKS(60000);
static const TickType_t USER_PRESENCE_WINDOW_TICKS = pdMS_TO_TICKS(10000);
static const TickType_t PAIRING_MODE_WINDOW_TICKS = pdMS_TO_TICKS(120000);

static size_t encode_len(uint8_t *out, size_t len);
static bool respond_data(const uint8_t *data, size_t data_len, uint8_t *response,
                         size_t *response_len, size_t response_cap);

static void decode_pem_cert(const char *pem, uint8_t *der, size_t der_cap, size_t *der_len) {
  *der_len = 0;
  const char *begin = strstr(pem, "-----BEGIN CERTIFICATE-----");
  const char *end = strstr(pem, "-----END CERTIFICATE-----");
  if (!begin || !end || end <= begin) return;
  begin = strchr(begin, '\n');
  if (!begin) return;
  begin++;
  size_t b64_len = (size_t)(end - begin);
  int rc = mbedtls_base64_decode(der, der_cap, der_len,
                                 (const unsigned char *)begin, b64_len);
  if (rc != 0) {
    *der_len = 0;
    ESP_LOGW(TAG, "certificate DER decode failed: -0x%x", -rc);
  }
}

static bool load_nvs_string(nvs_handle_t handle, const char *name, char *out, size_t cap) {
  size_t length = cap;
  esp_err_t result = nvs_get_blob(handle, name, out, &length);
  if (result != ESP_OK || length == 0 || length > cap) return false;
  out[cap - 1] = '\0';
  return true;
}

static void reset_key_contexts(void) {
  if (piv_keys_initialized) {
    mbedtls_pk_free(&auth_key);
    mbedtls_pk_free(&key_mgmt_key);
  }
  mbedtls_pk_init(&auth_key);
  mbedtls_pk_init(&key_mgmt_key);
  piv_keys_initialized = true;
}

static void clear_provisioned_identity(void) {
  reset_key_contexts();
  memset(cert_9a_der, 0, sizeof(cert_9a_der));
  memset(cert_9d_der, 0, sizeof(cert_9d_der));
  cert_9a_der_len = 0;
  cert_9d_der_len = 0;
  using_provisioned_keys = false;
}

bool piv_uses_provisioned_keys(void) {
  if (piv_mutex) xSemaphoreTake(piv_mutex, portMAX_DELAY);
  bool result = using_provisioned_keys;
  if (piv_mutex) xSemaphoreGive(piv_mutex);
  return result;
}

static bool append_sw(uint8_t *response, size_t *response_len, size_t response_cap,
                      uint16_t sw) {
  if (*response_len + 2 > response_cap) return false;
  response[(*response_len)++] = (uint8_t)(sw >> 8);
  response[(*response_len)++] = (uint8_t)(sw & 0xff);
  return true;
}

static bool respond_data(const uint8_t *data, size_t data_len, uint8_t *response,
                         size_t *response_len, size_t response_cap) {
  if (data_len + 2 > response_cap) return false;
  memcpy(response, data, data_len);
  *response_len = data_len;
  return append_sw(response, response_len, response_cap, 0x9000);
}

static size_t encode_len(uint8_t *out, size_t len) {
  if (len < 0x80) {
    out[0] = (uint8_t)len;
    return 1;
  }
  if (len <= 0xff) {
    out[0] = 0x81;
    out[1] = (uint8_t)len;
    return 2;
  }
  out[0] = 0x82;
  out[1] = (uint8_t)(len >> 8);
  out[2] = (uint8_t)len;
  return 3;
}

static size_t encoded_len_size(size_t len) {
  if (len < 0x80) return 1;
  if (len <= 0xff) return 2;
  return 3;
}

static size_t apdu_le(const uint8_t *apdu, size_t apdu_len, size_t default_len) {
  if (apdu_len == 4) return default_len;
  if (apdu_len == 5) return apdu[4] == 0 ? 256 : apdu[4];
  uint8_t lc = apdu[4];
  if (apdu_len > 5 + lc) return apdu[5 + lc] == 0 ? 256 : apdu[5 + lc];
  return default_len;
}

static bool respond_maybe_chunked(const uint8_t *data, size_t data_len,
                                  const uint8_t *apdu, size_t apdu_len,
                                  uint8_t *response, size_t *response_len,
                                  size_t response_cap) {
  size_t le = apdu_le(apdu, apdu_len, response_cap - 2);
  if (le > response_cap - 2) le = response_cap - 2;
  if (le >= data_len) return respond_data(data, data_len, response, response_len, response_cap);
  if (((le + 12) % 64) == 0 && le > 1) le--;

  if (data_len > sizeof(pending_response)) return false;
  memcpy(pending_response, data, data_len);
  pending_response_len = data_len;
  pending_response_off = le;
  memcpy(response, data, le);
  *response_len = le;
  size_t remain = pending_response_len - pending_response_off;
  uint16_t sw = (uint16_t)(0x6100 | (remain > 255 ? 0x00 : remain));
  return append_sw(response, response_len, response_cap, sw);
}

static bool handle_get_response(const uint8_t *apdu, size_t apdu_len,
                                uint8_t *response, size_t *response_len,
                                size_t response_cap) {
  if (pending_response_off >= pending_response_len) {
    pending_response_len = 0;
    pending_response_off = 0;
    return append_sw(response, response_len, response_cap, 0x6a86);
  }
  size_t le = apdu_le(apdu, apdu_len, response_cap - 2);
  size_t remain = pending_response_len - pending_response_off;
  size_t take = remain < le ? remain : le;
  if (take > response_cap - 2) take = response_cap - 2;
  if (((take + 12) % 64) == 0 && take > 1) take--;
  memcpy(response, pending_response + pending_response_off, take);
  pending_response_off += take;
  *response_len = take;
  remain = pending_response_len - pending_response_off;
  if (remain == 0) {
    pending_response_len = 0;
    pending_response_off = 0;
    return append_sw(response, response_len, response_cap, 0x9000);
  }
  uint16_t sw = (uint16_t)(0x6100 | (remain > 255 ? 0x00 : remain));
  return append_sw(response, response_len, response_cap, sw);
}

static bool read_lc_data(const uint8_t *apdu, size_t apdu_len,
                         const uint8_t **data, size_t *data_len) {
  if (apdu_len < 5) return false;
  if (apdu[4] == 0x00) {
    if (apdu_len < 7) return false;
    size_t lc = ((size_t)apdu[5] << 8) | apdu[6];
    if (lc > apdu_len - 7) return false;
    *data = apdu + 7;
    *data_len = lc;
    return true;
  }
  uint8_t lc = apdu[4];
  if ((size_t)lc > apdu_len - 5) return false;
  *data = apdu + 5;
  *data_len = lc;
  return true;
}

static bool tlv_read_len(const uint8_t *buf, size_t buf_len, size_t *off, size_t *len) {
  if (*off >= buf_len) return false;
  uint8_t b = buf[(*off)++];
  if ((b & 0x80) == 0) {
    *len = b;
    return true;
  }
  size_t n = b & 0x7f;
  if (n == 0 || n > 2 || *off > buf_len || n > buf_len - *off) return false;
  size_t v = 0;
  for (size_t i = 0; i < n; i++) v = (v << 8) | buf[(*off)++];
  *len = v;
  return true;
}

static bool tlv_find_one(const uint8_t *buf, size_t buf_len, uint8_t tag,
                         const uint8_t **value, size_t *value_len) {
  size_t off = 0;
  while (off < buf_len) {
    uint8_t t = buf[off++];
    size_t len = 0;
    if (!tlv_read_len(buf, buf_len, &off, &len) || off > buf_len || len > buf_len - off) return false;
    if (t == tag) {
      *value = buf + off;
      *value_len = len;
      return true;
    }
    off += len;
  }
  return false;
}

static int piv_rng(void *ctx, unsigned char *out, size_t len) {
  (void)ctx;
  esp_fill_random(out, len);
  return 0;
}

static bool handle_select(const uint8_t *apdu, size_t apdu_len, uint8_t *response,
                          size_t *response_len, size_t response_cap) {
  const uint8_t *data = NULL;
  size_t data_len = 0;
  if (!read_lc_data(apdu, apdu_len, &data, &data_len)) return append_sw(response, response_len, response_cap, 0x6700);
  bool base_aid = data_len == sizeof(PIV_AID) && memcmp(data, PIV_AID, sizeof(PIV_AID)) == 0;
  bool versioned_aid = data_len == sizeof(PIV_AID_VERSIONED) &&
                       memcmp(data, PIV_AID_VERSIONED, sizeof(PIV_AID_VERSIONED)) == 0;
  if (!base_aid && !versioned_aid) {
    return append_sw(response, response_len, response_cap, 0x6a82);
  }
  const uint8_t fci[] = {
    0x61, 0x11,
    0x4f, 0x06, 0x00, 0x00, 0x10, 0x00, 0x01, 0x00,
    0x79, 0x07,
    0x4f, 0x05, 0xa0, 0x00, 0x00, 0x03, 0x08
  };
  return respond_data(fci, sizeof(fci), response, response_len, response_cap);
}

static bool handle_get_data(const uint8_t *apdu, size_t apdu_len, uint8_t *response,
                            size_t *response_len, size_t response_cap) {
  const uint8_t *data = NULL;
  size_t data_len = 0;
  if (!read_lc_data(apdu, apdu_len, &data, &data_len)) return append_sw(response, response_len, response_cap, 0x6700);

  if (data_len == 3 && data[0] == 0x5c && data[1] == 0x01 && data[2] == 0x7e) {
    return respond_maybe_chunked(DISCOVERY_OBJECT, sizeof(DISCOVERY_OBJECT), apdu, apdu_len,
                                 response, response_len, response_cap);
  }

  if (data_len == 5 && data[0] == 0x5c && data[1] == 0x03 &&
      data[2] == 0x5f && data[3] == 0xc1 && data[4] == 0x07) {
    return respond_maybe_chunked(CCC_OBJECT, sizeof(CCC_OBJECT), apdu, apdu_len,
                                 response, response_len, response_cap);
  }

  if (data_len == 5 && data[0] == 0x5c && data[1] == 0x03 &&
      data[2] == 0x5f && data[3] == 0xc1 && data[4] == 0x02) {
    return respond_maybe_chunked(CHUID_OBJECT, sizeof(CHUID_OBJECT), apdu, apdu_len,
                                 response, response_len, response_cap);
  }

  if (data_len == 5 && data[0] == 0x5c && data[1] == 0x03 &&
      data[2] == 0x5f && data[3] == 0xc1 && data[4] == 0x05) {
    if (!using_provisioned_keys || cert_9a_der_len == 0) {
      return append_sw(response, response_len, response_cap, 0x6a88);
    }
    uint8_t object[1700];
    size_t off = 0;
    size_t inner_len = 1 + encoded_len_size(cert_9a_der_len) + cert_9a_der_len + 3 + 2;
    object[off++] = 0x53;
    off += encode_len(object + off, inner_len);
    object[off++] = 0x70;
    off += encode_len(object + off, cert_9a_der_len);
    memcpy(object + off, cert_9a_der, cert_9a_der_len);
    off += cert_9a_der_len;
    object[off++] = 0x71;
    object[off++] = 0x01;
    object[off++] = 0x00;
    object[off++] = 0xfe;
    object[off++] = 0x00;
    return respond_maybe_chunked(object, off, apdu, apdu_len, response, response_len, response_cap);
  }

  if (data_len == 5 && data[0] == 0x5c && data[1] == 0x03 &&
      data[2] == 0x5f && data[3] == 0xc1 && data[4] == 0x0b) {
    if (!using_provisioned_keys || cert_9d_der_len == 0) {
      return append_sw(response, response_len, response_cap, 0x6a88);
    }
    uint8_t object[1700];
    size_t off = 0;
    size_t inner_len = 1 + encoded_len_size(cert_9d_der_len) + cert_9d_der_len + 3 + 2;
    object[off++] = 0x53;
    off += encode_len(object + off, inner_len);
    object[off++] = 0x70;
    off += encode_len(object + off, cert_9d_der_len);
    memcpy(object + off, cert_9d_der, cert_9d_der_len);
    off += cert_9d_der_len;
    object[off++] = 0x71;
    object[off++] = 0x01;
    object[off++] = 0x00;
    object[off++] = 0xfe;
    object[off++] = 0x00;
    return respond_maybe_chunked(object, off, apdu, apdu_len, response, response_len, response_cap);
  }

  if (data_len == 5 && data[0] == 0x5c && data[1] == 0x03 &&
      data[2] == 0x5f && data[3] == 0xc1 && data[4] == 0x0c) {
    return respond_maybe_chunked(KEY_HISTORY_OBJECT, sizeof(KEY_HISTORY_OBJECT), apdu, apdu_len,
                                 response, response_len, response_cap);
  }

  return append_sw(response, response_len, response_cap, 0x6a88);
}

static bool handle_verify(const uint8_t *apdu, size_t apdu_len,
                          uint8_t *response, size_t *response_len, size_t response_cap) {
  if (!using_provisioned_keys) {
    pin_verified_until = 0;
    return append_sw(response, response_len, response_cap, 0x6985);
  }
  const uint8_t *data = NULL;
  size_t data_len = 0;
  if (apdu[2] != 0x00 || apdu[3] != 0x80 ||
      !read_lc_data(apdu, apdu_len, &data, &data_len)) {
    pin_verified_until = 0;
    return append_sw(response, response_len, response_cap, 0x6a86);
  }
  (void)data;
  (void)data_len;
  pin_verified_until = xTaskGetTickCount() + PIN_VERIFIED_WINDOW_TICKS;
  return append_sw(response, response_len, response_cap, 0x9000);
}

static bool handle_general_authenticate(const uint8_t *apdu, size_t apdu_len,
                                        uint8_t *response, size_t *response_len,
                                        size_t response_cap) {
  if (apdu[2] != 0x07 || !(apdu[3] == 0x9a || apdu[3] == 0x9d)) {
    return append_sw(response, response_len, response_cap, 0x6a86);
  }
  if (!using_provisioned_keys) {
    pin_verified_until = 0;
    user_presence_until = 0;
    return append_sw(response, response_len, response_cap, 0x6985);
  }
  if (!deadline_active(pin_verified_until, PIN_VERIFIED_WINDOW_TICKS)) {
    pin_verified_until = 0;
    return append_sw(response, response_len, response_cap, 0x6982);
  }
  const uint8_t *data = NULL;
  size_t data_len = 0;
  if (!read_lc_data(apdu, apdu_len, &data, &data_len)) {
    return append_sw(response, response_len, response_cap, 0x6700);
  }

  size_t outer_off = 0;
  if (data_len < 2 || data[outer_off++] != 0x7c) {
    return append_sw(response, response_len, response_cap, 0x6a80);
  }
  size_t outer_len = 0;
  if (!tlv_read_len(data, data_len, &outer_off, &outer_len) || outer_off + outer_len > data_len) {
    return append_sw(response, response_len, response_cap, 0x6a80);
  }

  const uint8_t *challenge = NULL;
  size_t challenge_len = 0;
  if (!tlv_find_one(data + outer_off, outer_len, 0x81, &challenge, &challenge_len)) {
    return append_sw(response, response_len, response_cap, 0x6a80);
  }
  mbedtls_pk_context *key = apdu[3] == 0x9d ? &key_mgmt_key : &auth_key;
  if (mbedtls_pk_get_type(key) != MBEDTLS_PK_RSA) {
    pin_verified_until = 0;
    return append_sw(response, response_len, response_cap, 0x6f00);
  }

  bool user_presence_valid = deadline_active(user_presence_until,
                                             USER_PRESENCE_WINDOW_TICKS);
  bool pairing_mode_valid = deadline_active(pairing_mode_until,
                                            PAIRING_MODE_WINDOW_TICKS);
  uint8_t slot_bit = apdu[3] == 0x9d ? 0x02 : 0x01;
  bool slot_already_used = (user_presence_slots_used & slot_bit) != 0;
  if ((!user_presence_valid || slot_already_used) && !pairing_mode_valid) {
    pin_verified_until = 0;
    if (!user_presence_valid) {
      user_presence_until = 0;
      user_presence_slots_used = 0;
    }
    return append_sw(response, response_len, response_cap, 0x6982);
  }
  // A macOS login can use 9a for authentication and then 9d to unlock the
  // login Keychain. One touch permits at most one operation in each slot; it
  // never permits repeated operations in either slot.
  if (!pairing_mode_valid) {
    user_presence_slots_used |= slot_bit;
    if (user_presence_slots_used == 0x03) user_presence_until = 0;
  }

  uint8_t sig[256];
  size_t sig_len = sizeof(sig);
  int rc = 0;
  if (challenge_len == sizeof(sig)) {
    mbedtls_rsa_context *rsa = mbedtls_pk_rsa(*key);
    rc = mbedtls_rsa_private(rsa, piv_rng, NULL, challenge, sig);
  } else {
    uint8_t hash[32];
    mbedtls_sha256(challenge, challenge_len, hash, 0);
    rc = mbedtls_pk_sign(key, MBEDTLS_MD_SHA256, hash, sizeof(hash),
                         sig, sizeof(sig), &sig_len, piv_rng, NULL);
  }
  pin_verified_until = 0;
  if (rc != 0) {
    ESP_LOGE(TAG, "sign failed: -0x%x", -rc);
    return append_sw(response, response_len, response_cap, 0x6f00);
  }

  size_t off = 0;
  response[off++] = 0x7c;
  off += encode_len(response + off, 1 + (sig_len >= 0x80 ? 3 : 1) + sig_len);
  response[off++] = 0x82;
  off += encode_len(response + off, sig_len);
  if (off + sig_len + 2 > response_cap) return false;
  memcpy(response + off, sig, sig_len);
  off += sig_len;
  *response_len = off;
  return append_sw(response, response_len, response_cap, 0x9000);
}

void piv_init(void) {
  if (!piv_mutex) piv_mutex = xSemaphoreCreateMutex();
  configASSERT(piv_mutex);
  uint8_t mac[6];
  uint8_t device_hash[32];
  if (esp_read_mac(mac, ESP_MAC_WIFI_STA) == ESP_OK) {
    mbedtls_sha256(mac, sizeof(mac), device_hash, 0);
    memcpy(CHUID_OBJECT + CHUID_GUID_OFFSET, device_hash, 16);
    CHUID_OBJECT[CHUID_GUID_OFFSET + 6] =
      (CHUID_OBJECT[CHUID_GUID_OFFSET + 6] & 0x0f) | 0x40;
    CHUID_OBJECT[CHUID_GUID_OFFSET + 8] =
      (CHUID_OBJECT[CHUID_GUID_OFFSET + 8] & 0x3f) | 0x80;
  }

  const char *cert_9a_pem = NULL;
  const char *key_9a_pem = NULL;
  const char *cert_9d_pem = NULL;
  const char *key_9d_pem = NULL;
  clear_provisioned_identity();
  bool have_provisioned_material = false;
  nvs_handle_t nvs_handle;
  if (nvs_open("piv_keys", NVS_READONLY, &nvs_handle) == ESP_OK) {
    have_provisioned_material =
      load_nvs_string(nvs_handle, "cert9a", stored_cert_9a, sizeof(stored_cert_9a)) &&
      load_nvs_string(nvs_handle, "key9a", stored_key_9a, sizeof(stored_key_9a)) &&
      load_nvs_string(nvs_handle, "cert9d", stored_cert_9d, sizeof(stored_cert_9d)) &&
      load_nvs_string(nvs_handle, "key9d", stored_key_9d, sizeof(stored_key_9d));
    nvs_close(nvs_handle);
    if (have_provisioned_material) {
      cert_9a_pem = stored_cert_9a;
      key_9a_pem = stored_key_9a;
      cert_9d_pem = stored_cert_9d;
      key_9d_pem = stored_key_9d;
    }
  }

  if (!have_provisioned_material) {
    ESP_LOGI(TAG, "PIV identity is unconfigured");
    return;
  }
  int rc = mbedtls_pk_parse_key(&auth_key,
                                (const unsigned char *)key_9a_pem,
                                strlen(key_9a_pem) + 1,
                                NULL, 0, NULL, NULL);
  bool auth_ok = rc == 0 && mbedtls_pk_get_type(&auth_key) == MBEDTLS_PK_RSA;
  if (!auth_ok) {
    ESP_LOGW(TAG, "provisioned auth private key could not be loaded");
  }

  rc = mbedtls_pk_parse_key(&key_mgmt_key,
                            (const unsigned char *)key_9d_pem,
                            strlen(key_9d_pem) + 1,
                            NULL, 0, NULL, NULL);
  bool key_mgmt_ok = rc == 0 && mbedtls_pk_get_type(&key_mgmt_key) == MBEDTLS_PK_RSA;
  if (!key_mgmt_ok) {
    ESP_LOGW(TAG, "provisioned key-management private key could not be loaded");
  }

  decode_pem_cert(cert_9a_pem, cert_9a_der, sizeof(cert_9a_der), &cert_9a_der_len);
  decode_pem_cert(cert_9d_pem, cert_9d_der, sizeof(cert_9d_der), &cert_9d_der_len);
  using_provisioned_keys = auth_ok && key_mgmt_ok && cert_9a_der_len > 0 && cert_9d_der_len > 0;
  if (!using_provisioned_keys) {
    ESP_LOGW(TAG, "provisioned PIV material is incomplete or unusable");
    // Credential readiness is aggregate. Never retain a usable key or
    // certificate from one slot when any member of the provisioned identity
    // failed validation.
    clear_provisioned_identity();
  }
}

void piv_reload_keys(void) {
  if (piv_mutex) xSemaphoreTake(piv_mutex, portMAX_DELAY);
  cert_9a_der_len = 0;
  cert_9d_der_len = 0;
  pending_response_len = 0;
  pending_response_off = 0;
  piv_init();
  if (piv_mutex) xSemaphoreGive(piv_mutex);
}

void piv_reset_transport_state(void) {
  if (piv_mutex) xSemaphoreTake(piv_mutex, portMAX_DELAY);
  pending_response_len = 0;
  pending_response_off = 0;
  chained_apdu_data_len = 0;
  pin_verified_until = 0;
  user_presence_until = 0;
  user_presence_slots_used = 0;
  if (piv_mutex) xSemaphoreGive(piv_mutex);
}

void piv_note_user_presence(void) {
  if (piv_mutex) xSemaphoreTake(piv_mutex, portMAX_DELAY);
  user_presence_until = xTaskGetTickCount() + USER_PRESENCE_WINDOW_TICKS;
  user_presence_slots_used = 0;
  if (piv_mutex) xSemaphoreGive(piv_mutex);
}

void piv_set_pairing_mode(bool enabled) {
  if (piv_mutex) xSemaphoreTake(piv_mutex, portMAX_DELAY);
  pairing_mode_until = enabled ? xTaskGetTickCount() + PAIRING_MODE_WINDOW_TICKS : 0;
  if (piv_mutex) xSemaphoreGive(piv_mutex);
}

bool piv_pairing_mode_active(void) {
  if (piv_mutex) xSemaphoreTake(piv_mutex, portMAX_DELAY);
  bool active = deadline_active(pairing_mode_until, PAIRING_MODE_WINDOW_TICKS);
  if (piv_mutex) xSemaphoreGive(piv_mutex);
  return active;
}

static bool piv_handle_apdu_locked(const uint8_t *apdu, size_t apdu_len,
                                   uint8_t *response, size_t *response_len,
                                   size_t response_cap) {
  *response_len = 0;
  if (apdu_len < 4) {
    return append_sw(response, response_len, response_cap, 0x6700);
  }

  uint8_t ins = apdu[1];
  uint8_t cla = apdu[0];

  if ((cla & 0x10) && ins == 0x87) {
    const uint8_t *data = NULL;
    size_t data_len = 0;
    bool same_chain = chained_apdu_data_len == 0 ||
                      (ins == chained_ins && apdu[2] == chained_p1 && apdu[3] == chained_p2);
    if (!same_chain || !read_lc_data(apdu, apdu_len, &data, &data_len) ||
        chained_apdu_data_len > sizeof(chained_apdu_data) ||
        data_len > sizeof(chained_apdu_data) - chained_apdu_data_len) {
      chained_apdu_data_len = 0;
      return append_sw(response, response_len, response_cap, 0x6700);
    }
    if (chained_apdu_data_len == 0) {
      chained_ins = ins;
      chained_p1 = apdu[2];
      chained_p2 = apdu[3];
    }
    memcpy(chained_apdu_data + chained_apdu_data_len, data, data_len);
    chained_apdu_data_len += data_len;
    return append_sw(response, response_len, response_cap, 0x9000);
  }

  uint8_t chained_apdu[8 + sizeof(chained_apdu_data)];
  if (chained_apdu_data_len && ins == chained_ins && apdu[2] == chained_p1 && apdu[3] == chained_p2) {
    const uint8_t *data = NULL;
    size_t data_len = 0;
    if (!read_lc_data(apdu, apdu_len, &data, &data_len) ||
        chained_apdu_data_len > sizeof(chained_apdu_data) ||
        data_len > sizeof(chained_apdu_data) - chained_apdu_data_len) {
      chained_apdu_data_len = 0;
      return append_sw(response, response_len, response_cap, 0x6700);
    }
    memcpy(chained_apdu_data + chained_apdu_data_len, data, data_len);
    chained_apdu_data_len += data_len;

    chained_apdu[0] = cla & (uint8_t)~0x10;
    chained_apdu[1] = ins;
    chained_apdu[2] = apdu[2];
    chained_apdu[3] = apdu[3];
    chained_apdu[4] = 0x00;
    chained_apdu[5] = (uint8_t)(chained_apdu_data_len >> 8);
    chained_apdu[6] = (uint8_t)chained_apdu_data_len;
    memcpy(chained_apdu + 7, chained_apdu_data, chained_apdu_data_len);
    apdu = chained_apdu;
    apdu_len = 7 + chained_apdu_data_len;
    chained_apdu_data_len = 0;
  } else if (chained_apdu_data_len) {
    chained_apdu_data_len = 0;
  }

  switch (ins) {
    case 0xa4:
      return handle_select(apdu, apdu_len, response, response_len, response_cap);
    case 0xc0:
      return handle_get_response(apdu, apdu_len, response, response_len, response_cap);
    case 0xcb:
      return handle_get_data(apdu, apdu_len, response, response_len, response_cap);
    case 0x20:
      return handle_verify(apdu, apdu_len, response, response_len, response_cap);
    case 0x87:
      return handle_general_authenticate(apdu, apdu_len, response, response_len, response_cap);
    default:
      return append_sw(response, response_len, response_cap, 0x6d00);
  }
}

bool piv_handle_apdu(const uint8_t *apdu, size_t apdu_len,
                     uint8_t *response, size_t *response_len,
                     size_t response_cap) {
  if (piv_mutex) xSemaphoreTake(piv_mutex, portMAX_DELAY);
  bool ok = piv_handle_apdu_locked(apdu, apdu_len, response, response_len, response_cap);
  if (piv_mutex) xSemaphoreGive(piv_mutex);
  return ok;
}

#include "piv.h"

#include <string.h>

#include "esp_random.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "fingerprint.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "mbedtls/pk.h"
#include "mbedtls/rsa.h"
#include "mbedtls/sha256.h"
#include "mbedtls/x509_crt.h"
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

static bool deadline_active(TickType_t deadline, TickType_t maximum_window) {
  if (deadline == 0) return false;
  int32_t remaining = (int32_t)(deadline - xTaskGetTickCount());
  return remaining > 0 && remaining <= (int32_t)maximum_window;
}
static const TickType_t PIN_VERIFIED_WINDOW_TICKS = pdMS_TO_TICKS(60000);
static const TickType_t USER_PRESENCE_WINDOW_TICKS = pdMS_TO_TICKS(10000);

static size_t encode_len(uint8_t *out, size_t len);
static int piv_rng(void *ctx, unsigned char *out, size_t len);
static bool respond_data(const uint8_t *data, size_t data_len, uint8_t *response,
                         size_t *response_len, size_t response_cap);

static void secure_wipe(void *data, size_t length) {
  volatile uint8_t *cursor = data;
  while (length--) *cursor++ = 0;
}

static void wipe_stored_identity(void) {
  secure_wipe(stored_cert_9a, sizeof(stored_cert_9a));
  secure_wipe(stored_key_9a, sizeof(stored_key_9a));
  secure_wipe(stored_cert_9d, sizeof(stored_cert_9d));
  secure_wipe(stored_key_9d, sizeof(stored_key_9d));
}

static bool load_certificate_for_key(const char *pem, mbedtls_pk_context *key,
                                     uint8_t *der, size_t der_cap, size_t *der_len) {
  if (der_len) *der_len = 0;
  mbedtls_x509_crt certificate;
  mbedtls_x509_crt_init(&certificate);
  int parse_result = mbedtls_x509_crt_parse(
      &certificate, (const unsigned char *)pem, strlen(pem) + 1);
  int pair_result = parse_result == 0
                        ? mbedtls_pk_check_pair(&certificate.pk, key, piv_rng, NULL)
                        : parse_result;
  bool valid = parse_result == 0 && pair_result == 0 &&
               (!der || (der_len && certificate.raw.len <= der_cap));
  if (valid && der) {
    memcpy(der, certificate.raw.p, certificate.raw.len);
    *der_len = certificate.raw.len;
  }
  mbedtls_x509_crt_free(&certificate);
  return valid;
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

static bool write_identity_part(nvs_handle_t handle, const char *name,
                                const char *value) {
  return nvs_set_blob(handle, name, value, strlen(value) + 1) == ESP_OK;
}

static bool create_certificate(mbedtls_pk_context *key, char *output,
                               size_t output_size) {
  uint8_t serial_bytes[16];
  mbedtls_x509write_cert certificate;
  mbedtls_x509write_crt_init(&certificate);
  esp_fill_random(serial_bytes, sizeof(serial_bytes));
  int result = 0;
  mbedtls_x509write_crt_set_subject_key(&certificate, key);
  mbedtls_x509write_crt_set_issuer_key(&certificate, key);
  if (result == 0) result = mbedtls_x509write_crt_set_subject_name(
      &certificate, "CN=tinyTouch PIV");
  if (result == 0) result = mbedtls_x509write_crt_set_issuer_name(
      &certificate, "CN=tinyTouch PIV");
  if (result == 0) result = mbedtls_x509write_crt_set_validity(
      &certificate, "20260101000000", "20460101000000");
  if (result == 0) result = mbedtls_x509write_crt_set_serial_raw(
      &certificate, serial_bytes, sizeof(serial_bytes));
  if (result == 0) mbedtls_x509write_crt_set_md_alg(&certificate, MBEDTLS_MD_SHA256);
  if (result == 0) result = mbedtls_x509write_crt_pem(
      &certificate, (unsigned char *)output, output_size, piv_rng, NULL);
  secure_wipe(serial_bytes, sizeof(serial_bytes));
  mbedtls_x509write_crt_free(&certificate);
  return result == 0;
}

static bool create_key_and_certificate(char *key_pem, size_t key_size,
                                       char *certificate_pem, size_t certificate_size) {
  mbedtls_pk_context key;
  mbedtls_pk_init(&key);
  int result = mbedtls_pk_setup(&key, mbedtls_pk_info_from_type(MBEDTLS_PK_RSA));
  if (result == 0) result = mbedtls_rsa_gen_key(mbedtls_pk_rsa(key), piv_rng, NULL, 2048, 65537);
  if (result == 0) result = mbedtls_pk_write_key_pem(&key, (unsigned char *)key_pem, key_size);
  bool ok = result == 0 && create_certificate(&key, certificate_pem, certificate_size);
  mbedtls_pk_free(&key);
  return ok;
}

bool piv_create_identity(void) {
  if (piv_uses_provisioned_keys()) return false;
  // RSA creation needs a worker task. Keep PEM output in static storage so
  // the task does not overflow its stack with four multi-kilobyte buffers.
  wipe_stored_identity();
  bool ok = create_key_and_certificate(stored_key_9a, sizeof(stored_key_9a),
                                       stored_cert_9a, sizeof(stored_cert_9a)) &&
            create_key_and_certificate(stored_key_9d, sizeof(stored_key_9d),
                                       stored_cert_9d, sizeof(stored_cert_9d));
  nvs_handle_t handle;
  if (ok && nvs_open("piv_keys", NVS_READWRITE, &handle) == ESP_OK) {
    ok = write_identity_part(handle, "cert9a", stored_cert_9a) &&
         write_identity_part(handle, "key9a", stored_key_9a) &&
         write_identity_part(handle, "cert9d", stored_cert_9d) &&
         write_identity_part(handle, "key9d", stored_key_9d) && nvs_commit(handle) == ESP_OK;
    nvs_close(handle);
  } else {
    ok = false;
  }
  wipe_stored_identity();
  if (!ok) return false;
  piv_reload_keys();
  return piv_uses_provisioned_keys();
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
  if (apdu[4] == 0x00) {
    if (apdu_len == 7) {
      size_t le = ((size_t)apdu[5] << 8) | apdu[6];
      return le == 0 ? 65536 : le;
    }
    if (apdu_len < 7) return default_len;
    size_t lc = ((size_t)apdu[5] << 8) | apdu[6];
    size_t data_end = 7 + lc;
    if (apdu_len == data_end + 2) {
      size_t le = ((size_t)apdu[data_end] << 8) | apdu[data_end + 1];
      return le == 0 ? 65536 : le;
    }
    return default_len;
  }
  size_t data_end = 5 + apdu[4];
  if (apdu_len == data_end + 1) {
    return apdu[data_end] == 0 ? 256 : apdu[data_end];
  }
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
    size_t data_end = 7 + lc;
    if (lc == 0 || (apdu_len != data_end && apdu_len != data_end + 2)) {
      return false;
    }
    *data = apdu + 7;
    *data_len = lc;
    return true;
  }
  size_t lc = apdu[4];
  size_t data_end = 5 + lc;
  if (apdu_len != data_end && apdu_len != data_end + 1) return false;
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

static bool parse_dynamic_auth(const uint8_t *buf, size_t buf_len,
                               const uint8_t **challenge, size_t *challenge_len) {
  size_t off = 0;
  bool saw_challenge = false;
  bool saw_empty_response = false;
  while (off < buf_len) {
    uint8_t tag = buf[off++];
    size_t len = 0;
    if (!tlv_read_len(buf, buf_len, &off, &len) || len > buf_len - off) return false;
    if (tag == 0x81 && !saw_challenge && len > 0) {
      *challenge = buf + off;
      *challenge_len = len;
      saw_challenge = true;
    } else if (tag == 0x82 && !saw_empty_response && len == 0) {
      saw_empty_response = true;
    } else {
      return false;
    }
    off += len;
  }
  return off == buf_len && saw_challenge && saw_empty_response;
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
  if (apdu[3] != 0x80) {
    pin_verified_until = 0;
    return append_sw(response, response_len, response_cap, 0x6a88);
  }
  if (apdu[2] == 0xff && apdu_len == 4) {
    pin_verified_until = 0;
    return append_sw(response, response_len, response_cap, 0x9000);
  }
  if (apdu[2] != 0x00) {
    pin_verified_until = 0;
    return append_sw(response, response_len, response_cap, 0x6a86);
  }
  if (apdu_len == 4) {
    return append_sw(response, response_len, response_cap,
                     deadline_active(pin_verified_until, PIN_VERIFIED_WINDOW_TICKS)
                         ? 0x9000 : 0x63c3);
  }
  const uint8_t *data = NULL;
  size_t data_len = 0;
  static const uint8_t expected_pin[8] = {
    '1', '1', '1', '1', '1', '1', 0xff, 0xff,
  };
  if (!read_lc_data(apdu, apdu_len, &data, &data_len) ||
      data_len != sizeof(expected_pin) ||
      memcmp(data, expected_pin, sizeof(expected_pin)) != 0) {
    pin_verified_until = 0;
    return append_sw(response, response_len, response_cap, 0x6a80);
  }
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
  // PIN verification authorizes one cryptographic attempt. Malformed input
  // must not leave a reusable authorization window behind.
  pin_verified_until = 0;
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
  if (!tlv_read_len(data, data_len, &outer_off, &outer_len) ||
      outer_off + outer_len != data_len) {
    return append_sw(response, response_len, response_cap, 0x6a80);
  }

  const uint8_t *challenge = NULL;
  size_t challenge_len = 0;
  if (!parse_dynamic_auth(data + outer_off, outer_len, &challenge, &challenge_len)) {
    return append_sw(response, response_len, response_cap, 0x6a80);
  }
  mbedtls_pk_context *key = apdu[3] == 0x9d ? &key_mgmt_key : &auth_key;
  if (mbedtls_pk_get_type(key) != MBEDTLS_PK_RSA) {
    pin_verified_until = 0;
    return append_sw(response, response_len, response_cap, 0x6f00);
  }

  bool user_presence_valid = deadline_active(user_presence_until,
                                             USER_PRESENCE_WINDOW_TICKS);
  uint8_t slot_bit = apdu[3] == 0x9d ? 0x02 : 0x01;
  bool slot_already_used = (user_presence_slots_used & slot_bit) != 0;
  if (!user_presence_valid || slot_already_used) {
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
  user_presence_slots_used |= slot_bit;
  if (user_presence_slots_used == 0x03) user_presence_until = 0;

  uint8_t sig[256];
  size_t sig_len = mbedtls_pk_get_len(key);
  if (sig_len != sizeof(sig) || challenge_len != sig_len) {
    return append_sw(response, response_len, response_cap, 0x6700);
  }
  mbedtls_rsa_context *rsa = mbedtls_pk_rsa(*key);
  int rc = mbedtls_rsa_private(rsa, piv_rng, NULL, challenge, sig);
  if (rc != 0) {
    ESP_LOGE(TAG, "sign failed: -0x%x", -rc);
    return append_sw(response, response_len, response_cap, 0x6f00);
  }

  size_t inner_len = 1 + encoded_len_size(sig_len) + sig_len;
  size_t required = 1 + encoded_len_size(inner_len) + inner_len + 2;
  if (required > response_cap) return false;
  size_t off = 0;
  response[off++] = 0x7c;
  off += encode_len(response + off, inner_len);
  response[off++] = 0x82;
  off += encode_len(response + off, sig_len);
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
    wipe_stored_identity();
    return;
  }
  int rc = mbedtls_pk_parse_key(&auth_key,
                                (const unsigned char *)key_9a_pem,
                                strlen(key_9a_pem) + 1,
                                NULL, 0, NULL, NULL);
  bool auth_ok = rc == 0 && mbedtls_pk_get_type(&auth_key) == MBEDTLS_PK_RSA &&
                 mbedtls_pk_get_bitlen(&auth_key) == 2048;
  if (!auth_ok) {
    ESP_LOGW(TAG, "provisioned auth private key could not be loaded");
  }

  rc = mbedtls_pk_parse_key(&key_mgmt_key,
                            (const unsigned char *)key_9d_pem,
                            strlen(key_9d_pem) + 1,
                            NULL, 0, NULL, NULL);
  bool key_mgmt_ok = rc == 0 &&
                     mbedtls_pk_get_type(&key_mgmt_key) == MBEDTLS_PK_RSA &&
                     mbedtls_pk_get_bitlen(&key_mgmt_key) == 2048;
  if (!key_mgmt_ok) {
    ESP_LOGW(TAG, "provisioned key-management private key could not be loaded");
  }

  bool cert_9a_ok = auth_ok && load_certificate_for_key(
      cert_9a_pem, &auth_key, cert_9a_der, sizeof(cert_9a_der), &cert_9a_der_len);
  bool cert_9d_ok = key_mgmt_ok && load_certificate_for_key(
      cert_9d_pem, &key_mgmt_key, cert_9d_der, sizeof(cert_9d_der), &cert_9d_der_len);
  using_provisioned_keys = auth_ok && key_mgmt_ok && cert_9a_ok && cert_9d_ok;
  wipe_stored_identity();
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

static bool piv_handle_apdu_locked(const uint8_t *apdu, size_t apdu_len,
                                   uint8_t *response, size_t *response_len,
                                   size_t response_cap) {
  *response_len = 0;
  if (apdu_len < 4) {
    return append_sw(response, response_len, response_cap, 0x6700);
  }

  uint8_t ins = apdu[1];
  uint8_t cla = apdu[0];

  if (cla != 0x00 && cla != 0x10) {
    chained_apdu_data_len = 0;
    return append_sw(response, response_len, response_cap, 0x6e00);
  }
  if ((cla & 0x10) && ins != 0x87) {
    chained_apdu_data_len = 0;
    return append_sw(response, response_len, response_cap, 0x6884);
  }
  if (ins != 0xc0) {
    pending_response_len = 0;
    pending_response_off = 0;
  }

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
  if (!response_len) return false;
  *response_len = 0;
  if (!apdu || !response || response_cap < 2) return false;
  if (piv_mutex) xSemaphoreTake(piv_mutex, portMAX_DELAY);
  bool ok = piv_handle_apdu_locked(apdu, apdu_len, response, response_len, response_cap);
  if (piv_mutex) xSemaphoreGive(piv_mutex);
  return ok;
}

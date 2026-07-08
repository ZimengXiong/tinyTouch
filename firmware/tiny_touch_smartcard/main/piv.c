#include "piv.h"

#include <string.h>

#include "esp_random.h"
#include "esp_log.h"
#include "fingerprint.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "mbedtls/base64.h"
#include "mbedtls/pk.h"
#include "mbedtls/rsa.h"
#include "mbedtls/sha256.h"
#include "secrets.h"

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
static const uint8_t CHUID_OBJECT[] = {
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
static const uint8_t KEY_HISTORY_OBJECT[] = {
  0x53, 0x09,
  0xc1, 0x01, 0x00, // retired key management certs present on card
  0xc2, 0x01, 0x00, // retired key management certs off card
  0xc3, 0x01, 0x00  // off-card cert URL not present
};

static mbedtls_pk_context auth_key;
static mbedtls_pk_context key_mgmt_key;
static uint8_t cert_9a_der[1536];
static size_t cert_9a_der_len;
static uint8_t cert_9d_der[1536];
static size_t cert_9d_der_len;
static uint8_t pending_response[1800];
static size_t pending_response_len;
static size_t pending_response_off;
static uint8_t chained_apdu_data[700];
static size_t chained_apdu_data_len;
static uint8_t chained_ins;
static uint8_t chained_p1;
static uint8_t chained_p2;
static TickType_t pin_verified_until;
static TickType_t biometric_verified_until;
static const TickType_t PIN_VERIFIED_WINDOW_TICKS = pdMS_TO_TICKS(120000);
static const TickType_t BIOMETRIC_VERIFIED_WINDOW_TICKS = pdMS_TO_TICKS(120000);

static size_t encode_len(uint8_t *out, size_t len);
static bool respond_data(const uint8_t *data, size_t data_len, uint8_t *response,
                         size_t *response_len, size_t response_cap);

#define TRACE_RECORDS 64
#define TRACE_APDU_BYTES 12
typedef struct {
  uint8_t len;
  uint8_t bytes[TRACE_APDU_BYTES];
  uint16_t sw;
} trace_record_t;

static trace_record_t trace_records[TRACE_RECORDS];
static size_t trace_next;
static size_t trace_count;

static void trace_apdu(const uint8_t *apdu, size_t apdu_len,
                       const uint8_t *response, size_t response_len) {
  trace_record_t *rec = &trace_records[trace_next];
  rec->len = apdu_len > 255 ? 255 : (uint8_t)apdu_len;
  memset(rec->bytes, 0, sizeof(rec->bytes));
  size_t copy = apdu_len < sizeof(rec->bytes) ? apdu_len : sizeof(rec->bytes);
  memcpy(rec->bytes, apdu, copy);
  rec->sw = response_len >= 2 ? ((uint16_t)response[response_len - 2] << 8) |
                                response[response_len - 1] : 0x0000;
  trace_next = (trace_next + 1) % TRACE_RECORDS;
  if (trace_count < TRACE_RECORDS) trace_count++;
}

static bool respond_trace(uint8_t *response, size_t *response_len, size_t response_cap) {
  uint8_t object[4 + TRACE_RECORDS * (1 + TRACE_APDU_BYTES + 2)];
  size_t off = 0;
  object[off++] = 0x53;
  off += encode_len(object + off, trace_count * (1 + TRACE_APDU_BYTES + 2));
  size_t start = trace_count == TRACE_RECORDS ? trace_next : 0;
  for (size_t i = 0; i < trace_count; i++) {
    const trace_record_t *rec = &trace_records[(start + i) % TRACE_RECORDS];
    object[off++] = rec->len;
    memcpy(object + off, rec->bytes, TRACE_APDU_BYTES);
    off += TRACE_APDU_BYTES;
    object[off++] = (uint8_t)(rec->sw >> 8);
    object[off++] = (uint8_t)rec->sw;
  }
  return respond_data(object, off, response, response_len, response_cap);
}

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
    if (apdu_len < 7 + lc) return false;
    *data = apdu + 7;
    *data_len = lc;
    return true;
  }
  uint8_t lc = apdu[4];
  if (apdu_len < 5 + lc) return false;
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
  if (n == 0 || n > 2 || *off + n > buf_len) return false;
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
    if (!tlv_read_len(buf, buf_len, &off, &len) || off + len > buf_len) return false;
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

  // Private diagnostic object for development: 0x7f7f01.
  if (data_len == 5 && data[0] == 0x5c && data[1] == 0x03 &&
      data[2] == 0x7f && data[3] == 0x7f && data[4] == 0x01) {
    return respond_trace(response, response_len, response_cap);
  }

  // PIV discovery object tag: 0x7e.
  if (data_len == 3 && data[0] == 0x5c && data[1] == 0x01 && data[2] == 0x7e) {
    return respond_maybe_chunked(DISCOVERY_OBJECT, sizeof(DISCOVERY_OBJECT), apdu, apdu_len,
                                 response, response_len, response_cap);
  }

  // Card Capability Container object tag: 0x5fc107.
  if (data_len == 5 && data[0] == 0x5c && data[1] == 0x03 &&
      data[2] == 0x5f && data[3] == 0xc1 && data[4] == 0x07) {
    return respond_maybe_chunked(CCC_OBJECT, sizeof(CCC_OBJECT), apdu, apdu_len,
                                 response, response_len, response_cap);
  }

  // CHUID object tag: 0x5fc102.
  if (data_len == 5 && data[0] == 0x5c && data[1] == 0x03 &&
      data[2] == 0x5f && data[3] == 0xc1 && data[4] == 0x02) {
    return respond_maybe_chunked(CHUID_OBJECT, sizeof(CHUID_OBJECT), apdu, apdu_len,
                                 response, response_len, response_cap);
  }

  // PIV authentication certificate object: 0x5fc105.
  if (data_len == 5 && data[0] == 0x5c && data[1] == 0x03 &&
      data[2] == 0x5f && data[3] == 0xc1 && data[4] == 0x05) {
    if (cert_9a_der_len == 0) return append_sw(response, response_len, response_cap, 0x6a88);
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

  // Key management certificate object: 0x5fc10b.
  if (data_len == 5 && data[0] == 0x5c && data[1] == 0x03 &&
      data[2] == 0x5f && data[3] == 0xc1 && data[4] == 0x0b) {
    if (cert_9d_der_len == 0) return append_sw(response, response_len, response_cap, 0x6a88);
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

  // Key History object tag: 0x5fc10c.
  if (data_len == 5 && data[0] == 0x5c && data[1] == 0x03 &&
      data[2] == 0x5f && data[3] == 0xc1 && data[4] == 0x0c) {
    return respond_maybe_chunked(KEY_HISTORY_OBJECT, sizeof(KEY_HISTORY_OBJECT), apdu, apdu_len,
                                 response, response_len, response_cap);
  }

  return append_sw(response, response_len, response_cap, 0x6a88);
}

static bool handle_verify(uint8_t *response, size_t *response_len, size_t response_cap) {
  // Prototype policy: the UI PIN is only a macOS prompt compatibility shim.
  // The actual user-presence check happens before each private-key operation.
  // Real token policy should also enforce retry counters and block state.
  pin_verified_until = xTaskGetTickCount() + PIN_VERIFIED_WINDOW_TICKS;
  return append_sw(response, response_len, response_cap, 0x9000);
}

void piv_note_biometric_verified(void) {
  biometric_verified_until = xTaskGetTickCount() + BIOMETRIC_VERIFIED_WINDOW_TICKS;
}

static bool handle_general_authenticate(const uint8_t *apdu, size_t apdu_len,
                                        uint8_t *response, size_t *response_len,
                                        size_t response_cap) {
  // RSA 2048 PIV authentication and key-management key references: 9A, 9D.
  if (apdu[2] != 0x07 || !(apdu[3] == 0x9a || apdu[3] == 0x9d)) {
    return append_sw(response, response_len, response_cap, 0x6a86);
  }
  if (pin_verified_until == 0 ||
      (TickType_t)(pin_verified_until - xTaskGetTickCount()) > PIN_VERIFIED_WINDOW_TICKS) {
    return append_sw(response, response_len, response_cap, 0x6982);
  }
  bool biometric_valid = biometric_verified_until != 0 &&
                         (TickType_t)(biometric_verified_until - xTaskGetTickCount()) <=
                           BIOMETRIC_VERIFIED_WINDOW_TICKS;
  if (!biometric_valid) {
    if (!fingerprint_authorize_once()) {
      pin_verified_until = 0;
      biometric_verified_until = 0;
      return append_sw(response, response_len, response_cap, 0x6982);
    }
    biometric_verified_until = xTaskGetTickCount() + BIOMETRIC_VERIFIED_WINDOW_TICKS;
  }

  const uint8_t *data = NULL;
  size_t data_len = 0;
  if (!read_lc_data(apdu, apdu_len, &data, &data_len)) return append_sw(response, response_len, response_cap, 0x6700);

  size_t outer_off = 0;
  if (data_len < 2 || data[outer_off++] != 0x7c) return append_sw(response, response_len, response_cap, 0x6a80);
  size_t outer_len = 0;
  if (!tlv_read_len(data, data_len, &outer_off, &outer_len) || outer_off + outer_len > data_len) {
    return append_sw(response, response_len, response_cap, 0x6a80);
  }

  const uint8_t *challenge = NULL;
  size_t challenge_len = 0;
  if (!tlv_find_one(data + outer_off, outer_len, 0x81, &challenge, &challenge_len)) {
    return append_sw(response, response_len, response_cap, 0x6a80);
  }

  uint8_t sig[256];
  size_t sig_len = sizeof(sig);
  int rc = 0;
  mbedtls_pk_context *key = apdu[3] == 0x9d ? &key_mgmt_key : &auth_key;
  if (challenge_len == sizeof(sig) && mbedtls_pk_get_type(key) == MBEDTLS_PK_RSA) {
    mbedtls_rsa_context *rsa = mbedtls_pk_rsa(*key);
    rc = mbedtls_rsa_private(rsa, piv_rng, NULL, challenge, sig);
  } else {
    uint8_t hash[32];
    mbedtls_sha256(challenge, challenge_len, hash, 0);
    rc = mbedtls_pk_sign(key, MBEDTLS_MD_SHA256, hash, sizeof(hash),
                         sig, sizeof(sig), &sig_len, piv_rng, NULL);
  }
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
  mbedtls_pk_init(&auth_key);
  mbedtls_pk_init(&key_mgmt_key);
  int rc = mbedtls_pk_parse_key(&auth_key,
                                (const unsigned char *)PIV_PRIVATE_KEY_9A_PEM,
                                strlen(PIV_PRIVATE_KEY_9A_PEM) + 1,
                                NULL, 0, NULL, NULL);
  if (rc != 0) {
    ESP_LOGW(TAG, "auth private key not loaded; replace secrets.h placeholders");
  }

  rc = mbedtls_pk_parse_key(&key_mgmt_key,
                            (const unsigned char *)PIV_PRIVATE_KEY_9D_PEM,
                            strlen(PIV_PRIVATE_KEY_9D_PEM) + 1,
                            NULL, 0, NULL, NULL);
  if (rc != 0) {
    ESP_LOGW(TAG, "key-management private key not loaded; replace secrets.h placeholders");
  }

  decode_pem_cert(PIV_CERT_9A_PEM, cert_9a_der, sizeof(cert_9a_der), &cert_9a_der_len);
  decode_pem_cert(PIV_CERT_9D_PEM, cert_9d_der, sizeof(cert_9d_der), &cert_9d_der_len);
}

bool piv_handle_apdu(const uint8_t *apdu, size_t apdu_len,
                     uint8_t *response, size_t *response_len,
                     size_t response_cap) {
  *response_len = 0;
  if (apdu_len < 4) {
    bool ok = append_sw(response, response_len, response_cap, 0x6700);
    trace_apdu(apdu, apdu_len, response, *response_len);
    return ok;
  }

  uint8_t ins = apdu[1];
  uint8_t cla = apdu[0];
  bool ok = false;

  if ((cla & 0x10) && ins == 0x87) {
    const uint8_t *data = NULL;
    size_t data_len = 0;
    if (!read_lc_data(apdu, apdu_len, &data, &data_len) ||
        data_len > sizeof(chained_apdu_data)) {
      chained_apdu_data_len = 0;
      ok = append_sw(response, response_len, response_cap, 0x6700);
      trace_apdu(apdu, apdu_len, response, *response_len);
      return ok;
    }
    memcpy(chained_apdu_data, data, data_len);
    chained_apdu_data_len = data_len;
    chained_ins = ins;
    chained_p1 = apdu[2];
    chained_p2 = apdu[3];
    ok = append_sw(response, response_len, response_cap, 0x9000);
    trace_apdu(apdu, apdu_len, response, *response_len);
    return ok;
  }

  uint8_t chained_apdu[8 + sizeof(chained_apdu_data)];
  if (chained_apdu_data_len && ins == chained_ins && apdu[2] == chained_p1 && apdu[3] == chained_p2) {
    const uint8_t *data = NULL;
    size_t data_len = 0;
    if (!read_lc_data(apdu, apdu_len, &data, &data_len) ||
        chained_apdu_data_len + data_len > sizeof(chained_apdu_data)) {
      chained_apdu_data_len = 0;
      ok = append_sw(response, response_len, response_cap, 0x6700);
      trace_apdu(apdu, apdu_len, response, *response_len);
      return ok;
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
      ok = handle_select(apdu, apdu_len, response, response_len, response_cap);
      break;
    case 0xc0:
      ok = handle_get_response(apdu, apdu_len, response, response_len, response_cap);
      break;
    case 0xcb:
      ok = handle_get_data(apdu, apdu_len, response, response_len, response_cap);
      break;
    case 0x20:
      ok = handle_verify(response, response_len, response_cap);
      break;
    case 0x87:
      ok = handle_general_authenticate(apdu, apdu_len, response, response_len, response_cap);
      break;
    default:
      ok = append_sw(response, response_len, response_cap, 0x6d00);
      break;
  }
  trace_apdu(apdu, apdu_len, response, *response_len);
  return ok;
}

#include <Arduino.h>
#include "USB.h"
#include "USBHIDKeyboard.h"
#include "mbedtls/aes.h"
#include "mbedtls/md.h"
#include "esp_system.h"
#include "secrets.h"

#if !ARDUINO_USB_MODE && !ARDUINO_USB_CDC_ON_BOOT
USBCDC USBSerial;
#endif

static const uint32_t UART_BAUD = 57600;
static const int FP_TX_PIN = 43;
static const int FP_RX_PIN = 44;
static const int FP_INT_PIN = 2;
static const int INT_ACTIVE_VALUE = 1;
static const bool USE_INT_PIN = true;
static const uint16_t START_SLOT = 1;
static const uint16_t END_SLOT = 5;
static const uint32_t RESULT_HOLD_MS = 500;
static const uint32_t HELPER_TIMEOUT_MS = 6000;
static const bool TYPE_RETURN_AFTER_PASSWORD = true;
static const bool ENABLE_TEST_COMMANDS = false;
static const bool DEBUG_FP_PACKETS = false;

static const uint8_t FP_LED_GREEN = 0x02;
static const uint8_t FP_LED_WHITE = 0x07;
static const uint8_t FP_LED_RED = 0x04;
// This unit showed visible purple at 0x03 in previous ESP testing.
static const uint8_t FP_LED_PURPLE = 0x03;
static const uint8_t FP_LED_FUNC_STEADY = 3;

USBHIDKeyboard Keyboard;
HardwareSerial Finger(1);
uint8_t currentLed = 0xff;
uint32_t eventCounter = 0;
uint8_t lastScanStatus = 0; // 0=no finger, 1=match, 2=no match/error after finger image
String serialCommand;

static int hexVal(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  return -1;
}

static bool fromHex(const String &hex, uint8_t *out, size_t outLen) {
  if (hex.length() != outLen * 2) return false;
  for (size_t i = 0; i < outLen; i++) {
    int hi = hexVal(hex[i * 2]);
    int lo = hexVal(hex[i * 2 + 1]);
    if (hi < 0 || lo < 0) return false;
    out[i] = (uint8_t)((hi << 4) | lo);
  }
  return true;
}

static String toHex(const uint8_t *data, size_t len) {
  static const char *digits = "0123456789abcdef";
  String s;
  s.reserve(len * 2);
  for (size_t i = 0; i < len; i++) {
    s += digits[data[i] >> 4];
    s += digits[data[i] & 0x0f];
  }
  return s;
}

static void secureWipe(uint8_t *data, size_t len) {
  volatile uint8_t *p = data;
  while (len--) *p++ = 0;
}

static void hmacSha256(const uint8_t *key, size_t keyLen, const uint8_t *data, size_t dataLen, uint8_t out[32]) {
  const mbedtls_md_info_t *info = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
  mbedtls_md_hmac(info, key, keyLen, data, dataLen, out);
}

static String hmacHex(const String &message) {
  uint8_t mac[32];
  hmacSha256(PAIRING_KEY, sizeof(PAIRING_KEY), (const uint8_t *)message.c_str(), message.length(), mac);
  String out = toHex(mac, sizeof(mac));
  secureWipe(mac, sizeof(mac));
  return out;
}

static void deriveSessionKey(const String &nonceHex, uint8_t key[32]) {
  String material = "SESSION|" + nonceHex;
  hmacSha256(PAIRING_KEY, sizeof(PAIRING_KEY), (const uint8_t *)material.c_str(), material.length(), key);
}

static bool randomBytes(uint8_t *out, size_t len) {
  esp_fill_random(out, len);
  return true;
}

static uint16_t fpChecksum(uint8_t packetId, const uint8_t *payload, size_t payloadLen) {
  uint16_t length = payloadLen + 2;
  uint32_t total = packetId + (length >> 8) + (length & 0xff);
  for (size_t i = 0; i < payloadLen; i++) total += payload[i];
  return (uint16_t)total;
}

static bool fpCommand(uint8_t instruction, const uint8_t *params, size_t paramLen,
                      uint8_t *confirm, uint8_t *data, size_t *dataLen, uint32_t timeoutMs) {
  while (Finger.available()) Finger.read();
  size_t dataCapacity = (data && dataLen) ? *dataLen : 0;
  size_t outLen = 0;
  bool sawAck = false;
  uint32_t postAckUntil = 0;
  if (data && dataLen) *dataLen = 0;

  uint8_t payload[32];
  if (paramLen + 1 > sizeof(payload)) return false;
  payload[0] = instruction;
  if (paramLen) memcpy(payload + 1, params, paramLen);
  size_t payloadLen = paramLen + 1;
  uint16_t length = payloadLen + 2;
  uint16_t sum = fpChecksum(0x01, payload, payloadLen);

  uint8_t header[] = {0xef, 0x01, 0xff, 0xff, 0xff, 0xff, 0x01,
                      (uint8_t)(length >> 8), (uint8_t)(length & 0xff)};
  Finger.write(header, sizeof(header));
  Finger.write(payload, payloadLen);
  Finger.write((uint8_t)(sum >> 8));
  Finger.write((uint8_t)(sum & 0xff));

  uint8_t response[96];
  size_t pos = 0;
  uint32_t start = millis();
  while (millis() - start < timeoutMs) {
    while (Finger.available() && pos < sizeof(response)) {
      response[pos++] = (uint8_t)Finger.read();
      while (pos >= 2 && !(response[0] == 0xef && response[1] == 0x01)) {
        memmove(response, response + 1, --pos);
      }
      if (pos >= 9 && response[0] == 0xef && response[1] == 0x01) {
        uint8_t packetId = response[6];
        uint16_t respLen = ((uint16_t)response[7] << 8) | response[8];
        size_t expected = 9 + respLen;
        if (pos >= expected) {
          if (packetId == 0x07) {
            *confirm = response[9];
            sawAck = true;
            size_t actualDataLen = respLen > 3 ? respLen - 3 : 0;
            if (DEBUG_FP_PACKETS && (instruction == 0x04 || instruction == 0x03)) {
              Serial.printf("FP_ACK %02x len=%u confirm=%u data=%s\n",
                            instruction, actualDataLen, *confirm,
                            actualDataLen ? toHex(response + 10, actualDataLen).c_str() : "-");
              Serial.flush();
            }
            if (data && dataLen && actualDataLen) {
              size_t copyLen = actualDataLen;
              if (copyLen > dataCapacity - outLen) copyLen = dataCapacity - outLen;
              memcpy(data + outLen, response + 10, copyLen);
              outLen += copyLen;
              *dataLen = outLen;
            }
            if (*confirm != 0x00 || !data || !dataLen || outLen >= dataCapacity) return true;
            postAckUntil = millis() + 120;
          } else if (packetId == 0x02 && data && dataLen) {
            size_t actualDataLen = respLen > 2 ? respLen - 2 : 0;
            if (DEBUG_FP_PACKETS && (instruction == 0x04 || instruction == 0x03)) {
              Serial.printf("FP_DATA %02x len=%u data=%s\n",
                            instruction, actualDataLen,
                            actualDataLen ? toHex(response + 9, actualDataLen).c_str() : "-");
              Serial.flush();
            }
            if (actualDataLen) {
              size_t copyLen = actualDataLen;
              if (copyLen > dataCapacity - outLen) copyLen = dataCapacity - outLen;
              memcpy(data + outLen, response + 9, copyLen);
              outLen += copyLen;
              *dataLen = outLen;
            }
            if (sawAck && outLen >= dataCapacity) return true;
          }
          size_t remaining = pos - expected;
          if (remaining) memmove(response, response + expected, remaining);
          pos = remaining;
        }
      }
    }
    if (sawAck && postAckUntil && millis() > postAckUntil) return true;
    delay(5);
  }
  return sawAck;
}

static void setAura(uint8_t color) {
  if (color == currentLed) return;
  uint8_t params[] = {FP_LED_FUNC_STEADY, color, color, 0};
  uint8_t confirm = 0xff;
  fpCommand(0x3c, params, sizeof(params), &confirm, nullptr, nullptr, 1000);
  currentLed = color;
}

static bool fingerPresent() {
  if (!USE_INT_PIN) return true;
  return digitalRead(FP_INT_PIN) == INT_ACTIVE_VALUE;
}

static bool verifySensor() {
  uint8_t params[] = {0x00, 0x00, 0x00, 0x00};
  uint8_t confirm = 0xff;
  return fpCommand(0x13, params, sizeof(params), &confirm, nullptr, nullptr, 2000) && confirm == 0x00;
}

static bool scanMatch(uint16_t *matchId, uint16_t *score) {
  lastScanStatus = 0;
  setAura(FP_LED_WHITE);
  uint8_t confirm = 0xff;
  if (!fpCommand(0x01, nullptr, 0, &confirm, nullptr, nullptr, 1000) || confirm != 0x00) {
    if (USE_INT_PIN) {
      Serial.printf("GENIMG_FAIL %u\n", confirm);
      Serial.flush();
    }
    return false;
  }
  lastScanStatus = 2;

  setAura(FP_LED_WHITE);
  uint8_t img2tz[] = {0x01};
  if (!fpCommand(0x02, img2tz, sizeof(img2tz), &confirm, nullptr, nullptr, 2000) || confirm != 0x00) {
    Serial.printf("IMG2TZ_FAIL %u\n", confirm);
    Serial.flush();
    return false;
  }

  uint16_t count = END_SLOT - START_SLOT + 1;
  uint8_t searchParams[] = {
    0x01,
    (uint8_t)(START_SLOT >> 8), (uint8_t)(START_SLOT & 0xff),
    (uint8_t)(count >> 8), (uint8_t)(count & 0xff)
  };
  uint8_t searchData[4];
  size_t searchLen = sizeof(searchData);
  confirm = 0xff;
  if (fpCommand(0x04, searchParams, sizeof(searchParams), &confirm, searchData, &searchLen, 2000)) {
    if (confirm == 0x00 && searchLen == sizeof(searchData)) {
      uint16_t searchScore = ((uint16_t)searchData[2] << 8) | searchData[3];
      if (searchScore > 0) {
        *matchId = ((uint16_t)searchData[0] << 8) | searchData[1];
        *score = searchScore;
        lastScanStatus = 1;
        return true;
      }
    }
    Serial.printf("SEARCH_FAIL %u %u\n", confirm, searchLen);
    Serial.flush();
  } else {
    Serial.println("SEARCH_CMD_FAIL");
    Serial.flush();
  }

  for (uint16_t slot = START_SLOT; slot <= END_SLOT; slot++) {
    uint8_t loadParams[] = {0x02, (uint8_t)(slot >> 8), (uint8_t)(slot & 0xff)};
    confirm = 0xff;
    if (!fpCommand(0x07, loadParams, sizeof(loadParams), &confirm, nullptr, nullptr, 1000) ||
        confirm != 0x00) {
      Serial.printf("LOAD_FAIL %u %u\n", slot, confirm);
      Serial.flush();
      continue;
    }

    uint8_t data[2];
    size_t dataLen = sizeof(data);
    confirm = 0xff;
    if (!fpCommand(0x03, nullptr, 0, &confirm, data, &dataLen, 1000)) {
      Serial.printf("MATCH_CMD_FAIL %u\n", slot);
      Serial.flush();
      continue;
    }
    if (confirm == 0x00 && dataLen == sizeof(data)) {
      uint16_t matchScore = ((uint16_t)data[0] << 8) | data[1];
      if (matchScore > 0) {
        *matchId = slot;
        *score = matchScore;
        lastScanStatus = 1;
        return true;
      }
    }
    Serial.printf("MATCH_FAIL %u %u %u\n", slot, confirm, dataLen);
    Serial.flush();
  }
  Serial.println("MATCH_LOOP_NO_HIT");
  Serial.flush();
  return false;
}

static bool parseToken(const String &line, int index, String *token) {
  int start = 0;
  int current = 0;
  while (start < (int)line.length()) {
    int end = line.indexOf(' ', start);
    if (end < 0) end = line.length();
    if (current == index) {
      *token = line.substring(start, end);
      return true;
    }
    current++;
    start = end + 1;
  }
  return false;
}

static bool readHelperLine(String *line, uint32_t timeoutMs) {
  line->remove(0);
  uint32_t start = millis();
  while (millis() - start < timeoutMs) {
    while (Serial.available()) {
      char c = (char)Serial.read();
      if (c == '\n') {
        line->trim();
        return line->length() > 0;
      }
      if (c != '\r' && line->length() < 600) *line += c;
    }
    delay(5);
  }
  return false;
}

static bool decryptPassword(const String &nonceHex, const String &line, uint8_t *password, size_t *passwordLen) {
  String kind, nonce, ivHex, ctHex, macHex;
  if (!parseToken(line, 0, &kind) || !parseToken(line, 1, &nonce) ||
      !parseToken(line, 2, &ivHex) || !parseToken(line, 3, &ctHex) ||
      !parseToken(line, 4, &macHex)) return false;
  if (kind != "PW" || nonce != nonceHex) return false;
  String expected = hmacHex("PW|" + nonce + "|" + ivHex + "|" + ctHex);
  if (!expected.equalsIgnoreCase(macHex)) return false;
  if ((ctHex.length() % 2) != 0 || ctHex.length() / 2 > *passwordLen) return false;

  uint8_t iv[16];
  if (!fromHex(ivHex, iv, sizeof(iv))) return false;
  size_t ctLen = ctHex.length() / 2;
  uint8_t ciphertext[160];
  if (ctLen > sizeof(ciphertext) || !fromHex(ctHex, ciphertext, ctLen)) return false;

  uint8_t key[32];
  deriveSessionKey(nonce, key);
  mbedtls_aes_context aes;
  mbedtls_aes_init(&aes);
  mbedtls_aes_setkey_enc(&aes, key, 256);
  size_t ncOff = 0;
  uint8_t streamBlock[16] = {0};
  int rc = mbedtls_aes_crypt_ctr(&aes, ctLen, &ncOff, iv, streamBlock, ciphertext, password);
  mbedtls_aes_free(&aes);
  secureWipe(key, sizeof(key));
  secureWipe(ciphertext, sizeof(ciphertext));
  secureWipe(streamBlock, sizeof(streamBlock));
  if (rc != 0) return false;
  *passwordLen = ctLen;
  return true;
}

static void typeAscii(const uint8_t *data, size_t len) {
  for (size_t i = 0; i < len; i++) Keyboard.write(data[i]);
  if (TYPE_RETURN_AFTER_PASSWORD) Keyboard.write(KEY_RETURN);
}

static void handleSerialCommands() {
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\r') continue;
    if (c == '\n') {
      serialCommand.trim();
      if (serialCommand == "PING") {
        Serial.println("PONG");
      } else if (ENABLE_TEST_COMMANDS && serialCommand == "TYPE_TEST") {
        const uint8_t test[] = "HID_TEST_OK";
        typeAscii(test, sizeof(test) - 1);
        Serial.println("TYPE_TEST_DONE");
      } else if (serialCommand.length()) {
        Serial.print("UNKNOWN_CMD ");
        Serial.println(serialCommand);
      }
      Serial.flush();
      serialCommand = "";
    } else if (serialCommand.length() < 96) {
      serialCommand += c;
    }
  }
}

static bool requestAndTypePassword(uint16_t matchId, uint16_t score) {
  uint8_t nonceBytes[16];
  if (!randomBytes(nonceBytes, sizeof(nonceBytes))) {
    Serial.println("ERR rng");
    return false;
  }
  String nonce = toHex(nonceBytes, sizeof(nonceBytes));
  secureWipe(nonceBytes, sizeof(nonceBytes));

  eventCounter++;
  String counter = String(eventCounter);
  String slot = String(matchId);
  String scoreStr = String(score);
  String mac = hmacHex("EV|" + nonce + "|" + counter + "|" + slot + "|" + scoreStr);
  Serial.print("EV ");
  Serial.print(nonce);
  Serial.print(" ");
  Serial.print(counter);
  Serial.print(" ");
  Serial.print(slot);
  Serial.print(" ");
  Serial.print(scoreStr);
  Serial.print(" ");
  Serial.println(mac);
  Serial.println("EV_SENT");
  Serial.flush();

  String line;
  if (!readHelperLine(&line, HELPER_TIMEOUT_MS)) return false;
  uint8_t password[160];
  size_t passwordLen = sizeof(password);
  bool ok = decryptPassword(nonce, line, password, &passwordLen);
  if (ok) {
    typeAscii(password, passwordLen);
  }
  secureWipe(password, sizeof(password));
  return ok;
}

void setup() {
  pinMode(FP_INT_PIN, INPUT);
  Serial.begin(115200);
  Keyboard.begin();
  USB.begin();
  Finger.begin(UART_BAUD, SERIAL_8N1, FP_RX_PIN, FP_TX_PIN);
  delay(1500);

  Serial.println("BOOT tinyTouch HID");
  if (!verifySensor()) {
    Serial.println("ERR fingerprint_verify");
    setAura(FP_LED_RED);
  } else {
    Serial.println("READY");
    setAura(FP_LED_PURPLE);
  }
}

void loop() {
  handleSerialCommands();

  if (!fingerPresent()) {
    setAura(FP_LED_PURPLE);
    delay(25);
    return;
  }

  setAura(FP_LED_WHITE);
  uint16_t matchId = 0;
  uint16_t score = 0;
  if (scanMatch(&matchId, &score)) {
    Serial.println("TOUCH");
    Serial.flush();
    setAura(FP_LED_GREEN);
    Serial.printf("MATCH %u %u\n", matchId, score);
    bool typed = requestAndTypePassword(matchId, score);
    Serial.println(typed ? "TYPED" : "ERR helper_or_crypto");
    if (!typed) setAura(FP_LED_RED);
  } else {
    if (USE_INT_PIN || lastScanStatus == 2) {
      setAura(FP_LED_RED);
      Serial.println("NO_MATCH");
    } else {
      setAura(FP_LED_PURPLE);
    }
  }

  if (USE_INT_PIN || lastScanStatus != 0) delay(RESULT_HOLD_MS);
  setAura(FP_LED_PURPLE);
  if (USE_INT_PIN) {
    while (fingerPresent()) delay(25);
  }
  if (!USE_INT_PIN) delay(200);
}

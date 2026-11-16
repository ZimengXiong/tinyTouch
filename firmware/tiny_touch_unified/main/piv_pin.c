#include "piv_pin.h"

#include <string.h>

#include "esp_random.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "mbedtls/sha256.h"
#include "nvs.h"

#define PIN_NAMESPACE "piv_keys"
#define PIN_KEY "pin_policy"
#define PIN_POLICY_VERSION 1
#define PIN_SALT_SIZE 16
#define PIN_HASH_SIZE 32
#define PIN_LOCKOUT_LEVEL_LIMIT 5

typedef struct {
  uint8_t version;
  uint8_t retries_left;
  // Failed searches since the last match, saved before each search runs.
  // Reaching the limit makes the PIN required.
  uint8_t match_failures;
  // Set when a search starts after the limit was reached, so a later match
  // cannot cancel it the way it cancels the pending fifth attempt.
  uint8_t required;
  // Lockouts since both factors last succeeded. Each one lengthens the wait.
  uint8_t lockouts;
  uint8_t salt[PIN_SALT_SIZE];
  uint8_t hash[PIN_HASH_SIZE];
} stored_pin_t;

const uint8_t PIV_PIN_DUMMY[PIV_PIN_MAX_LENGTH] = {
  '1', '1', '1', '1', '1', '1', 0xff, 0xff,
};

static SemaphoreHandle_t pin_mutex;
static stored_pin_t pin;
static bool pin_enabled;
// A record that exists but cannot be parsed. Only a factory reset clears it.
static bool pin_unreadable;
// RAM only. Losing power ends the wait and restarts a lockout from the full
// duration, so neither can be shortened by unplugging.
static bool awaiting_finger;
// Set when the current search spent a retry inside the window. A search that
// began before the PIN was accepted cannot complete it for free.
static bool search_paid;
static TickType_t pin_accepted_at;
static TickType_t lockout_started;

static void lock(void) { xSemaphoreTake(pin_mutex, portMAX_DELAY); }
static void unlock(void) { xSemaphoreGive(pin_mutex); }

static void wipe(void *data, size_t length) {
  volatile uint8_t *cursor = data;
  while (length--) *cursor++ = 0;
}

static bool valid_stored(const stored_pin_t *value) {
  return value->version == PIN_POLICY_VERSION &&
         value->retries_left <= PIV_PIN_RETRY_LIMIT &&
         value->match_failures <= PIV_PIN_MATCH_FAILURE_LIMIT && value->required <= 1 &&
         value->lockouts <= PIN_LOCKOUT_LEVEL_LIMIT &&
         (value->retries_left > 0 || value->lockouts > 0);
}

static TickType_t lockout_ticks(uint8_t level) {
  static const uint16_t seconds[PIN_LOCKOUT_LEVEL_LIMIT] = PIV_PIN_LOCKOUT_SECONDS;
  if (level == 0) level = 1;
  if (level > PIN_LOCKOUT_LEVEL_LIMIT) level = PIN_LOCKOUT_LEVEL_LIMIT;
  return pdMS_TO_TICKS((uint32_t)seconds[level - 1] * 1000);
}

static TickType_t lockout_remaining_locked(void) {
  if (!pin_enabled || pin_unreadable || pin.retries_left > 0) return 0;
  TickType_t elapsed = xTaskGetTickCount() - lockout_started;
  TickType_t duration = lockout_ticks(pin.lockouts);
  return elapsed < duration ? duration - elapsed : 0;
}

static bool save_locked(const stored_pin_t *candidate) {
  nvs_handle_t handle;
  if (nvs_open(PIN_NAMESPACE, NVS_READWRITE, &handle) != ESP_OK) return false;
  esp_err_t result = nvs_set_blob(handle, PIN_KEY, candidate, sizeof(*candidate));
  if (result == ESP_OK) result = nvs_commit(handle);
  nvs_close(handle);
  if (result != ESP_OK) return false;
  pin = *candidate;
  pin_enabled = true;
  return true;
}

static bool erase_locked(void) {
  nvs_handle_t handle;
  if (nvs_open(PIN_NAMESPACE, NVS_READWRITE, &handle) != ESP_OK) return false;
  esp_err_t result = nvs_erase_key(handle, PIN_KEY);
  if (result == ESP_ERR_NVS_NOT_FOUND) result = ESP_OK;
  if (result == ESP_OK) result = nvs_commit(handle);
  nvs_close(handle);
  if (result != ESP_OK) return false;
  wipe(&pin, sizeof(pin));
  pin_enabled = false;
  awaiting_finger = false;
  return true;
}

static bool awaiting_locked(void) {
  if (awaiting_finger && (TickType_t)(xTaskGetTickCount() - pin_accepted_at) >=
                             pdMS_TO_TICKS(PIV_PIN_FINGER_WINDOW_MS))
    awaiting_finger = false;
  return pin_enabled && awaiting_finger;
}

static bool restore_locked(void) {
  awaiting_finger = false;
  stored_pin_t candidate = pin;
  candidate.retries_left = PIV_PIN_RETRY_LIMIT;
  candidate.match_failures = 0;
  candidate.required = 0;
  candidate.lockouts = 0;
  return save_locked(&candidate);
}

// Spends one attempt, saved before the attempt runs. After a lockout ends one
// attempt is available, and using it starts the next, longer lockout. RAM
// keeps the spend even if the write fails; callers that must not proceed
// without it restore *before.
static bool spend_locked(stored_pin_t *before) {
  *before = pin;
  if (before->retries_left == 0) before->retries_left = 1;
  stored_pin_t candidate = *before;
  candidate.retries_left--;
  if (candidate.retries_left == 0) {
    if (candidate.lockouts < PIN_LOCKOUT_LEVEL_LIMIT) candidate.lockouts++;
    // However the retries ran out, only the PIN then a finger clears it.
    candidate.required = 1;
  }
  bool saved = save_locked(&candidate);
  pin = candidate;
  if (pin.retries_left == 0) lockout_started = xTaskGetTickCount();
  return saved;
}

// Accepts 6-8 ASCII digits followed only by 0xff padding.
static bool valid_piv_format(const uint8_t value[PIV_PIN_MAX_LENGTH]) {
  size_t length = 0;
  while (length < PIV_PIN_MAX_LENGTH && value[length] >= '0' && value[length] <= '9') length++;
  if (length < PIV_PIN_MIN_LENGTH) return false;
  for (size_t i = length; i < PIV_PIN_MAX_LENGTH; i++) {
    if (value[i] != 0xff) return false;
  }
  return true;
}

static bool pad_console_pin(const char *text, uint8_t out[PIV_PIN_MAX_LENGTH]) {
  memset(out, 0xff, PIV_PIN_MAX_LENGTH);
  if (!text) return false;
  size_t length = strlen(text);
  if (length < PIV_PIN_MIN_LENGTH || length > PIV_PIN_MAX_LENGTH) return false;
  memcpy(out, text, length);
  return valid_piv_format(out);
}

static void hash_pin(const uint8_t salt[PIN_SALT_SIZE], const uint8_t value[PIV_PIN_MAX_LENGTH],
                     uint8_t out[PIN_HASH_SIZE]) {
  uint8_t material[PIN_SALT_SIZE + PIV_PIN_MAX_LENGTH];
  memcpy(material, salt, PIN_SALT_SIZE);
  memcpy(material + PIN_SALT_SIZE, value, PIV_PIN_MAX_LENGTH);
  mbedtls_sha256(material, sizeof(material), out, 0);
  wipe(material, sizeof(material));
}

static bool equal_constant_time(const uint8_t *left, const uint8_t *right, size_t length) {
  uint8_t difference = 0;
  for (size_t i = 0; i < length; i++) difference |= left[i] ^ right[i];
  return difference == 0;
}

// Spends an attempt and compares. *before is the record to restore if the
// caller refunds a correct PIN.
static piv_pin_result_t check_locked(const uint8_t value[PIV_PIN_MAX_LENGTH],
                                     stored_pin_t *before) {
  if (!pin_enabled) return PIV_PIN_INVALID;
  if (pin_unreadable) return PIV_PIN_BLOCKED;
  if (lockout_remaining_locked()) return PIV_PIN_LOCKED;
  // The dummy PIN is public and is never a valid custom PIN, so it cannot
  // reveal anything. Rejecting it without a retry keeps auto-typing harmless.
  if (!valid_piv_format(value) || memcmp(value, PIV_PIN_DUMMY, sizeof(PIV_PIN_DUMMY)) == 0)
    return PIV_PIN_WRONG;
  if (!spend_locked(before)) {
    pin = *before;
    return PIV_PIN_STORAGE_ERROR;
  }
  uint8_t digest[PIN_HASH_SIZE];
  hash_pin(pin.salt, value, digest);
  bool matches = equal_constant_time(digest, pin.hash, sizeof(digest));
  wipe(digest, sizeof(digest));
  if (!matches) return pin.retries_left == 0 ? PIV_PIN_LOCKED : PIV_PIN_WRONG;
  return PIV_PIN_OK;
}

// Console commands run after fingerprint authorization, so a correct PIN
// completes both factors at once.
static piv_pin_result_t verify_locked(const uint8_t value[PIV_PIN_MAX_LENGTH]) {
  stored_pin_t before;
  piv_pin_result_t result = check_locked(value, &before);
  if (result == PIV_PIN_OK && !restore_locked()) result = PIV_PIN_STORAGE_ERROR;
  wipe(&before, sizeof(before));
  return result;
}

void piv_pin_init(void) {
  if (!pin_mutex) pin_mutex = xSemaphoreCreateMutex();
  configASSERT(pin_mutex);
  lock();
  wipe(&pin, sizeof(pin));
  pin_enabled = false;
  pin_unreadable = false;
  awaiting_finger = false;
  lockout_started = xTaskGetTickCount();
  nvs_handle_t handle;
  esp_err_t opened = nvs_open(PIN_NAMESPACE, NVS_READONLY, &handle);
  esp_err_t read = opened;
  stored_pin_t loaded = {0};
  size_t length = sizeof(loaded);
  if (opened == ESP_OK) {
    read = nvs_get_blob(handle, PIN_KEY, &loaded, &length);
    nvs_close(handle);
  }
  if (read == ESP_OK && length == sizeof(loaded) && loaded.version == PIN_POLICY_VERSION) {
    pin = loaded;
    pin_enabled = true;
    if (!valid_stored(&loaded)) {
      // Out-of-range counters must not fall back to fingerprint-only
      // signing. Keep the PIN hash and demand it.
      if (pin.retries_left > PIV_PIN_RETRY_LIMIT) pin.retries_left = PIV_PIN_RETRY_LIMIT;
      if (pin.lockouts > PIN_LOCKOUT_LEVEL_LIMIT) pin.lockouts = PIN_LOCKOUT_LEVEL_LIMIT;
      if (pin.retries_left == 0 && pin.lockouts == 0) pin.lockouts = 1;
      pin.match_failures = PIV_PIN_MATCH_FAILURE_LIMIT;
      pin.required = 1;
    }
  } else if (read != ESP_ERR_NVS_NOT_FOUND) {
    // A record that exists but cannot be read, including one from a newer
    // format, may hold a PIN. Treat it as blocked rather than as no PIN.
    pin.version = PIN_POLICY_VERSION;
    pin_enabled = true;
    pin_unreadable = true;
  }
  wipe(&loaded, sizeof(loaded));
  unlock();
}

piv_pin_state_t piv_pin_state(void) {
  lock();
  // A lockout implies the fallback: after it ends, the PIN is still needed.
  piv_pin_state_t state = !pin_enabled ? PIV_PIN_STATE_OFF :
                          pin_unreadable ? PIV_PIN_STATE_BLOCKED :
                          lockout_remaining_locked() ? PIV_PIN_STATE_LOCKED :
                          pin.required || pin.retries_left == 0 ||
                                  pin.match_failures >= PIV_PIN_MATCH_FAILURE_LIMIT
                              ? PIV_PIN_STATE_REQUIRED : PIV_PIN_STATE_ON;
  unlock();
  return state;
}

const char *piv_pin_state_name(void) {
  switch (piv_pin_state()) {
    case PIV_PIN_STATE_ON: return "on";
    case PIV_PIN_STATE_REQUIRED: return "required";
    case PIV_PIN_STATE_LOCKED: return "locked";
    case PIV_PIN_STATE_BLOCKED: return "blocked";
    default: return "off";
  }
}

uint32_t piv_pin_lockout_seconds_left(void) {
  lock();
  TickType_t remaining = lockout_remaining_locked();
  unlock();
  return (uint32_t)((remaining * portTICK_PERIOD_MS + 999) / 1000);
}

uint8_t piv_pin_retries_left(void) {
  lock();
  uint8_t value = !pin_enabled ? PIV_PIN_RETRY_LIMIT : pin.retries_left;
  // After a lockout ends one attempt is available. Reporting 0 would make the
  // host treat the PIN as blocked and stop offering the prompt.
  if (value == 0 && !pin_unreadable && !lockout_remaining_locked()) value = 1;
  unlock();
  return value;
}

bool piv_pin_required(void) {
  piv_pin_state_t state = piv_pin_state();
  return state == PIV_PIN_STATE_REQUIRED || state == PIV_PIN_STATE_LOCKED ||
         state == PIV_PIN_STATE_BLOCKED;
}

piv_pin_result_t piv_pin_verify_piv(const uint8_t value[PIV_PIN_MAX_LENGTH]) {
  lock();
  stored_pin_t before;
  piv_pin_result_t result = check_locked(value, &before);
  if (result == PIV_PIN_OK) {
    // A correct PIN costs nothing, so an unreadable finger or a host that
    // repeats VERIFY cannot lock the card. Wrong fingers pay instead.
    (void)save_locked(&before);
    awaiting_finger = true;
    search_paid = false;
    pin_accepted_at = xTaskGetTickCount();
  }
  wipe(&before, sizeof(before));
  unlock();
  return result;
}

bool piv_pin_awaiting_finger(void) {
  lock();
  bool value = awaiting_locked();
  unlock();
  return value;
}

piv_pin_result_t piv_pin_set(const char *current, const char *next) {
  uint8_t padded_next[PIV_PIN_MAX_LENGTH];
  uint8_t padded_current[PIV_PIN_MAX_LENGTH];
  if (!pad_console_pin(next, padded_next) ||
      memcmp(padded_next, PIV_PIN_DUMMY, sizeof(PIV_PIN_DUMMY)) == 0) {
    wipe(padded_next, sizeof(padded_next));
    return PIV_PIN_INVALID;
  }
  lock();
  piv_pin_result_t result = PIV_PIN_OK;
  if (pin_enabled) {
    result = !current ? PIV_PIN_INVALID :
             pad_console_pin(current, padded_current) ? verify_locked(padded_current) :
             PIV_PIN_WRONG;
  } else if (current) {
    result = PIV_PIN_INVALID;
  }
  if (result == PIV_PIN_OK) {
    stored_pin_t candidate = {
      .version = PIN_POLICY_VERSION, .retries_left = PIV_PIN_RETRY_LIMIT,
    };
    esp_fill_random(candidate.salt, sizeof(candidate.salt));
    hash_pin(candidate.salt, padded_next, candidate.hash);
    if (!save_locked(&candidate)) result = PIV_PIN_STORAGE_ERROR;
    wipe(&candidate, sizeof(candidate));
  }
  unlock();
  wipe(padded_next, sizeof(padded_next));
  wipe(padded_current, sizeof(padded_current));
  return result;
}

piv_pin_result_t piv_pin_clear(const char *current) {
  uint8_t padded[PIV_PIN_MAX_LENGTH];
  lock();
  piv_pin_result_t result = !pin_enabled ? PIV_PIN_INVALID :
                            pad_console_pin(current, padded) ? verify_locked(padded) :
                            PIV_PIN_WRONG;
  if (result == PIV_PIN_OK && !erase_locked()) result = PIV_PIN_STORAGE_ERROR;
  unlock();
  wipe(padded, sizeof(padded));
  return result;
}

piv_pin_result_t piv_pin_unlock(const char *current) {
  uint8_t padded[PIV_PIN_MAX_LENGTH];
  lock();
  piv_pin_result_t result = !pin_enabled ? PIV_PIN_INVALID :
                            pad_console_pin(current, padded) ? verify_locked(padded) :
                            PIV_PIN_WRONG;
  unlock();
  wipe(padded, sizeof(padded));
  return result;
}

void piv_pin_note_attempt(void) {
  lock();
  if (awaiting_locked()) {
    // Each search after a correct PIN spends a retry before it runs, so
    // someone who knows the PIN gets a bounded number of fingers. A match
    // restores them. A correct PIN always leaves at least one retry, so none
    // left means searches in this window used them all: the wait ends, and
    // the PIN is needed again once the lockout is over.
    stored_pin_t before;
    if (pin.retries_left == 0) awaiting_finger = false;
    else (void)spend_locked(&before);
    search_paid = awaiting_finger;
    wipe(&before, sizeof(before));
  } else if (pin_enabled && !pin.required && pin.retries_left > 0) {
    stored_pin_t candidate = pin;
    if (candidate.match_failures >= PIV_PIN_MATCH_FAILURE_LIMIT) candidate.required = 1;
    else candidate.match_failures++;
    // The search still runs if the write fails, so a storage fault cannot lock
    // out the enrolled user. The count still holds until power is removed.
    if (!save_locked(&candidate)) pin = candidate;
  }
  unlock();
}

bool piv_pin_note_match(void) {
  lock();
  bool completed = false;
  if (awaiting_locked()) {
    // Failed matches inside the window do not cancel it; each one has
    // already spent a retry in piv_pin_note_attempt.
    if (search_paid) completed = restore_locked();
    search_paid = false;
  } else if (pin_enabled && !pin.required && pin.retries_left > 0 && pin.match_failures != 0) {
    stored_pin_t candidate = pin;
    candidate.match_failures = 0;
    (void)save_locked(&candidate);
  }
  unlock();
  return completed;
}

bool piv_pin_reset(void) {
  lock();
  bool ok = erase_locked();
  unlock();
  return ok;
}

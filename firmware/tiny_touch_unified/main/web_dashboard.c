#include "web_dashboard.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifndef TINYTOUCH_FIRMWARE_VERSION
#define TINYTOUCH_FIRMWARE_VERSION "development"
#endif

#include "config_console.h"
#include "dashboard_api.h"
#include "device_config.h"
#include "esp_http_server.h"
#include "esp_log.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "fingerprint.h"
#include "nvs.h"
#include "usb_ncm.h"

extern const uint8_t index_html_gz_start[] asm("_binary_index_html_gz_start");
extern const uint8_t index_html_gz_end[] asm("_binary_index_html_gz_end");

static const char *TAG = "web_dash";
static char slot_labels[DASHBOARD_MAX_SLOTS][DASHBOARD_LABEL_MAX];

static void fill_random(void *buf, size_t len) {
  esp_fill_random(buf, len);
}

static int64_t now_us(void) {
  return esp_timer_get_time();
}

static void labels_load(void) {
  memset(slot_labels, 0, sizeof(slot_labels));
  nvs_handle_t handle;
  if (nvs_open("dashboard", NVS_READONLY, &handle) != ESP_OK) return;
  for (int i = 0; i < DASHBOARD_MAX_SLOTS; i++) {
    char key[4];
    snprintf(key, sizeof(key), "l%d", i + 1);
    size_t len = sizeof(slot_labels[i]);
    nvs_get_str(handle, key, slot_labels[i], &len);
  }
  nvs_close(handle);
}

static bool labels_save_slot(int slot, const char *label) {
  if (slot < 1 || slot > DASHBOARD_MAX_SLOTS) return false;
  nvs_handle_t handle;
  if (nvs_open("dashboard", NVS_READWRITE, &handle) != ESP_OK) return false;
  char key[4];
  snprintf(key, sizeof(key), "l%d", slot);
  esp_err_t result = label[0] ? nvs_set_str(handle, key, label) : nvs_erase_key(handle, key);
  if (result == ESP_ERR_NVS_NOT_FOUND) result = ESP_OK;
  if (result == ESP_OK) result = nvs_commit(handle);
  nvs_close(handle);
  if (result == ESP_OK) {
    snprintf(slot_labels[slot - 1], sizeof(slot_labels[slot - 1]), "%s", label);
  }
  return result == ESP_OK;
}

static bool host_ok(httpd_req_t *req) {
  char host[40] = {0};
  if (httpd_req_get_hdr_value_str(req, "Host", host, sizeof(host)) != ESP_OK) {
    return false;
  }
  return dashboard_host_allowed(host);
}

static bool session_ok(httpd_req_t *req) {
  if (config_console_authorized()) return true;
  char cookie[160] = {0};
  if (httpd_req_get_hdr_value_str(req, "Cookie", cookie, sizeof(cookie)) != ESP_OK) {
    return false;
  }
  const char *found = strstr(cookie, "tt_session=");
  if (!found) return false;
  found += strlen("tt_session=");
  char token[DASHBOARD_SESSION_HEX];
  size_t n = 0;
  while (found[n] && found[n] != ';' && n + 1 < sizeof(token)) {
    token[n] = found[n];
    n++;
  }
  token[n] = '\0';
  return dashboard_session_valid(token, now_us());
}

static void send_json(httpd_req_t *req, const char *json) {
  httpd_resp_set_type(req, "application/json");
  httpd_resp_send(req, json, HTTPD_RESP_USE_STRLEN);
}

static void send_error(httpd_req_t *req, int status, const char *code) {
  httpd_resp_set_status(req, status == 401 ? "401 Unauthorized" :
                             status == 403 ? "403 Forbidden" : "400 Bad Request");
  char json[96];
  snprintf(json, sizeof(json), "{\"error\":\"%s\"}", code);
  send_json(req, json);
}

static bool require_host(httpd_req_t *req) {
  if (host_ok(req)) return true;
  send_error(req, 403, "bad_host");
  return false;
}

static bool require_unlock(httpd_req_t *req) {
  if (session_ok(req)) return true;
  dashboard_log_event("ERR CONFIG_LOCKED");
  send_error(req, 401, "CONFIG_LOCKED");
  return false;
}

static esp_err_t read_body(httpd_req_t *req, char *buf, size_t buf_len) {
  int total = req->content_len;
  if (total < 0 || (size_t)total >= buf_len) return ESP_FAIL;
  int got = 0;
  while (got < total) {
    int n = httpd_req_recv(req, buf + got, total - got);
    if (n <= 0) return ESP_FAIL;
    got += n;
  }
  buf[got] = '\0';
  return ESP_OK;
}

static void enrollment_prompt(const char *message) {
  char line[48];
  snprintf(line, sizeof(line), "PROMPT %s", message);
  dashboard_log_event(line);
}

static esp_err_t handle_index(httpd_req_t *req) {
  if (!require_host(req)) return ESP_OK;
  httpd_resp_set_type(req, "text/html");
  httpd_resp_set_hdr(req, "Content-Encoding", "gzip");
  httpd_resp_set_hdr(req, "Cache-Control", "no-store");
  return httpd_resp_send(req, (const char *)index_html_gz_start,
                         index_html_gz_end - index_html_gz_start);
}

static esp_err_t handle_status(httpd_req_t *req) {
  if (!require_host(req)) return ESP_OK;
  dashboard_status_t status;
  int count = fingerprint_count();
  *(&status) = (dashboard_status_t){0};
  status.firmware_version = TINYTOUCH_FIRMWARE_VERSION;
  status.mode = device_config_mode_name();
  status.sensor = count < 0 ? "no_response" : "ok";
  status.fingerprints = count;
  status.hid_hosts = (unsigned)device_config_hid_host_count();
  status.hid_key_configured = device_config_hid_key_configured();
  status.submit_enter = device_config_submit_enter();
  status.typing_delay_ms = device_config_typing_delay_ms();
  status.idle_led = device_config_idle_led();
  status.paused = dashboard_is_paused();
  status.authorized = session_ok(req) || config_console_authorized();
  status.ncm_ready = usb_ncm_ready();
  for (int i = 0; i < DASHBOARD_MAX_SLOTS; i++) status.labels[i] = slot_labels[i];
  char json[1024];
  if (dashboard_build_status_json(json, sizeof(json), &status) < 0) {
    send_error(req, 400, "status");
    return ESP_OK;
  }
  send_json(req, json);
  return ESP_OK;
}

static esp_err_t handle_log(httpd_req_t *req) {
  if (!require_host(req)) return ESP_OK;
  char json[1536];
  if (dashboard_build_log_json(json, sizeof(json)) < 0) {
    send_error(req, 400, "log");
    return ESP_OK;
  }
  send_json(req, json);
  return ESP_OK;
}

static void grant_session(httpd_req_t *req) {
  char token[DASHBOARD_SESSION_HEX];
  dashboard_session_grant(token, sizeof(token), now_us(), fill_random);
  char cookie[96];
  snprintf(cookie, sizeof(cookie),
           "tt_session=%s; Path=/; Max-Age=120; HttpOnly; SameSite=Strict", token);
  httpd_resp_set_hdr(req, "Set-Cookie", cookie);
}

static esp_err_t handle_unlock(httpd_req_t *req) {
  if (!require_host(req)) return ESP_OK;
  dashboard_set_paused(true);
  bool ok = config_console_unlock();
  dashboard_set_paused(false);
  if (!ok) {
    send_error(req, 401, "CONFIG_UNLOCK");
    return ESP_OK;
  }
  grant_session(req);
  send_json(req, "{\"ok\":true}");
  return ESP_OK;
}

static esp_err_t handle_pause(httpd_req_t *req) {
  if (!require_host(req)) return ESP_OK;
  char body[64];
  if (read_body(req, body, sizeof(body)) != ESP_OK) {
    send_error(req, 400, "body");
    return ESP_OK;
  }
  bool paused = false;
  if (!dashboard_json_get_bool(body, "paused", &paused)) {
    send_error(req, 400, "paused");
    return ESP_OK;
  }
  dashboard_set_paused(paused);
  dashboard_log_event(paused ? "HID paused" : "HID resumed");
  send_json(req, "{\"ok\":true}");
  return ESP_OK;
}

static esp_err_t handle_enroll(httpd_req_t *req) {
  if (!require_host(req) || !require_unlock(req)) return ESP_OK;
  char body[64];
  int slot = 0;
  if (read_body(req, body, sizeof(body)) != ESP_OK ||
      !dashboard_json_get_int(body, "slot", &slot) ||
      slot < 1 || slot > DASHBOARD_MAX_SLOTS) {
    send_error(req, 400, "slot");
    return ESP_OK;
  }
  dashboard_set_paused(true);
  bool ok = fingerprint_enroll((uint16_t)slot, enrollment_prompt);
  dashboard_set_paused(false);
  char line[40];
  snprintf(line, sizeof(line), ok ? "OK ENROLL slot=%d" : "ERR ENROLL slot=%d", slot);
  dashboard_log_event(line);
  if (!ok) {
    send_error(req, 400, "ENROLL");
    return ESP_OK;
  }
  send_json(req, "{\"ok\":true}");
  return ESP_OK;
}

static esp_err_t handle_delete(httpd_req_t *req) {
  if (!require_host(req) || !require_unlock(req)) return ESP_OK;
  char body[64];
  int slot = 0;
  if (read_body(req, body, sizeof(body)) != ESP_OK ||
      !dashboard_json_get_int(body, "slot", &slot) ||
      slot < 1 || slot > DASHBOARD_MAX_SLOTS) {
    send_error(req, 400, "slot");
    return ESP_OK;
  }
  bool ok = fingerprint_delete((uint16_t)slot);
  char line[40];
  snprintf(line, sizeof(line), ok ? "OK DELETE slot=%d" : "ERR DELETE slot=%d", slot);
  dashboard_log_event(line);
  if (ok) labels_save_slot(slot, "");
  if (!ok) {
    send_error(req, 400, "DELETE");
    return ESP_OK;
  }
  send_json(req, "{\"ok\":true}");
  return ESP_OK;
}

static esp_err_t handle_label(httpd_req_t *req) {
  if (!require_host(req) || !require_unlock(req)) return ESP_OK;
  char body[96];
  int slot = 0;
  char label[DASHBOARD_LABEL_MAX];
  char sanitized[DASHBOARD_LABEL_MAX];
  if (read_body(req, body, sizeof(body)) != ESP_OK ||
      !dashboard_json_get_int(body, "slot", &slot) ||
      !dashboard_json_get_string(body, "label", label, sizeof(label)) ||
      !dashboard_sanitize_label(label, sanitized, sizeof(sanitized)) ||
      slot < 1 || slot > DASHBOARD_MAX_SLOTS) {
    send_error(req, 400, "label");
    return ESP_OK;
  }
  if (!labels_save_slot(slot, sanitized)) {
    send_error(req, 400, "nvs");
    return ESP_OK;
  }
  dashboard_log_event("OK LABEL");
  send_json(req, "{\"ok\":true}");
  return ESP_OK;
}

static esp_err_t handle_settings(httpd_req_t *req) {
  if (!require_host(req) || !require_unlock(req)) return ESP_OK;
  char body[128];
  if (read_body(req, body, sizeof(body)) != ESP_OK) {
    send_error(req, 400, "body");
    return ESP_OK;
  }
  bool submit_enter = device_config_submit_enter();
  int delay = (int)device_config_typing_delay_ms();
  bool idle_led = device_config_idle_led();
  bool has_enter = dashboard_json_get_bool(body, "submit_enter", &submit_enter);
  bool has_delay = dashboard_json_get_int(body, "typing_delay_ms", &delay);
  bool has_idle = dashboard_json_get_bool(body, "idle_led", &idle_led);
  if (!has_enter && !has_delay && !has_idle) {
    send_error(req, 400, "settings");
    return ESP_OK;
  }
  if (has_enter && !device_config_set_submit_enter(submit_enter)) {
    send_error(req, 400, "submit_enter");
    return ESP_OK;
  }
  if (has_delay && (delay < 0 || delay > 100 ||
                    !device_config_set_typing_delay_ms((uint16_t)delay))) {
    send_error(req, 400, "typing_delay_ms");
    return ESP_OK;
  }
  if (has_idle && !device_config_set_idle_led(idle_led)) {
    send_error(req, 400, "idle_led");
    return ESP_OK;
  }
  if (has_idle) fingerprint_led_idle();
  dashboard_log_event("OK SETTINGS");
  send_json(req, "{\"ok\":true}");
  return ESP_OK;
}

void web_dashboard_start(void) {
  labels_load();
  httpd_config_t config = HTTPD_DEFAULT_CONFIG();
  config.server_port = 80;
  config.lru_purge_enable = true;
  config.stack_size = 8192;
  config.max_uri_handlers = 12;
  // Unlock/enroll can hold a worker for tens of seconds while waiting on the sensor.
  config.recv_wait_timeout = 60;
  config.send_wait_timeout = 60;
  httpd_handle_t server = NULL;
  if (httpd_start(&server, &config) != ESP_OK) {
    ESP_LOGE(TAG, "httpd start failed");
    return;
  }
  const httpd_uri_t uris[] = {
      {.uri = "/", .method = HTTP_GET, .handler = handle_index},
      {.uri = "/index.html", .method = HTTP_GET, .handler = handle_index},
      {.uri = "/api/status", .method = HTTP_GET, .handler = handle_status},
      {.uri = "/api/log", .method = HTTP_GET, .handler = handle_log},
      {.uri = "/api/unlock", .method = HTTP_POST, .handler = handle_unlock},
      {.uri = "/api/pause", .method = HTTP_POST, .handler = handle_pause},
      {.uri = "/api/enroll", .method = HTTP_POST, .handler = handle_enroll},
      {.uri = "/api/delete", .method = HTTP_POST, .handler = handle_delete},
      {.uri = "/api/label", .method = HTTP_POST, .handler = handle_label},
      {.uri = "/api/settings", .method = HTTP_POST, .handler = handle_settings},
  };
  for (size_t i = 0; i < sizeof(uris) / sizeof(uris[0]); i++) {
    httpd_register_uri_handler(server, &uris[i]);
  }
  ESP_LOGI(TAG, "dashboard listening on " DASHBOARD_URL);
}

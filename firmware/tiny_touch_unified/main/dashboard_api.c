#include "dashboard_api.h"

#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static bool paused;
static char log_lines[DASHBOARD_LOG_LINES][DASHBOARD_LOG_LINE_MAX];
static int log_head;
static int log_count;
static char session_token[DASHBOARD_SESSION_HEX];
static int64_t session_until_us;

void dashboard_reset_for_tests(void) {
  paused = false;
  memset(log_lines, 0, sizeof(log_lines));
  log_head = 0;
  log_count = 0;
  memset(session_token, 0, sizeof(session_token));
  session_until_us = 0;
}

bool dashboard_sanitize_label(const char *in, char *out, size_t out_len) {
  if (!in || !out || out_len < 2) return false;
  while (*in && isspace((unsigned char)*in)) in++;
  char tmp[DASHBOARD_LABEL_MAX];
  size_t n = 0;
  for (; *in && n + 1 < sizeof(tmp); in++) {
    unsigned char c = (unsigned char)*in;
    if (c == '"' || c == '\\' || c == '<' || c == '>' || c == '&') return false;
    if (isspace(c) && c != ' ') continue;
    if (!(isalnum(c) || c == ' ' || c == '_' || c == '-' || c == '.')) return false;
    tmp[n++] = (char)c;
  }
  while (n && isspace((unsigned char)tmp[n - 1])) n--;
  tmp[n] = '\0';
  if (n >= out_len) return false;
  memcpy(out, tmp, n + 1);
  return true;
}

void dashboard_set_paused(bool value) {
  paused = value;
}

bool dashboard_is_paused(void) {
  return paused;
}

void dashboard_log_event(const char *message) {
  if (!message) message = "";
  snprintf(log_lines[log_head], sizeof(log_lines[log_head]), "%s", message);
  log_head = (log_head + 1) % DASHBOARD_LOG_LINES;
  if (log_count < DASHBOARD_LOG_LINES) log_count++;
}

static int json_escape(char *out, size_t out_len, const char *in) {
  if (!out || out_len < 3) return -1;
  size_t used = 0;
  out[used++] = '"';
  for (; in && *in; in++) {
    unsigned char c = (unsigned char)*in;
    const char *rep = NULL;
    char hex[7];
    if (c == '"' || c == '\\') {
      if (used + 3 >= out_len) return -1;
      out[used++] = '\\';
      out[used++] = (char)c;
      continue;
    }
    if (c == '\n') rep = "\\n";
    else if (c == '\r') rep = "\\r";
    else if (c == '\t') rep = "\\t";
    else if (c < 0x20) {
      snprintf(hex, sizeof(hex), "\\u%04x", c);
      rep = hex;
    }
    if (rep) {
      size_t len = strlen(rep);
      if (used + len + 1 >= out_len) return -1;
      memcpy(out + used, rep, len);
      used += len;
    } else {
      if (used + 2 >= out_len) return -1;
      out[used++] = (char)c;
    }
  }
  if (used + 1 >= out_len) return -1;
  out[used++] = '"';
  out[used] = '\0';
  return (int)used;
}

int dashboard_build_log_json(char *out, size_t out_len) {
  if (!out || out_len < 12) return -1;
  size_t used = 0;
  int n = snprintf(out, out_len, "{\"events\":[");
  if (n < 0 || (size_t)n >= out_len) return -1;
  used = (size_t)n;
  int start = (log_count == DASHBOARD_LOG_LINES) ? log_head : 0;
  for (int i = 0; i < log_count; i++) {
    const char *line = log_lines[(start + i) % DASHBOARD_LOG_LINES];
    if (i) {
      if (used + 2 >= out_len) return -1;
      out[used++] = ',';
    }
    int wrote = json_escape(out + used, out_len - used, line);
    if (wrote < 0) return -1;
    used += (size_t)wrote;
  }
  if (used + 3 >= out_len) return -1;
  memcpy(out + used, "]}", 3);
  used += 2;
  return (int)used;
}

int dashboard_build_status_json(char *out, size_t out_len, const dashboard_status_t *st) {
  if (!out || !st || out_len < 32) return -1;
  char version[48], mode[24], sensor[32];
  if (json_escape(version, sizeof(version), st->firmware_version ? st->firmware_version : "") < 0 ||
      json_escape(mode, sizeof(mode), st->mode ? st->mode : "") < 0 ||
      json_escape(sensor, sizeof(sensor), st->sensor ? st->sensor : "") < 0) {
    return -1;
  }
  int n = snprintf(
      out, out_len,
      "{\"firmware_version\":%s,\"mode\":%s,\"sensor\":%s,\"fingerprints\":%d,"
      "\"hid_hosts\":%u,\"hid_key_configured\":%s,\"submit_enter\":%s,"
      "\"typing_delay_ms\":%u,\"idle_led\":%s,\"paused\":%s,\"authorized\":%s,\"ncm_ready\":%s,"
      "\"dashboard\":\"%s\",\"slots\":[",
      version, mode, sensor, st->fingerprints, st->hid_hosts,
      st->hid_key_configured ? "true" : "false",
      st->submit_enter ? "true" : "false", st->typing_delay_ms,
      st->idle_led ? "true" : "false",
      st->paused ? "true" : "false", st->authorized ? "true" : "false",
      st->ncm_ready ? "true" : "false", DASHBOARD_URL);
  if (n < 0 || (size_t)n >= out_len) return -1;
  size_t used = (size_t)n;
  for (int slot = 0; slot < DASHBOARD_MAX_SLOTS; slot++) {
    char label[DASHBOARD_LABEL_MAX * 2];
    if (json_escape(label, sizeof(label),
                    st->labels[slot] ? st->labels[slot] : "") < 0) {
      return -1;
    }
    n = snprintf(out + used, out_len - used, "%s{\"slot\":%d,\"label\":%s}",
                 slot ? "," : "", slot + 1, label);
    if (n < 0 || used + (size_t)n >= out_len) return -1;
    used += (size_t)n;
  }
  if (used + 3 >= out_len) return -1;
  memcpy(out + used, "]}", 3);
  used += 2;
  return (int)used;
}

static void bytes_to_hex(const uint8_t *data, size_t length, char *output) {
  static const char digits[] = "0123456789abcdef";
  for (size_t i = 0; i < length; i++) {
    output[i * 2] = digits[data[i] >> 4];
    output[i * 2 + 1] = digits[data[i] & 0x0f];
  }
  output[length * 2] = '\0';
}

void dashboard_session_grant(char *token_out, size_t token_len, int64_t now_us,
                             void (*fill_random)(void *buf, size_t len)) {
  uint8_t raw[DASHBOARD_SESSION_BYTES];
  if (fill_random) fill_random(raw, sizeof(raw));
  else memset(raw, 0, sizeof(raw));
  bytes_to_hex(raw, sizeof(raw), session_token);
  session_until_us = now_us + DASHBOARD_SESSION_TTL_US;
  if (token_out && token_len) snprintf(token_out, token_len, "%s", session_token);
}

bool dashboard_session_valid(const char *token, int64_t now_us) {
  if (!token || !session_token[0] || now_us >= session_until_us) return false;
  if (strlen(token) != strlen(session_token)) return false;
  unsigned diff = 0;
  for (size_t i = 0; session_token[i]; i++) {
    diff |= (unsigned char)token[i] ^ (unsigned char)session_token[i];
  }
  return diff == 0;
}

void dashboard_session_clear(void) {
  memset(session_token, 0, sizeof(session_token));
  session_until_us = 0;
}

static const char *json_find_key(const char *json, const char *key) {
  if (!json || !key) return NULL;
  char pattern[48];
  snprintf(pattern, sizeof(pattern), "\"%s\"", key);
  const char *found = strstr(json, pattern);
  if (!found) return NULL;
  found += strlen(pattern);
  while (*found && isspace((unsigned char)*found)) found++;
  if (*found != ':') return NULL;
  found++;
  while (*found && isspace((unsigned char)*found)) found++;
  return found;
}

bool dashboard_json_get_int(const char *json, const char *key, int *out) {
  const char *value = json_find_key(json, key);
  if (!value || !out || (*value != '-' && !isdigit((unsigned char)*value))) return false;
  *out = (int)strtol(value, NULL, 10);
  return true;
}

bool dashboard_json_get_bool(const char *json, const char *key, bool *out) {
  const char *value = json_find_key(json, key);
  if (!value || !out) return false;
  if (strncmp(value, "true", 4) == 0) {
    *out = true;
    return true;
  }
  if (strncmp(value, "false", 5) == 0) {
    *out = false;
    return true;
  }
  return false;
}

bool dashboard_json_get_string(const char *json, const char *key, char *out, size_t out_len) {
  const char *value = json_find_key(json, key);
  if (!value || !out || out_len == 0 || *value != '"') return false;
  value++;
  size_t n = 0;
  while (*value && *value != '"') {
    if (*value == '\\') return false;
    if (n + 1 >= out_len) return false;
    out[n++] = *value++;
  }
  out[n] = '\0';
  return *value == '"';
}

bool dashboard_host_allowed(const char *host) {
  if (!host || !host[0]) return false;
  return strcmp(host, "192.168.7.1") == 0 ||
         strcmp(host, "192.168.7.1:80") == 0;
}

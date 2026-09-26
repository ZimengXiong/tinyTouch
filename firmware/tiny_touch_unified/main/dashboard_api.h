#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define DASHBOARD_URL "http://192.168.7.1/"
#define DASHBOARD_IP_A 192
#define DASHBOARD_IP_B 168
#define DASHBOARD_IP_C 7
#define DASHBOARD_IP_D 1
#define DASHBOARD_HOST_IP 0xC0A80701u
#define DASHBOARD_MAX_SLOTS 5
#define DASHBOARD_LABEL_MAX 32
#define DASHBOARD_LOG_LINES 24
#define DASHBOARD_LOG_LINE_MAX 96
#define DASHBOARD_SESSION_BYTES 16
#define DASHBOARD_SESSION_HEX (DASHBOARD_SESSION_BYTES * 2 + 1)
#define DASHBOARD_SESSION_TTL_US (120LL * 1000000LL)

typedef struct {
  const char *firmware_version;
  const char *mode;
  const char *sensor;
  int fingerprints;
  unsigned hid_hosts;
  bool hid_key_configured;
  bool submit_enter;
  unsigned typing_delay_ms;
  bool idle_led;
  bool paused;
  bool authorized;
  bool ncm_ready;
  const char *labels[DASHBOARD_MAX_SLOTS];
} dashboard_status_t;

void dashboard_reset_for_tests(void);
bool dashboard_sanitize_label(const char *in, char *out, size_t out_len);
void dashboard_set_paused(bool paused);
bool dashboard_is_paused(void);
void dashboard_log_event(const char *message);
int dashboard_build_log_json(char *out, size_t out_len);
int dashboard_build_status_json(char *out, size_t out_len, const dashboard_status_t *st);
bool dashboard_session_valid(const char *token, int64_t now_us);
void dashboard_session_grant(char *token_out, size_t token_len, int64_t now_us,
                             void (*fill_random)(void *buf, size_t len));
void dashboard_session_clear(void);
bool dashboard_json_get_int(const char *json, const char *key, int *out);
bool dashboard_json_get_bool(const char *json, const char *key, bool *out);
bool dashboard_json_get_string(const char *json, const char *key, char *out, size_t out_len);
bool dashboard_host_allowed(const char *host);

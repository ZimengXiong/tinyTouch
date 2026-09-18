#include "dashboard_api.h"

#include <stdio.h>
#include <string.h>

static int failures;

static void expect_true(int cond, const char *name) {
  if (!cond) {
    printf("FAIL %s\n", name);
    failures++;
  }
}

static void fill_zero(void *buf, size_t len) {
  memset(buf, 0, len);
}

static void fill_aa(void *buf, size_t len) {
  memset(buf, 0xaa, len);
}

int main(void) {
  dashboard_reset_for_tests();

  char label[DASHBOARD_LABEL_MAX];
  expect_true(dashboard_sanitize_label("Index", label, sizeof(label)) &&
                  strcmp(label, "Index") == 0,
              "keeps a simple label");
  expect_true(dashboard_sanitize_label("  left thumb\n", label, sizeof(label)) &&
                  strcmp(label, "left thumb") == 0,
              "trims whitespace");
  expect_true(!dashboard_sanitize_label("bad\"quote", label, sizeof(label)),
              "rejects quotes that would break JSON");
  expect_true(!dashboard_sanitize_label("<script>", label, sizeof(label)),
              "rejects angle brackets");

  expect_true(!dashboard_is_paused(), "starts unpaused");
  dashboard_set_paused(true);
  expect_true(dashboard_is_paused(), "pause flag sticks");
  dashboard_set_paused(false);
  expect_true(!dashboard_is_paused(), "pause flag clears");

  dashboard_log_event("PROMPT TOUCH");
  dashboard_log_event("OK ENROLL slot=1");
  char log_json[512];
  expect_true(dashboard_build_log_json(log_json, sizeof(log_json)) > 0, "log json");
  expect_true(strstr(log_json, "PROMPT TOUCH") != NULL, "log contains prompt");
  expect_true(strstr(log_json, "OK ENROLL slot=1") != NULL, "log contains enroll");

  const char *labels[DASHBOARD_MAX_SLOTS] = {"Index", "", "Ring", "", ""};
  dashboard_status_t status = {
      .firmware_version = "0.5.1-preprod",
      .mode = "hid",
      .sensor = "ok",
      .fingerprints = 2,
      .hid_hosts = 1,
      .hid_key_configured = true,
      .submit_enter = true,
      .typing_delay_ms = 7,
      .idle_led = true,
      .paused = false,
      .authorized = false,
      .ncm_ready = true,
      .labels = {labels[0], labels[1], labels[2], labels[3], labels[4]},
  };
  char json[1024];
  expect_true(dashboard_build_status_json(json, sizeof(json), &status) > 0,
              "status json");
  expect_true(strstr(json, "\"mode\":\"hid\"") != NULL, "status mode");
  expect_true(strstr(json, "\"fingerprints\":2") != NULL, "status count");
  expect_true(strstr(json, "\"idle_led\":true") != NULL, "status idle_led");
  expect_true(strstr(json, "\"dashboard\":\"http://192.168.7.1/\"") != NULL,
              "status dashboard url");
  expect_true(strstr(json, "\"label\":\"Index\"") != NULL, "status label");
  expect_true(strstr(json, "password") == NULL, "status has no password field");
  expect_true(strstr(json, "pairing") == NULL, "status has no pairing key");

  char token[DASHBOARD_SESSION_HEX];
  dashboard_session_grant(token, sizeof(token), 1000, fill_aa);
  expect_true(dashboard_session_valid(token, 1000), "fresh session valid");
  expect_true(!dashboard_session_valid("deadbeef", 1000), "wrong token rejected");
  expect_true(!dashboard_session_valid(token, 1000 + DASHBOARD_SESSION_TTL_US + 1),
              "expired session rejected");
  dashboard_session_grant(token, sizeof(token), 2000, fill_zero);
  expect_true(dashboard_session_valid(token, 2000), "zero-fill token still granted");

  int slot = 0;
  bool paused = false;
  char extracted[32];
  expect_true(dashboard_json_get_int("{\"slot\":3}", "slot", &slot) && slot == 3,
              "json int");
  expect_true(dashboard_json_get_bool("{\"paused\":true}", "paused", &paused) && paused,
              "json bool");
  expect_true(dashboard_json_get_string("{\"label\":\"Left\"}", "label", extracted,
                                        sizeof(extracted)) &&
                  strcmp(extracted, "Left") == 0,
              "json string");

  if (failures) {
    printf("%d failures\n", failures);
    return 1;
  }
  printf("ok\n");
  return 0;
}

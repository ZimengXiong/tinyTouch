#include "../../firmware/tiny_touch_unified/main/fingerprint_led.h"

#include <assert.h>
#include <stdio.h>
#include <string.h>

static bool acknowledge;
static unsigned commands;
static uint8_t last_command[4];

bool fingerprint_led_command(const uint8_t params[4]) {
  memcpy(last_command, params, sizeof(last_command));
  commands++;
  return acknowledge;
}

static void expect_command(const uint8_t expected[4]) {
  assert(memcmp(last_command, expected, sizeof(last_command)) == 0);
}

static void boot(uint32_t now) {
  acknowledge = true;
  commands = 0;
  fingerprint_led_init(now);
  fingerprint_led_service(false, now);
}

static void idle_delay(void) {
  boot(0);
  fingerprint_led_service(false, 2999);
  assert(commands == 1);
  fingerprint_led_service(false, 3000);
  expect_command((uint8_t[]){6, 1, 1, 1});
}

static void held_finger(void) {
  boot(0);
  fingerprint_led_service(true, 1000);
  fingerprint_led_service(true, 9000);
  assert(commands == 1);
  fingerprint_led_service(false, 9100);
  fingerprint_led_service(false, 12099);
  assert(commands == 1);
  fingerprint_led_service(false, 12100);
  expect_command((uint8_t[]){6, 1, 1, 1});
}

static void fade_finishes(void) {
  boot(0);
  fingerprint_led_service(false, 3000);
  fingerprint_led_service(false, 4399);
  assert(commands == 2);
  fingerprint_led_service(false, 4400);
  expect_command((uint8_t[]){4, 0, 0, 0});
  fingerprint_led_service(false, 9000);
  assert(commands == 3);
}

static void wake_from_off(void) {
  boot(0);
  fingerprint_led_service(false, 3000);
  fingerprint_led_service(false, 4400);
  fingerprint_led_service(true, 4500);
  expect_command((uint8_t[]){3, 1, 1, 0});
}

static void interrupt_fade(void) {
  boot(0);
  fingerprint_led_service(false, 3000);
  fingerprint_led_service(true, 3010);
  expect_command((uint8_t[]){3, 1, 1, 0});
  fingerprint_led_service(true, 4400);
  assert(commands == 3);
}

static void result_expires(uint8_t color) {
  boot(0);
  fingerprint_led_show(color, true, 10);
  fingerprint_led_service(true, 359);
  expect_command((uint8_t[]){3, color, color, 0});
  fingerprint_led_service(true, 360);
  expect_command((uint8_t[]){3, 1, 1, 0});
}

static void green_expires(void) { result_expires(2); }
static void red_expires(void) { result_expires(4); }

static void restore_retries(void) {
  boot(0);
  fingerprint_led_show(2, true, 10);
  acknowledge = false;
  fingerprint_led_service(true, 360);
  fingerprint_led_service(true, 459);
  assert(commands == 3);
  acknowledge = true;
  fingerprint_led_service(true, 460);
  assert(commands == 4);
  expect_command((uint8_t[]){3, 1, 1, 0});
  fingerprint_led_service(true, 600);
  assert(commands == 4);
}

static void blackout_retries(void) {
  boot(0);
  fingerprint_led_service(false, 3000);
  acknowledge = false;
  fingerprint_led_service(false, 4400);
  acknowledge = true;
  fingerprint_led_service(false, 4500);
  assert(commands == 4);
  expect_command((uint8_t[]){4, 0, 0, 0});
}

static void lost_fade_ack(void) {
  boot(0);
  acknowledge = false;
  fingerprint_led_service(false, 3000);
  fingerprint_led_service(false, 3100);
  acknowledge = true;
  fingerprint_led_service(false, 4400);
  expect_command((uint8_t[]){4, 0, 0, 0});
}

static void wake_retries(void) {
  boot(0);
  fingerprint_led_service(false, 3000);
  acknowledge = false;
  fingerprint_led_service(true, 3010);
  acknowledge = true;
  fingerprint_led_service(true, 3110);
  assert(commands == 4);
  expect_command((uint8_t[]){3, 1, 1, 0});
}

static void reassert_after_capture(void) {
  boot(0);
  fingerprint_led_show(1, true, 100);
  assert(commands == 2);
  expect_command((uint8_t[]){3, 1, 1, 0});
}

static void clock_wrap(void) {
  uint32_t start = UINT32_MAX - 1000;
  boot(start);
  fingerprint_led_service(false, start + 2999);
  assert(commands == 1);
  fingerprint_led_service(false, start + 3000);
  expect_command((uint8_t[]){6, 1, 1, 1});
  fingerprint_led_service(false, start + 4400);
  expect_command((uint8_t[]){4, 0, 0, 0});
}

static void stale_result_retry(void) {
  boot(0);
  acknowledge = false;
  fingerprint_led_show(2, true, 10);
  acknowledge = true;
  fingerprint_led_service(true, 360);
  expect_command((uint8_t[]){3, 1, 1, 0});
}

static const struct {
  const char *name;
  void (*run)(void);
} cases[] = {
  {"idle_delay", idle_delay},
  {"held_finger", held_finger},
  {"fade_finishes", fade_finishes},
  {"wake_from_off", wake_from_off},
  {"interrupt_fade", interrupt_fade},
  {"green_expires", green_expires},
  {"red_expires", red_expires},
  {"restore_retries", restore_retries},
  {"blackout_retries", blackout_retries},
  {"lost_fade_ack", lost_fade_ack},
  {"wake_retries", wake_retries},
  {"reassert_after_capture", reassert_after_capture},
  {"clock_wrap", clock_wrap},
  {"stale_result_retry", stale_result_retry},
};

int main(int argc, char **argv) {
  if (argc != 2) return 2;
  for (size_t i = 0; i < sizeof(cases) / sizeof(cases[0]); i++) {
    if (strcmp(argv[1], "--help") == 0) puts(cases[i].name);
    else if (strcmp(argv[1], cases[i].name) == 0) {
      cases[i].run();
      return 0;
    }
  }
  return strcmp(argv[1], "--help") == 0 ? 0 : 2;
}

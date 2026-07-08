#pragma once

#include <stdbool.h>

void fingerprint_init(void);
bool fingerprint_present_hint(void);
void fingerprint_led_idle(void);
bool fingerprint_authorize_poll_once(void);
bool fingerprint_authorize_once(void);

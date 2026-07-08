#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef bool (*ccid_apdu_handler_t)(const uint8_t *apdu, size_t apdu_len,
                                    uint8_t *response, size_t *response_len,
                                    size_t response_cap);

void usb_ccid_start(ccid_apdu_handler_t handler);
void usb_ccid_task(void);

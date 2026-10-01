#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef bool (*ccid_apdu_handler_t)(const uint8_t *apdu, size_t apdu_len,
                                    uint8_t *response, size_t *response_len,
                                    size_t response_cap);

void usb_ccid_start(ccid_apdu_handler_t handler);
void usb_ccid_rescan(void);
void usb_ccid_open_setup(void);
bool usb_ccid_touch_enabled(void);
bool usb_ccid_piv_visible(void);
void usb_ccid_touch_begin(void);
void usb_ccid_touch_cancel(void);
bool usb_ccid_wait_for_piv(void);
// Keep automatic reconnects from interrupting a console command's reply.
void usb_ccid_begin_console_command(void);
void usb_ccid_end_console_command(void);

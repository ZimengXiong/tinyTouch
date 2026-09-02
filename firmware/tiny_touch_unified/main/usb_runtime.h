#pragma once

#include <stdbool.h>

void usb_runtime_init(void);
void usb_runtime_on_attached(void);
bool usb_runtime_is_suspended(void);
bool usb_runtime_can_poll_sensor(void);
bool usb_runtime_request_remote_wakeup(void);
bool usb_runtime_service_reconnect(void);
bool usb_runtime_take_release_request(void);
void usb_runtime_request_release(void);

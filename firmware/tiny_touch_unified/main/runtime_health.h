#pragma once

#include <stdbool.h>
#include <stddef.h>

typedef enum {
  RUNTIME_USB_STARTING = 0,
  RUNTIME_USB_ATTACHED,
  RUNTIME_USB_SUSPENDED,
  RUNTIME_USB_RECOVERING,
} runtime_usb_state_t;

void runtime_health_init(void);
void runtime_health_note_usb(runtime_usb_state_t state);
void runtime_health_note_usb_reconnect(void);
void runtime_health_note_auth(bool success);
void runtime_health_note_sensor_protocol_error(void);
void runtime_health_format(char *output, size_t output_size);

#pragma once

#include <stdint.h>

#include "device_config.h"
#include "tusb.h"

extern tusb_desc_device_t const tiny_touch_device_descriptor;
extern uint8_t const tiny_touch_hid_report_descriptor[];
extern char const *tiny_touch_string_descriptors[];
extern int const tiny_touch_string_descriptor_count;

void tiny_touch_init_serial(void);
void usb_descriptors_apply_mode(device_mode_t mode);
const uint8_t *usb_descriptors_configuration(void);

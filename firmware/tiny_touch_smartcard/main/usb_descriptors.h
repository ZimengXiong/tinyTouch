#pragma once

#include <stdint.h>

#include "tusb.h"

extern tusb_desc_device_t const tiny_touch_device_descriptor;
extern uint8_t const tiny_touch_fs_configuration_descriptor[];
extern uint8_t const tiny_touch_hid_report_descriptor[];
extern char const *tiny_touch_string_descriptors[];
extern int const tiny_touch_string_descriptor_count;

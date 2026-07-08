#pragma once

#include "sdkconfig.h"

#define CFG_TUSB_MCU OPT_MCU_ESP32S3
#define CFG_TUSB_OS OPT_OS_FREERTOS

#define CFG_TUSB_RHPORT0_MODE (OPT_MODE_DEVICE)

#ifndef CFG_TUSB_MEM_SECTION
#define CFG_TUSB_MEM_SECTION
#endif

#ifndef CFG_TUSB_MEM_ALIGN
#define CFG_TUSB_MEM_ALIGN __attribute__((aligned(4)))
#endif

#define CFG_TUD_ENDPOINT0_SIZE 64

#define CFG_TUD_CDC 0
#define CFG_TUD_MSC 0
#define CFG_TUD_HID 1
#define CFG_TUD_MIDI 0
#define CFG_TUD_VENDOR 0

#define CFG_TUD_CCID 1
#define CFG_TUD_CCID_RX_BUFSIZE 512
#define CFG_TUD_CCID_TX_BUFSIZE 512

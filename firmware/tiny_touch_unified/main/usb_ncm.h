#pragma once

#include <stdbool.h>

void usb_ncm_start(void);
bool usb_ncm_ready(void);
void usb_ncm_task(void);
void usb_ncm_get_stats(unsigned *rx, unsigned *tx_ok, unsigned *tx_fail, unsigned *arp);

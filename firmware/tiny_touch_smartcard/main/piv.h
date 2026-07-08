#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

void piv_init(void);
void piv_note_biometric_verified(void);
bool piv_handle_apdu(const uint8_t *apdu, size_t apdu_len,
                     uint8_t *response, size_t *response_len,
                     size_t response_cap);

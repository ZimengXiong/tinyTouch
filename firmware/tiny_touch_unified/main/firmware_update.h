#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

bool firmware_update_supported(void);
bool firmware_update_begin(size_t size, const uint8_t expected_sha256[32]);
bool firmware_update_write(size_t offset, const uint8_t *data, size_t length);
bool firmware_update_commit(void);
void firmware_update_abort(void);
size_t firmware_update_written(void);

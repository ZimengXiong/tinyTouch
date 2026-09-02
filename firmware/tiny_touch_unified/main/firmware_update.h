#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define FIRMWARE_UPDATE_CHUNK_MAX 3072

bool firmware_update_begin(size_t size, const uint8_t expected_sha256[32]);
bool firmware_update_write(size_t offset, const uint8_t *data, size_t length);
bool firmware_update_commit(void);
void firmware_update_abort(void);
bool firmware_update_active(void);
bool firmware_update_staged(void);
size_t firmware_update_written(void);

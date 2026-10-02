#pragma once
#include <stdbool.h>
#include <stdint.h>

#define FINGER_PROFILE_COUNT 10
#define FINGER_PROFILE_VIEWS 4
#define FINGER_TEMPLATE_LIMIT 40

typedef struct {
  uint8_t version;
  uint8_t reserved[7];
  uint64_t pending;
} finger_profiles_t;

bool finger_profiles_load(finger_profiles_t *profiles);
bool finger_profiles_save(const finger_profiles_t *profiles);
unsigned finger_profiles_count(uint64_t mask);
uint64_t finger_profiles_block(unsigned finger);
bool finger_profiles_block_fits(unsigned finger, unsigned capacity);

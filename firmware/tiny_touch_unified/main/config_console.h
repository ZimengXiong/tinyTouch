#pragma once

#include <stdbool.h>

#define CONFIG_CONSOLE_STACK_SIZE 8192

void config_console_start(void);
void config_console_send_line(const char *line);
bool config_console_authorized(void);
bool config_console_unlock(void);

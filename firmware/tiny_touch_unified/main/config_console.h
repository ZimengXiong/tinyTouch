#pragma once

#define CONFIG_CONSOLE_STACK_SIZE 8192

void config_console_start(void);
void config_console_send_line(const char *line);

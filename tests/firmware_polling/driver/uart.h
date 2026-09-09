#pragma once
#include "freertos/FreeRTOS.h"
typedef int uart_port_t;
typedef struct {
  int baud_rate, data_bits, parity, stop_bits, flow_ctrl, source_clk;
} uart_config_t;
enum { UART_NUM_1, UART_DATA_8_BITS, UART_PARITY_DISABLE, UART_STOP_BITS_1,
       UART_HW_FLOWCTRL_DISABLE, UART_SCLK_DEFAULT, UART_PIN_NO_CHANGE };
int uart_read_bytes(uart_port_t port, void *data, size_t len, TickType_t ticks);
int uart_write_bytes(uart_port_t port, const void *data, size_t len);
int uart_driver_install(int port, int rx, int tx, int size, void *queue, int flags);
int uart_param_config(int port, const uart_config_t *config);
int uart_set_pin(int port, int tx, int rx, int rts, int cts);
int uart_flush_input(int port);
int uart_set_baudrate(int port, uint32_t baud);

#pragma once
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef uint32_t TickType_t;
typedef int BaseType_t;
typedef int portMUX_TYPE;
typedef int uart_port_t;
typedef bool *SemaphoreHandle_t;
typedef struct {
  uint64_t pin_bit_mask;
  int mode, pull_up_en, pull_down_en, intr_type;
} gpio_config_t;
typedef struct {
  int baud_rate, data_bits, parity, stop_bits, flow_ctrl, source_clk;
} uart_config_t;

#define UART_NUM_1 1
#define UART_DATA_8_BITS 8
#define UART_PARITY_DISABLE 0
#define UART_STOP_BITS_1 1
#define UART_HW_FLOWCTRL_DISABLE 0
#define UART_SCLK_DEFAULT 0
#define UART_PIN_NO_CHANGE -1
#define GPIO_MODE_INPUT 0
#define GPIO_PULLUP_DISABLE 0
#define GPIO_PULLDOWN_ENABLE 1
#define GPIO_INTR_DISABLE 0
#define portMUX_INITIALIZER_UNLOCKED 0
#define portENTER_CRITICAL(lock) ((void)(lock))
#define portEXIT_CRITICAL(lock) ((void)(lock))
#define pdMS_TO_TICKS(ms) (ms)
#define portTICK_PERIOD_MS 1
#define pdTRUE 1
#define pdPASS 1
#define configASSERT(value) assert(value)
#define ESP_ERROR_CHECK(value) assert((value) == 0)
#define ESP_LOGI(...) test_log(__VA_ARGS__)
#define ESP_LOGW(...) test_log(__VA_ARGS__)

void test_log(const char *tag, const char *format, ...);
TickType_t xTaskGetTickCount(void);
void vTaskDelay(TickType_t ticks);
BaseType_t xTaskCreate(void (*task)(void *), const char *name, uint32_t stack,
                      void *arg, unsigned priority, void *handle);
SemaphoreHandle_t xSemaphoreCreateMutex(void);
BaseType_t xSemaphoreTake(SemaphoreHandle_t mutex, TickType_t timeout);
void xSemaphoreGive(SemaphoreHandle_t mutex);
int gpio_config(const gpio_config_t *config);
int gpio_get_level(int pin);
int uart_driver_install(uart_port_t port, int rx, int tx, int count, void *queue, int flags);
int uart_param_config(uart_port_t port, const uart_config_t *config);
int uart_set_pin(uart_port_t port, int tx, int rx, int rts, int cts);
int uart_flush_input(uart_port_t port);
int uart_set_baudrate(uart_port_t port, uint32_t rate);
int uart_write_bytes(uart_port_t port, const void *data, size_t size);
int uart_read_bytes(uart_port_t port, void *data, uint32_t size, TickType_t timeout);

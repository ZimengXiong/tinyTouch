#pragma once
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>
typedef uint32_t TickType_t;
typedef int *SemaphoreHandle_t;
typedef int portMUX_TYPE;
typedef int uart_port_t;
typedef int esp_err_t;
typedef int nvs_handle_t;
typedef enum { ESP_RST_POWERON, ESP_RST_SW, ESP_RST_WDT, ESP_RST_BROWNOUT } esp_reset_reason_t;
esp_reset_reason_t esp_reset_reason(void);
typedef struct { uint64_t pin_bit_mask; int mode, pull_up_en, pull_down_en, intr_type; } gpio_config_t;
typedef struct { int baud_rate, data_bits, parity, stop_bits, flow_ctrl, source_clk; } uart_config_t;
#define pdTRUE 1
#define portMAX_DELAY UINT32_MAX
#define pdMS_TO_TICKS(ms) (ms)
#define portMUX_INITIALIZER_UNLOCKED 0
#define portENTER_CRITICAL(lock) ((void)(lock))
#define portEXIT_CRITICAL(lock) ((void)(lock))
#define configASSERT assert
#define ESP_OK 0
#define ESP_ERR_NVS_NOT_FOUND -1
#define ESP_ERROR_CHECK(expr) assert((expr) == ESP_OK)
#define ESP_LOGW(tag, ...) ((void)(tag))
#define ESP_LOGI(tag, ...) ((void)(tag))
#define UART_NUM_1 1
#define GPIO_MODE_INPUT 0
#define GPIO_PULLUP_DISABLE 0
#define GPIO_PULLDOWN_ENABLE 1
#define GPIO_INTR_DISABLE 0
#define UART_DATA_8_BITS 8
#define UART_PARITY_DISABLE 0
#define UART_STOP_BITS_1 1
#define UART_HW_FLOWCTRL_DISABLE 0
#define UART_SCLK_DEFAULT 0
#define UART_PIN_NO_CHANGE -1
#define NVS_READWRITE 1
#define NVS_READONLY 0
TickType_t xTaskGetTickCount(void);
void vTaskDelay(TickType_t ticks);
SemaphoreHandle_t xSemaphoreCreateMutex(void);
int xSemaphoreTake(SemaphoreHandle_t mutex, TickType_t ticks);
int xSemaphoreGive(SemaphoreHandle_t mutex);
int uart_read_bytes(uart_port_t port, void *data, size_t size, TickType_t ticks);
int uart_write_bytes(uart_port_t port, const void *data, size_t size);
int uart_driver_install(int port, int rx, int tx, int queue_size, void *queue, int flags);
int uart_param_config(int port, const uart_config_t *config);
int uart_set_pin(int port, int tx, int rx, int rts, int cts);
int uart_flush_input(int port);
int uart_set_baudrate(int port, int rate);
int gpio_config(const gpio_config_t *config);
int gpio_get_level(int pin);
int nvs_open(const char *name, int mode, nvs_handle_t *handle);
int nvs_get_blob(nvs_handle_t handle, const char *key, void *data, size_t *length);
int nvs_set_blob(nvs_handle_t handle, const char *key, const void *data, size_t length);
int nvs_get_u8(nvs_handle_t handle, const char *key, uint8_t *value);
int nvs_set_u8(nvs_handle_t handle, const char *key, uint8_t value);
int nvs_commit(nvs_handle_t handle);
void nvs_close(nvs_handle_t handle);
int mbedtls_sha256(const unsigned char *data, size_t length, unsigned char output[32], int is224);

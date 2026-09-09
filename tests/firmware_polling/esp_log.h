#pragma once
#define ESP_LOGW(tag, ...) ((void)(tag))
#define ESP_LOGI(tag, ...) ((void)(tag))
#define ESP_ERROR_CHECK(value) assert((value) == 0)

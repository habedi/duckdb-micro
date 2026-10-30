#include "esp_cpu.h"
#include "esp_memory_utils.h"
#include "esp_partition.h"
#include "freertos/FreeRTOS.h"
#include "freertos/idf_additions.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

enum flash_op {
    FLASH_OP_READ,
    FLASH_OP_WRITE,
    FLASH_OP_ERASE
};

typedef struct {
    enum flash_op op;
    const esp_partition_t *partition;
    size_t offset;
    void *buffer;
    size_t size;
    esp_err_t result;
    TaskHandle_t caller;
} flash_request_t;

static volatile flash_request_t *s_current_req = NULL;
static TaskHandle_t s_worker_task = NULL;
static SemaphoreHandle_t s_worker_mutex = NULL;

extern esp_err_t __real_esp_partition_read(const esp_partition_t *partition, size_t src_offset, void *dst, size_t size);
extern esp_err_t __real_esp_partition_write(const esp_partition_t *partition, size_t dst_offset, const void *src, size_t size);
extern esp_err_t __real_esp_partition_erase_range(const esp_partition_t *partition, size_t start_addr, size_t size);

static void flash_worker(void *arg)
{
    (void)arg;
    while (1) {
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
        volatile flash_request_t *req = s_current_req;
        if (!req) {
            continue;
        }

        switch (req->op) {
        case FLASH_OP_READ:
            req->result = __real_esp_partition_read(
                req->partition, req->offset, req->buffer, req->size);
            break;
        case FLASH_OP_WRITE:
            req->result = __real_esp_partition_write(
                req->partition, req->offset, req->buffer, req->size);
            break;
        case FLASH_OP_ERASE:
            req->result = __real_esp_partition_erase_range(
                req->partition, req->offset, req->size);
            break;
        }

        TaskHandle_t caller = req->caller;
        s_current_req = NULL;
        xTaskNotifyGive(caller);
    }
}

static esp_err_t ensure_worker_started(void)
{
    if (!s_worker_mutex) {
        s_worker_mutex = xSemaphoreCreateMutex();
        if (!s_worker_mutex) {
            return ESP_ERR_NO_MEM;
        }
    }
    if (!s_worker_task) {
        // Priority 10 ensures the flash operation executes immediately
        if (xTaskCreatePinnedToCoreWithCaps(flash_worker, "flash_wrk", 4096, NULL, 10,
                                          &s_worker_task, 0, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT) != pdPASS) {
            return ESP_ERR_NO_MEM;
        }
    }
    return ESP_OK;
}

esp_err_t flash_stack_shim_init(void)
{
    return ensure_worker_started();
}

static esp_err_t dispatch_to_worker(enum flash_op op, const esp_partition_t *partition,
                                     size_t offset, void *buffer, size_t size)
{
    esp_err_t err = ensure_worker_started();
    if (err != ESP_OK) {
        return err;
    }
    xSemaphoreTake(s_worker_mutex, portMAX_DELAY);

    flash_request_t req = {
        .op = op,
        .partition = partition,
        .offset = offset,
        .buffer = buffer,
        .size = size,
        .result = ESP_FAIL,
        .caller = xTaskGetCurrentTaskHandle()
    };

    s_current_req = &req;
    xTaskNotifyGive(s_worker_task);
    ulTaskNotifyTake(pdTRUE, portMAX_DELAY);

    xSemaphoreGive(s_worker_mutex);
    return req.result;
}

esp_err_t __wrap_esp_partition_read(const esp_partition_t *partition, size_t src_offset, void *dst, size_t size)
{
    if (!esp_stack_ptr_in_extram((uint32_t)esp_cpu_get_sp())) {
        return __real_esp_partition_read(partition, src_offset, dst, size);
    }
    return dispatch_to_worker(FLASH_OP_READ, partition, src_offset, dst, size);
}

esp_err_t __wrap_esp_partition_write(const esp_partition_t *partition, size_t dst_offset, const void *src, size_t size)
{
    if (!esp_stack_ptr_in_extram((uint32_t)esp_cpu_get_sp())) {
        return __real_esp_partition_write(partition, dst_offset, src, size);
    }
    return dispatch_to_worker(FLASH_OP_WRITE, partition, dst_offset, (void *)src, size);
}

esp_err_t __wrap_esp_partition_erase_range(const esp_partition_t *partition, size_t start_addr, size_t size)
{
    if (!esp_stack_ptr_in_extram((uint32_t)esp_cpu_get_sp())) {
        return __real_esp_partition_erase_range(partition, start_addr, size);
    }
    return dispatch_to_worker(FLASH_OP_ERASE, partition, start_addr, NULL, size);
}

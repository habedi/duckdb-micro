"""Check flash worker allocation failures without a board."""

import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest


STUBS = r"""
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
typedef int esp_err_t;
typedef void *TaskHandle_t;
typedef void *SemaphoreHandle_t;
typedef struct { int unused; } esp_partition_t;
#define ESP_OK 0
#define ESP_FAIL -1
#define ESP_ERR_NO_MEM -2
#define pdTRUE 1
#define pdPASS 1
#define portMAX_DELAY -1
#define MALLOC_CAP_INTERNAL 1
#define MALLOC_CAP_8BIT 2
static bool fail_mutex, fail_task;
static int takes, notifications, direct_calls;
static uintptr_t esp_cpu_get_sp(void) { return 0; }
static bool esp_stack_ptr_in_extram(uint32_t sp) { (void)sp; return true; }
static SemaphoreHandle_t xSemaphoreCreateMutex(void)
{ return fail_mutex ? NULL : (void *)1; }
static int xSemaphoreTake(SemaphoreHandle_t sem, int timeout)
{ assert(sem); (void)timeout; takes++; return pdTRUE; }
static void xSemaphoreGive(SemaphoreHandle_t sem) { assert(sem); }
static int xTaskCreatePinnedToCoreWithCaps(void (*fn)(void *), const char *name,
    int stack, void *arg, int priority, TaskHandle_t *task, int cpu, int caps)
{
    (void)fn; (void)name; (void)stack; (void)arg;
    (void)priority; (void)cpu; (void)caps;
    if (fail_task) return 0;
    *task = (void *)2;
    return pdPASS;
}
static TaskHandle_t xTaskGetCurrentTaskHandle(void) { return (void *)3; }
static void xTaskNotifyGive(TaskHandle_t task);
static int ulTaskNotifyTake(int clear, int timeout)
{ (void)clear; (void)timeout; return 1; }
"""

CHECKS = r"""
static void xTaskNotifyGive(TaskHandle_t task)
{
    assert(task);
    notifications++;
    s_current_req->result = ESP_OK;
    s_current_req = NULL;
}
esp_err_t __real_esp_partition_read(const esp_partition_t *p, size_t o, void *b, size_t s)
{ (void)p; (void)o; (void)b; (void)s; direct_calls++; return ESP_OK; }
esp_err_t __real_esp_partition_write(const esp_partition_t *p, size_t o, const void *b, size_t s)
{ (void)p; (void)o; (void)b; (void)s; direct_calls++; return ESP_OK; }
esp_err_t __real_esp_partition_erase_range(const esp_partition_t *p, size_t o, size_t s)
{ (void)p; (void)o; (void)s; direct_calls++; return ESP_OK; }
int main(void)
{
    fail_mutex = true;
    assert(__wrap_esp_partition_read(NULL, 0, NULL, 0) == ESP_ERR_NO_MEM);
    assert(!s_worker_mutex && !s_worker_task);
    fail_mutex = false;
    fail_task = true;
    assert(__wrap_esp_partition_write(NULL, 0, NULL, 0) == ESP_ERR_NO_MEM);
    assert(s_worker_mutex && !s_worker_task);
    assert(takes == 0 && notifications == 0 && direct_calls == 0);
    fail_task = false;
    assert(__wrap_esp_partition_erase_range(NULL, 0, 0) == ESP_OK);
    assert(takes == 1 && notifications == 1 && direct_calls == 0);
    assert(s_worker_task && !s_current_req);
    return 0;
}
"""


class FlashStackTests(unittest.TestCase):
    def test_allocation_failure_and_retry(self):
        source = (Path(__file__).parent / "../main/flash_stack_shim.c").read_text()
        source = "\n".join(line for line in source.splitlines()
                           if not line.startswith("#include"))
        with tempfile.TemporaryDirectory() as directory:
            harness = Path(directory) / "flash.c"
            binary = Path(directory) / "flash"
            harness.write_text(STUBS + source + CHECKS)
            command = shlex.split(os.environ.get("CC", "cc"))
            command += ["-std=c11", "-Wall", "-Wextra", "-Werror", str(harness), "-o", str(binary)]
            subprocess.run(command, check=True)
            subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    unittest.main()

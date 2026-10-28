#include <stdexcept>

#include "duckdb.hpp"
#include "duckdb_static_extension.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_rom_sys.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

extern "C" int32_t duckdb_extension_core_functions_describe(
    duckdb_extension_descriptor* descriptor);

static void memory(const char* stage) {
    ESP_LOGI("probe",
             "%s: internal=%u PSRAM=%u PSRAM_low_water=%u largest_PSRAM=%u stack_margin=%u", stage,
             (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL),
             (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM),
             (unsigned)heap_caps_get_minimum_free_size(MALLOC_CAP_SPIRAM),
             (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_SPIRAM),
             (unsigned)uxTaskGetStackHighWaterMark(nullptr));
}

static void expect(duckdb::Connection& connection, const char* sql, int64_t expected) {
    const auto start = esp_timer_get_time();
    auto result = connection.Query(sql);
    if (result->HasError()) {
        throw std::runtime_error(result->GetError());
    }
    if (result->RowCount() != 1 || result->ColumnCount() != 1) {
        throw std::runtime_error("Unexpected SQL result shape");
    }
    auto chunk = result->Fetch();
    if (!chunk || chunk->GetValue(0, 0).IsNull() ||
        chunk->GetValue(0, 0).GetValue<int64_t>() != expected) {
        throw std::runtime_error("Unexpected SQL result");
    }
    ESP_LOGI("probe", "PASS: %s -> %lld (%lld us)", sql, (long long)expected,
             (long long)(esp_timer_get_time() - start));
    memory("after query");
}

static void run_probe(void*) {
    // Leave internal RAM available for FreeRTOS stacks and mutexes.
    heap_caps_malloc_extmem_enable(0);
    ESP_ERROR_CHECK(heap_caps_register_failed_alloc_callback(
        [](size_t size, uint32_t caps, const char* function) {
            esp_rom_printf("ALLOC_FAIL: size=%u caps=0x%x function=%s internal_free=%u\n",
                           (unsigned)size, (unsigned)caps, function,
                           (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL));
        }));
    memory("before open");
    try {
        {
            auto first = std::make_shared<int>(42);
            auto second = first;
            if (*second != 42) {
                throw std::runtime_error("C++ shared ownership check failed");
            }
        }
        memory("after shared ownership check");
        if (duckdb_register_static_extension(duckdb_extension_core_functions_describe) != 0) {
            throw std::runtime_error("Cannot register core functions");
        }
        {
            duckdb::DBConfig config;
            memory("after config constructor");
            config.SetOptionByName("threads", duckdb::Value("1"));
            memory("after threads setting");
            config.SetOptionByName("memory_limit", duckdb::Value("8MB"));
            config.SetOptionByName("temp_directory", duckdb::Value(""));
            config.SetOptionByName("enable_external_access", duckdb::Value(false));
            duckdb::DuckDB database(nullptr, &config);
            duckdb::Connection connection(database);
            memory("after open");
            expect(connection, "SELECT 42", 42);
            expect(connection, "CREATE TABLE samples AS SELECT * FROM (VALUES (1), (2), (3)) t(x)",
                   3);
            expect(connection, "SELECT sum(x) FROM samples WHERE x > 1", 5);
            expect(connection, "SELECT length('hello')", 5);
            expect(connection, "SELECT week(DATE '2021-01-01')", 53);
            expect(connection, "SELECT CAST(123456789012345678901234567890 AS HUGEINT) % 97", 52);
            auto invalid = connection.Query("SELECT * FROM missing_table");
            if (!invalid->HasError()) {
                throw std::runtime_error("Invalid query unexpectedly succeeded");
            }
            expect(connection, "SELECT 7", 7);
        }
        memory("after close");
        ESP_LOGI("probe", "ALL SQL CHECKS PASSED");
    } catch (const std::exception& error) {
        ESP_LOGE("probe", "FAIL: %s", error.what());
        memory("failure");
    }
    vTaskDelete(nullptr);
}

extern "C" void app_main() {
    if (xTaskCreate(run_probe, "duckdb", 65536, nullptr, 1, nullptr) != pdPASS) {
        ESP_LOGE("probe", "FAIL: cannot allocate DuckDB task stack");
    }
}

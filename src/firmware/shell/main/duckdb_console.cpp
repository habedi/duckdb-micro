#include "duckdb_console.h"

#include <cerrno>
#include <cstdio>
#include <cstring>
#include <stdexcept>

#include "duckdb.hpp"
#include "duckdb/parallel/task_scheduler.hpp"
#include "duckdb_static_extension.h"
#include "esp_console.h"
#include "esp_heap_caps.h"
#include "esp_pthread.h"
#include "esp_rom_sys.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/idf_additions.h"
#include "freertos/task.h"
#include "linenoise/linenoise.h"

extern "C" int32_t duckdb_extension_core_functions_describe(duckdb_extension_descriptor*);
extern "C" int32_t duckdb_extension_parquet_describe(duckdb_extension_descriptor*);
extern "C" int32_t duckdb_extension_json_describe(duckdb_extension_descriptor*);
extern "C" bool shell_resolve_arg(const char* cmd, const char* in, char* out, size_t out_len);
extern "C" void shell_restore_linenoise_callbacks(void);
extern "C" esp_err_t flash_stack_shim_init(void);

namespace {
struct Request {
    const char* db_path;
    const char* sql;
    TaskHandle_t caller;
    int status;
};

bool next_page() {
    printf("-- More -- [Space: next, q: quit]");
    fflush(stdout);
    int key;
    do {
        key = getchar();
    } while (key != ' ' && key != 'q' && key != 'Q' && key != '\x03' && key != EOF);
    if (key == EOF) {
        clearerr(stdin);
    }
    printf("\r\x1b[K");
    return key == ' ';
}

bool query(duckdb::Connection& connection, const char* sql, bool interactive) {
    const auto start = esp_timer_get_time();
    auto result = connection.Query(sql);
    if (result->HasError()) {
        printf("SQL error: %s\n", result->GetError().c_str());
        return false;
    }
    const auto elapsed = esp_timer_get_time() - start;
    for (const auto& name : result->GetNames()) {
        printf("%s\t", name.c_str());
    }
    printf("\n");
    duckdb::idx_t shown = 0;
    duckdb::idx_t limit = interactive ? result->RowCount() : 100;
    while (shown < limit) {
        auto chunk = result->Fetch();
        if (!chunk || chunk->size() == 0) {
            break;
        }
        for (duckdb::idx_t row = 0; row < chunk->size() && shown < limit; ++row, ++shown) {
            if (interactive && shown != 0 && shown % 25 == 0 && !next_page()) {
                limit = shown;
                break;
            }
            for (duckdb::idx_t col = 0; col < chunk->ColumnCount(); ++col) {
                const auto value = chunk->GetValue(col, row);
                printf("%s\t", value.IsNull() ? "NULL" : value.ToString().c_str());
            }
            printf("\n");
        }
    }
    printf("%llu row(s), %lld ms", (unsigned long long)result->RowCount(),
           (long long)(elapsed / 1000));
    if (result->RowCount() > shown) {
        printf(" (first %llu shown)", (unsigned long long)shown);
    }
    printf("\n");
    return true;
}

void sql_task(void* argument) {
    auto& request = *static_cast<Request*>(argument);
    heap_caps_malloc_extmem_enable(0);
    printf("Opening DuckDB: internal=%u PSRAM=%u\n",
           (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL),
           (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM));
    try {
        // Initialize the shared flash worker before either query core can use it.
        if (flash_stack_shim_init() != ESP_OK) {
            throw std::runtime_error("Cannot initialize DuckDB flash worker");
        }
        auto worker = esp_pthread_get_default_config();
        worker.stack_size = 65536;
        worker.stack_alloc_caps = MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT;
        worker.pin_to_core = 1;
        worker.prio = 1;
        worker.thread_name = "duckdb_worker";
        worker.inherit_cfg = true;
        if (esp_pthread_set_cfg(&worker) != ESP_OK) {
            throw std::runtime_error("Cannot configure DuckDB worker stack");
        }
        duckdb::DBConfig config;
        config.SetOptionByName("threads", duckdb::Value("2"));
        config.SetOptionByName("async_threads", duckdb::Value("0"));
        config.SetOptionByName("memory_limit", duckdb::Value("8MB"));
        config.SetOptionByName("temp_directory", duckdb::Value(""));
        duckdb::DuckDB database(request.db_path, &config);
        if (duckdb::TaskScheduler::GetScheduler(*database.instance).NumberOfThreads() != 2) {
            throw std::runtime_error("Cannot start DuckDB query worker");
        }
        duckdb::Connection connection(database);
        if (request.db_path) {
            printf("DuckDB 2.0.0, database %s; storage under /sdcard and /data.\n",
                   request.db_path);
            printf("One SQL statement per line. .quit returns to msh.\n");
        } else {
            printf("DuckDB 2.0.0, in-memory database; storage under /sdcard and /data.\n");
            printf("One SQL statement per line. .quit returns to msh; tables disappear on exit.\n");
        }
        printf("Free internal RAM: %u; free PSRAM: %u\n",
               (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL),
               (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM));
        if (request.sql) {
            request.status = query(connection, request.sql, false) ? 0 : 1;
        } else {
            request.status = 0;
            linenoiseSetCompletionCallback(nullptr);
            linenoiseSetHintsCallback(nullptr);
            while (true) {
                errno = 0;
                char* line = linenoise("duckdb> ");
                if (!line) {
                    if (errno == EAGAIN) {
                        continue;
                    }
                    break;
                }
                if (strcmp(line, ".quit") == 0 || strcmp(line, ".exit") == 0) {
                    linenoiseFree(line);
                    break;
                }
                if (strcmp(line, ".help") == 0 || strcmp(line, "help") == 0) {
                    printf("DuckDB Micro SQL Help:\n");
                    printf("  .quit, .exit        Exit back to msh\n");
                    printf("  .tables             List tables in current database\n");
                    printf("  .help               Show this help message\n\n");
                    printf(
                        "Results pause every 25 rows. Space advances; q or Ctrl+C stops "
                        "output.\n\n");
                    printf("SQL Examples:\n");
                    printf("  SELECT 42 AS answer;\n");
                    printf("  CREATE TABLE t(id INT, name VARCHAR);\n");
                    printf("  INSERT INTO t VALUES (1, 'p4');\n");
                    printf("  SELECT * FROM t;\n");
                    printf("  SHOW TABLES;\n");
                    printf("  SELECT * FROM read_csv('/sdcard/p4_test.txt');\n");
                    printf("  SELECT * FROM read_parquet('/sdcard/test.parquet');\n");
                    printf("  SELECT * FROM read_json_auto('/sdcard/test.json');\n");
                    linenoiseFree(line);
                    continue;
                }
                if (strcmp(line, ".tables") == 0) {
                    linenoiseHistoryAdd(line);
                    query(connection, "SHOW TABLES;", true);
                    linenoiseFree(line);
                    continue;
                }
                if (*line) {
                    linenoiseHistoryAdd(line);
                    try {
                        query(connection, line, true);
                    } catch (const std::exception& error) {
                        printf("SQL error: %s\n", error.what());
                    }
                }
                linenoiseFree(line);
                vTaskDelay(1);
            }
        }
    } catch (const std::exception& error) {
        printf("duckdb: %s\n", error.what());
        request.status = 1;
    }
    if (!request.sql) {
        shell_restore_linenoise_callbacks();
    }
    xTaskNotifyGive(request.caller);
    vTaskDeleteWithCaps(nullptr);
}

int command(int argc, char** argv) {
    const char* file_arg = nullptr;
    const char* sql = nullptr;
    for (int i = 1; i < argc; ++i) {
        if (strcmp(argv[i], "-c") == 0 && i + 1 < argc && !sql) {
            sql = argv[++i];
        } else if (argv[i][0] != '-' && !file_arg) {
            file_arg = argv[i];
        } else {
            printf("Usage: duckdb [FILE] [-c \"SQL\"]\n");
            return 1;
        }
    }
    char resolved_path[512];
    const char* db_path = nullptr;
    if (file_arg && strcmp(file_arg, ":memory:") != 0) {
        if (!shell_resolve_arg("duckdb", file_arg, resolved_path, sizeof(resolved_path))) {
            return 1;
        }
        db_path = resolved_path;
    }
    Request request{db_path, sql, xTaskGetCurrentTaskHandle(), 1};
    // Keep scarce internal RAM available for DuckDB's mutexes and device drivers.
    if (xTaskCreatePinnedToCoreWithCaps(sql_task, "duckdb", 65536, &request, 1, nullptr, 0,
                                        MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT) != pdPASS) {
        printf("duckdb: cannot allocate SQL task stack\n");
        return 1;
    }
    ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
    return request.status;
}
}  // namespace

extern "C" void duckdb_console_register(void) {
    ESP_ERROR_CHECK(heap_caps_register_failed_alloc_callback(
        [](size_t size, uint32_t caps, const char* function) {
            esp_rom_printf("ALLOC_FAIL size=%u caps=0x%x function=%s internal=%u\n", (unsigned)size,
                           (unsigned)caps, function,
                           (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL));
        }));
    if (duckdb_register_static_extension(duckdb_extension_core_functions_describe) != 0) {
        ESP_ERROR_CHECK(ESP_FAIL);
    }
    if (duckdb_register_static_extension(duckdb_extension_parquet_describe) != 0) {
        ESP_ERROR_CHECK(ESP_FAIL);
    }
    if (duckdb_register_static_extension(duckdb_extension_json_describe) != 0) {
        ESP_ERROR_CHECK(ESP_FAIL);
    }
    esp_console_cmd_t cmd{};
    cmd.command = "duckdb";
    cmd.help = "Open a SQL prompt or run SQL: duckdb [FILE] [-c \"SQL\"]";
    cmd.func = command;
    ESP_ERROR_CHECK(esp_console_cmd_register(&cmd));
}

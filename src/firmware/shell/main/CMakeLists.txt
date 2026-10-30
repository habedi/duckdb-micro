set(ROOT "${CMAKE_CURRENT_LIST_DIR}/../../../..")
idf_component_register(
    SRCS "main.c" "editor.c" "path_util.c" "flash_stack_shim.c"
         "network.cpp" "duckdb_console.cpp" "${ROOT}/src/firmware/main/llroundl.cpp"
    INCLUDE_DIRS "." "${ROOT}/external/duckdb/src/include"
    LDFRAGMENTS "duckdb.lf"
    REQUIRES driver esp_driver_gpio fatfs sdmmc console esp_wifi esp_netif esp_event
             nvs_flash lwip esp_psram app_update esp_hw_support
             esp_http_client esp_http_server mbedtls esp_timer pthread esp_libc vfs cxx esp_hosted esp_littlefs)
add_prebuilt_library(duckdb_port "${ROOT}/build/duckdb/src/libduckdb_esp32p4.a"
                    REQUIRES esp_libc vfs pthread cxx)
add_prebuilt_library(duckdb_core_functions
                    "${ROOT}/build/duckdb/extension/core_functions/libcore_functions_extension.a")
add_prebuilt_library(duckdb_parquet
                    "${ROOT}/build/duckdb/extension/parquet/libparquet_extension.a")
add_prebuilt_library(duckdb_json
                    "${ROOT}/build/duckdb/extension/json/libjson_extension.a")
add_prebuilt_library(duckdb_zstd
                    "${ROOT}/build/duckdb/third_party/zstd/libduckdb_zstd.a")
add_prebuilt_library(duckdb_yyjson
                    "${ROOT}/build/duckdb/third_party/yyjson/libduckdb_yyjson.a")
target_link_libraries(${COMPONENT_LIB} PRIVATE
                      duckdb_parquet duckdb_json duckdb_zstd duckdb_yyjson
                      duckdb_core_functions duckdb_port)
target_compile_definitions(${COMPONENT_LIB} PRIVATE NDEBUG)
target_compile_options(${COMPONENT_LIB} PRIVATE "$<$<COMPILE_LANGUAGE:CXX>:-std=gnu++17>"
                       "$<$<COMPILE_LANGUAGE:C>:-Wno-error=unused-function>")
set_source_files_properties("${ROOT}/src/firmware/main/llroundl.cpp" PROPERTIES COMPILE_OPTIONS "-fno-builtin-llroundl")
target_link_options(${COMPONENT_LIB} INTERFACE "-Wl,--wrap=write"
                                               "-Wl,--wrap=esp_partition_read"
                                               "-Wl,--wrap=esp_partition_write"
                                               "-Wl,--wrap=esp_partition_erase_range"
                                               "-Wl,--undefined=llroundl")

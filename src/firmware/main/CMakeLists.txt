set(ROOT "${CMAKE_CURRENT_LIST_DIR}/../../..")
idf_component_register(SRCS "probe.cpp" "llroundl.cpp" INCLUDE_DIRS "${ROOT}/external/duckdb/src/include"
                       REQUIRES esp_psram esp_timer pthread esp_libc vfs cxx)
add_prebuilt_library(duckdb_port "${ROOT}/build/duckdb/src/libduckdb_esp32p4.a"
                     REQUIRES esp_libc vfs pthread cxx)
add_prebuilt_library(duckdb_core_functions
                     "${ROOT}/build/duckdb/extension/core_functions/libcore_functions_extension.a")
target_link_libraries(${COMPONENT_LIB} PRIVATE duckdb_core_functions duckdb_port)
target_compile_definitions(${COMPONENT_LIB} PRIVATE NDEBUG DUCKDB_NO_THREADS)
set_target_properties(${COMPONENT_LIB} PROPERTIES CXX_STANDARD 17 CXX_STANDARD_REQUIRED ON CXX_EXTENSIONS OFF)
target_compile_options(${COMPONENT_LIB} PRIVATE -std=gnu++17)
set_source_files_properties(llroundl.cpp PROPERTIES COMPILE_OPTIONS "-fno-builtin-llroundl")
target_link_options(${COMPONENT_LIB} INTERFACE "-Wl,--undefined=llroundl")

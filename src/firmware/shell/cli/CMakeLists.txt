cmake_minimum_required(VERSION 3.20)
project(duckdb_micro_cli LANGUAGES CXX)

get_filename_component(ROOT "${CMAKE_CURRENT_LIST_DIR}/../../../.." ABSOLUTE)
set(CLI_ROOT "${ROOT}/external/duckdb/tools/shell")
add_library(duckdb_micro_cli STATIC
    complete.cpp
    "${ROOT}/external/duckdb/extension/autocomplete/autocomplete_extension.cpp"
    "${CLI_ROOT}/shell.cpp" "${CLI_ROOT}/shell_command_line_option.cpp"
    "${CLI_ROOT}/shell_extension.cpp" "${CLI_ROOT}/shell_grammar_extension.cpp"
    "${CLI_ROOT}/shell_helpers.cpp" "${CLI_ROOT}/shell_manual.cpp"
    "${CLI_ROOT}/shell_metadata_command.cpp" "${CLI_ROOT}/shell_prompt.cpp"
    "${CLI_ROOT}/shell_renderer.cpp" "${CLI_ROOT}/shell_highlight.cpp"
    "${CLI_ROOT}/shell_profiler_renderer.cpp" "${CLI_ROOT}/shell_progress_bar.cpp"
    "${CLI_ROOT}/shell_render_table_metadata.cpp" "${CLI_ROOT}/linenoise/highlighting.cpp")
target_include_directories(duckdb_micro_cli PRIVATE
    "${ROOT}/external/duckdb/src/include" "${CLI_ROOT}/include" "${CLI_ROOT}/linenoise/include"
    "${ROOT}/external/duckdb/third_party/utf8proc/include"
    "${ROOT}/external/duckdb/third_party/fmt/include"
    "${ROOT}/external/duckdb/extension/autocomplete/include"
    "${ROOT}/external/esp-idf/components/console")
target_compile_features(duckdb_micro_cli PRIVATE cxx_std_17)
target_compile_options(duckdb_micro_cli PRIVATE -fno-rtti -ffunction-sections -fdata-sections)
target_compile_definitions(duckdb_micro_cli PRIVATE
    NDEBUG SHELL_INLINE_AUTOCOMPLETE=1 SQLITE_OMIT_POPEN=1 SQLITE_NOHAVE_SYSTEM=1 SQLITE_DISABLE_LFS=1)

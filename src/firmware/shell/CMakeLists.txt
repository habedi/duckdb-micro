cmake_minimum_required(VERSION 3.22)
# All firmware components are vendored; configure without registry access.
set(ENV{IDF_COMPONENT_MANAGER} 0)
set(SDKCONFIG_DEFAULTS "${CMAKE_CURRENT_LIST_DIR}/../sdkconfig.defaults;${CMAKE_CURRENT_LIST_DIR}/sdkconfig.defaults")
set(EXTRA_COMPONENT_DIRS
    "${CMAKE_CURRENT_LIST_DIR}/../../../external/esp_hosted"
    "${CMAKE_CURRENT_LIST_DIR}/../../../external/esp_wifi_remote"
    "${CMAKE_CURRENT_LIST_DIR}/../../../external/esp_littlefs"
)
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
idf_build_set_property(MINIMAL_BUILD ON)
project(duckdb_shell)

#pragma once

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif
void duckdb_console_register(void);
int duckdb_remote_query(const char* path, const char* sql, char* output, size_t capacity);
#ifdef __cplusplus
}
#endif

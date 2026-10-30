"""Exercise production network download, request, and JSON boundary handling."""

import importlib.util
import io
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/firmware/shell/main/network.cpp"


class NetworkTests(unittest.TestCase):
    def compile_run(self, source):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "check.cpp"
            path.write_text(source)
            binary = Path(directory) / "check"
            command = shlex.split(os.environ.get("CXX", "c++"))
            for name in ("CXXFLAGS", "SANFLAGS"):
                command += shlex.split(os.environ.get(name, ""))
            command += ["-std=c++17", "-Wall", "-Wextra", "-I",
                        str(ROOT / "src/firmware/shell/main"), str(path), "-o", str(binary)]
            command += shlex.split(os.environ.get("LDFLAGS", ""))
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_hardware_http_fixtures(self):
        spec = importlib.util.spec_from_file_location("network", ROOT / "scripts/check-network.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for path in ("/csv", "/chunked", "/truncated", "/redirect", "/missing"):
            handler = Mock(path=path, wfile=io.BytesIO())
            module.FixtureHandler.do_GET(handler)
            if path == "/redirect":
                handler.send_response.assert_called_once_with(302)
                handler.send_header.assert_called_once_with("Location", "/csv")
            elif path == "/missing":
                handler.send_error.assert_called_once_with(404)
            else:
                handler.send_response.assert_called_once_with(200)
                body = handler.wfile.getvalue()
                if path == "/chunked":
                    self.assertEqual(body, b"14\r\n" + module.CSV + b"\r\n0\r\n\r\n")
                else:
                    self.assertEqual(body, module.CSV)
                    length = len(module.CSV) + (10 if path == "/truncated" else 0)
                    handler.send_header.assert_called_once_with("Content-Length", str(length))

    def test_json_escaping_and_capacity(self):
        self.compile_run(r'''
#include "network_json.h"
#include <cassert>
int main() {
    char buffer[64];
    NetworkJson json(buffer, sizeof(buffer));
    json.string("a\"\\\n\t\x01");
    assert(strcmp(buffer, "\"a\\u0022\\u005c\\u000a\\u0009\\u0001\"") == 0);
    char small[5] = {};
    NetworkJson bounded(small, sizeof(small));
    bounded.append("1234");
    bool failed = false;
    try { bounded.append("5"); } catch (const std::length_error&) { failed = true; }
    assert(failed && strcmp(small, "1234") == 0);
    NetworkJson empty(small, 0);
    failed = false;
    try { empty.append(""); } catch (const std::length_error&) { failed = true; }
    assert(failed);
}
''')

    def test_download_failures_preserve_existing_files(self):
        source = SOURCE.read_text()
        production = source[source.index("struct Download {"):source.index("int fetch_command(")]
        self.compile_run(r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cerrno>
#include <fcntl.h>
#include <sys/types.h>
using TaskHandle_t = int;
constexpr int ESP_OK = 0;
constexpr size_t DOWNLOAD_LIMIT = 16 * 1024 * 1024;
struct esp_http_client_config_t {
    const char* url; int timeout_ms; int (*crt_bundle_attach)(void*);
    bool disable_auto_redirect;
};
int esp_crt_bundle_attach(void*) { return 0; }
int scenario, reads, removed, closed, cleaned, bytes;
bool sync_clock(const char*) { return scenario != 13; }
int* esp_http_client_init(esp_http_client_config_t* c) {
    assert(c->disable_auto_redirect && c->crt_bundle_attach && c->timeout_ms == 15000);
    return scenario == 1 ? nullptr : reinterpret_cast<int*>(1);
}
int esp_http_client_open(int*, int) { return scenario == 2 ? -1 : 0; }
int64_t esp_http_client_fetch_headers(int*) {
    return scenario == 3 ? -1 : scenario == 4 ? DOWNLOAD_LIMIT + 1 : 4;
}
int esp_http_client_get_status_code(int*) { return scenario == 5 ? 404 : 200; }
int fake_open(const char*, int flags, int) {
    assert(flags & O_EXCL);
    return scenario == 6 ? -1 : 7;
}
#define open fake_open
int esp_http_client_read(int*, char*, size_t) {
    if (scenario == 7) return -1;
    return reads++ == 0 ? 4 : 0;
}
ssize_t write(int, const void*, size_t n) {
    if (scenario == 8) return -1;
    if (scenario == 12 && bytes++ == 0) { errno = EINTR; return -1; }
    return n > 2 ? 2 : n;
}
bool esp_http_client_is_complete_data_received(int*) { return scenario != 9; }
int fsync(int) { return scenario == 10 ? -1 : 0; }
int close(int) { closed++; return scenario == 11 ? -1 : 0; }
int unlink(const char*) { removed++; return 0; }
void esp_http_client_cleanup(int*) { cleaned++; }
void xTaskNotifyGive(int) {}
void vTaskDeleteWithCaps(void*) {}
''' + production + r'''
int main() {
    for (scenario = 0; scenario <= 13; ++scenario) {
        reads = removed = closed = cleaned = bytes = 0;
        Download request{"https://example.test/data.csv", "new.csv", 0, 1};
        download_task(&request);
        assert((request.status == 0) == (scenario == 0 || scenario == 12));
        assert(removed == (scenario >= 7 && scenario <= 11 ? 1 : 0));
        assert(closed == (scenario == 0 || (scenario >= 7 && scenario <= 12) ? 1 : 0));
        assert(cleaned == (scenario == 1 || scenario == 13 ? 0 : 1));
    }
}
''')

    def test_rest_authentication_and_request_limits(self):
        source = SOURCE.read_text()
        production = source[source.index("esp_err_t query_handler("):source.index("int server_command(")]
        self.compile_run(r'''
#include <cassert>
#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <string>
#include <algorithm>
using esp_err_t = int;
constexpr int ESP_OK = 0, ESP_FAIL = -1;
constexpr int HTTPD_401_UNAUTHORIZED = 401, HTTPD_400_BAD_REQUEST = 400;
constexpr size_t RESPONSE_LIMIT = 65536;
constexpr int MALLOC_CAP_SPIRAM = 1, MALLOC_CAP_8BIT = 2;
char authorization[72] = "Bearer secret";
char database_path[512] = "/sdcard/test.duckdb";
struct httpd_req_t { size_t content_len; };
int scenario, called, received, allocated, freed, error_status;
std::string response, status;
int httpd_req_get_hdr_value_str(httpd_req_t*, const char*, char* out, size_t) {
    strcpy(out, scenario == 1 ? "Bearer wrong" : authorization);
    return scenario == 2 ? -1 : 0;
}
int httpd_resp_set_hdr(httpd_req_t*, const char*, const char*) { return 0; }
int httpd_resp_set_type(httpd_req_t*, const char*) { return 0; }
int httpd_resp_set_status(httpd_req_t*, const char* s) { status = s; return 0; }
int httpd_resp_sendstr(httpd_req_t*, const char* s) { response = s; return 0; }
int httpd_resp_send_err(httpd_req_t*, int s, const char*) { error_status = s; return 0; }
int httpd_req_recv(httpd_req_t*, char* out, size_t size) {
    received++;
    if (scenario == 3) return -1;
    memset(out, scenario == 4 ? 0 : 'x', std::min(size, size_t(2)));
    return std::min(size, size_t(2));
}
void* heap_caps_malloc(size_t n, int caps) {
    assert(n == RESPONSE_LIMIT && caps == 3);
    allocated++;
    return scenario == 5 ? nullptr : malloc(n);
}
void heap_caps_free(void* p) { freed++; free(p); }
int duckdb_remote_query(const char*, const char* sql, char* output, size_t) {
    assert(strlen(sql) == 4);
    called++;
    strcpy(output, "{}");
    return scenario == 6 ? 503 : scenario == 7 ? 400 : 200;
}
''' + production + r'''
int main() {
    for (scenario = 0; scenario <= 9; ++scenario) {
        called = received = allocated = freed = error_status = 0;
        response.clear(); status.clear();
        httpd_req_t request{scenario == 8 ? size_t(0) : scenario == 9 ? size_t(4097) : size_t(4)};
        query_handler(&request);
        if (scenario == 1 || scenario == 2) assert(error_status == 401 && !received);
        if (scenario == 4 || scenario >= 8) assert(error_status == 400 && !called);
        if (scenario == 5 || scenario == 6) assert(status == "503 Service Unavailable");
        if (scenario == 7) assert(status == "400 Bad Request");
        if (scenario == 0) assert(status == "200 OK" && response == "{}" && received == 2);
        assert(freed == (allocated && scenario != 5 ? 1 : 0));
    }
}
''')

    def test_https_clock_initialization_and_cleanup(self):
        source = SOURCE.read_text()
        production = source[source.index("bool sync_clock("):source.index("struct Download {")]
        self.compile_run(r'''
#include <cassert>
#include <cstdio>
#include <cstring>
#include <ctime>
constexpr int ESP_OK = 0;
int scenario, initialized, waited, freed;
struct esp_sntp_config_t { const char* server; };
#define ESP_NETIF_SNTP_DEFAULT_CONFIG(server) {server}
#define pdMS_TO_TICKS(value) (value)
using esp_err_t = int;
std::time_t fake_time(std::time_t*) { return scenario == 1 ? 1793376000 : 0; }
#define time fake_time
int esp_netif_sntp_init(esp_sntp_config_t* config) {
    assert(!strcmp(config->server, "pool.ntp.org")); initialized++;
    return scenario == 2 ? -1 : 0;
}
int esp_netif_sntp_sync_wait(int delay) { assert(delay == 15000); waited++; return scenario == 3 ? -1 : 0; }
void esp_netif_sntp_deinit() { freed++; }
''' + production + r'''
int main() {
    assert(sync_clock("http://example.test/") && !initialized);
    for (scenario = 0; scenario <= 3; ++scenario) {
        initialized = waited = freed = 0;
        assert(sync_clock("https://example.test/") == (scenario <= 1));
        assert(initialized == (scenario == 1 ? 0 : 1));
        assert(waited == (scenario == 0 || scenario == 3 ? 1 : 0));
        assert(freed == waited);
    }
}
''')

    def test_remote_sql_policy_and_busy_cleanup(self):
        source = (ROOT / "src/firmware/shell/main/duckdb_console.cpp").read_text()
        production = source[source.index('extern "C" int duckdb_remote_query('):
                            source.index('extern "C" void duckdb_console_register(')]
        self.compile_run(r'''
#include "network_json.h"
#include <atomic>
#include <cassert>
#include <memory>
#include <string>
#include <vector>
std::atomic_flag sql_busy = ATOMIC_FLAG_INIT;
int scenario, options;
constexpr int ESP_OK = 0;
void heap_caps_malloc_extmem_enable(int) {}
int flash_stack_shim_init() { return scenario == 1 ? -1 : 0; }
namespace duckdb {
using idx_t = size_t;
enum class StatementType { SELECT_STATEMENT, OTHER };
struct SQLStatement { StatementType type; };
struct Value {
    std::string text;
    Value(const char* s) : text(s) {}
    static Value BOOLEAN(bool b) { assert(!b); return Value("false"); }
    bool IsNull() const { return false; }
    std::string ToString() const { return text; }
};
struct DBConfig {
    void SetOptionByName(const char* key, const Value& value) {
        options++;
        if (!strcmp(key, "access_mode")) assert(value.text == "READ_ONLY");
        if (!strcmp(key, "enable_external_access")) assert(value.text == "false");
        if (!strcmp(key, "threads")) assert(value.text == "1");
        if (!strcmp(key, "memory_limit")) assert(value.text == "8MB");
        if (!strcmp(key, "temp_directory")) assert(value.text.empty());
    }
};
struct DuckDB { DuckDB(const char*, DBConfig*) { assert(options == 6); } };
struct Chunk {
    idx_t size() const { return scenario == 6 ? 101 : scenario == 7 ? 100 : 1; }
    idx_t ColumnCount() const { return 1; }
    Value GetValue(idx_t, idx_t) const { return Value("42"); }
};
struct Result {
    bool fetched = false;
    bool HasError() const { return scenario == 4; }
    std::string GetError() const { return "failure"; }
    std::vector<std::string> GetNames() const { return {"answer"}; }
    std::unique_ptr<Chunk> Fetch() {
        if (fetched) return nullptr;
        fetched = true;
        return std::make_unique<Chunk>();
    }
};
struct Connection {
    Connection(DuckDB&) {}
    std::vector<std::unique_ptr<SQLStatement>> ExtractStatements(const char*) {
        std::vector<std::unique_ptr<SQLStatement>> result;
        int count = scenario == 2 ? 2 : 1;
        for (int i = 0; i < count; ++i) {
            result.push_back(std::make_unique<SQLStatement>(SQLStatement{
                scenario == 3 ? StatementType::OTHER : StatementType::SELECT_STATEMENT}));
        }
        return result;
    }
    std::unique_ptr<Result> Submit(std::unique_ptr<SQLStatement>) {
        return std::make_unique<Result>();
    }
};
template <class T = void> struct QueryResultStream {
    std::unique_ptr<Result> result;
    QueryResultStream(std::unique_ptr<Result> value) : result(std::move(value)) {}
    std::unique_ptr<Chunk> Fetch() { return result->Fetch(); }
    bool HasError() { return result->HasError(); }
    std::string GetError() { return result->GetError(); }
    std::vector<std::string> GetNames() { return result->GetNames(); }
};
}
''' + production + r'''
int main() {
    char output[128];
    for (scenario = 0; scenario <= 5; ++scenario) {
        options = 0;
        int result = duckdb_remote_query("test.db", "SELECT 42", output,
                                         scenario == 5 ? 48 : sizeof(output));
        assert(result == (scenario == 0 ? 200 : 400));
        assert(!sql_busy.test_and_set());
        assert(duckdb_remote_query("test.db", "SELECT 42", output, sizeof(output)) == 503);
        sql_busy.clear();
        if (scenario == 0) {
            assert(!strcmp(output, "{\"columns\":[\"answer\"],\"rows\":[[\"42\"]],\"truncated\":false}"));
        }
    }
    for (scenario = 6; scenario <= 7; ++scenario) {
        options = 0;
        char large[4096];
        assert(duckdb_remote_query("test.db", "SELECT 42", large, sizeof(large)) == 200);
        const char* expected = scenario == 6 ? "\"truncated\":true" : "\"truncated\":false";
        assert(strstr(large, expected));
        size_t rows = 0;
        for (const char* at = large; (at = strstr(at, "[\"42\"]")); at++) rows++;
        assert(rows == 100);
    }
}
''')


if __name__ == "__main__":
    unittest.main()

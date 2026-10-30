#include "network.h"

#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>

#include <cerrno>
#include <cstdio>
#include <cstring>
#include <ctime>

#include "duckdb_console.h"
#include "esp_console.h"
#include "esp_crt_bundle.h"
#include "esp_heap_caps.h"
#include "esp_http_client.h"
#include "esp_http_server.h"
#include "esp_netif_sntp.h"
#include "esp_random.h"
#include "freertos/FreeRTOS.h"
#include "freertos/idf_additions.h"
#include "freertos/task.h"

extern "C" bool shell_resolve_arg(const char*, const char*, char*, size_t);

namespace {
constexpr size_t DOWNLOAD_LIMIT = 16 * 1024 * 1024;
constexpr size_t RESPONSE_LIMIT = 65536;
httpd_handle_t server = nullptr;
char database_path[512];
char authorization[72];

bool sync_clock(const char* url) {
    if (strncmp(url, "https://", 8) != 0 || time(nullptr) >= 1704067200) return true;
    esp_sntp_config_t config = ESP_NETIF_SNTP_DEFAULT_CONFIG("pool.ntp.org");
    if (esp_netif_sntp_init(&config) != ESP_OK) return false;
    esp_err_t result = esp_netif_sntp_sync_wait(pdMS_TO_TICKS(15000));
    esp_netif_sntp_deinit();
    if (result != ESP_OK) printf("fetch_csv: cannot synchronize HTTPS certificate clock\n");
    return result == ESP_OK;
}

struct Download {
    const char* url;
    const char* path;
    TaskHandle_t caller;
    int status;
};

void download_task(void* argument) {
    auto& request = *static_cast<Download*>(argument);
    esp_http_client_config_t config{};
    config.url = request.url;
    config.timeout_ms = 15000;
    config.crt_bundle_attach = esp_crt_bundle_attach;
    // Redirects must not silently downgrade HTTPS or change the download source.
    config.disable_auto_redirect = true;
    auto client = sync_clock(request.url) ? esp_http_client_init(&config) : nullptr;
    int fd = -1;
    bool created = false;
    size_t total = 0;
    char buffer[2048];
    if (client && esp_http_client_open(client, 0) == ESP_OK) {
        int64_t length = esp_http_client_fetch_headers(client);
        if (length >= 0 && length <= DOWNLOAD_LIMIT &&
            esp_http_client_get_status_code(client) == 200) {
            fd = open(request.path, O_WRONLY | O_CREAT | O_EXCL, 0666);
            created = fd >= 0;
            if (created) {
                bool ok = true;
                while (true) {
                    int count = esp_http_client_read(client, buffer, sizeof(buffer));
                    if (count < 0 || (count > 0 && total + count > DOWNLOAD_LIMIT)) {
                        ok = false;
                        break;
                    }
                    if (count == 0) break;
                    size_t written = 0;
                    while (written < static_cast<size_t>(count)) {
                        ssize_t n = write(fd, buffer + written, count - written);
                        if (n < 0 && errno == EINTR) continue;
                        if (n <= 0) {
                            ok = false;
                            break;
                        }
                        written += n;
                    }
                    if (!ok) break;
                    total += count;
                }
                ok = ok && esp_http_client_is_complete_data_received(client);
                if (ok && fsync(fd) != 0) ok = false;
                if (close(fd) != 0) ok = false;
                fd = -1;
                if (ok) request.status = 0;
            }
        }
    }
    if (fd >= 0) close(fd);
    if (client) esp_http_client_cleanup(client);
    if (request.status && created && unlink(request.path) != 0) {
        printf("fetch_csv: cannot remove partial file %s\n", request.path);
    }
    if (request.status)
        printf("fetch_csv: download failed (HTTP 200 required; new files only)\n");
    else
        printf("Downloaded %u bytes to %s\n", (unsigned)total, request.path);
    xTaskNotifyGive(request.caller);
    vTaskDeleteWithCaps(nullptr);
}

int fetch_command(int argc, char** argv) {
    if (argc != 3 ||
        (strncmp(argv[1], "http://", 7) != 0 && strncmp(argv[1], "https://", 8) != 0)) {
        printf("Usage: fetch_csv http[s]://HOST/PATH FILE (maximum 16 MiB)\n");
        return 1;
    }
    char path[512];
    if (!shell_resolve_arg("fetch_csv", argv[2], path, sizeof(path))) return 1;
    Download request{argv[1], path, xTaskGetCurrentTaskHandle(), 1};
    if (xTaskCreatePinnedToCoreWithCaps(download_task, "fetch_csv", 65536, &request, 1, nullptr, 0,
                                        MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT) != pdPASS) {
        printf("fetch_csv: cannot allocate download stack\n");
        return 1;
    }
    ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
    return request.status;
}

esp_err_t query_handler(httpd_req_t* request) {
    char supplied[sizeof(authorization)];
    if (httpd_req_get_hdr_value_str(request, "Authorization", supplied, sizeof(supplied)) !=
            ESP_OK ||
        strcmp(supplied, authorization) != 0) {
        httpd_resp_set_hdr(request, "WWW-Authenticate", "Bearer");
        return httpd_resp_send_err(request, HTTPD_401_UNAUTHORIZED, "Bearer token required");
    }
    if (request->content_len == 0 || request->content_len > 4096) {
        return httpd_resp_send_err(request, HTTPD_400_BAD_REQUEST,
                                   "SQL body must be 1..4096 bytes");
    }
    char sql[4097];
    size_t used = 0;
    while (used < request->content_len) {
        int count = httpd_req_recv(request, sql + used, request->content_len - used);
        if (count <= 0) return ESP_FAIL;
        used += count;
    }
    if (memchr(sql, 0, used)) {
        return httpd_resp_send_err(request, HTTPD_400_BAD_REQUEST, "NUL in SQL body");
    }
    sql[used] = 0;
    auto output =
        static_cast<char*>(heap_caps_malloc(RESPONSE_LIMIT, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    if (!output) {
        httpd_resp_set_status(request, "503 Service Unavailable");
        return httpd_resp_sendstr(request, "Out of memory");
    }
    int status = duckdb_remote_query(database_path, sql, output, RESPONSE_LIMIT);
    httpd_resp_set_type(request, "application/json");
    httpd_resp_set_hdr(request, "Cache-Control", "no-store");
    httpd_resp_set_status(request, status == 200   ? "200 OK"
                                   : status == 503 ? "503 Service Unavailable"
                                                   : "400 Bad Request");
    esp_err_t result =
        httpd_resp_sendstr(request, status == 503 ? "{\"error\":\"SQL engine busy\"}" : output);
    heap_caps_free(output);
    return result;
}

int server_command(int argc, char** argv) {
    if (argc == 2 && strcmp(argv[1], "stop") == 0) {
        if (server && httpd_stop(server) != ESP_OK) return 1;
        server = nullptr;
        memset(authorization, 0, sizeof(authorization));
        printf("Query server stopped\n");
        return 0;
    }
    if (argc != 3 || strcmp(argv[1], "start") != 0 || server) {
        printf("Usage: query_server start DATABASE | query_server stop (port 8080)\n");
        return 1;
    }
    struct stat info;
    if (!shell_resolve_arg("query_server", argv[2], database_path, sizeof(database_path)) ||
        stat(database_path, &info) != 0 || !S_ISREG(info.st_mode)) {
        printf("query_server: an existing database file is required\n");
        return 1;
    }
    unsigned char random[24];
    esp_fill_random(random, sizeof(random));
    memcpy(authorization, "Bearer ", 7);
    for (size_t i = 0; i < sizeof(random); ++i) {
        snprintf(authorization + 7 + i * 2, 3, "%02x", random[i]);
    }
    httpd_config_t config = HTTPD_DEFAULT_CONFIG();
    config.server_port = 8080;
    config.stack_size = 65536;
    config.task_caps = MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT;
    config.core_id = 0;
    config.max_open_sockets = 2;
    config.max_uri_handlers = 1;
    config.recv_wait_timeout = 5;
    config.send_wait_timeout = 5;
    esp_err_t error = httpd_start(&server, &config);
    if (error == ESP_OK) {
        httpd_uri_t endpoint{};
        endpoint.uri = "/query";
        endpoint.method = HTTP_POST;
        endpoint.handler = query_handler;
        error = httpd_register_uri_handler(server, &endpoint);
    }
    if (error != ESP_OK) {
        if (server) httpd_stop(server);
        server = nullptr;
        memset(authorization, 0, sizeof(authorization));
        printf("query_server: start failed (%d)\n", error);
        return 1;
    }
    printf(
        "Read-only HTTP query server on port 8080. Trusted networks only; HTTP is unencrypted.\n");
    printf("Authorization: %s\n", authorization);
    return 0;
}
}  // namespace

extern "C" void network_register(void) {
    esp_console_cmd_t cmd{};
    cmd.command = "fetch_csv";
    cmd.help = "Download a remote CSV: fetch_csv URL NEW_FILE";
    cmd.func = fetch_command;
    ESP_ERROR_CHECK(esp_console_cmd_register(&cmd));
    cmd.command = "query_server";
    cmd.help = "Read-only REST queries: query_server start DATABASE | stop";
    cmd.func = server_command;
    ESP_ERROR_CHECK(esp_console_cmd_register(&cmd));
}

"""Check ESP32 worker settings and thread limits without a board."""

import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def function(source, name):
    start = source.index("void " + name + "(")
    body = source.index("{", start)
    depth = 1
    end = body + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


class DualCoreTests(unittest.TestCase):
    def compile_and_run(self, source):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "check.cpp"
            binary = path.with_suffix("")
            path.write_text(source)
            command = shlex.split(os.environ.get("CXX", "c++"))
            subprocess.run([*command, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                            str(path), "-o", str(binary)], check=True)
            subprocess.run([str(binary)], check=True)

    def test_worker_uses_psram_stack_and_cpu_one_and_reports_configuration_failure(self):
        source = (ROOT / "src/firmware/shell/main/duckdb_console.cpp").read_text()
        settings = source[source.index("auto worker ="):source.index("duckdb::DBConfig config;")]
        self.compile_and_run(r'''
#include <cassert>
#include <stdexcept>
#include <cstring>
constexpr int MALLOC_CAP_SPIRAM = 1, MALLOC_CAP_8BIT = 2, ESP_OK = 0;
struct Config {
    int stack_size = 0, stack_alloc_caps = 0, pin_to_core = 0, prio = 0;
    const char *thread_name = "";
    bool inherit_cfg = false;
};
Config configured;
bool fail = false;
Config esp_pthread_get_default_config() { return {}; }
int esp_pthread_set_cfg(const Config *config) { configured = *config; return fail ? -1 : ESP_OK; }
void configure() {
''' + settings + r'''
}
int main() {
    configure();
    assert(configured.stack_size == 65536 && configured.stack_alloc_caps == 3);
    assert(configured.pin_to_core == 1 && configured.prio == 1 && configured.inherit_cfg);
    assert(std::strcmp(configured.thread_name, "duckdb_worker") == 0);
    fail = true;
    try { configure(); } catch (const std::runtime_error &) { return 0; }
    return 1;
}
''')

    def test_platform_rejects_extra_threads_and_async_workers(self):
        source = (ROOT / "external/duckdb/src/main/settings/custom_settings.cpp").read_text()
        methods = "\n".join(function(source, name) for name in (
            "ThreadsSetting::SetGlobal", "AsyncThreadsSetting::SetGlobal",
            "ExternalThreadsSetting::OnSet"))
        self.compile_and_run(r'''
#include <cassert>
#include <cstdint>
#include <stdexcept>
#define ESP_PLATFORM 1
using idx_t = unsigned long;
using InvalidInputException = std::runtime_error;
using SyntaxException = std::runtime_error;
struct Value {
    int64_t value;
    bool IsNull() const { return false; }
    template<class T> T GetValue() const { return static_cast<T>(value); }
};
struct DatabaseInstance {};
struct DBConfig { struct { idx_t maximum_threads = 2, async_threads = 0; } options; };
struct SettingCallbackInfo { DatabaseInstance *db; DBConfig &config; };
template<class T, class U> T NumericCast(U value) { return static_cast<T>(value); }
struct ThreadsSetting { static void SetGlobal(DatabaseInstance *, DBConfig &, const Value &); };
struct AsyncThreadsSetting { static void SetGlobal(DatabaseInstance *, DBConfig &, const Value &); };
struct ExternalThreadsSetting { static void OnSet(SettingCallbackInfo &, Value &); };
struct Settings { template<class T> static idx_t Get(DBConfig &) { return 1; } };
struct TaskScheduler {
    static TaskScheduler &GetScheduler(DatabaseInstance &) { static TaskScheduler s; return s; }
    void SetThreads(idx_t count, idx_t external) { assert(count <= 2 && external == 1); }
    void SetAsyncThreads(idx_t count) { assert(count == 0); }
};
void ResizeAutomaticHTTPClientPool(DatabaseInstance *, DBConfig &) {}
''' + methods + r'''
int main() {
    DBConfig config;
    DatabaseInstance db;
    for (int threads : {1, 2}) {
        ThreadsSetting::SetGlobal(&db, config, Value{threads});
        assert(config.options.maximum_threads == static_cast<idx_t>(threads));
    }
    for (int threads : {0, 3, 8}) {
        bool rejected = false;
        try { ThreadsSetting::SetGlobal(&db, config, Value{threads}); }
        catch (const std::runtime_error &) { rejected = true; }
        assert(rejected && config.options.maximum_threads == 2);
    }
    AsyncThreadsSetting::SetGlobal(&db, config, Value{0});
    for (int threads : {-1, 1}) {
        bool rejected = false;
        try { AsyncThreadsSetting::SetGlobal(&db, config, Value{threads}); }
        catch (const std::runtime_error &) { rejected = true; }
        assert(rejected && config.options.async_threads == 0);
    }
    SettingCallbackInfo info{&db, config};
    Value one{1};
    ExternalThreadsSetting::OnSet(info, one);
    Value zero{0};
    bool rejected = false;
    try { ExternalThreadsSetting::OnSet(info, zero); }
    catch (const std::runtime_error &) { rejected = true; }
    assert(rejected);
}
''')

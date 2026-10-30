"""Check ESP32 worker settings and thread limits without a board."""

import os
import importlib.util
import io
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch


RUNNER = shlex.split(os.environ.get("TEST_RUNNER", ""))


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
            for name in ("CXXFLAGS", "SANFLAGS"):
                command += shlex.split(os.environ.get(name, ""))
            command += ["-std=c++17", "-Wall", "-Wextra", "-Werror",
                        str(path), "-o", str(binary)]
            command += shlex.split(os.environ.get("LDFLAGS", ""))
            subprocess.run(command, check=True)
            subprocess.run([*RUNNER, str(binary)], check=True)

    def test_failed_worker_semaphore_allocation_throws_without_assertions(self):
        header = ROOT / "external/duckdb/third_party/concurrentqueue/lightweightsemaphore.h"
        self.compile_and_run(r'''
#define NDEBUG
#include <cerrno>
#define ESP_PLATFORM 1
#define sem_init injected_sem_init
''' + f'#include "{header}"\n' + r'''
#undef sem_init
extern "C" int injected_sem_init(sem_t *, int, unsigned) noexcept { return -1; }
int main() {
    try { duckdb_moodycamel::details::Semaphore sem; }
    catch (const std::bad_alloc &) { return 0; }
    return 1;
}
''')

    def test_hardware_check_requires_cpu_one_psram_execution_after_restart(self):
        spec = importlib.util.spec_from_file_location("dualcore", ROOT / "scripts/check-dualcore.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        marker = b"Query worker: CPU 1, PSRAM stack yes"
        for response, passes in ((marker, True), (b"CPU 0", False), (b"PSRAM stack no", False)):
            with self.subTest(response=response), \
                    patch.object(module.stress, "command", return_value=response) as command:
                if passes:
                    module.session(Mock(), io.BytesIO())
                    lines = [call.args[2] for call in command.call_args_list]
                    self.assertIn("SET threads=1;", lines)
                    self.assertIn("SET threads=2;", lines)
                else:
                    with self.assertRaisesRegex(RuntimeError, "Missing CPU 1"):
                        module.session(Mock(), io.BytesIO())
        reports = iter([marker])
        with patch.object(module.stress, "command",
                          side_effect=lambda *args, **kwargs: next(reports, b"")):
            with self.assertRaisesRegex(RuntimeError, "after worker restart"):
                module.session(Mock(), io.BytesIO())

    def test_worker_uses_psram_stack_and_cpu_one_and_reports_configuration_failure(self):
        source = (ROOT / "src/firmware/shell/main/duckdb_console.cpp").read_text()
        settings = source[source.index("auto worker ="):source.index("if (request.cli)")]
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

    def test_queue_allocations_preserve_alignment_and_never_fall_back_to_internal_ram(self):
        source = (ROOT / "external/duckdb/src/parallel/task_scheduler_queue.cpp").read_text()
        start = source.index("struct TaskQueueTraits")
        traits = source[start:source.index("typedef duckdb_moodycamel::ConcurrentQueue<", start)]
        self.compile_and_run(r'''
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#define ESP_PLATFORM 1
constexpr int MALLOC_CAP_SPIRAM = 1, MALLOC_CAP_8BIT = 2;
bool fail = false;
void *heap_caps_aligned_alloc(size_t alignment, size_t size, int caps) {
    assert(alignment == alignof(std::max_align_t) && caps == 3);
    return fail ? nullptr : std::malloc(size);
}
void heap_caps_free(void *ptr) { std::free(ptr); }
namespace duckdb_moodycamel { struct ConcurrentQueueDefaultTraits {}; }
''' + traits + r'''
int main() {
    auto ptr = TaskQueueTraits::malloc(128);
    assert(ptr != nullptr);
    TaskQueueTraits::free(ptr);
    fail = true;
    assert(TaskQueueTraits::malloc(128) == nullptr);
}
''')

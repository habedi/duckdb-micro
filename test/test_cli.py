"""Host checks for the upstream CLI input and ESP32 adapter."""

import contextlib
import importlib.util
import io
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
RUNNER = shlex.split(os.environ.get("TEST_RUNNER", ""))


class CliTests(unittest.TestCase):
    def compile_run(self, code, arguments=(), flags=()):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "cli.cpp"
            binary = Path(directory) / "cli"
            source.write_text(code)
            command = shlex.split(os.environ.get("CXX", "c++"))
            for name in ("CXXFLAGS", "SANFLAGS"):
                command += shlex.split(os.environ.get(name, ""))
            command += ["-std=c++17", "-Wall", "-Wextra", *flags,
                        str(source), "-o", str(binary)]
            command += shlex.split(os.environ.get("LDFLAGS", ""))
            subprocess.run(command, check=True)
            subprocess.run([*RUNNER, str(binary), *arguments], check=True)

    def test_console_selection_and_one_shot_compatibility(self):
        source = (ROOT / "src/firmware/shell/main/duckdb_console.cpp").read_text()
        parser = source[source.index("int command(int argc, char** argv) {"):]
        parser = parser[:parser.index("    char resolved_path[512];")]
        self.compile_run(r'''
#include <cassert>
#include <cstdio>
#include <cstring>
#include <initializer_list>
''' + parser + r'''
    return cli ? 2 : 0;
}
int main() {
    auto run = [](std::initializer_list<const char*> input) {
        char* args[8]; int n = 0;
        for (auto arg : input) args[n++] = const_cast<char*>(arg);
        return command(n, args);
    };
    assert(run({"duckdb"}) == 2);
    assert(run({"duckdb", "/sdcard/test.duckdb"}) == 2);
    assert(run({"duckdb", "--cli"}) == 2);
    assert(run({"duckdb", "--compact"}) == 0);
    assert(run({"duckdb", "-c", "SELECT 42"}) == 0);
    assert(run({"duckdb", "--compact", "-c", "SELECT 42"}) == 0);
    assert(run({"duckdb", "--cli", "-c", "SELECT 42"}) == 1);
    assert(run({"duckdb", "--cli", "--compact"}) == 1);
    assert(run({"duckdb", "--compact", "--compact"}) == 1);
    assert(run({"duckdb", "-c"}) == 1);
}
''')

    def test_task_cleanup_finishes_before_the_engine_is_released(self):
        source = (ROOT / "src/firmware/shell/main/duckdb_console.cpp").read_text()
        dispatch = source[source.index("    Request request{db_path"):
                          source.index("}  // namespace")]
        self.compile_run(r'''
#include <cassert>
#include <cstdio>
using TaskHandle_t = void*;
constexpr int MALLOC_CAP_SPIRAM = 1, MALLOC_CAP_8BIT = 2, pdPASS = 1;
constexpr int pdTRUE = 1, portMAX_DELAY = -1;
bool allocation_fails, notified, deleted;
struct Busy {
    void clear() { assert(allocation_fails || deleted); }
} sql_busy;
struct Request { const char* path; const char* sql; bool cli; TaskHandle_t caller; int status; };
TaskHandle_t xTaskGetCurrentTaskHandle() { return nullptr; }
void sql_task(void*) {}
int xTaskCreatePinnedToCoreWithCaps(void (*)(void*), const char*, int stack, void* request,
                                   int, TaskHandle_t* task, int core, int caps) {
    assert(stack == 98304 && core == 0 && caps == 3 && task);
    if (allocation_fails) return 0;
    *task = reinterpret_cast<void*>(42);
    static_cast<Request*>(request)->status = 0;
    return pdPASS;
}
void ulTaskNotifyTake(int, int) { notified = true; }
void vTaskDeleteWithCaps(TaskHandle_t task) {
    assert(notified && task == reinterpret_cast<void*>(42)); deleted = true;
}
int dispatch() {
    const char* db_path = nullptr; const char* sql = nullptr; bool cli = true;
''' + dispatch + r'''
int main() {
    assert(dispatch() == 0 && deleted);
    allocation_fails = true; notified = deleted = false;
    assert(dispatch() == 1 && !notified && !deleted);
}
''')

    def test_soak_duration_validation(self):
        spec = importlib.util.spec_from_file_location("cli_check", ROOT / "scripts/check-cli.py")
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        self.assertIsNone(cli.arguments(["fake"]).duration)
        self.assertEqual(cli.arguments(["fake", "--duration", "3600"]).duration, 3600)
        for value in ("0", "-1", "nan", "inf"):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                cli.arguments(["fake", "--duration", value])

    def test_catalog_completion_is_bounded_and_preserves_the_last_result(self):
        source = (ROOT / "src/firmware/shell/cli/complete.cpp").read_text()
        code = source[source.index('extern "C" bool esp_cli_sql_complete'):]
        stubs = r'''
#include <cassert>
#include <memory>
#include <string>
#include <vector>
#include <stdexcept>
#include <type_traits>
#include <cstdint>
static int scenario;
struct linenoiseCompletions { size_t len = 0; std::vector<std::string> values; };
void linenoiseAddCompletion(linenoiseCompletions* out, const char* text) {
    ++out->len; out->values.emplace_back(text);
}
namespace duckdb {
using string = std::string;
template<class T> using unique_ptr = std::unique_ptr<T>;
struct Value { string text; explicit Value(string s): text(std::move(s)) {} };
struct Row {
    string text;
    int64_t start;
    template<class T> T GetValue(int) {
        if constexpr (std::is_same<T, string>::value) return text;
        else return start;
    }
};
struct QueryResult {
    std::vector<Row> rows;
    bool HasError() { return scenario == 1; }
    auto begin() { return rows.begin(); }
    auto end() { return rows.end(); }
};
struct Connection { unique_ptr<QueryResult> Query(const char*, Value); };
}
namespace duckdb_shell {
struct ShellState {
    duckdb::unique_ptr<duckdb::QueryResult> last_result;
    bool last_result_referenced = true;
    duckdb::unique_ptr<duckdb::Connection> conn = std::make_unique<duckdb::Connection>();
    static ShellState& Get() { static ShellState state; return state; }
};
}
duckdb::unique_ptr<duckdb::QueryResult> duckdb::Connection::Query(const char* sql, Value input) {
    assert(string(sql).find("$1") != string::npos);
    assert(input.text == "SELECT * FROM cli_d");
    auto& state = duckdb_shell::ShellState::Get();
    state.last_result.reset();
    state.last_result_referenced = false;
    if (scenario == 2) throw std::runtime_error("injected");
    auto result = std::make_unique<QueryResult>();
    result->rows = {{"bad", -1}, {"bad", 9999}, {string(1024, 'x'), 14}};
    for (int i = 0; i < 32; ++i) result->rows.push_back({"cli_data", 14});
    return result;
}
'''
        checks = r'''
int main() {
    auto& state = duckdb_shell::ShellState::Get();
    for (scenario = 0; scenario < 3; ++scenario) {
        state.last_result = std::make_unique<duckdb::QueryResult>();
        state.last_result_referenced = true;
        auto saved = state.last_result.get();
        linenoiseCompletions out;
        assert(esp_cli_sql_complete("SELECT * FROM cli_d", &out, 1024) == (scenario == 0));
        assert(state.last_result.get() == saved && state.last_result_referenced);
        if (scenario == 0) {
            assert(out.len == 16);
            for (auto& text : out.values) assert(text == "SELECT * FROM cli_data");
        } else assert(out.len == 0);
    }
}
'''
        self.compile_run(stubs + code + checks)

    def test_thread_ids_work_for_unregistered_freertos_tasks(self):
        source = (ROOT / "external/duckdb/src/common/thread_util.cpp").read_text()
        code = source[source.index("thread_id ThreadUtil::GetThreadId()"):
                      source.index("\n#else\n\nvoid ThreadUtil::SleepMs")]
        stubs = r'''
#define ESP_PLATFORM 1
#include <cassert>
#include <thread>
#include <sstream>
#include <string>
#include <cstdint>
static void* current_task = reinterpret_cast<void*>(uintptr_t(0x1000));
static void* xTaskGetCurrentTaskHandle() { return current_task; }
using thread_id = std::thread::id;
using string = std::string;
struct ThreadUtil {
    static thread_id GetThreadId();
    static string GetThreadIdString();
};
'''
        checks = r'''
int main() {
    auto first = ThreadUtil::GetThreadId();
    assert(first != thread_id{} && first == ThreadUtil::GetThreadId());
    assert(!ThreadUtil::GetThreadIdString().empty());
    current_task = reinterpret_cast<void*>(uintptr_t(0x2000));
    assert(first != ThreadUtil::GetThreadId());
}
'''
        self.compile_run(stubs + code + checks)

    def test_session_pointer_survives_cleanup_and_clears_after_exceptions(self):
        source = (ROOT / "src/firmware/shell/main/duckdb_cli.cpp").read_text()
        code = source[source.index("int duckdb_cli_run("):]
        stubs = r'''
#include <cassert>
#include <cstdio>
#include <memory>
#include <string>
#include <stdexcept>
static bool fail_input;
static int destroyed;
static void cli_complete(const char*, int*) {}
static void linenoiseSetCompletionCallback(void(*)(const char*, int*)) {}
static void linenoiseSetHintsCallback(std::nullptr_t) {}
namespace duckdb {
struct Value { explicit Value(const char*) {} };
template<class T> auto make_uniq() { return std::make_unique<T>(); }
}
namespace duckdb_shell {
enum class InputMode { STANDARD };
enum class PagerMode { PAGER_OFF };
struct Prompt { void ParsePrompt(const char*) {} };
struct Config { void SetOptionByName(const char*, duckdb::Value) {} };
struct ShellState {
    Config config;
    FILE* out;
    std::unique_ptr<Prompt> main_prompt = std::make_unique<Prompt>();
    char continuePrompt[20];
    std::string zDbFilename;
    bool highlighting_enabled;
    PagerMode pager_mode;
    size_t max_width, max_rows, max_analyze_rows;
    static ShellState*& GetReference() { static ShellState* state; return state; }
    ~ShellState() { assert(GetReference() == this); ++destroyed; }
    void Initialize() {}
    void OpenDB() {}
    int ProcessInput(InputMode) {
        if (fail_input) throw std::runtime_error("injected");
        return 7;
    }
};
}
'''
        checks = r'''
int main() {
    assert(duckdb_cli_run(nullptr) == 7);
    assert(destroyed == 1 && duckdb_shell::ShellState::GetReference() == nullptr);
    fail_input = true;
    try { duckdb_cli_run(nullptr); assert(false); }
    catch (const std::runtime_error&) {}
    assert(destroyed == 2 && duckdb_shell::ShellState::GetReference() == nullptr);
}
'''
        self.compile_run(stubs + code + checks)

    def test_completion_bounds_and_prefixes(self):
        source = (ROOT / "src/firmware/shell/main/duckdb_cli.cpp").read_text()
        code = source[source.index("namespace {"):source.index("bool esp_cli_read_file(")]
        stubs = r'''
#include <cassert>
#include <string>
#include <vector>
#include <algorithm>
#include <cctype>
#include <stdexcept>
struct linenoiseCompletions { size_t len = 0; std::vector<std::string> values; };
void linenoiseAddCompletion(linenoiseCompletions* out, const char* text) {
    out->values.emplace_back(text); ++out->len;
}
size_t shell_command_line_limit() { return 1024; }
bool esp_cli_sql_complete(const char*, linenoiseCompletions*, size_t) { return false; }
namespace duckdb {
struct StringUtil {
    static std::string Lower(std::string text) {
        for (auto& c : text) c = std::tolower(static_cast<unsigned char>(c));
        return text;
    }
    static std::string Upper(std::string text) {
        for (auto& c : text) c = std::toupper(static_cast<unsigned char>(c));
        return text;
    }
    static bool StartsWith(const std::string& a, const std::string& b) {
        return a.compare(0, b.size(), b) == 0;
    }
};
struct ParserKeyword { std::string name; };
struct Parser {
    static std::vector<ParserKeyword> KeywordList() { return {{"select"}, {"set"}}; }
};
}
namespace duckdb_shell {
struct ShellState {
    static std::vector<std::string> GetMetadataCompletions(const char*, size_t) {
        return std::vector<std::string>(32, ".tables");
    }
};
}
'''
        checks = r'''
int main() {
    linenoiseCompletions out;
    cli_complete("SELECT se", &out);
    assert((out.values == std::vector<std::string>{"SELECT SELECT", "SELECT SET"}));
    out = {};
    cli_complete(".t", &out);
    assert(out.len == 16);
    out = {};
    cli_complete((std::string(1018, ' ') + "sel").c_str(), &out);
    assert(out.len == 0);
    cli_complete((std::string(1015, ' ') + "sel").c_str(), &out);
    assert(out.len == 1 && out.values[0].size() == 1021);
    out = {};
    cli_complete("", &out);
    cli_complete("SELECT ", &out);
    assert(out.len == 0);
}
'''
        self.compile_run(stubs + code + checks)

    def test_converted_display_memory_budget(self):
        source = (ROOT / "external/duckdb/tools/shell/shell_renderer.cpp").read_text()
        start = source.index("\t// Converted strings are outside")
        guard = source[start:source.index("#endif", start)]
        stubs = r'''
#include <cassert>
#include <cstdint>
#include <stdexcept>
#include <vector>
using idx_t = uint64_t;
namespace duckdb {
using InvalidInputException = std::runtime_error;
struct string_t { uint32_t length; uint32_t GetSize() const { return length; } };
struct Vector { string_t value; };
struct FlatVector {
    template<class T> static const T* GetData(Vector& v) { return &v.value; }
    static bool IsNull(Vector&, idx_t) { return false; }
};
}
struct Chunk {
    uint32_t bytes;
    std::vector<duckdb::Vector> data;
    uint32_t GetAllocationSize() { return bytes; }
    idx_t size() const { return 1; }
    idx_t ColumnCount() const { return data.size(); }
};
struct Renderer {
    uint32_t render_bytes = 0;
    void add(uint32_t size, uint32_t length = 0) {
        Chunk chunk{size, {{{length}}}};
        auto varchar_chunk = &chunk;
'''
        checks = r'''
    }
};
int main() {
    Renderer renderer;
    renderer.add(1024 * 1024);
    renderer.add(1024 * 1024);
    for (uint32_t size : {1U, 2U * 1024 * 1024 + 1, UINT32_MAX}) {
        try { renderer.add(size); assert(false); }
        catch (const std::runtime_error&) {}
        assert(renderer.render_bytes == 2 * 1024 * 1024);
    }
    Renderer referenced;
    referenced.add(0, 1024 * 1024);
    referenced.add(0, 1024 * 1024);
    try { referenced.add(0, 1); assert(false); }
    catch (const std::runtime_error&) {}
}
'''
        self.compile_run(stubs + guard + checks)

    def test_sql_completion_with_quotes_comments_and_unsigned_char(self):
        source = (ROOT / "external/duckdb/tools/shell/shell.cpp").read_text()
        code = source[source.index("enum class SQLParseState"):
                      source.index("void ShellState::ShellAddHistory")]
        stubs = """
#include <cassert>
#include <cstring>
#include <cstdint>
using idx_t = uint64_t;
struct ShellState { static bool SQLIsComplete(const char*); };
"""
        checks = r'''
int main() {
    const char* complete[] = {"SELECT 42;", "SELECT 1; SELECT 2;", "SELECT\n42;",
        "SELECT ';'; -- comment", "SELECT \"a;b\";", "SELECT $$a;b$$;",
        "SELECT $tag$a;b$tag$;", "SELECT $é$a;b$é$;", "SELECT 1; /* comment */"};
    const char* incomplete[] = {"SELECT 42", "SELECT ';'", "SELECT 'x;",
        "SELECT $$x;", "SELECT $tag$x;", "SELECT 1; /* open", "-- only a comment"};
    for (auto sql : complete) assert(ShellState::SQLIsComplete(sql));
    for (auto sql : incomplete) assert(!ShellState::SQLIsComplete(sql));
}
'''
        for flags in ((), ("-funsigned-char",)):
            with self.subTest(flags=flags):
                self.compile_run(stubs + code + checks, flags=flags)

    def test_read_file_restores_state_on_success_failure_and_exception(self):
        source = (ROOT / "src/firmware/shell/main/duckdb_cli.cpp").read_text()
        code = source[source.index("bool esp_cli_read_file("):
                      source.index("namespace duckdb {", source.index("bool esp_cli_read_file("))]
        stubs = r'''
#include <cassert>
#include <cstdio>
#include <cstring>
#include <memory>
#include <stdexcept>
#include <string>
#include <unordered_set>
namespace duckdb { using string = std::string; }
namespace duckdb_shell {
enum class InputMode { FILE };
struct ShellState {
    bool safe_mode = false;
    std::unordered_set<std::string> active_read_files;
    FILE* in = nullptr;
    int lineno = 77;
    int behavior = 0;
    void PrintDatabaseError(const char*) {}
    int ProcessInput(InputMode) {
        assert(in && active_read_files.size() == 1);
        lineno = 4;
        if (behavior == 2) throw std::runtime_error("injected");
        return behavior;
    }
};
}
extern "C" bool shell_resolve_arg(const char*, const char* path, char* out, size_t n) {
    if (strcmp(path, "reject") == 0) return false;
    return snprintf(out, n, "%s", path) >= 0;
}
'''
        checks = r'''
int main(int argc, char** argv) {
    assert(argc == 2);
    duckdb_shell::ShellState state;
    for (int behavior = 0; behavior < 3; behavior++) {
        state.behavior = behavior;
        try {
            assert(esp_cli_read_file(state, argv[1]) == (behavior == 0));
            assert(behavior != 2);
        } catch (const std::runtime_error&) { assert(behavior == 2); }
        assert(state.in == nullptr && state.lineno == 77 && state.active_read_files.empty());
    }
    assert(!esp_cli_read_file(state, "reject"));
    assert(!esp_cli_read_file(state, "/missing-cli-test-file"));
    state.safe_mode = true;
    assert(!esp_cli_read_file(state, argv[1]));
    state.safe_mode = false;
    state.active_read_files.insert(argv[1]);
    assert(!esp_cli_read_file(state, argv[1]));
    state.active_read_files = {"a", "b", "c", "d"};
    assert(!esp_cli_read_file(state, argv[1]));
}
'''
        with tempfile.NamedTemporaryFile() as fixture:
            self.compile_run(stubs + code + checks, (fixture.name,))

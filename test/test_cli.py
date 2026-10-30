"""Host checks for the upstream CLI input and ESP32 adapter."""

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
namespace duckdb { using InvalidInputException = std::runtime_error; }
struct Chunk {
    uint32_t bytes;
    uint32_t GetAllocationSize() { return bytes; }
};
struct Renderer {
    uint32_t render_bytes = 0;
    void add(uint32_t size) {
        Chunk chunk{size};
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

"""Exercise the SQL result renderer without an ESP32 or a host DuckDB build."""

import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import unittest


RUNNER = shlex.split(os.environ.get("TEST_RUNNER", ""))


ROOT = Path(__file__).resolve().parents[4]

STUBS = r'''
#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <memory>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

static long long esp_timer_get_time() { return 0; }
static std::string scenario;
static size_t fetched_rows = 0;
static bool drained = false;

namespace duckdb {
using idx_t = size_t;
struct Value {
    idx_t value;
    bool IsNull() const { return scenario == "null"; }
    std::string ToString() const {
        if (scenario == "text") return std::string("a\0b\n\r\t\\\x1b\x7f", 9) + u8"é";
        return std::to_string(value);
    }
};
struct Chunk {
    idx_t first, count;
    idx_t size() const { return count; }
    idx_t ColumnCount() const { return 1; }
    Value GetValue(idx_t, idx_t row) const { return {first + row}; }
};
enum class StatementType { SELECT_STATEMENT, OTHER };
struct Statement { StatementType type; };
struct Result {
    idx_t total, chunk_size, fetched = 0;
    bool failed = false;
    bool HasError() const { return failed; }
    std::string GetError() const { return "test failure"; }
    std::vector<std::string> GetNames() const {
        return {scenario == "text" ? "a\nb" : "i"};
    }
    std::unique_ptr<Chunk> Fetch() {
        if (scenario == "fetch_error" && fetched) { failed = true; return nullptr; }
        if (fetched == total) { drained = true; return nullptr; }
        auto chunk = std::make_unique<Chunk>();
        chunk->first = fetched;
        chunk->count = std::min(chunk_size, total - fetched);
        fetched += chunk->count;
        fetched_rows = fetched;
        return chunk;
    }
};
struct Connection {
    idx_t total, chunk_size;
    std::vector<std::unique_ptr<Statement>> ExtractStatements(const char* sql) {
        std::vector<std::unique_ptr<Statement>> result;
        int count = std::string(sql) == "multiple" ? 2 : (std::string(sql) == "empty" ? 0 : 1);
        while (count--) result.push_back(std::make_unique<Statement>(Statement{
            scenario == "write" ? StatementType::OTHER : StatementType::SELECT_STATEMENT}));
        return result;
    }
    std::unique_ptr<Result> Submit(std::unique_ptr<Statement>) {
        return std::make_unique<Result>(Result{total, chunk_size, 0, scenario == "submit_error"});
    }
};
static bool StreamsByDefault(StatementType type, const Result&, bool) {
    return type == StatementType::SELECT_STATEMENT;
}
template<class T = void> struct QueryResultStream {
    std::unique_ptr<Result> result;
    explicit QueryResultStream(std::unique_ptr<Result> r): result(std::move(r)) {}
    std::unique_ptr<Chunk> Fetch() { return result->Fetch(); }
    bool HasError() const { return result->HasError(); }
    std::string GetError() const { return result->GetError(); }
    std::vector<std::string> GetNames() const { return result->GetNames(); }
};
}
'''

CHECK = r'''
int main(int argc, char** argv) {
    if (argc != 5) return 2;
    scenario = argv[4];
    duckdb::Connection connection{std::strtoul(argv[1], nullptr, 10),
                                  std::strtoul(argv[2], nullptr, 10)};
    bool ok = query(connection, argv[4], std::atoi(argv[3]) != 0);
    fprintf(stderr, "fetched=%zu drained=%d\n", fetched_rows, drained);
    return ok ? 0 : 1;
}
'''


class DuckDBConsoleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(directory.cleanup)
        work = Path(directory.name)
        source = (ROOT / "src/firmware/shell/main/duckdb_console.cpp").read_text()
        renderer = source[source.index("bool next_page("):source.index("void sql_task(")]
        harness = work / "console.cpp"
        harness.write_text(STUBS + renderer + CHECK)
        cls.binary = work / "console"
        command = shlex.split(os.environ.get("CXX", "c++"))
        for name in ("CXXFLAGS", "SANFLAGS"):
            command += shlex.split(os.environ.get(name, ""))
        command += ["-std=c++17", "-Wall", "-Wextra", str(harness), "-o", str(cls.binary)]
        command += shlex.split(os.environ.get("LDFLAGS", ""))
        subprocess.run(command, check=True)

    def render(self, total, keys="", interactive=True, chunk_size=17, scenario="SELECT i"):
        result = subprocess.run(
            [*RUNNER, str(self.binary),
             str(total), str(chunk_size), str(int(interactive)), scenario],
            input=keys, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = result.stdout.replace("\x1b[K", "")
        rows = [int(row) for row in re.findall(r"^(\d+)\t$", output, re.MULTILINE)]
        return output, rows

    def test_page_boundaries_and_chunk_boundaries(self):
        for total in (0, 1, 24, 25, 26, 50, 51, 105):
            for chunk_size in (17, 25, 2048):
                with self.subTest(total=total, chunk_size=chunk_size):
                    output, rows = self.render(total, " " * 4, chunk_size=chunk_size)
                    self.assertEqual(rows, list(range(total)))
                    self.assertEqual(output.count("-- More --"), max(0, (total - 1) // 25))
                    self.assertNotIn("shown)", output)

    def test_quit_interrupt_and_end_of_input(self):
        for key in ("q", "Q", "\x03", ""):
            with self.subTest(key=key):
                output, rows = self.render(105, key)
                self.assertEqual(rows, list(range(25)))
                self.assertIn("25+ row(s), 0 ms (first 25 shown)", output)

    def test_quit_after_next_page(self):
        output, rows = self.render(105, " q")
        self.assertEqual(rows, list(range(50)))
        self.assertIn("(first 50 shown)", output)

    def test_other_keys_do_not_advance(self):
        output, rows = self.render(105, "\r\nxq")
        self.assertEqual(rows, list(range(25)))
        self.assertEqual(output.count("-- More --"), 1)

    def test_one_shot_never_reads_input(self):
        for total in (0, 25, 100, 105):
            with self.subTest(total=total):
                output, rows = self.render(total, "q", interactive=False)
                self.assertEqual(rows, list(range(min(total, 100))))
                self.assertNotIn("-- More --", output)
                self.assertEqual("(first 100 shown)" in output, total > 100)

    def test_large_result_stops_without_draining(self):
        result = subprocess.run([*RUNNER, str(self.binary), "1000000000", "2048", "0", "SELECT i"],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0)
        self.assertIn("100+ row(s)", result.stdout)
        self.assertIn("fetched=2048 drained=0", result.stderr)

    def test_multiple_and_empty_statements_never_execute(self):
        for sql in ("multiple", "empty"):
            result = subprocess.run([*RUNNER, str(self.binary), "1", "17", "0", sql],
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 1)
            self.assertIn("Exactly one SQL statement", result.stdout)
            self.assertIn("fetched=0 drained=0", result.stderr)

    def test_writes_drain_after_output_stops(self):
        for interactive, keys, shown in (("0", "", 100), ("1", "q", 25)):
            result = subprocess.run([*RUNNER, str(self.binary), "105", "17", interactive, "write"],
                                    input=keys, capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0)
            self.assertIn(f"105 row(s), 0 ms (first {shown} shown)", result.stdout)
            self.assertIn("fetched=105 drained=1", result.stderr)

    def test_control_characters_and_null(self):
        output, _ = self.render(1, scenario="text")
        self.assertIn(r"a\nb" + "\t\n", output)
        self.assertIn(r"a\0b\n\r\t\\\x1b\x7f" + "é", output)
        self.assertIn("NULL\t", self.render(1, scenario="null")[0])

    def test_submission_and_midstream_errors(self):
        for scenario in ("submit_error", "fetch_error"):
            result = subprocess.run([*RUNNER, str(self.binary), "20", "17", "0", scenario],
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 1)
            self.assertIn("SQL error: test failure", result.stdout)
            self.assertNotIn("row(s)", result.stdout)

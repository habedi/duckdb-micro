"""Exercise the SQL result renderer without an ESP32 or a host DuckDB build."""

import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[4]

STUBS = r'''
#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <memory>
#include <string>
#include <vector>

static long long esp_timer_get_time() { return 0; }

namespace duckdb {
using idx_t = size_t;
struct Value {
    idx_t value;
    bool IsNull() const { return false; }
    std::string ToString() const { return std::to_string(value); }
};
struct Chunk {
    idx_t first, count;
    idx_t size() const { return count; }
    idx_t ColumnCount() const { return 1; }
    Value GetValue(idx_t, idx_t row) const { return {first + row}; }
};
struct Result {
    idx_t total, chunk_size, fetched = 0;
    bool HasError() const { return false; }
    std::string GetError() const { return {}; }
    std::vector<std::string> GetNames() const { return {"i"}; }
    idx_t RowCount() const { return total; }
    std::unique_ptr<Chunk> Fetch() {
        auto chunk = std::make_unique<Chunk>();
        chunk->first = fetched;
        chunk->count = std::min(chunk_size, total - fetched);
        fetched += chunk->count;
        return chunk;
    }
};
struct Connection {
    idx_t total, chunk_size;
    std::unique_ptr<Result> Query(const char*) {
        return std::make_unique<Result>(Result{total, chunk_size});
    }
};
}
'''

CHECK = r'''
int main(int argc, char** argv) {
    if (argc != 4) return 2;
    duckdb::Connection connection{std::strtoul(argv[1], nullptr, 10),
                                  std::strtoul(argv[2], nullptr, 10)};
    return query(connection, "SELECT i", std::atoi(argv[3]) != 0) ? 0 : 1;
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

    def render(self, total, keys="", interactive=True, chunk_size=17):
        result = subprocess.run(
            [str(self.binary), str(total), str(chunk_size), str(int(interactive))],
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
                self.assertIn("105 row(s), 0 ms (first 25 shown)", output)

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

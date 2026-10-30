"""Check ESP32 open failures using the real portability code and disposable files."""

import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest


RUNNER = shlex.split(os.environ.get("TEST_RUNNER", ""))


ROOT = Path(__file__).resolve().parents[1]
STUBS = r'''
#include <cassert>
#include <cerrno>
#include <cstdio>
#include <cstring>
#include <fcntl.h>
#include <memory>
#include <stdexcept>
#include <string>
#include <unistd.h>
using std::string;
struct OpenFlags {
    bool missing, exists;
    bool ReturnNullIfNotExists() const { return missing; }
    bool ReturnNullIfExists() const { return exists; }
};
struct IOException : std::runtime_error {
    int code;
    IOException(std::initializer_list<std::pair<string, string>> properties,
                const char *, const string &, const char *)
        : std::runtime_error("open failed"), code(std::stoi(properties.begin()->second)) {}
};
static std::unique_ptr<int> attempt(const string &path, int open_flags, OpenFlags flags) {
    mode_t filesec = 0600;
'''
CHECKS = r'''
    return std::make_unique<int>(fd);
}
int main(int argc, char **argv) {
    assert(argc == 3);
    string mode = argv[2];
    string path = string(argv[1]) + "/" + mode;
    if (mode == "exclusive") {
        int fd = open(path.c_str(), O_WRONLY | O_CREAT | O_EXCL, 0600);
        assert(fd >= 0 && write(fd, "keep-me", 7) == 7);
        close(fd);
        assert(!attempt(path, O_WRONLY | O_CREAT | O_EXCL, {false, true}));
        assert(errno == EEXIST);
        fd = open(path.c_str(), O_RDONLY);
        char buffer[8] = {};
        assert(fd >= 0 && read(fd, buffer, sizeof(buffer)) == 7);
        assert(strcmp(buffer, "keep-me") == 0);
        close(fd);
    } else if (mode == "missing") {
        assert(!attempt(path, O_RDONLY, {true, false}));
        assert(errno == ENOENT);
        assert(access(path.c_str(), F_OK) == -1 && errno == ENOENT);
    } else {
        try {
            attempt(path, O_RDONLY, {false, false});
            assert(false);
        } catch (const IOException &error) {
            assert(error.code == ENOENT);
        }
        assert(access(path.c_str(), F_OK) == -1 && errno == ENOENT);
    }
}
'''


class LocalFileOpenTests(unittest.TestCase):
    def test_flags_match_newlib_instead_of_picolibc(self):
        source = (ROOT / "external/duckdb/src/common/local_file_system.cpp").read_text()
        start = source.index("\tint open_flags = 0;")
        end = source.index("\n\t// Open the file", start)
        flags = source[start:end]
        harness_source = r'''
#include <cassert>
#include <stdexcept>
#define D_ASSERT assert
#define O_RDONLY 0
#define O_WRONLY 1
#define O_RDWR 2
#define O_CREAT 0x0200
#define O_APPEND 0x0008
#define O_EXCL 0x0800
#define O_CLOEXEC 0
#define O_DIRECT 0
using InternalException = std::runtime_error;
struct OpenFlags {
    bool read, write, create, append, exclusive;
    bool OpenForReading() const { return read; }
    bool OpenForWriting() const { return write; }
    bool CreateFileIfNotExists() const { return create; }
    bool OverwriteExistingFile() const { return false; }
    bool OpenForAppending() const { return append; }
    bool DirectIO() const { return false; }
    bool CreatePrivateFile() const { return false; }
    bool ExclusiveCreate() const { return exclusive; }
};
static int flags_for(OpenFlags flags) {
// FLAGS
    (void)filesec;
    return open_flags;
}
int main() {
    assert(flags_for({true, false, false, false, false}) == O_RDONLY);
    assert(flags_for({true, true, true, false, false}) == (O_RDWR | O_CREAT));
    assert(flags_for({false, true, true, true, false}) == (O_WRONLY | O_CREAT | O_APPEND));
    assert(flags_for({false, true, true, false, true}) == (O_WRONLY | O_CREAT | O_EXCL));
}
'''
        with tempfile.TemporaryDirectory() as directory:
            harness = Path(directory) / "flags.cpp"
            binary = Path(directory) / "flags"
            harness.write_text(harness_source.replace("// FLAGS", flags))
            command = shlex.split(os.environ.get("CXX", "c++"))
            for name in ("CXXFLAGS", "SANFLAGS"):
                command += shlex.split(os.environ.get(name, ""))
            command += ["-std=c++17", "-Wall", "-Wextra", "-Werror", "-DESP_PLATFORM",
                        str(harness), "-o", str(binary)]
            command += shlex.split(os.environ.get("LDFLAGS", ""))
            subprocess.run(command, check=True)
            subprocess.run([*RUNNER, str(binary)], check=True)

    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="duckdb-open-test-")
        cls.addClassCleanup(cls.directory.cleanup)
        work = Path(cls.directory.name)
        source = (ROOT / "external/duckdb/src/common/local_file_system.cpp").read_text()
        start = source.index("\tint fd = open(path.c_str(), open_flags, filesec);")
        end = source.index("\n#if defined(__DARWIN__) || defined(__APPLE__)", start)
        harness = work / "open.cpp"
        harness.write_text(STUBS + source[start:end] + CHECKS)
        cls.binary = work / "open"
        command = shlex.split(os.environ.get("CXX", "c++"))
        for name in ("CXXFLAGS", "SANFLAGS"):
            command += shlex.split(os.environ.get(name, ""))
        command += ["-std=c++17", "-Wall", "-Wextra", "-Werror", "-DESP_PLATFORM",
                    str(harness), "-o", str(cls.binary)]
        command += shlex.split(os.environ.get("LDFLAGS", ""))
        subprocess.run(command, check=True)

    def test_failed_opens_preserve_data_and_errors(self):
        for case in ("exclusive", "missing", "exception"):
            with self.subTest(case=case):
                result = subprocess.run([*RUNNER, str(self.binary), self.directory.name, case],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()

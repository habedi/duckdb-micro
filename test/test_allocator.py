"""Exercise the ESP32 allocator with a failing PSRAM heap."""

import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest


RUNNER = shlex.split(os.environ.get("TEST_RUNNER", ""))


ROOT = Path(__file__).resolve().parents[1]
STUBS = r'''
#include <algorithm>
#include <cassert>
#include <cstddef>
#include <cstdlib>
#include <cstring>
#include <new>
#define MALLOC_CAP_SPIRAM 1
#define MALLOC_CAP_8BIT 2
static bool exhausted;
static int calls;
static void *heap_caps_aligned_alloc(size_t alignment, size_t size, int caps) {
    assert(caps == (MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    assert(alignment == alignof(std::max_align_t));
    calls++;
    if (exhausted) return nullptr;
    void *result = nullptr;
    return posix_memalign(&result, alignment, size) == 0 ? result : nullptr;
}
using idx_t = size_t;
using data_ptr_t = unsigned char *;
using PrivateAllocatorData = void;
static data_ptr_t data_ptr_cast(void *ptr) { return static_cast<data_ptr_t>(ptr); }
template<class T> static T MinValue(T a, T b) { return std::min(a, b); }
struct Allocator {
    static data_ptr_t DefaultAllocate(PrivateAllocatorData *, idx_t);
    static void DefaultFree(PrivateAllocatorData *, data_ptr_t, idx_t);
    static data_ptr_t DefaultReallocate(PrivateAllocatorData *, data_ptr_t, idx_t, idx_t);
};
'''
CHECKS = r'''
int main() {
    auto ptr = Allocator::DefaultAllocate(nullptr, 17);
    assert(calls == 1);
    memset(ptr, 42, 17);
    ptr = Allocator::DefaultReallocate(nullptr, ptr, 17, 65);
    for (int i = 0; i < 17; i++) assert(ptr[i] == 42);
    ptr = Allocator::DefaultReallocate(nullptr, ptr, 65, 9);
    for (int i = 0; i < 9; i++) assert(ptr[i] == 42);
    exhausted = true;
    try {
        Allocator::DefaultAllocate(nullptr, 17);
        assert(false);
    } catch (const std::bad_alloc &) {}
    try {
        Allocator::DefaultReallocate(nullptr, ptr, 9, 65);
        assert(false);
    } catch (const std::bad_alloc &) {}
    for (int i = 0; i < 9; i++) assert(ptr[i] == 42);
    assert(Allocator::DefaultReallocate(nullptr, ptr, 9, 0) == nullptr);
    exhausted = false;
    ptr = Allocator::DefaultReallocate(nullptr, nullptr, 0, 17);
    Allocator::DefaultFree(nullptr, ptr, 17);
    assert(calls == 6);
}
'''


class AllocatorTests(unittest.TestCase):
    def test_psram_exhaustion_and_reallocation(self):
        source = (ROOT / "external/duckdb/src/common/allocator/allocator_standard.cpp").read_text()
        start = source.index("data_ptr_t Allocator::DefaultAllocate(")
        end = source.index("optional_idx Allocator::DecayDelay(", start)
        with tempfile.TemporaryDirectory() as directory:
            harness = Path(directory) / "allocator.cpp"
            binary = Path(directory) / "allocator"
            harness.write_text(STUBS + source[start:end] + CHECKS)
            command = shlex.split(os.environ.get("CXX", "c++"))
            for name in ("CXXFLAGS", "SANFLAGS"):
                command += shlex.split(os.environ.get(name, ""))
            command += ["-std=c++17", "-DESP_PLATFORM", str(harness), "-o", str(binary)]
            command += shlex.split(os.environ.get("LDFLAGS", ""))
            subprocess.run(command, check=True)
            subprocess.run([*RUNNER, str(binary)], check=True)

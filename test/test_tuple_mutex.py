"""Check arena mutex ownership with a mutex that needs explicit destruction."""

import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
STUBS = r'''
#include <cassert>
#include <cstdlib>
#include <functional>
#include <memory>
#include <new>
#include <utility>
#include <vector>
using idx_t = size_t;
struct mutex {
    inline static int live = 0;
    mutex() { live++; }
    ~mutex() { live--; }
    mutex(const mutex &) = delete;
};
template<class T> using reference = std::reference_wrapper<T>;
template<class T> struct arena_deleter {
    void operator()(T *ptr) const { ptr->~T(); }
};
template<class T> using arena_ptr = std::unique_ptr<T, arena_deleter<T>>;
template<class T> using unsafe_arena_ptr = arena_ptr<T>;
struct ContinuousIdSet { int value = 0; };
struct TupleDataChunkPart;
struct TupleDataSegment;
struct Arena {
    std::vector<void *> allocations;
    ~Arena() { for (auto ptr : allocations) std::free(ptr); }
    template<class T, class... Args> T *Make(Args &&... args) {
        auto ptr = std::malloc(sizeof(T));
        assert(ptr);
        allocations.push_back(ptr);
        return new (ptr) T(std::forward<Args>(args)...);
    }
    template<class T, class... Args> arena_ptr<T> MakePtr(Args &&... args) {
        return arena_ptr<T>(Make<T>(std::forward<Args>(args)...));
    }
    template<class T, class... Args> arena_ptr<T> MakeUnsafePtr(Args &&... args) {
        return MakePtr<T>(std::forward<Args>(args)...);
    }
};
'''
CHECKS = r'''
int main() {
    mutex borrowed;
    for (int i = 0; i < 100; i++) {
        Arena arena;
        auto stl_allocator = &arena;
        std::vector<arena_ptr<TupleDataChunk>> chunks;
        // FACTORY
        assert(mutex::live == 2);
        chunks.clear();
        assert(mutex::live == 1);
    }
    {
        TupleDataChunk chunk(borrowed);
        assert(&chunk.lock.get() == &borrowed);
    }
    assert(mutex::live == 1);
    alignas(mutex) unsigned char storage[3][sizeof(mutex)];
    auto first_mutex = new (storage[0]) mutex;
    auto second_mutex = new (storage[1]) mutex;
    {
        TupleDataChunk first{arena_ptr<mutex>(first_mutex)};
        first.count = 42;
        TupleDataChunk second(std::move(first));
        assert(!first.owned_lock);
        assert(second.owned_lock.get() == first_mutex);
        assert(&second.lock.get() == first_mutex);
        assert(second.count == 42);
        TupleDataChunk third{arena_ptr<mutex>(second_mutex)};
        third = std::move(second);
        assert(third.owned_lock.get() == first_mutex);
        assert(&third.lock.get() == first_mutex);
        assert(second.owned_lock.get() == second_mutex);
        assert(&second.lock.get() == second_mutex);
        assert(mutex::live == 3);
    }
    assert(mutex::live == 1);
    {
        std::vector<arena_ptr<TupleDataChunk>> chunks;
        alignas(TupleDataChunk) unsigned char chunk_storage[sizeof(TupleDataChunk)];
        auto ptr = new (chunk_storage) TupleDataChunk{arena_ptr<mutex>(new (storage[2]) mutex)};
        chunks.emplace_back(ptr);
        assert(mutex::live == 2);
        chunks.clear();
        assert(mutex::live == 1);
    }
}
'''


class TupleMutexTests(unittest.TestCase):
    def test_arena_mutex_destruction_move_and_reset(self):
        base = ROOT / "external/duckdb/src"
        header = (base / "include/duckdb/common/types/row/tuple_data_segment.hpp").read_text()
        chunk = header[header.index("struct TupleDataChunk {\n"):
                       header.index("struct TupleDataSegment {\n")]
        source = (base / "common/types/row/tuple_data_segment.cpp").read_text()
        methods = source[source.index("TupleDataChunk::TupleDataChunk("):
                         source.index("TupleDataChunkPart &TupleDataChunk::AddPart(")]
        allocator = (base / "common/types/row/tuple_data_allocator.cpp").read_text()
        start = allocator.index("if (chunks.empty() || chunks.back()->count == STANDARD_VECTOR_SIZE) {")
        factory = allocator[start:].split("{", 1)[1].split("}", 1)[0]
        with tempfile.TemporaryDirectory() as directory:
            harness = Path(directory) / "mutex.cpp"
            binary = Path(directory) / "mutex"
            harness.write_text(STUBS + chunk + methods + CHECKS.replace("// FACTORY", factory))
            command = shlex.split(os.environ.get("CXX", "c++"))
            for name in ("CXXFLAGS", "SANFLAGS"):
                command += shlex.split(os.environ.get(name, ""))
            command += ["-std=c++17", "-Wall", "-Wextra", str(harness), "-o", str(binary)]
            command += shlex.split(os.environ.get("LDFLAGS", ""))
            subprocess.run(command, check=True)
            subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    unittest.main()

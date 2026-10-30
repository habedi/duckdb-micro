#!/usr/bin/env bash
set -euo pipefail
export PROBE_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export IDF_PATH="$PROBE_ROOT/external/esp-idf"
export IDF_TOOLS_PATH="${IDF_TOOLS_PATH:-$PROBE_ROOT/build/espressif}"
. "$IDF_PATH/export.sh" >/dev/null
cd "$PROBE_ROOT"
DUCKDB_COMMIT=d54cbbe3df17e8e5ce68312f483b4155836112aa
if test "$(git -C external/duckdb rev-parse HEAD)" != "$DUCKDB_COMMIT"; then
  # Also accept a committed copy of exactly our patched release tree.
  git -C external/duckdb diff --no-ext-diff --abbrev=11 "$DUCKDB_COMMIT" -- | cmp - src/patches/duckdb-esp32p4.diff
fi
if ! git -C external/duckdb apply --reverse --check "$PROBE_ROOT/src/patches/duckdb-esp32p4.diff" 2>/dev/null; then
  git -C external/duckdb apply --check "$PROBE_ROOT/src/patches/duckdb-esp32p4.diff"
  git -C external/duckdb apply "$PROBE_ROOT/src/patches/duckdb-esp32p4.diff"
fi
idf.py -C src/firmware -B "$PROBE_ROOT/build/firmware" \
    -D SDKCONFIG="$PROBE_ROOT/build/firmware/sdkconfig" -D IDF_TARGET=esp32p4 reconfigure
python - <<'PY'
import json
import os
from pathlib import Path
import shlex

root = Path(os.environ["PROBE_ROOT"])
commands = json.loads((root / "build/firmware/compile_commands.json").read_text())
main = next(item for item in commands if item["file"].endswith("/probe.cpp"))
flags = [arg for arg in shlex.split(main["command"]) if arg.startswith(("-I", "-D"))]
(root / "build/firmware/sdkflags").write_text(shlex.join(flags) + "\n")
PY
TOOLCHAIN="${TOOLCHAIN:-clang}"
if [ "$TOOLCHAIN" = "clang" ]; then
  TOOLCHAIN_FILE="$PROBE_ROOT/src/cmake/esp32p4-clang.cmake"
  C_FLAGS="--target=riscv32-esp-elf -mcpu=esp32p4eco4 @\"$PROBE_ROOT/build/firmware/sdkflags\" -Oz -ftls-model=local-exec -ffunction-sections -fdata-sections"
  CXX_FLAGS="--target=riscv32-esp-elf -mcpu=esp32p4eco4 @\"$PROBE_ROOT/build/firmware/sdkflags\" -Oz -ftls-model=local-exec -ffunction-sections -fdata-sections"
  EXTRA_OPTS=(
    "-DCMAKE_C_FLAGS_MINSIZEREL=-Oz -DNDEBUG"
    "-DCMAKE_CXX_FLAGS_MINSIZEREL=-Oz -DNDEBUG"
  )
else
  TOOLCHAIN_FILE="$PROBE_ROOT/src/cmake/esp32p4.cmake"
  C_FLAGS="@\"$PROBE_ROOT/build/firmware/toolchain/cflags\" @\"$PROBE_ROOT/build/firmware/sdkflags\" -ffunction-sections -fdata-sections"
  CXX_FLAGS="@\"$PROBE_ROOT/build/firmware/toolchain/cxxflags\" @\"$PROBE_ROOT/build/firmware/sdkflags\" -ffunction-sections -fdata-sections"
  EXTRA_OPTS=()
fi

cmake -S external/duckdb -B build/duckdb -G Ninja \
    -DCMAKE_TOOLCHAIN_FILE="$TOOLCHAIN_FILE" \
    -DCMAKE_BUILD_TYPE=MinSizeRel -DCMAKE_POSITION_INDEPENDENT_CODE=OFF \
    "-DCMAKE_C_COMPILER_LAUNCHER=${CMAKE_C_COMPILER_LAUNCHER:-}" \
    "-DCMAKE_CXX_COMPILER_LAUNCHER=${CMAKE_CXX_COMPILER_LAUNCHER:-}" \
    "-DCMAKE_C_FLAGS=$C_FLAGS" \
    "-DCMAKE_CXX_FLAGS=$CXX_FLAGS" \
    "${EXTRA_OPTS[@]}" \
    -DDUCKDB_EXPLICIT_PLATFORM=esp32p4 -DDUCKDB_EXPLICIT_VERSION=v2.0.0 -DDUCKDB_OPTIMIZATION_PROFILE=NONE \
    -DDISABLE_UNITY=ON -DDISABLE_THREADS=OFF -DDISABLE_RTTI=ON \
    -DSMALLER_BINARY=ON -DENABLE_JEMALLOC=OFF \
    -DENABLE_EXTENSION_LOAD=OFF -DENABLE_BUILTIN_HTTPLIB=OFF \
    -DDUCKDB_CAPABILITIES=none \
    -DSTATICALLY_LINK_EXTENSIONS="core_functions;parquet;json" '-DSKIP_EXTENSIONS=icu' \
    -DBUILD_SHELL=OFF -DBUILD_UNITTESTS=OFF \
    -DENABLE_SANITIZER=OFF -DENABLE_UBSAN=OFF
cmake --build build/duckdb --target duckdb_static core_functions_extension parquet_extension json_extension duckdb_zstd duckdb_yyjson --parallel "${JOBS:-4}"
python scripts/isolate-crypto.py
idf.py -C src/firmware -B "$PROBE_ROOT/build/firmware" build

#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export IDF_PATH="$PWD/external/esp-idf"
export IDF_TOOLS_PATH="${IDF_TOOLS_PATH:-$PWD/build/espressif}"
bash scripts/build-probe.sh
. "$IDF_PATH/export.sh" >/dev/null
if [ "${TOOLCHAIN:-clang}" = clang ]; then
  CLI_TOOLCHAIN="$PWD/src/cmake/esp32p4-clang.cmake"
  CLI_FLAGS="--target=riscv32-esp-elf -mcpu=esp32p4eco4 @\"$PWD/build/firmware/sdkflags\" -Oz -ftls-model=local-exec"
  CLI_OPT=-Oz
else
  CLI_TOOLCHAIN="$PWD/src/cmake/esp32p4.cmake"
  CLI_FLAGS="@\"$PWD/build/firmware/toolchain/cxxflags\" @\"$PWD/build/firmware/sdkflags\""
  CLI_OPT=-Os
fi
cmake -S src/firmware/shell/cli -B build/cli -G Ninja \
    -DCMAKE_TOOLCHAIN_FILE="$CLI_TOOLCHAIN" \
    -DCMAKE_BUILD_TYPE=MinSizeRel \
    "-DCMAKE_CXX_COMPILER_LAUNCHER=${CMAKE_CXX_COMPILER_LAUNCHER:-}" \
    "-DCMAKE_CXX_FLAGS=$CLI_FLAGS" \
    "-DCMAKE_CXX_FLAGS_MINSIZEREL=$CLI_OPT -DNDEBUG"
cmake --build build/cli --parallel "${JOBS:-4}"
idf.py -C src/firmware/shell -B "$PWD/build/shell" \
    -D SDKCONFIG="$PWD/build/shell/sdkconfig" -D IDF_TARGET=esp32p4 build

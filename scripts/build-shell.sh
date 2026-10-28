#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export IDF_PATH="$PWD/external/esp-idf"
export IDF_TOOLS_PATH="${IDF_TOOLS_PATH:-$PWD/build/espressif}"
bash scripts/build-probe.sh
. "$IDF_PATH/export.sh" >/dev/null
idf.py -C src/firmware/shell -B "$PWD/build/shell" \
    -D SDKCONFIG="$PWD/build/shell/sdkconfig" -D IDF_TARGET=esp32p4 build

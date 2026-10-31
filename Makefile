SHELL := /bin/bash
.SHELLFLAGS := -e -o pipefail -c
.DEFAULT_GOAL := help

DEFAULT_TTY := $(firstword $(wildcard /dev/ttyACM* /dev/ttyUSB*) /dev/ttyACM0)
TTY ?= $(DEFAULT_TTY)
BAUD ?= 115200
ESPTOOL_BAUD ?= 921600
PYTHON ?= python3
CLI_ARGS ?=
STRESS_ARGS ?=
TPCH_ARGS ?=
TPCH_MANIFEST ?= build/tpch/manifest.json
TPCH_REPORT ?=
TPCH_REPORT_ARGS ?=
CLANG_FORMAT ?= $(firstword $(wildcard /usr/bin/clang-format) clang-format)

export CFLAGS CXXFLAGS SANFLAGS LDFLAGS TEST_RUNNER

FORMAT_FILES := $(wildcard src/firmware/main/*.cpp src/firmware/shell/main/*.cpp src/firmware/shell/cli/*.cpp src/firmware/shell/main/duckdb_console.h test/*.cpp)

.PHONY: help clean clean-all submodules probe-build shell-build firmware-package shell-flash flash probe-flash console \
 check-shell check-cli check-probe check-stress check-network check-dualcore prepare-tpch check-tpch report-tpch espidf-install test probe-test shell-test \
  asan ubsan memcheck format lint setup-hooks test-hooks

help: ## Show available commands
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z0-9_-]+:.*?## / {printf "  %-20s %s\n", $$1, $$2}' Makefile

clean: ## Remove host test binaries; keep the files in `build/` directory
	rm -rf -- build/test

clean-all: clean ## Also remove DuckDB and firmware builds; keep tools, logs, and backups
	rm -rf -- build/duckdb build/cli build/firmware build/shell build/duckdb-micro-shell.tar.gz build/duckdb-micro-probe.tar.gz

submodules: ## Initialize and update Git submodules recursively
	git submodule update --init --recursive --depth 1

probe-build: ## Cross-compile the DuckDB ESP32-P4 feasibility probe
	bash scripts/build-probe.sh

probe-flash: ## Flash the standalone SQL probe firmware (e.g. make probe-flash TTY=/dev/ttyACM0)
	@if [ ! -f build/firmware/flash_args ]; then $(MAKE) probe-build; fi
	@export IDF_PATH="$${IDF_PATH:-$(CURDIR)/external/esp-idf}"; \
	export IDF_TOOLS_PATH="$${IDF_TOOLS_PATH:-$(CURDIR)/build/espressif}"; \
	source "$$IDF_PATH/export.sh" >/dev/null 2>&1; \
	(cd build/firmware && python -m esptool --chip esp32p4 --port $(TTY) --baud $(ESPTOOL_BAUD) --after hard-reset write-flash @flash_args)

shell-build: ## Build the shell firmware with interactive DuckDB
	bash scripts/build-shell.sh

firmware-package: ## Package existing shell and probe builds with their flashing files
	tar -czf build/duckdb-micro-shell.tar.gz -C build/shell flash_args duckdb_shell.bin bootloader/bootloader.bin partition_table/partition-table.bin
	tar -czf build/duckdb-micro-probe.tar.gz -C build/firmware flash_args duckdb_probe.bin bootloader/bootloader.bin partition_table/partition-table.bin

shell-flash: ## Flash the shell firmware to the ESP32-P4 (e.g. make flash TTY=/dev/ttyACM0)
	@if [ ! -f build/shell/flash_args ]; then $(MAKE) shell-build; fi
	@export IDF_PATH="$${IDF_PATH:-$(CURDIR)/external/esp-idf}"; \
	export IDF_TOOLS_PATH="$${IDF_TOOLS_PATH:-$(CURDIR)/build/espressif}"; \
	source "$$IDF_PATH/export.sh" >/dev/null 2>&1; \
	(cd build/shell && python -m esptool --chip esp32p4 --port $(TTY) --baud $(ESPTOOL_BAUD) --after hard-reset write-flash @flash_args)

flash: shell-flash ## Alias for shell-flash

console: ## Connect to ESP32-P4 serial console (e.g. make console [TTY=/dev/ttyACM0])
	$(PYTHON) -m serial.tools.miniterm --raw --eol CR $(TTY) $(BAUD)

check-shell: ## Run hardware serial check for the shell (e.g. make check-shell [TTY=/dev/ttyACM0])
	@export IDF_PATH="$${IDF_PATH:-$(CURDIR)/external/esp-idf}"; \
	export IDF_TOOLS_PATH="$${IDF_TOOLS_PATH:-$(CURDIR)/build/espressif}"; \
	source "$$IDF_PATH/export.sh" >/dev/null 2>&1; \
	python scripts/check-shell.py $(TTY)

check-cli: ## Verify the upstream CLI, output modes, file commands, and session cleanup
	$(PYTHON) scripts/check-cli.py $(TTY) $(CLI_ARGS)

check-stress: ## Stress SQL and storage, and record heap trends (STRESS_ARGS='--duration 28800' for overnight)
	$(PYTHON) scripts/check-stress.py $(TTY) $(STRESS_ARGS)

check-network: ## Verify HTTP/HTTPS downloads and authenticated REST queries over Wi-Fi
	$(PYTHON) scripts/check-network.py $(TTY)

check-dualcore: ## Verify CPU 1 query execution, PSRAM worker stacks, and worker teardown
	$(PYTHON) scripts/check-dualcore.py $(TTY)

prepare-tpch: ## Generate a small TPC-H database and host reference results (requires host DuckDB with tpch)
	$(PYTHON) scripts/check-tpch.py prepare $(TPCH_ARGS)

check-tpch: ## Prepare, load over serial, and benchmark a small TPC-H database on the SD card
	$(PYTHON) scripts/check-tpch.py run $(TTY) "$(TPCH_MANIFEST)" $(TPCH_ARGS)

report-tpch: ## Generate a Markdown report from TPC-H benchmark results
	$(PYTHON) scripts/check-tpch.py report $(if $(TPCH_REPORT),--input "$(TPCH_REPORT)",) $(TPCH_REPORT_ARGS)

check-probe: ## Run hardware serial check for standalone probe (e.g. make check-probe [TTY=/dev/ttyACM0])
	@export IDF_PATH="$${IDF_PATH:-$(CURDIR)/external/esp-idf}"; \
	export IDF_TOOLS_PATH="$${IDF_TOOLS_PATH:-$(CURDIR)/build/espressif}"; \
	source "$$IDF_PATH/export.sh" >/dev/null 2>&1; \
	python scripts/check-board.py $(TTY)

espidf-install: ## Install the pinned ESP-IDF tools, Espressif Clang, and Python environment for ESP32-P4
	export IDF_TOOLS_PATH="$${IDF_TOOLS_PATH:-$(CURDIR)/build/espressif}"; \
	bash external/esp-idf/install.sh esp32p4; \
	$(PYTHON) external/esp-idf/tools/idf_tools.py install esp-clang

test: probe-test shell-test ## Run host portability, shell-path, and editor regression tests
	$(PYTHON) -m unittest discover -s test -p 'test_*.py'

asan: ## Run all host tests with AddressSanitizer and leak detection
	ASAN_OPTIONS="$${ASAN_OPTIONS:-detect_leaks=1:halt_on_error=1}" $(MAKE) test \
		SANFLAGS='-g -O1 -fno-omit-frame-pointer -fsanitize=address' TEST_RUNNER=

ubsan: ## Run all host tests with UndefinedBehaviorSanitizer
	UBSAN_OPTIONS="$${UBSAN_OPTIONS:-halt_on_error=1:print_stacktrace=1}" $(MAKE) test \
		SANFLAGS='-g -O1 -fno-omit-frame-pointer -fsanitize=undefined' TEST_RUNNER=

memcheck: ## Run native host test binaries under Valgrind (without sanitizers)
	$(MAKE) test SANFLAGS= \
		TEST_RUNNER='valgrind --quiet --leak-check=full --show-leak-kinds=all --errors-for-leak-kinds=definite,indirect,possible --error-exitcode=99'

format: ## Format the project's firmware and host-test C++ code
	$(CLANG_FORMAT) -i $(FORMAT_FILES)

lint: ## Check host-test and math-shim code with cppcheck
	cppcheck --enable=warning,performance,portability --error-exitcode=1 --std=c++17 \
		--suppress=missingIncludeSystem -Iexternal/duckdb/third_party/utf8proc/include \
		test src/firmware/main/llroundl.cpp

setup-hooks: ## Install Git hooks
	pre-commit install --hook-type pre-commit --hook-type pre-push
	pre-commit install-hooks

test-hooks: ## Run Git hooks on all files
	pre-commit run --all-files

probe-test: ## Test the Unicode and math portability fixes on the host
	if ! git -C external/duckdb apply --reverse --check "$(CURDIR)/src/patches/duckdb-esp32p4.diff" 2>/dev/null; then \
		git -C external/duckdb apply "$(CURDIR)/src/patches/duckdb-esp32p4.diff"; \
	fi
	mkdir -p build/test
	$(CXX) $(CXXFLAGS) $(SANFLAGS) -std=c++17 -Wall -Wextra -Iexternal/duckdb/third_party/utf8proc/include \
		test/test_unicode.cpp external/duckdb/third_party/utf8proc/utf8proc.cpp \
		-o build/test/test_unicode $(LDFLAGS)
	$(TEST_RUNNER) build/test/test_unicode
	$(CXX) $(CXXFLAGS) $(SANFLAGS) -std=c++17 -Os -Wall -Wextra -fno-builtin-llroundl \
		test/test_llroundl.cpp src/firmware/main/llroundl.cpp -o build/test/test_llroundl $(LDFLAGS)
	# Valgrind changes x87 long-double precision; keep this arithmetic test native.
	build/test/test_llroundl

shell-test: ## Run shell path, editor, completion, filesystem, and Wi-Fi helper host tests
	mkdir -p build/test
	$(CC) $(CFLAGS) $(SANFLAGS) -std=c17 -Wall -Wextra -Isrc/firmware/shell/main \
		src/firmware/shell/test/test_path_util.c src/firmware/shell/main/path_util.c \
		-o build/test/test_path_util $(LDFLAGS)
	$(TEST_RUNNER) build/test/test_path_util
	$(CC) $(CFLAGS) $(SANFLAGS) -std=c17 -Wall -Wextra src/firmware/shell/test/test_editor.c -o build/test/test_editor $(LDFLAGS)
	$(TEST_RUNNER) build/test/test_editor
	$(PYTHON) -m unittest discover -s src/firmware/shell/test -p test_autocomplete.py
	$(PYTHON) -m unittest discover -s src/firmware/shell/test -p test_filesystem.py
	$(PYTHON) -m unittest discover -s src/firmware/shell/test -p test_wifi.py
	$(PYTHON) -m unittest discover -s src/firmware/shell/test -p test_flash_stack.py
	$(PYTHON) -m unittest discover -s src/firmware/shell/test -p test_duckdb_console.py

SHELL := /bin/bash
.SHELLFLAGS := -e -o pipefail -c
.DEFAULT_GOAL := help

DEFAULT_TTY := $(firstword $(wildcard /dev/ttyACM* /dev/ttyUSB*) /dev/ttyACM0)
TTY ?= $(DEFAULT_TTY)
BAUD ?= 115200
ESPTOOL_BAUD ?= 921600
PYTHON ?= python3

FORMAT_FILES := $(wildcard src/firmware/main/*.cpp src/firmware/shell/main/*.cpp src/firmware/shell/main/duckdb_console.h test/*.cpp)

.PHONY: help clean clean-all submodules probe-build shell-build shell-flash flash probe-flash console check-shell check-probe espidf-install test probe-test shell-test format lint setup-hooks test-hooks

help: ## Show available commands
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z0-9_-]+:.*?## / {printf "  %-20s %s\n", $$1, $$2}' Makefile

clean: ## Remove host test binaries; keep the files in `build/` directory
	rm -rf -- build/test

clean-all: clean ## Also remove DuckDB and firmware builds; keep tools, logs, and backups
	rm -rf -- build/duckdb build/firmware build/shell

submodules: ## Initialize and update Git submodules recursively
	git submodule update --init --recursive --depth 1

probe-build: ## Cross-compile the DuckDB ESP32-P4 feasibility probe
	bash scripts/build-probe.sh

probe-flash: ## Flash the standalone SQL probe firmware (e.g. make probe-flash TTY=/dev/ttyACM0)
	@if [ ! -f build/firmware/flash_args ]; then $(MAKE) probe-build; fi
	@export IDF_PATH="$${IDF_PATH:-$(CURDIR)/external/esp-idf}"; \
	export IDF_TOOLS_PATH="$${IDF_TOOLS_PATH:-$(CURDIR)/build/espressif}"; \
	source "$$IDF_PATH/export.sh" >/dev/null 2>&1; \
	(cd build/firmware && python -m esptool --chip esp32p4 --port $(TTY) --baud $(ESPTOOL_BAUD) --after no-reset write-flash @flash_args)

shell-build: ## Build the shell firmware with interactive DuckDB
	bash scripts/build-shell.sh

shell-flash: ## Flash the shell firmware to the ESP32-P4 (e.g. make flash TTY=/dev/ttyACM0)
	@if [ ! -f build/shell/flash_args ]; then $(MAKE) shell-build; fi
	@export IDF_PATH="$${IDF_PATH:-$(CURDIR)/external/esp-idf}"; \
	export IDF_TOOLS_PATH="$${IDF_TOOLS_PATH:-$(CURDIR)/build/espressif}"; \
	source "$$IDF_PATH/export.sh" >/dev/null 2>&1; \
	(cd build/shell && python -m esptool --chip esp32p4 --port $(TTY) --baud $(ESPTOOL_BAUD) --after no-reset write-flash @flash_args)

flash: shell-flash ## Alias for shell-flash

console: ## Connect to ESP32-P4 serial console (e.g. make console [TTY=/dev/ttyACM0])
	$(PYTHON) -m serial.tools.miniterm $(TTY) $(BAUD)

check-shell: ## Run hardware serial check for the shell (e.g. make check-shell [TTY=/dev/ttyACM0])
	@export IDF_PATH="$${IDF_PATH:-$(CURDIR)/external/esp-idf}"; \
	export IDF_TOOLS_PATH="$${IDF_TOOLS_PATH:-$(CURDIR)/build/espressif}"; \
	source "$$IDF_PATH/export.sh" >/dev/null 2>&1; \
	python scripts/check-shell.py $(TTY)

check-probe: ## Run hardware serial check for standalone probe (e.g. make check-probe [TTY=/dev/ttyACM0])
	@export IDF_PATH="$${IDF_PATH:-$(CURDIR)/external/esp-idf}"; \
	export IDF_TOOLS_PATH="$${IDF_TOOLS_PATH:-$(CURDIR)/build/espressif}"; \
	source "$$IDF_PATH/export.sh" >/dev/null 2>&1; \
	python scripts/check-board.py $(TTY)

espidf-install: ## Install the pinned ESP-IDF tools and Python environment for ESP32-P4
	IDF_TOOLS_PATH="$${IDF_TOOLS_PATH:-$(CURDIR)/build/espressif}" bash external/esp-idf/install.sh esp32p4

test: probe-test shell-test ## Run host portability, shell-path, and editor regression tests
	$(PYTHON) -m unittest discover -s test -p 'test_*.py'

format: ## Format the project's firmware and host-test C++ code
	clang-format -i $(FORMAT_FILES)

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
	$(CXX) -std=c++17 -Wall -Wextra -Iexternal/duckdb/third_party/utf8proc/include \
		test/test_unicode.cpp external/duckdb/third_party/utf8proc/utf8proc.cpp \
		-o build/test/test_unicode
	build/test/test_unicode
	$(CXX) -std=c++17 -Os -Wall -Wextra -fno-builtin-llroundl \
		test/test_llroundl.cpp src/firmware/main/llroundl.cpp -o build/test/test_llroundl
	build/test/test_llroundl

shell-test: ## Run shell path, editor, completion, filesystem, and Wi-Fi helper host tests
	mkdir -p build/test
	$(CC) -std=c11 -Wall -Wextra -Isrc/firmware/shell/main \
		src/firmware/shell/test/test_path_util.c src/firmware/shell/main/path_util.c \
		-o build/test/test_path_util
	build/test/test_path_util
	$(CC) -std=c11 -Wall -Wextra src/firmware/shell/test/test_editor.c \
		-o build/test/test_editor
	build/test/test_editor
	$(PYTHON) -m unittest discover -s src/firmware/shell/test -p test_autocomplete.py
	$(PYTHON) -m unittest discover -s src/firmware/shell/test -p test_filesystem.py
	$(PYTHON) -m unittest discover -s src/firmware/shell/test -p test_wifi.py
	$(PYTHON) -m unittest discover -s src/firmware/shell/test -p test_flash_stack.py
	$(PYTHON) -m unittest discover -s src/firmware/shell/test -p test_duckdb_console.py

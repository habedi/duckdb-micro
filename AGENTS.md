# AGENTS.md

This file guides coding agents working on DuckDB Micro.

## Mission

Port DuckDB 2.0 to the Waveshare ESP32-P4-WIFI6 using ESP-IDF 6.1.
Prioritize firmware that builds and works on the physical board, reproducible dependencies,
and notes that distinguish verified behavior from experimental features.

## Hardware And Current Scope

- The P4 is silicon revision v1.3 with 32 MB flash and 32 MB PSRAM. Keep its revision checks,
  16-line 200 MHz PSRAM settings, and large-flash cache-addressing configuration.
- UART0 uses the onboard CH343 USB bridge, normally `/dev/ttyACM0`, at 115200 baud.
  Verify the port before accessing a board; device names can change.
- The shell provides `msh>` (microshell) and a `duckdb>` prompt. SQL, error recovery,
  small CSV queries from `/sdcard`, and Wi-Fi scanning via the C6 coprocessor have passed hardware checks.
- Wi-Fi transport is enabled via the onboard ESP32-C6 over SDIO Slot 1 using `esp_hosted`.
  Storage includes `/sdcard` (FATFS on MicroSD) and `/data` (LittleFS on internal flash via DRAM worker task).
  Persistent DuckDB databases, power-loss recovery, and larger workloads are not validated.
- Preserve the separate onboard ESP32-C6 firmware. P4 build or flash authorization does
  not authorize erasing or flashing the C6.

## Repository Layout

- `src/cmake/`: ESP32-P4 cross-compilation toolchain configuration.
- `src/firmware/`: standalone SQL self-check probe and its shared configuration and math shim.
- `src/firmware/shell/`: interactive shell integration, configuration, and README.
- `external/duckdb/`: upstream DuckDB Git submodule, pinned to commit
  `d54cbbe3df17e8e5ce68312f483b4155836112aa` (2.0.0).
- `external/esp-idf/`: ESP-IDF v6.1 Git submodule, pinned to
  `fff9895c82d744c7237be8847347bdd1b07c6643`, including its nested submodules.
- `external/esp_hosted/`: Espressif ESP-Hosted MCU Git submodule, pinned to `2e3f6c8bd717d67698319a6a24f9f4218d8db068` (v3.0.10).
- `external/esp_wifi_remote/`: Espressif Wi-Fi Remote Git submodule, pinned to `ad5497293a362bdd529c76fc8a53f5855484a3f5` (v1.6.5).
- `src/patches/`: DuckDB portability changes.
- `scripts/`: cross-build, crypto-symbol isolation, and serial hardware checks.
- `test/`: host regression tests for the Unicode port and long-double rounding shim.
- `build/`: generated libraries, firmware, logs, and private board backups. Never commit them.

Keep vendored dependencies in `external/` and firmware integration in `src/firmware/`.
The project must not depend on another project checkout or its cached components.

## Dependencies And Build Environment

Enter the host development shell with `nix develop`. Python project tools use uv and
`pyproject.toml`; the project Python version is 3.15. The Nix shell includes pyserial for
serial checks, but ESP-IDF uses its own installed Python environment.

The Nix shell sets `IDF_PATH` to `external/esp-idf` and defaults `IDF_TOOLS_PATH` to
`build/espressif`. Run `make espidf-install` once to install the P4 tools and SDK Python
environment. An explicit `IDF_TOOLS_PATH` can reuse another compatible tool installation.
Build scripts always use this repository's SDK and source its `export.sh`.
Use Espressif's `riscv32-esp-elf` compiler; a generic RISC-V compiler is not an ABI-compatible substitute.

Initialize dependencies with `git submodule update --init --recursive --depth 1`.
The build applies its portability patch to the submodule working tree, so local modifications
there are expected. Keep port changes in `src/patches/duckdb-esp32p4.diff`; do not commit them
inside the submodule or record a local-only commit as its pinned revision.
Maintain the integrated shell sources directly under `src/firmware/shell/`.
Preserve upstream licenses. Update dependency revisions deliberately.

## Build And Validation

Run commands from the repository root:

- `make shell-build`: build the interactive shell and DuckDB; builds probe libraries if absent.
- `make espidf-install`: install the pinned SDK's ESP32-P4 tools and Python environment.
- `make probe-build`: build the standalone SQL self-check firmware and engine libraries.
- `make probe-test`: host Unicode and long-double rounding regression checks.
- `make test`: run all host portability and shell tests; these do not replace board tests.
- `make shell-test`: host path, editor, completion, filesystem, and Wi-Fi helper tests,
  also included in `make test`.
- `nix flake check --no-build`: evaluate the development environment.

The cross-builds require the SDK environment described above. When SDK or compiler settings
change, regenerate `build/duckdb`, `build/firmware`, and `build/shell` because library
flags and CMake caches reference SDK paths. Preserve `build/hardware` and `build/espressif`.
Do not run overlapping builds against the same build directory.

Run the checks appropriate to the change. Firmware changes need an affected firmware build;
use physical hardware when behavior depends on the board. Documentation-only changes do not
require flashing. New nontrivial platform-independent logic needs a focused host test;
firmware SQL behavior is checked by the serial self-checks.

Serial checks, from an activated ESP-IDF environment, reset the board:

- `python scripts/check-board.py /dev/ttyACM0` checks the standalone probe.
- `python scripts/check-shell.py /dev/ttyACM0` checks the shell and creates,
  queries, then removes one uniquely named CSV file on the SD card.

Close other serial monitors before running a check. A success marker must accompany correct
results and no crashes or watchdog warnings. Report which image was built and tested, and
keep the root README and shell README consistent with actual results.

## Hardware And Data Changes

Access a serial port or flash firmware only when authorized by the user's task. Authorization
for ongoing board work persists; do not ask again unless the scope changes or a host permission
prompt requires it. Follow the shell README for its flash commands and partition layout.

Keep the verified original P4 flash backup under `build/hardware/`. Do not delete that directory
as routine cleanup. Partition changes can overwrite existing flash data; preserve a full backup
before changing the layout. Do not automatically format SD cards or LittleFS, migrate user data,
or overwrite existing files as part of a test. Use disposable, uniquely named test files.

## Change And Reporting Rules

Use English, plain language, and small focused changes. Reuse existing code and tools.
Avoid unrelated refactoring and speculative features. Keep the P4 portability fixes, allocation
alignment, crypto-symbol isolation, task memory settings, and math shim intact unless testing
supports a replacement. Do not remove validation to make a build or hardware check pass.

Never commit private absolute paths, MAC addresses, eFuse contents, serial numbers, Wi-Fi
credentials, survey results, full flash backups, or unfiltered boot logs. Use `IDF_PATH` and
`IDF_TOOLS_PATH` in setup instructions. Summarize hardware evidence without identifiers.
Scope commits to one change, and distinguish build checks, host tests, and board tests.
State limitations and checks that were not run.

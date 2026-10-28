# AGENTS.md

This file provides guidance to coding agents collaborating on this repository.

## Mission

DuckDB Micro is an experimental port of DuckDB 2.0 to the ESP32-P4 microcontroller (targeting the Waveshare ESP32-P4-WIFI6 board).
It combines embedded DuckDB with FreeRTOS and ESP-IDF, providing:

1. A standalone SQL engine probe firmware that verifies query execution directly on the microcontroller.
2. An interactive shell (MicroShell) firmware featuring filesystem navigation, an in-place text editor, serial terminal REPL, Wi-Fi networking, and an
   interactive DuckDB SQL console.
3. Portability patches and custom shims bridging DuckDB 2.0 and the ESP32-P4 toolchain.

Priorities, in order:

1. Firmware stability, memory safety, and prevention of heap exhaustion in PSRAM and internal SRAM.
2. Portability and compatibility with pinned ESP-IDF and DuckDB 2.0 versions.
3. Clean separation between DuckDB core patches, firmware shell features, and host test infrastructure.
4. Small, well-tested changes verified by host unit tests and hardware serial checks.

## Core Rules

- Use English for code, comments, docs, tests, and commit messages.
- Prefer focused fixes over broad refactoring.
- Do not edit vendored code under `external/` directly; upstream DuckDB changes must be stored in `src/patches/duckdb-esp32p4.diff`.
- Do not add new dependencies, background tasks, or memory allocations unless the requirement clearly calls for them.
- Respect the memory constraints of the ESP32-P4: internal SRAM is strictly reserved for FreeRTOS tasks and system buffers; all DuckDB heap
  allocations must use PSRAM.
- Keep docs and examples aligned with actual firmware and shell behavior.

## Writing Style

- Write in simple, plain English. Use short sentences and everyday words.
- Use Oxford commas in inline lists: "a, b, and c" not "a, b, c".
- Do not use em dashes. Restructure the sentence, or use a colon or semicolon instead.
- Avoid colorful adjectives and adverbs. Write "TCP proxy" not "lightweight TCP proxy", "scoring components" not "transparent scoring components".
- Use noun phrases for checklist items, not imperative verbs. Write "redundant index detection" not "detect redundant indexes".
- Headings in Markdown files must be in the title case: "Build from Source" not "Build from source". Minor words (a, an, the, and, but, or, for, in,
  on, at, to, by, of, is, are, was, were, be) stay lowercase unless they are the first word.

## Repository Layout

- `src/firmware/main/`: Source code for the standalone SQL feasibility probe firmware (`probe.cpp`, `llroundl.cpp`).
- `src/firmware/shell/main/`: Source code for the MicroShell firmware.
    - `main.c`: Shell entry point, command dispatch, and FreeRTOS tasks.
    - `duckdb_console.cpp`: Interactive DuckDB console integration (`duckdb>`) and one-shot `-c` query runner.
    - `duckdb_console.h`: Declarations for DuckDB console functions.
    - `editor.c`: In-place text editor implementation.
    - `path_util.c`: Path normalization and completion utilities.
    - `flash_stack_shim.c`: LittleFS SPI flash worker thread shim.
- `src/firmware/shell/test/`: Unit tests for shell path handling, editor, autocompletion, filesystem, and Wi-Fi helpers.
- `src/patches/duckdb-esp32p4.diff`: Portability patch applied against the upstream DuckDB 2.0 submodule.
- `src/cmake/`: CMake toolchain files for cross-compiling DuckDB to ESP32-P4 (`esp32p4.cmake`, `esp32p4-clang.cmake`).
- `scripts/`: Build automation and hardware validation scripts.
    - `scripts/build-probe.sh`: Cross-compilation script for the probe firmware.
    - `scripts/build-shell.sh`: Build script for the MicroShell firmware.
    - `scripts/check-board.py`: Serial hardware test script for probe firmware.
    - `scripts/check-shell.py`: Serial hardware test script for MicroShell.
    - `scripts/check-features.py`: Hardware test script for LittleFS, JSON, and Parquet on the board.
    - `scripts/isolate-crypto.py`: Renaming script for mbedTLS symbol isolation.
- `test/`: Host tests for cross-platform math shims, Unicode parsing, and Python tooling.
    - `test/test_unicode.cpp`: utf8proc regression tests on the host.
    - `test/test_llroundl.cpp`: RISC-V math shim precision and recursion tests on the host.
    - `test/test_local_file_open.py`, `test/test_scripts.py`: Host Python regression tests.
- `external/`: Pinned Git submodules (`duckdb`, `esp-idf`, `esp_hosted`, `esp_wifi_remote`, and `esp_littlefs`).
- `Makefile`: Build entry points, host test runners, formatting, and flashing commands.
- `flake.nix`: Reproducible development shell with compiler toolchains and dependencies.
- `pyproject.toml`: Python dependency configuration for tools and scripts.
- `.github/workflows/`: CI workflows for linter checks (`lints.yml`) and host test execution (`tests.yml`).

## Architecture Notes

### DuckDB Portability and Patching

DuckDB 2.0 is included as a Git submodule in `external/duckdb`.
Modifications to DuckDB are stored as a unified patch in `src/patches/duckdb-esp32p4.diff`.
The patch handles:

- Memory allocation routing to PSRAM (`SPIRAM`) rather than internal SRAM.
- Disabling unsupported POSIX syscalls (`mlock`, `madvise`, and dynamic threading primitives not present in newlib or FreeRTOS).
- Filesystem adaptations for FATFS (`/sdcard`) and LittleFS (`/data`).
- Workarounds for compiler bugs and missing standard library functions on RISC-V 32-bit (such as `llroundl`).

When updating DuckDB behavior, update the patch file or generate a fresh diff against `external/duckdb`.

### Firmware Targets

The repository produces two distinct firmware targets:

1. Feasibility probe (`src/firmware`): A minimal binary that boots, initializes PSRAM, runs a set of hardcoded DuckDB queries, prints results to
   serial, and halts. Used to verify core engine feasibility and compilation size.
2. Interactive shell (`src/firmware/shell`): A full-featured interactive shell (`msh`) running on FreeRTOS. It provides standard Unix-like utilities,
   an in-memory or SD-card-backed DuckDB console (`duckdb>`), flash memory mounting via LittleFS, and Wi-Fi management via ESP-Hosted.

### Memory Architecture and PSRAM

The target board is the Waveshare ESP32-P4-WIFI6 (revision v1.3 with 32 MB PSRAM and 32 MB flash).
Memory constraints require strict discipline:

- Internal SRAM is scarce and must be conserved for FreeRTOS task stacks, DMA buffers, and interrupt service routines.
- DuckDB buffer manager memory is capped at 8 MB with temporary spilling disabled.
- FreeRTOS tasks running DuckDB queries require dedicated worker stacks allocated in PSRAM.
- Buffer resizing operations must preserve `max_align_t` alignment boundaries.

### Toolchain Integration

Cross-compilation is driven by ESP-IDF v6.1 using either `riscv32-esp-elf` (GCC 15.2) or `esp-clang` (LLVM 21.1.3).
Size optimization (`-Oz` or `-Os`) is mandatory to ensure `.flash.text` stays within the 16 MB ESP32-P4 virtual MMU instruction cache limit.

## Generated and Derived Files

- `build/`: Generated build output, object files, and compiled firmware binaries.
- `src/patches/duckdb-esp32p4.diff`: Tracked patch file for upstream DuckDB. Submodule files under `external/duckdb` are modified by applying this
  patch.
- Do not edit files inside `external/` directly unless updating a submodule pointer or testing a new patch.

## Code Conventions

### C and C++ Conventions

- Follow C11 for shell C code and C++17 for DuckDB and C++ firmware code.
- Format code using `make format` (Google style, 4-space indent, and 100-character line limit).
- Static analysis must pass `make lint` (`cppcheck`).
- Avoid dynamic allocations in high-frequency interrupt paths or internal SRAM.
- Release all acquired system resources and file descriptors to avoid leaks on embedded storage.

### Python Conventions

- Python code targets Python 3.15 or newer (managed by Nix or `uv`).
- Standard library `unittest` is used for host test suites.
- Follow PEP 8 guidelines.

## Required Validation

Run the narrowest relevant checks, then expand if the change crosses layers.

| Area                 | Command                             | Use When                                                     |
|----------------------|-------------------------------------|--------------------------------------------------------------|
| Code formatting      | `make format`                       | C or C++ source files changed                                |
| Static analysis      | `make lint`                         | C++ or test files changed                                    |
| Host tests           | `make test`                         | Portability shims, shell helpers, or host test logic changed |
| Shell unit tests     | `make shell-test`                   | Shell path, editor, completion, or Wi-Fi code changed        |
| Probe unit tests     | `make probe-test`                   | Unicode or math shim code changed                            |
| Git hooks            | `make test-hooks`                   | Before pushing commits or creating pull requests             |
| Probe build          | `make probe-build`                  | Changes to probe firmware, DuckDB patch, or CMake toolchains |
| Shell build          | `make shell-build`                  | Changes to shell firmware or DuckDB integration              |
| Hardware probe check | `make check-probe TTY=/dev/ttyACM0` | Hardware verification of the probe firmware                  |
| Hardware shell check | `make check-shell TTY=/dev/ttyACM0` | Hardware verification of the shell firmware                  |

Minimum expectations:

- Shell helper or host test changes: `make lint` and `make test`.
- Code formatting updates: `make format`.
- Firmware C or C++ changes: `make format`, `make lint`, `make test`, and `make shell-build` (or `make probe-build`).
- Pre-submission check: `make test-hooks`.

## Testing Expectations

- Host tests in `test/` and `src/firmware/shell/test/` run without physical hardware on standard Linux environments.
- CI workflows run `make lint` and `make test` on every push and pull request.
- Hardware-in-the-loop checks (`check-probe.py`, `check-shell.py`, and `check-features.py`) require a physical ESP32-P4 board connected over serial
  (`TTY=/dev/ttyACM0`).
- If adding shell commands, path utilities, or math shims, add corresponding unit tests under `test/` or `src/firmware/shell/test/`.

## Change Design Checklist

Before coding:

1. Identification of the affected layer (DuckDB patch, firmware shell, shims, build scripts, or host tests).
2. Assessment of memory budget and allocation target (internal SRAM versus PSRAM).
3. Consideration of binary size impact on the 16 MB instruction cache limit.
4. Selection of host test cases and hardware verification scripts.

Before submitting:

1. Clean run of `make format`, `make lint`, and `make test`.
2. Passing status for Git hooks via `make test-hooks`.
3. Successful compilation via `make probe-build` or `make shell-build` for firmware changes.
4. Real hardware test confirmation noted in the description for hardware-touching changes.

## Commit and Pull Request Hygiene

- Keep commits scoped to one logical change.
- Mention affected layers when relevant: for example, "update shell path resolution" or "patch DuckDB allocator for PSRAM".
- Pull request descriptions should include:
    1. Summary of behavioral changes.
    2. Record of local validation runs (host tests and hardware checks if tested on board).
    3. Note on memory and binary size impact if applicable.

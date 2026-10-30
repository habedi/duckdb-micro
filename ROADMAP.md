## Feature Roadmap

### 1. Core SQL Engine and Portability

* **Toolchain and Cross-Compilation**
    * [x] ESP-IDF v6.1 cross-compilation support using `riscv32-esp-elf`.
    * [x] Espressif LLVM (`esp-clang` v21.1.3) toolchain with aggressive size optimization (`-Oz`), fitting `.flash.text` under the 16 MB ESP32-P4
      virtual MMU instruction cache limit.
    * [x] Local-exec TLS model (`-ftls-model=local-exec`) resolving microcontroller thread-local storage relocations.
    * [x] Reproducible developer environment via Nix flake.
    * [x] Upstream DuckDB 2.0 submodule integration with patch automation (`src/patches/duckdb-esp32p4.diff`).
    * [x] Symbol conflict isolation renaming bundled mbedTLS symbols against ESP-IDF crypto libraries.
* **Architecture and Memory Model**
    * [x] Target ESP32-P4 revision v1.3 with 32 MB PSRAM (16-line 200 MHz) and 32 MB flash.
    * [x] 8 MB buffer-manager memory limit with temporary spilling disabled.
    * [x] PSRAM allocation priority to preserve scarce internal SRAM for task stacks and FreeRTOS mutexes.
    * [x] Allocation alignment preserving `max_align_t` boundaries across buffer resizing.
    * [x] Non-swapping memory management (omitting unsupported `mlock`/`madvise` calls).
    * [x] Custom libc math shim (`llroundl`) resolving GCC 15.2 RISC-V recursion and precision issues.
    * [x] Optional dual-core query execution using a CPU 1 FreeRTOS worker with a 64 KB PSRAM stack (`SET threads=2`; one thread by default).

---

### 2. Storage and Filesystem Integration

* **SD Card and FATFS**
    * [x] MicroSD card FATFS VFS driver mounted at `/sdcard`.
    * [x] Querying external CSV files from `/sdcard` via DuckDB `read_csv`.
    * [x] POSIX-compatible overwrite handling in `LocalFileSystem::MoveFile` over FatFS `f_rename`.
* **Database Persistence and File Formats**
    * [x] In-memory database sessions (`:memory:` default).
    * [x] On-disk DuckDB database files on `/sdcard` (`duckdb [FILE]` and `ATTACH`).
    * [x] Internal SPI flash storage support via LittleFS mounted at `/data` with PSRAM flash-stack worker shim.
    * [x] Parquet read (`read_parquet`) and write (`COPY ... TO ... (FORMAT PARQUET)`) support.
    * [x] JSON extension support (`read_json`, `json_extract`, `json_array_length`).
    * [ ] Durability and WAL checkpoint recovery validation after abrupt power loss.
    * [ ] Cluster alignment and block size tuning (4 KiB vs. 256 KiB) for SD media performance.

---

### 3. Interactive Shell and Developer Tooling

* **Command Line Interface (`msh` / MicroShell)**
    * [x] Interactive serial REPL over UART0 (CH343 USB bridge, 115200 baud).
    * [x] Filesystem navigation and management (`ls`, `cd`, `pwd`, `mkdir`, `rm`, `cp`, `cat`, `write`).
    * [x] In-place text editor (`nano` / `edit`) with multi-line buffering and key navigation.
    * [x] Tab completion for commands, filesystem paths, and `/sdcard` directories.
    * [x] Graceful `Ctrl+C` interrupt handling without terminating the shell session.
* **DuckDB Console (`duckdb>`)**
    * [x] Interactive `duckdb>` SQL REPL with query timing and memory statistics.
    * [x] One-shot command execution (`duckdb -c "SQL"` and `duckdb [FILE] -c "SQL"`).
    * [x] Upstream DuckDB CLI mode with multiline SQL, output formats, and file commands (`duckdb --cli`).
    * [x] Clean prompt separation with temporary suppression of shell tab completion.
    * [x] Dot-commands in the SQL console (like `.quit`, `.exit`, `.help`,and `.tables`).
    * [x] Result pagination for large query outputs in the serial terminal (25 rows per page; Space advances, and q stops output).

---

### 4. Networking and Remote Querying

* **Wireless Transport (ESP32-C6 Co-Processor)**
    * [x] ESP-Hosted SDIO transport initialization communicating with onboard ESP32-C6.
    * [x] Wi-Fi 6 station connection management and credential persistence.
    * [x] Shell commands for wireless networking (`wifi_scan`, `wifi_connect`, `ifconfig`, `ping`).
* **Remote Data and Protocols**
    * [x] HTTP/HTTPS downloader for remote CSV files (`fetch_csv`).
    * [x] Read-only REST server for queries (`query_server`).

---

### 5. Validation, Testing and Reliability

* **Host Regression Tests**
    * [x] Unicode decomposition and UTF-8 boundary regression tests (`test_unicode`).
    * [x] Long-double rounding and precision regression tests (`test_llroundl`).
    * [x] Shell path resolution, editor logic, autocomplete, and FATFS simulation tests.
    * [x] Automated static analysis (`cppcheck`, `clang-format`) and Flake evaluation.
* **Hardware-in-the-Loop (HIL) Validation**
    * [x] Standalone SQL self-check firmware probe (`scripts/check-board.py`).
    * [x] Interactive shell verification script (`scripts/check-shell.py`).
    * [x] Feature verification script (`scripts/check-features.py`) testing LittleFS, JSON, and Parquet on a physical board.
    * [x] Stress testing and memory fragmentation benchmarks (`make check-stress`).
    * [x] TPC-H subset runner with automatic serial fixture loading, host reference results, query timings, and memory reports (`make check-tpch`).
    * [x] Hardware validation of TPC-H queries 1, 6, and 12 at scale factor 0.001.
    * [x] Hardware validation of TPC-H queries 1, 3, 6, 12, and 14 at scale factor 0.001 with a session-only 12 MB buffer limit.
    * [x] Hardware validation of TPC-H queries 1, 6, and 12 at scale factor 0.01 with a session-only 12 MB buffer limit.

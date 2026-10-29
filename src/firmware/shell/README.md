## DuckDB Shell Firmware

This directory contains the source code and configuration for MicroShell (`msh`).
MicroShell combines FreeRTOS, an interactive command shell, storage drivers, Wi-Fi networking, and DuckDB 2.0 on the ESP32-P4.

### Overview

The firmware runs on the Waveshare ESP32-P4-WIFI6 board.
It provides a serial terminal environment over UART0 at 115200 baud.
Users can inspect filesystems, edit text files, manage network connections, and execute SQL queries directly on the microcontroller.

Key components include:

- MicroShell command prompt (`msh>`) with tab completion and history navigation.
- In-place text editor (`nano` or `edit`) for file modifications on mounted storage.
- MicroSD storage mounted at `/sdcard` through the FATFS filesystem driver.
- Internal SPI flash storage mounted at `/data` through LittleFS.
- Wi-Fi 6 station support via the onboard ESP32-C6 coprocessor.
- Interactive DuckDB console (`duckdb>`) and one-shot execution via the `-c` flag.

### Build and Flash Instructions

Build and flash commands use the repository root Makefile.

1. Build the firmware binary:

```shell
make shell-build
```

2. Flash the firmware to the board:

```shell
make shell-flash TTY=/dev/ttyACM0
```

3. Connect to the serial console:

```shell
make console TTY=/dev/ttyACM0
```

### Partition Layout

The firmware uses custom partitions defined in `partitions.csv`:

- `nvs`: Non-volatile storage for Wi-Fi credentials and calibration data (24 KiB at `0x9000`).
- `phy_init`: Physical layer radio initialization data (4 KiB at `0xf000`).
- `factory`: Application partition holding the executable firmware image (26,560 KiB at `0x10000`).
- `storage`: LittleFS partition mounted at `/data` (6,144 KiB at `0x1a00000`).

### Shell Commands

MicroShell provides standard utility commands:

- Filesystem navigation: `pwd`, `cd`, `ls`, and `tree`.
- File manipulation: `cat`, `write`, `cp`, `rm`, and `mkdir`.
- File editing: `nano` and `edit` for terminal-based file editing.
- Storage inspection: `df` to view mounted filesystem capacity and usage.
- Wireless networking: `wifi_scan`, `wifi_connect`, `wifi_disconnect`, `wifi_forget`, `ifconfig`, and `ping`.
- Coprocessor control: `reset_c6` to reset the onboard ESP32-C6 wireless coprocessor.
- System diagnostics: `ps`, `top`, `free`, `uname`, and `cpuinfo`.
- System reboot: `reboot` to restart the microcontroller.

The editor writes a temporary file before replacing the saved file. On FAT, it keeps the original in a `.msh-backup-*` file
beside the edited file until replacement succeeds. If replacement and restoration fail, use `ls` to find the backup and `cp`
to restore it. A power loss during FAT replacement can also leave the original at that backup path.

### Interactive DuckDB Console

Run `duckdb` from `msh>` to enter the interactive SQL console:

```text
msh> duckdb
Opening DuckDB: internal=125432 PSRAM=31457280
DuckDB 2.0.0, in-memory database; storage under /sdcard and /data.
One SQL statement per line. .quit returns to msh; tables disappear on exit.
duckdb>
```

Features and behavior of the console:

- Persistent databases: Run `duckdb /sdcard/database.db` to open or create an on-disk database.
- One-shot execution: Run `duckdb -c "SELECT 42;"` to execute SQL and return to `msh>`.
- Dot-commands: Use `.tables` to inspect tables, `.help` for examples, and `.quit` or `.exit` to return to `msh>`.
- Extensions: Core functions, Parquet reader and writer (`read_parquet`), and JSON (`read_json_auto`).
- Resource limits: Thread count is set to 1, buffer memory limit is capped at 8 MB, and spilling to disk is disabled.
- Execution stack: The SQL engine runs in a dedicated FreeRTOS task with a 64 KB stack allocated in PSRAM.

### Verification and Testing

Host and hardware tests validate shell features:

- Run host unit tests for path normalization, autocompletion, filesystem logic, and the text editor:

```shell
make shell-test
```

- Run hardware serial validation on a connected board:

```shell
make check-shell TTY=/dev/ttyACM0
```

### Stress Testing and Memory Trends

Run a smoke test with a mounted SD card and no other serial console attached:

```shell
make check-stress TTY=/dev/ttyACM0
```

The runner resets the board, runs 3 warm-up cycles, and measures 20 more cycles.
Each cycle opens and closes in-memory and persistent databases, checks aggregation and
join results, reads JSON, writes and reads Parquet, and verifies committed rows after reopening.
Workloads use 1,000 rows to stay within the firmware's 8 MB buffer limit.

For an eight-hour run after warm-up:

```shell
make check-stress TTY=/dev/ttyACM0 STRESS_ARGS='--duration 28800'
```

The unique serial log and CSV report are saved under `build/hardware/stress-*`.
The CSV records elapsed time, cycle time, free bytes, minimum free bytes, largest free block,
and fragmentation (`100 * (1 - largest / free)`) for internal SRAM and PSRAM after each cycle.
Minimum free bytes are allocator low-water marks since boot, not per-cycle measurements.

The test fails on firmware faults, allocation failures, SQL errors, incorrect results,
or a 60-second command timeout. It also fails when free memory or the largest block falls
more than 5% below the last warm-up sample for 3 consecutive measured cycles.
These thresholds flag sustained loss; they do not prove the absence of small leaks.
Use `STRESS_ARGS='--cycles 100 --warmup 5 --max-loss-percent 2'` to adjust them.
Duration runs finish complete cycles and measure at least 3 cycles.

Files use a unique `duckdb-stress-*` prefix and are removed after success.
Failed runs retain their files for inspection; the runner prints their prefix.
`STRESS_ARGS='--storage /data'` uses LittleFS instead of SD storage, but repeated writes
consume flash erase cycles. These tests cover clean reopen behavior, not abrupt power loss.

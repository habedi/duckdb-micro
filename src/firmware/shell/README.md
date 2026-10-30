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

The hardware smoke test on October 29, 2026, stopped at cycle 6 after all SQL and persistence
checks passed. Internal SRAM's largest free block fell from 192,512 bytes after warm-up
to 114,688 bytes, a 40.4% drop over 3 measured cycles. Free internal SRAM fell from
490,147 to 485,539 bytes. PSRAM's largest block stayed at 29,360,128 bytes.
The capture is `build/hardware/stress-157dfd8039b54759a17ba867435f8e04.log`, with a matching
`.csv` report. Isolated hardware tests found stable memory for empty sessions, `SELECT 1`,
persistent writes, JSON, and Parquet. Joins retained 16 mutexes per run, accounting for
1,536 bytes of internal SRAM. Tuple-data chunks allocated these mutexes in an arena without
calling their destructors. The portability patch now gives each chunk ownership of its mutex,
including during moves and resets, so it releases the FreeRTOS resources before arena cleanup.
This adds one pointer per chunk in PSRAM and no additional heap allocations.

Rebuilding also exposed an old Picolibc file-flag remapping that prevented file creation with
the pinned Newlib configuration. The patch now uses the flags from the matching libc headers.

The fixed firmware passed 3 warm-up cycles and 100 measured cycles on the same board
in 854.5 seconds. Internal SRAM stayed at 490,703 free bytes and a 249,856-byte largest block
throughout the measured run. PSRAM free bytes ranged from 32,598,952 to 32,598,992, and
its largest block stayed at 29,360,128 bytes. All SQL, transaction, and reopen checks passed,
and the runner removed its test files. The capture and CSV are under
`build/hardware/stress-7ca7727d36024fff8798d57318c8e1e2.*`.
The shell regression also passed, including CSV access, pagination, and one-shot queries.
The LittleFS, JSON, and Parquet feature checks passed on `/data` as well.
The shell's `.flash.text` is 15,583,858 bytes, below the 16 MiB limit, and its application
partition has 8% free space. The first eight-hour SD-card soak stopped after three warm-up
cycles and one measured cycle. Its capture and CSV are under
`build/hardware/stress-ef1270d98e4040d69ca25b16698906ea.*`; it did not establish an eight-hour result.

On October 30, 2026, host tests, static analysis, formatting, and Git hooks passed.
The probe and shell rebuilt successfully under Nix. Shell `.flash.text` remained at
15,583,858 bytes, and the application partition retained 8% free space. The board continues
to use the firmware from the previous successful hardware run.
A hardware check passed three warm-up cycles and three measured cycles, with internal SRAM
remaining at 490,703 free bytes and a 249,856-byte largest block. Its capture and CSV are under
`build/hardware/stress-e684195c3e8646d4acbf7afcf83615c5.*`.
The SD-card soak was restarted as a detached process, with capture and CSV under
`build/hardware/stress-6c43c384dc5145d3bf20826ee7326e8e.*`.
Runner output and the final exit code are saved to
`build/hardware/soak-ace6912f0586465082949555af9bd5a4.out`, with the process ID in the matching
`.pid` file. To inspect the saved result, run:

```bash
tail -n 10 build/hardware/soak-ace6912f0586465082949555af9bd5a4.out
```

A completed successful run normally prints `PASS` and `EXIT_CODE=0`.

The restarted run was limited to one hour of total runner time at the user's request.
A detached timer stopped it at approximately 11:10 CET on October 30, with exit code 130.
Timer status is saved in `build/hardware/soak-ace6912f0586465082949555af9bd5a4.stop.out`.
The CSV records three warm-up cycles and 423 completed measured cycles, ending at 3,588.201
seconds. Internal SRAM stayed at 490,703 free bytes and a 249,856-byte largest block.
PSRAM free bytes ranged from 32,598,964 to 32,598,996, with a 29,360,128-byte largest block.
No workload or memory-threshold failures were reported before the intentional interruption.
The interrupted cycle was not verified, and the runner did not print its normal `PASS`
message. The run's JSON, Parquet, and DuckDB files were then removed, and a directory listing
confirmed that no files with this run's prefix remained. The cleanup capture is
`build/hardware/soak-cleanup-6c43c384.log`.
The roadmap item is complete for the agreed one-hour scope. Eight-hour testing is optional;
this run does not establish eight-hour stability or power-loss recovery.

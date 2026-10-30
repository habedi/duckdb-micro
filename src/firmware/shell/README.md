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

`make console` sends CR for Enter.
With another serial terminal, select CR line endings to avoid an extra empty prompt from CRLF input.

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
- SQL input: Enter submits one complete statement on the current line. The final semicolon is optional.
  Multiple statements on one line are rejected before execution. Multiline SQL is unsupported.
- Results: Read-only SELECT results stream as they are displayed. Stopping output cancels the remaining
  SELECT work; `25+ row(s)` means the full row count is unknown. Writes finish even when output stops.
  Control characters in column names and values are escaped, including `\0`, `\n`, and `\t`.
- Dot-commands: Use `.tables` to inspect tables, `.help` for examples, and `.quit` or `.exit` to return to `msh>`.
- Extensions: Core functions, Parquet reader and writer (`read_parquet`), and JSON (`read_json_auto`).
- Resource limits: One query thread by default, an 8 MB buffer memory limit, and spilling disabled.
- Query execution: The calling task runs on CPU 0, and one pthread-backed FreeRTOS worker runs on CPU 1 with a 64 KB PSRAM stack.
  `SET threads=1` disables the worker; `SET threads=2` restores it. More than two query threads and asynchronous workers are rejected.
- Execution stack: The SQL engine runs in a dedicated FreeRTOS task with a 64 KB stack allocated in PSRAM.

The masked Wi-Fi password prompt ignores blank lines and supports Backspace and Ctrl+C.
For an open network, pass an explicit empty password: `wifi_connect "SSID" ""`.

### Upstream DuckDB CLI

Run `duckdb --cli` or `duckdb --cli /sdcard/database.duckdb` for the ported upstream CLI.
The plain `duckdb` command keeps the compact console described above.

```text
msh> duckdb --cli
DuckDB upstream CLI. End SQL with ;. .help lists commands; .quit returns to msh.
duckdb> SELECT
   ...> 6 * 7 AS answer;
```

This mode accepts multiline SQL and multiple statements. End SQL with a semicolon.
It includes upstream table, box, column, CSV, JSON, JSON Lines, Markdown, list, tabs,
line, quote, HTML, LaTeX, Tcl, ASCII, insert, and trash output modes. Use `.mode MODE`
and `.headers on|off` to choose the output. `.help` lists the available commands.

Useful commands include `.tables`, `.schema`, `.indexes`, `.databases`, `.manual`,
`.last`, `.timer`, `.changes`, `.separator`, `.nullvalue`, `.open`, `.import`,
`.read`, `.output`, and `.once`. File paths use MicroShell's current directory.
CSV imports default to a 64 KiB input buffer. Up/Down navigate history; Tab completes
keywords and dot commands, with at most 16 suggestions.

The CLI uses a 96 KiB PSRAM task stack, an 8 MB buffer limit, and no temporary spilling.
SQL input is limited to 16 KiB across lines, and `.read` allows four nested files.
Converted display data is limited to 2 MiB. Width-aware modes may collect a complete
result before display, so use `LIMIT` for large queries. Exceeding a limit reports an error.

Ctrl+C cancels pending input at the prompt. Interruption of a running query is unavailable.
External pagers, system commands, external editors, browser launchers, working-directory
changes, and the upstream SQL history view are unavailable. `.quit`, `.exit`, and Ctrl+D
return to MicroShell. The compact console retains its serial pager.

Run `make check-cli TTY=/dev/ttyACM0` to check output modes, multiline SQL, file commands,
database reopening, and heap usage across repeated sessions.

### Remote CSV Downloads

Connect Wi-Fi, then download a CSV file to a new local path:

```text
msh> wifi_connect
msh> fetch_csv https://example.org/data.csv /sdcard/data.csv
msh> duckdb -c "SELECT * FROM read_csv('/sdcard/data.csv') LIMIT 10;"
```

`fetch_csv` supports HTTP and HTTPS, needs HTTP status 200, and limits downloads to 16 MiB.
It streams data in 2 KiB buffers on a temporary 64 KiB PSRAM task stack.
TLS allocations use PSRAM. Existing files are never overwritten. Failed downloads remove their partial files.
Redirects are rejected; use the final URL. The command downloads bytes without checking CSV syntax.
HTTPS verifies the server hostname, certificate chain, and certificate dates using the ESP-IDF CA bundle.
On the first HTTPS download after boot, it synchronizes the system clock with `pool.ntp.org`.
DNS and outbound NTP access are needed for that initial synchronization.

Hugging Face text files can also contain CSV data. Use the raw file URL rather than the HTML `blob` page:

```text
msh> fetch_csv https://huggingface.co/datasets/habedi/graphina-graphs/raw/main/wikipedia_chameleon.txt /sdcard/chameleon.csv
msh> duckdb -c "SELECT count(*) FROM read_csv('/sdcard/chameleon.csv', buffer_size=65536, max_line_size=1024);"
```

The network check uses a pinned revision of this graph dataset and verifies its edge count and endpoint sums.
Downloads stream to local storage; direct SQL scans of HTTP URLs are not supported.

### REST Query Server

The REST server is off at boot. Start it with an existing database file:

```text
msh> query_server start /sdcard/database.duckdb
```

The command prints a fresh bearer token. From a computer on the same trusted network, use the board's
address from `ifconfig` and that token:

```shell
curl --fail-with-body http://BOARD_IP:8080/query \
  -H 'Authorization: Bearer TOKEN_FROM_SERIAL' \
  -H 'Content-Type: text/plain' \
  --data 'SELECT count(*) FROM samples;'
```

Responses contain `columns`, `rows`, and `truncated`. Values are strings, with SQL NULL represented by JSON null.
The endpoint accepts one SELECT statement, opens the selected database read-only for each request, and disables
external file access. Close any serial database session before sending a request. The console and REST server
share a busy guard, so they cannot open separate SQL engines at the same time. Busy requests return HTTP 503;
invalid queries return HTTP 400, and missing or incorrect tokens return HTTP 401.
Requests are limited to 4 KiB, responses to 64 KiB, and returned rows to 100. Larger responses fail rather than
returning incomplete JSON. Queries use one thread, an 8 MB buffer limit, and no spilling. The HTTP task uses a
60 KiB PSRAM stack. There is no query deadline, so expensive queries can keep the server busy until they finish.

Stop the server with `query_server stop`. Starting it again creates a new token.
The server uses unencrypted HTTP. Tokens and query results are visible to network observers; use only a trusted
network, and do not expose port 8080 to the Internet. WebSockets and remote database writes are not supported.

With a connected board, mounted SD card, saved Wi-Fi credentials, Internet access, and a host reachable from the
board, run:

```shell
make check-network TTY=/dev/ttyACM0
```

The runner serves temporary CSV fixtures from the host, checks HTTP and HTTPS downloads, validates REST results
and access controls, and checks memory across five server restarts. Its capture is under `build/hardware/network-*`.
Successful runs remove their test files; failed runs retain them for inspection. A host firewall must allow the
board to reach the runner's temporary HTTP port.

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

### TPC-H Subset Benchmarks

The default benchmark uses the pinned DuckDB SQL for Q1, Q6, and Q12.
These cover scans, aggregates, and a join. Q3 and Q14 are available as optional queries.
No TPC-H extension is needed in the firmware.
Use a host DuckDB CLI that has the `tpch` extension installed, a mounted SD card,
and no other serial console attached:

```shell
duckdb -c 'INSTALL tpch;'
make check-tpch TTY=/dev/ttyACM0
```

The default scale factor is 0.001, with about 6,000 line items. If the manifest is
missing, the runner prepares `build/tpch/tpch.duckdb` and `build/tpch/manifest.json`,
including expected rows, the host version, and a database SHA-256 digest.
Generation and reference queries run on the host with one thread. Host artifacts
are reused on later runs and stay ignored by Git. `make prepare-tpch` also allows
preparation separately and refuses to overwrite an existing output directory.

The runner verifies the host digest, resets the board, creates a unique
`/sdcard/tpch-*.duckdb`, and loads the host schema and rows over serial. Transactions
and sessions are limited to 20 insert batches to bound internal SRAM use. Each table's
row count is checked. Loading can take several
minutes and is excluded from query timings. No manual file copy is required.

The prepared SD database is then attached read-only. Each query
has one warm-up execution and three measured executions. Every execution uses a
fresh session, so warm-up does not keep a DuckDB buffer cache alive. The CSV records
firmware query time, excluding session setup and serial output, plus free memory and
the largest free block in internal SRAM and PSRAM after closing the session.
Results must match the host in order, with numeric tolerance of `max(1e-8, abs(expected) * 1e-9)`.
SQL errors, incorrect results, firmware faults, and command timeouts fail the run.

Logs, CSV reports, and a copy of the reference manifest and run settings are saved
under `build/hardware/tpch-*`. Automatically created SD databases are removed after
success and retained after failure for inspection. TLS allocations use PSRAM. Existing files are never overwritten.
The recorded digest identifies the host fixture; serial loading creates a new database
with the same table contents, rather than copying its binary representation.
The firmware defaults to one query thread and an 8 MB buffer limit with spilling disabled.
The subset is a workload experiment, not an official TPC-H result.

For scale factor 0.01, select a separate fixture directory and the scale:

```shell
make check-tpch TPCH_MANIFEST=build/tpch-001/manifest.json TPCH_ARGS='--scale 0.01'
```

Use `TPCH_ARGS='--repeats 5 --timeout 180'` to change hardware repetitions or the
per-command timeout. `TPCH_ARGS='--database /sdcard/tpch.duckdb'` uses an existing
database without loading or deleting it. Its contents must match the host manifest.
`TPCH_ARGS='--queries 1 6'` selects fewer queries. Q3 and Q14 exceed the current 8 MB
buffer limit at scale factor 0.001 and are excluded from the default subset; use
`--queries 3 14` to investigate that limit. Larger queries can also exhaust the board's
memory budget.

For a session-only 12 MB query buffer test, leaving the firmware default at 8 MB:

```shell
make check-tpch TPCH_ARGS='--memory-limit-mb 12 --queries 1 6 3 12 14'
```

The selected limit is applied in each query session and saved in the run settings.
Imports retain the firmware limit. The CSV also records minimum free bytes since boot;
this low-water mark includes loading and earlier queries, rather than measuring each query alone.
Use `--threads 2` in `TPCH_ARGS` to benchmark the CPU 1 worker. Thread count is saved
with the run settings. Parallel execution can increase memory use and does not guarantee a speedup.

### Dual-Core Verification

```shell
make check-dualcore TTY=/dev/ttyACM0
```

This check runs five sessions with two independent scans of one million rows each.
It compares aggregate results, switches between one and two query threads, and needs CPU 1 execution
with a PSRAM stack before and after restarting the worker. Post-session memory
checks use the same sustained-loss thresholds as the stress runner. Captures are
saved under `build/hardware/dualcore-*`.

The standalone probe remains configured for one thread. Small queries may use only
one core, and parallel queries can need more buffer memory than single-threaded queries.
